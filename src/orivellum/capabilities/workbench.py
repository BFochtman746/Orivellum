"""Project Workbench — agentic build / edit / repair with version history.

The user describes a project (an Excel workbook or a small code project) in
plain words. The local LLM writes ONE Python build script; the script runs
in the same locked-down sandbox the Document Workshop uses (no network,
scrubbed environment, resource caps). The script reads the previous
version's files from ``inputs/`` and writes the COMPLETE new state of the
project into ``out/``.

Every successful, verified iteration becomes an immutable version under
``data/workbench/{project}/v{n}/``. Verification before a version is
accepted:

- **xlsx** projects: at least one ``.xlsx`` output; every workbook must
  load cleanly in openpyxl in BOTH value and formula view.
- **code** projects: at least one output file; every ``.py`` must parse
  (``ast.parse``) and every ``.json`` must parse.

A failed build or failed verification stores the error on the project row
and creates NO version — the last good version remains the truth.

Completing a project zips every version plus a ``manifest.json`` with
SHA-256 hashes into ``data/workbench/archives/`` and marks the project
archived. Archived projects are read-only. Archival re-hashes every file on
disk and refuses to produce an archive that does not match the recorded
version manifests.

Concurrency: all mutating operations (build, revert, archive, delete) must
first claim the project via ``db.claim_wb_build()`` — an atomic
conditional UPDATE on ``building`` — so two requests can never build from
the same predecessor or archive mid-build.

Threat model note: the sandbox is the same one the Document Workshop uses —
scrubbed environment, no inherited network config, POSIX resource caps. It
is a guard against accidental damage from generated code on the operator's
own machine, not a hostile-code security boundary; Orivellum is a
single-operator local system and the operator reviews what they run.
"""

from __future__ import annotations

import ast
import hashlib
import json
import logging
import pathlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from typing import Any

logger = logging.getLogger(__name__)

KINDS = ("xlsx", "code")
_SCRIPT_TIMEOUT_S = 120
_MAX_FIX_RETRIES = 2
_MAX_OUTPUT_FILES = 200
_MAX_OUTPUT_BYTES = 25 * 1024 * 1024  # 25 MB per version
_CODE_CONTEXT_PER_FILE = 4000  # chars of each source file shown to the LLM
_CODE_CONTEXT_TOTAL = 24000


# ── Paths ─────────────────────────────────────────────────────────────────────


def _workbench_root(cfg) -> pathlib.Path:
    return pathlib.Path(getattr(cfg, "data_dir", "data")) / "workbench"


def project_dir(cfg, project_id: str) -> pathlib.Path:
    return _workbench_root(cfg) / project_id


def version_dir(cfg, project_id: str, version_no: int) -> pathlib.Path:
    return project_dir(cfg, project_id) / f"v{version_no}"


def archives_dir(cfg) -> pathlib.Path:
    return _workbench_root(cfg) / "archives"


# ── File helpers ──────────────────────────────────────────────────────────────


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot(dir_path: pathlib.Path) -> list[dict]:
    """Relative name + size + sha256 for every file under *dir_path*."""
    out = []
    for p in sorted(dir_path.rglob("*")):
        if p.is_file():
            out.append(
                {
                    "name": str(p.relative_to(dir_path)),
                    "size": p.stat().st_size,
                    "sha256": _sha256(p),
                }
            )
    return out


# ── Input description (what the LLM sees about the current version) ─────────


def _describe_inputs(kind: str, inputs: pathlib.Path) -> str:
    files = sorted(p for p in inputs.rglob("*") if p.is_file())
    if not files:
        return "(no existing files — this is the first build)"
    lines: list[str] = []
    if kind == "xlsx":
        from openpyxl import load_workbook

        for p in files:
            rel = p.relative_to(inputs)
            if p.suffix.lower() != ".xlsx":
                lines.append(f"- {rel} ({p.stat().st_size} bytes)")
                continue
            try:
                wb = load_workbook(p, read_only=True)
                for ws in wb.worksheets:
                    header = []
                    for row in ws.iter_rows(min_row=1, max_row=1, values_only=True):
                        header = [str(v) for v in row if v is not None][:12]
                    lines.append(
                        f"- {rel} :: sheet '{ws.title}' dims={ws.calculate_dimension()}"
                        f" headers={header}"
                    )
                wb.close()
            except Exception as exc:  # noqa: BLE001
                lines.append(f"- {rel} (unreadable: {exc})")
    else:
        budget = _CODE_CONTEXT_TOTAL
        for p in files:
            rel = p.relative_to(inputs)
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                lines.append(f"- {rel} (binary, {p.stat().st_size} bytes)")
                continue
            take = min(_CODE_CONTEXT_PER_FILE, max(0, budget))
            budget -= take
            snippet = text[:take]
            suffix = "" if len(text) <= take else f"\n... [truncated, {len(text)} chars total]"
            lines.append(f"### {rel}\n```\n{snippet}{suffix}\n```")
    return "\n".join(lines)


# ── LLM script generation ─────────────────────────────────────────────────────

_XLSX_RULES = """Allowed imports: openpyxl and the Python standard library ONLY
(json, csv, math, datetime, statistics, ...). NO network, NO pip.
Excel rules:
- Formulas are strings starting with '=' assigned to cells; use real cell
  references, never hardcoded results of other cells.
- Give every sheet a header row with bold font where it makes sense.
- Set sensible number formats (currency, dates, percentages).
- Never invent data the user did not ask for; leave input areas blank."""

_CODE_RULES = """Allowed imports in the BUILD SCRIPT: Python standard library ONLY.
The script's only job is to write the project's source files with open()/write.
The project files themselves may target any language the user asked for."""


def _system_prompt(kind: str) -> str:
    return (
        "You are the build engine of a project workbench. You write ONE complete "
        "Python script and nothing else — no prose, no markdown fences.\n"
        "The script runs in a sandbox with this contract:\n"
        "- Existing project files are in ./inputs/ (empty on first build).\n"
        "- Write the COMPLETE new state of the project into ./out/ — every file "
        "the project should contain after this change, not only changed files. "
        "Copy unchanged files from inputs/ to out/ (shutil.copy2 or re-create).\n"
        "- Print a one-line summary of what was built at the end.\n"
        + (_XLSX_RULES if kind == "xlsx" else _CODE_RULES)
    )


def _user_prompt(kind: str, brief: str, instruction: str, inputs_desc: str) -> str:
    return (
        f"PROJECT BRIEF:\n{brief}\n\n"
        f"CURRENT PROJECT FILES:\n{inputs_desc}\n\n"
        f"INSTRUCTION FOR THIS ITERATION:\n{instruction}\n\n"
        "Write the build script now."
    )


# ── Sandbox execution (reuses the Workshop's hardened sandbox pieces) ────────


def _run_build_script(script: str, workdir: pathlib.Path, cfg, db, request: str) -> dict:
    """Run *script* sandboxed with ./inputs and ./out available under
    *workdir*. Retries with LLM correction, same policy as the Workshop."""
    from orivellum.capabilities.llm import llm_call
    from orivellum.capabilities.workshop import (
        _SANDBOX_RUNNER,
        _clean_script,
        _sandbox_env,
        _sandbox_preexec,
    )

    current = script
    for attempt in range(_MAX_FIX_RETRIES + 1):
        script_path = workdir / "build_project.py"
        script_path.write_text(current, encoding="utf-8")
        runner_path = workdir / "_sandbox_runner.py"
        runner_path.write_text(_SANDBOX_RUNNER, encoding="utf-8")
        try:
            result = subprocess.run(
                [sys.executable, "-I", str(runner_path), str(script_path)],
                capture_output=True,
                text=True,
                timeout=_SCRIPT_TIMEOUT_S,
                cwd=str(workdir),
                env=_sandbox_env(str(workdir)),
                preexec_fn=_sandbox_preexec if sys.platform != "win32" else None,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Build script timed out ({_SCRIPT_TIMEOUT_S}s)"}
        stdout, stderr = result.stdout[:3000], result.stderr[:3000]
        if result.returncode == 0:
            return {"ok": True, "stdout": stdout, "script": current}
        if attempt >= _MAX_FIX_RETRIES:
            return {
                "ok": False,
                "error": f"Build failed after {attempt + 1} attempt(s):\n{stderr[-1000:]}",
                "stdout": stdout,
            }
        logger.warning("Workbench build attempt %d failed: %s", attempt + 1, stderr[:400])
        fix = llm_call(
            [
                {
                    "role": "system",
                    "content": "You are a Python debugging expert. A project build script failed. "
                    "Fix ONLY the errors shown. Return ONLY the corrected raw script.",
                },
                {
                    "role": "user",
                    "content": f"Request:\n{request[:500]}\n\nScript:\n```python\n{current}\n```\n\n"
                    f"Error:\n{stderr[-2000:]}",
                },
            ],
            cfg=cfg,
            db=db,
            purpose="workbench.fix",
            temperature=0.1,
            max_tokens=6000,
            timeout=90,
        )
        if not (fix.ok and fix.text):
            return {"ok": False, "error": f"Fix generation failed: {fix.error}"}
        current = _clean_script(fix.text)
    return {"ok": False, "error": "Max retries exceeded"}


# ── Verification ──────────────────────────────────────────────────────────────


def _verify_output(kind: str, out_dir: pathlib.Path) -> tuple[bool, dict]:
    files = [p for p in out_dir.rglob("*") if p.is_file()]
    checks: dict[str, Any] = {"file_count": len(files)}
    if not files:
        checks["error"] = "build produced no files"
        return False, checks
    if len(files) > _MAX_OUTPUT_FILES:
        checks["error"] = f"too many output files ({len(files)})"
        return False, checks
    total = sum(p.stat().st_size for p in files)
    checks["total_bytes"] = total
    if total > _MAX_OUTPUT_BYTES:
        checks["error"] = f"output too large ({total} bytes)"
        return False, checks

    problems: list[str] = []
    if kind == "xlsx":
        from openpyxl import load_workbook

        workbooks = [p for p in files if p.suffix.lower() == ".xlsx"]
        checks["workbooks"] = len(workbooks)
        if not workbooks:
            problems.append("no .xlsx file produced")
        for p in workbooks:
            rel = str(p.relative_to(out_dir))
            for data_only in (False, True):
                try:
                    load_workbook(p, data_only=data_only).close()
                except Exception as exc:  # noqa: BLE001
                    problems.append(
                        f"{rel} fails to load ({'values' if data_only else 'formulas'}): {exc}"
                    )
                    break
    else:
        for p in files:
            rel = str(p.relative_to(out_dir))
            if p.suffix == ".py":
                try:
                    ast.parse(p.read_text(encoding="utf-8", errors="replace"))
                except SyntaxError as exc:
                    problems.append(f"{rel}: syntax error line {exc.lineno}: {exc.msg}")
            elif p.suffix == ".json":
                try:
                    json.loads(p.read_text(encoding="utf-8", errors="replace"))
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"{rel}: invalid JSON: {exc}")
    checks["problems"] = problems
    return not problems, checks


# ── Main entry points ─────────────────────────────────────────────────────────


def run_build(db, cfg, project_id: str, instruction: str) -> None:
    """Build the next version of a project. Runs on the background executor.
    The caller must already hold the build claim (``db.claim_wb_build``);
    this function releases it in ``finally``. All outcomes (including
    failure) land on the project row."""
    from orivellum.capabilities.llm import llm_call
    from orivellum.capabilities.workshop import _clean_script

    proj = db.get_wb_project(project_id)
    if not proj or proj["status"] != "active":
        db.update_wb_project(project_id, building=0)
        return
    db.update_wb_project(project_id, last_error=None)
    try:
        versions = db.list_wb_versions(project_id)
        prev_no = versions[-1]["version_no"] if versions else 0

        with tempfile.TemporaryDirectory() as tmp:
            work = pathlib.Path(tmp)
            inputs = work / "inputs"
            out = work / "out"
            inputs.mkdir()
            out.mkdir()
            if prev_no:
                src = version_dir(cfg, project_id, prev_no)
                if src.is_dir():
                    shutil.copytree(src, inputs, dirs_exist_ok=True)

            desc = _describe_inputs(proj["kind"], inputs)
            gen = llm_call(
                [
                    {"role": "system", "content": _system_prompt(proj["kind"])},
                    {
                        "role": "user",
                        "content": _user_prompt(proj["kind"], proj["brief"], instruction, desc),
                    },
                ],
                cfg=cfg,
                db=db,
                purpose="workbench.build",
                temperature=0.2,
                max_tokens=8000,
                timeout=180,
            )
            if not (gen.ok and gen.text):
                raise RuntimeError(f"model call failed: {gen.error or 'empty reply'}")
            script = _clean_script(gen.text)

            run = _run_build_script(script, work, cfg, db, instruction)
            if not run["ok"]:
                raise RuntimeError(run["error"])

            ok, checks = _verify_output(proj["kind"], out)
            if not ok:
                raise RuntimeError(
                    "verification failed: "
                    + "; ".join(checks.get("problems") or [checks.get("error", "unknown")])
                )

            # Accept: publish files FIRST (staging dir + atomic rename), and
            # only then commit the version row — a crash can leave an unused
            # staging dir behind, but never a version row without files.
            files = _snapshot(out)
            note = (run.get("stdout") or "").strip()[:500]
            row = _publish_version(db, cfg, project_id, out, instruction, files, checks, note)
        logger.info("Workbench %s built v%d", project_id, row["version_no"])
    except Exception as exc:  # noqa: BLE001
        # Surfaced to the user on the project row — never swallowed.
        logger.exception("Workbench build failed for %s", project_id)
        db.update_wb_project(project_id, last_error=str(exc)[:500])
    finally:
        db.update_wb_project(project_id, building=0)


def _publish_version(
    db,
    cfg,
    project_id: str,
    src_dir: pathlib.Path,
    instruction: str,
    files: list[dict],
    checks: dict | None,
    note: str = "",
) -> dict:
    """Copy *src_dir* into the project as the next version: stage, insert the
    row, atomically rename staging → v{n}. If the rename fails the row is
    deleted again, so a verified row always has its files."""
    import uuid as _uuid_mod

    pdir = project_dir(cfg, project_id)
    pdir.mkdir(parents=True, exist_ok=True)
    staging = pdir / f".staging-{_uuid_mod.uuid4().hex}"
    shutil.copytree(src_dir, staging)
    try:
        row = db.create_wb_version(
            project_id, instruction, files, checks=checks, verdict="verified", note=note
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    dest = version_dir(cfg, project_id, row["version_no"])
    try:
        staging.replace(dest)
    except Exception:
        db.delete_wb_version(row["id"])
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return row


def revert_to(db, cfg, project_id: str, version_no: int) -> dict:
    """Copy an existing version's files forward as a NEW version (history is
    append-only; nothing is ever rewritten)."""
    src = version_dir(cfg, project_id, version_no)
    if not src.is_dir():
        raise FileNotFoundError(f"version v{version_no} has no files on disk")
    files = _snapshot(src)
    return _publish_version(
        db, cfg, project_id, src, f"Revert to v{version_no}", files, {"reverted_from": version_no}
    )


def archive_project(db, cfg, project_id: str) -> str:
    """Zip every version + a hash manifest; mark the project archived."""
    proj = db.get_wb_project(project_id)
    if not proj:
        raise FileNotFoundError("project not found")
    versions = db.list_wb_versions(project_id)
    if not versions:
        raise ValueError("nothing to archive — the project has no versions")

    archives_dir(cfg).mkdir(parents=True, exist_ok=True)
    safe_title = (
        "".join(c if c.isalnum() or c in "-_" else "-" for c in proj["title"])[:60] or "project"
    )
    zip_path = archives_dir(cfg) / f"{safe_title}_{project_id[:8]}.zip"

    from orivellum.version import code_version

    manifest = {
        "code_version": code_version(),
        "project": {
            "id": proj["id"],
            "title": proj["title"],
            "kind": proj["kind"],
            "brief": proj["brief"],
            "created_at": proj["created_at"],
        },
        "archived_at": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .isoformat(),
        "versions": [
            {
                "version_no": v["version_no"],
                "instruction": v["instruction"],
                "verdict": v["verdict"],
                "created_at": v["created_at"],
                "files": json.loads(v["files_json"] or "[]"),
            }
            for v in versions
        ],
    }
    # Integrity gate: every recorded file must exist on disk and re-hash to
    # the value stored when the version was accepted. Never produce an
    # archive that silently disagrees with the version history.
    for v in versions:
        vdir = version_dir(cfg, project_id, v["version_no"])
        for f in json.loads(v["files_json"] or "[]"):
            p = vdir / f["name"]
            if not p.is_file():
                raise RuntimeError(f"v{v['version_no']}/{f['name']} is missing on disk")
            if _sha256(p) != f["sha256"]:
                raise RuntimeError(
                    f"v{v['version_no']}/{f['name']} does not match "
                    "its recorded hash — refusing to archive"
                )

    tmp_path = zip_path.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        for v in versions:
            vdir = version_dir(cfg, project_id, v["version_no"])
            if not vdir.is_dir():
                continue
            for p in sorted(vdir.rglob("*")):
                if p.is_file():
                    z.write(p, f"v{v['version_no']}/{p.relative_to(vdir)}")
    tmp_path.replace(zip_path)
    db.update_wb_project(project_id, status="archived", archive_path=str(zip_path))
    return str(zip_path)


def delete_project(db, cfg, project_id: str) -> None:
    """Remove the project rows and its working files. The archive zip (if the
    project was archived) is intentionally kept."""
    db.delete_wb_project(project_id)
    pdir = project_dir(cfg, project_id)
    if pdir.is_dir():
        shutil.rmtree(pdir, ignore_errors=True)


def make_version_zip(cfg, project_id: str, version_no: int) -> pathlib.Path:
    """Build (or reuse) a downloadable zip of one version's files."""
    vdir = version_dir(cfg, project_id, version_no)
    if not vdir.is_dir():
        raise FileNotFoundError(f"version v{version_no} has no files on disk")
    zpath = project_dir(cfg, project_id) / f"v{version_no}.zip"
    tmp = zpath.with_suffix(".zip.part")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(vdir.rglob("*")):
            if p.is_file():
                z.write(p, str(p.relative_to(vdir)))
    tmp.replace(zpath)
    return zpath
