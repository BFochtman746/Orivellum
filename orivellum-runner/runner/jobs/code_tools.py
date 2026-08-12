"""Scanner orchestration — mature tools find the generic defects (doctrine D7).

The bespoke security-pattern era is over: ruff/bandit/mypy/tsc/eslint each do
their class better than a private regex list, and are maintained by someone
else. This module runs them, NORMALISES their output into the finding schema,
and — the fail-closed part — records every tool that could not run as a
TOOL-GAP finding: that defect class is unexamined, not clean.

Parsers are separated from runners so they are testable without the binaries.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

from .. import store
from ..config import CFG

CAP = 200  # per tool; overflow is disclosed, never silently dropped

INSTALL_HINT = {
    "ruff": "pip install ruff",
    "bandit": "pip install bandit",
    "mypy": "pip install mypy",
    "semgrep": "pip install semgrep",
    "tsc": "npm install -g typescript",
    "eslint": "npm install -g eslint",
}


def _rel(path, root):
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return str(path)


def _proc(cmd, cwd=None, timeout=900):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)


def _emit(run_id, rows, tool):
    for r in rows[:CAP]:
        store.add_finding(
            run_id,
            r["severity"],
            r["code"],
            r["ref"],
            r["title"],
            detail=r.get("detail", ""),
            fix=r.get("fix", ""),
            source=tool,
        )
    if len(rows) > CAP:
        store.add_finding(
            run_id,
            "INFO",
            f"{tool.upper()}-CAPPED",
            "(repository)",
            f"{len(rows) - CAP} further {tool} findings not itemized (total {len(rows)})",
            source=tool,
        )
    return len(rows)


def _gap(run_id, tool, reason):
    store.add_finding(
        run_id,
        "INFO",
        "TOOL-GAP",
        tool,
        f"{tool} did not run — its defect class is UNEXAMINED, not clean",
        detail=reason[:300],
        fix=INSTALL_HINT.get(tool, ""),
        source="orchestrator",
        unique=True,
    )


# ── normalizers (pure — unit-testable without the binaries) ─────────────────
def normalize_ruff(data, root):
    rows = []
    for r in data:
        code = r.get("code") or "RULE"
        sev = "HIGH" if str(code).startswith("E9") else "MEDIUM"
        loc = r.get("location") or {}
        rows.append(
            {
                "severity": sev,
                "code": f"RUFF-{code}",
                "ref": f"{_rel(r.get('filename', ''), root)}:{loc.get('row', 0)}",
                "title": (r.get("message") or "")[:200],
                "detail": r.get("url") or "",
                "fix": f"See ruff rule {code}",
            }
        )
    return rows


def normalize_bandit(data, root):
    rows = []
    for r in data.get("results", []):
        sev = {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}.get(
            r.get("issue_severity", "LOW"), "LOW"
        )
        rows.append(
            {
                "severity": sev,
                "code": f"BANDIT-{r.get('test_id')}",
                "ref": f"{_rel(r.get('filename', ''), root)}:{r.get('line_number')}",
                "title": (r.get("issue_text") or "")[:200],
                "detail": (r.get("code") or "")[:300],
                "fix": f"See bandit rule {r.get('test_id')}",
            }
        )
    return rows


_MYPY_LINE = re.compile(
    r"^(.*?):(\d+):(?:\d+:)?\s*(error|warning|note):\s*(.*?)(?:\s+\[([\w-]+)\])?$"
)


def normalize_mypy(text, root):
    rows = []
    for line in text.splitlines():
        m = _MYPY_LINE.match(line.strip())
        if not m or m.group(3) == "note":
            continue
        f, ln, kind, msg, code = m.groups()
        rows.append(
            {
                "severity": "MEDIUM" if kind == "error" else "LOW",
                "code": f"MYPY-{code or 'error'}",
                "ref": f"{_rel(f, root)}:{ln}",
                "title": msg[:200],
                "fix": "The type contract and the code disagree; one of them is wrong.",
            }
        )
    return rows


_TSC_LINE = re.compile(r"^(.+?)\((\d+),\d+\):\s*(error|warning)\s+(TS\d+):\s*(.*)$")


def normalize_tsc(text, root):
    rows = []
    for line in text.splitlines():
        m = _TSC_LINE.match(line.strip())
        if not m:
            continue
        f, ln, kind, code, msg = m.groups()
        rows.append(
            {
                "severity": "MEDIUM" if kind == "error" else "LOW",
                "code": f"TSC-{code}",
                "ref": f"{_rel(f, root)}:{ln}",
                "title": msg[:200],
                "fix": f"typescript {code}",
            }
        )
    return rows


def normalize_eslint(data, root):
    rows = []
    for f in data:
        for m in f.get("messages", []):
            rows.append(
                {
                    "severity": "MEDIUM" if m.get("severity") == 2 else "LOW",
                    "code": f"ESLINT-{m.get('ruleId') or 'parse'}",
                    "ref": f"{_rel(f.get('filePath', ''), root)}:{m.get('line', 0)}",
                    "title": (m.get("message") or "")[:200],
                }
            )
    return rows


def normalize_semgrep(data, root):
    rows = []
    for r in data.get("results", []):
        ex = r.get("extra", {})
        sev = {"ERROR": "HIGH", "WARNING": "MEDIUM", "INFO": "LOW"}.get(
            ex.get("severity", "INFO"), "LOW"
        )
        rows.append(
            {
                "severity": sev,
                "code": f"SEMGREP-{str(r.get('check_id', ''))[:40]}",
                "ref": f"{_rel(r.get('path', ''), root)}:{r.get('start', {}).get('line')}",
                "title": (ex.get("message") or "")[:200],
            }
        )
    return rows


# ── runners ─────────────────────────────────────────────────────────────────
# ruff scope: E9 (syntax), F (correctness), B (bugbear), S (flake8-bandit
# security), BLE (blind except — doctrine D7's swallow class).
RUFF_SELECT = "E9,F,B,S,BLE"


def run_ruff(root, run_id):
    exe = shutil.which("ruff")
    if not exe:
        _gap(run_id, "ruff", "not on PATH")
        return "unavailable"
    p = _proc(
        [
            exe,
            "check",
            str(root),
            "--isolated",
            "--select",
            RUFF_SELECT,
            "--output-format",
            "json",
            "--exit-zero",
        ]
    )
    rows = normalize_ruff(json.loads(p.stdout or "[]"), root)
    return f"ran ({_emit(run_id, rows, 'ruff')} findings)"


def run_bandit(root, run_id):
    if not CFG.use_bandit:
        return "disabled"
    exe = shutil.which("bandit")
    if not exe:
        _gap(run_id, "bandit", "not on PATH")
        return "unavailable"
    p = _proc([exe, "-r", str(root), "-f", "json", "-q"])
    rows = normalize_bandit(json.loads(p.stdout or "{}"), root)
    return f"ran ({_emit(run_id, rows, 'bandit')} findings)"


def run_mypy(root, run_id):
    exe = shutil.which("mypy")
    if not exe:
        _gap(run_id, "mypy", "not on PATH")
        return "unavailable"
    p = _proc(
        [exe, str(root), "--ignore-missing-imports", "--no-error-summary", "--no-color-output"]
    )
    rows = normalize_mypy(p.stdout or "", root)
    return f"ran ({_emit(run_id, rows, 'mypy')} findings)"


def run_tsc(root, run_id):
    root = Path(root)
    if not (root / "tsconfig.json").exists():
        return "skipped: no tsconfig.json at repo root"
    exe = shutil.which("tsc")
    if not exe:
        _gap(run_id, "tsc", "not on PATH")
        return "unavailable"
    p = _proc([exe, "-p", str(root), "--noEmit", "--pretty", "false"], cwd=root, timeout=1200)
    rows = normalize_tsc(p.stdout or "", root)
    return f"ran ({_emit(run_id, rows, 'tsc')} findings)"


_ESLINT_CONFIGS = (
    "eslint.config.js",
    "eslint.config.mjs",
    "eslint.config.cjs",
    "eslint.config.ts",
    ".eslintrc",
    ".eslintrc.json",
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.yml",
)


def run_eslint(root, run_id):
    root = Path(root)
    if not any((root / c).exists() for c in _ESLINT_CONFIGS):
        return "skipped: no eslint config in target repo"
    exe = shutil.which("eslint")
    if not exe:
        _gap(run_id, "eslint", "not on PATH")
        return "unavailable"
    p = _proc(
        [exe, ".", "--format", "json", "--no-error-on-unmatched-pattern"],
        cwd=root,
        timeout=1200,
    )
    try:
        data = json.loads(p.stdout or "[]")
    except json.JSONDecodeError:
        _gap(run_id, "eslint", f"unparseable output: {p.stderr[:120]}")
        return "error"
    rows = normalize_eslint(data, root)
    return f"ran ({_emit(run_id, rows, 'eslint')} findings)"


def run_semgrep(root, run_id):
    if not CFG.use_semgrep:
        return "disabled"
    exe = shutil.which("semgrep")
    if not exe:
        _gap(run_id, "semgrep", "not on PATH")
        return "unavailable"
    p = _proc([exe, "--config", "auto", "--json", "--quiet", str(root)], timeout=1800)
    rows = normalize_semgrep(json.loads(p.stdout or "{}"), root)
    return f"ran ({_emit(run_id, rows, 'semgrep')} findings)"


ALL = {
    "ruff": run_ruff,
    "bandit": run_bandit,
    "mypy": run_mypy,
    "tsc": run_tsc,
    "eslint": run_eslint,
    "semgrep": run_semgrep,
}


def run_all(root, run_id):
    """Run every scanner; a crash in one is that tool's gap, never the run's."""
    status = {}
    for name, fn in ALL.items():
        try:
            status[name] = fn(root, run_id)
        except Exception as e:  # noqa: BLE001 — one broken tool must not kill the audit
            _gap(run_id, name, f"{type(e).__name__}: {e}"[:200])
            status[name] = "error"
    return status


def missing_tools():
    """For plan-time disclosure: which scanners this environment lacks."""
    out = []
    for name in ("ruff", "bandit", "mypy", "tsc", "eslint"):
        if not shutil.which(name):
            out.append(f"{name} ({INSTALL_HINT[name]})")
    return out
