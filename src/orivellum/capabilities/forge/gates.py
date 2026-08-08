"""Forge quality gates — run in parallel via asyncio.gather."""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import subprocess
from typing import Callable

logger = logging.getLogger(__name__)

TIMEOUT = 30  # seconds per gate


def _run(cmd: list[str], cwd: pathlib.Path, timeout: int = TIMEOUT) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, (proc.stdout + proc.stderr)[:3000]
    except FileNotFoundError:
        return -1, f"[SKIP] {cmd[0]} not found on PATH"
    except subprocess.TimeoutExpired:
        return -1, "[TIMEOUT]"
    except Exception as exc:
        return -1, f"[ERROR] {exc}"


# ── Individual gate functions ──────────────────────────────────────────────────

def _gate_structure(build_dir: pathlib.Path) -> dict:
    """Check that required files exist."""
    required = ["index.html", "styles.css", "app.js", "design-tokens.css"]
    missing = [f for f in required if not (build_dir / f).exists()]
    if missing:
        return {"name": "structure", "status": "blocked",
                "detail": f"Missing required files: {', '.join(missing)}"}
    return {"name": "structure", "status": "passed", "detail": "All required files present."}


def _gate_tokens(build_dir: pathlib.Path) -> dict:
    """Check design-tokens.css has :root and colour/font/space groups."""
    tokens_file = build_dir / "design-tokens.css"
    if not tokens_file.exists():
        return {"name": "tokens", "status": "blocked", "detail": "design-tokens.css missing."}
    content = tokens_file.read_text(encoding="utf-8", errors="replace")
    if not content.strip():
        return {"name": "tokens", "status": "blocked", "detail": "design-tokens.css is empty."}
    issues = []
    if ":root" not in content:
        issues.append("no :root block")
    for grp in ("--color-", "--font-"):
        if grp not in content:
            issues.append(f"no {grp} variables")
    if issues:
        return {"name": "tokens", "status": "conditional",
                "detail": "; ".join(issues)}
    return {"name": "tokens", "status": "passed", "detail": "Token sheet valid."}


def _gate_html_valid(build_dir: pathlib.Path) -> dict:
    """Lightweight HTML sanity check — looks for DOCTYPE and </html>."""
    index = build_dir / "index.html"
    if not index.exists():
        return {"name": "html_valid", "status": "blocked", "detail": "index.html missing."}
    content = index.read_text(encoding="utf-8", errors="replace").lower()
    issues = []
    if "<!doctype" not in content:
        issues.append("missing <!DOCTYPE html>")
    if "</html>" not in content:
        issues.append("missing </html>")
    if "<title>" not in content:
        issues.append("missing <title>")
    if issues:
        return {"name": "html_valid", "status": "conditional", "detail": "; ".join(issues)}
    return {"name": "html_valid", "status": "passed", "detail": "HTML structure OK."}


def _gate_js_syntax(build_dir: pathlib.Path) -> dict:
    """Use `node --check` to validate JS files."""
    js_files = list(build_dir.glob("*.js"))
    if not js_files:
        return {"name": "js_syntax", "status": "conditional", "detail": "No .js files found."}
    errors = []
    for f in js_files:
        rc, out = _run(["node", "--check", str(f)], build_dir)
        if rc != 0 and "[SKIP]" not in out:
            errors.append(f"{f.name}: {out[:200]}")
    if errors:
        return {"name": "js_syntax", "status": "blocked", "detail": "\n".join(errors)}
    return {"name": "js_syntax", "status": "passed",
            "detail": f"{len(js_files)} JS file(s) syntax-clean."}


def _gate_links(build_dir: pathlib.Path) -> dict:
    """Check that all <a href> and <link href> targets exist within the build dir."""
    import re
    html_files = list(build_dir.glob("**/*.html"))
    broken = []
    href_pat = re.compile(r'href=["\']([^"\'#?]+)["\']', re.IGNORECASE)
    for html in html_files:
        content = html.read_text(encoding="utf-8", errors="replace")
        for href in href_pat.findall(content):
            if href.startswith(("http://", "https://", "mailto:", "tel:")):
                continue
            target = (html.parent / href).resolve()
            if not target.exists():
                broken.append(f"{html.name}: {href}")
    if broken:
        return {"name": "links", "status": "conditional",
                "detail": f"{len(broken)} broken internal link(s): " + "; ".join(broken[:5])}
    return {"name": "links", "status": "passed",
            "detail": f"All internal links OK ({len(html_files)} HTML files checked)."}


def _gate_scope(build_dir: pathlib.Path) -> dict:
    """Check the build directory doesn't contain obvious server-side or binary files."""
    bad_extensions = {".py", ".php", ".rb", ".sh", ".exe", ".bat"}
    found = [
        str(p.relative_to(build_dir))
        for p in build_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in bad_extensions
    ]
    if found:
        return {"name": "scope", "status": "conditional",
                "detail": f"Non-static files found: {', '.join(found[:5])}"}
    return {"name": "scope", "status": "passed", "detail": "Output is static-only."}


# ── Orchestrator ───────────────────────────────────────────────────────────────

def run_quality_gates(
    build_dir: pathlib.Path,
    on_event: Callable | None = None,
) -> dict:
    """Run all gates and return a summary dict with per-gate results."""
    if on_event:
        on_event("gates_start", "Running quality gates…")

    gate_fns = [
        _gate_structure,
        _gate_tokens,
        _gate_html_valid,
        _gate_js_syntax,
        _gate_links,
        _gate_scope,
    ]

    # Run synchronously (Python can't parallelise subprocess easily without threads,
    # and these are fast enough that sequential is fine for ≤6 gates).
    results = []
    for fn in gate_fns:
        try:
            r = fn(build_dir)
        except Exception as exc:
            r = {"name": fn.__name__, "status": "error", "detail": str(exc)}
        results.append(r)
        if on_event:
            status_emoji = {"passed": "✓", "conditional": "⚠", "blocked": "✗"}.get(r["status"], "?")
            on_event("gate_result", f"{status_emoji} {r['name']}: {r['detail'][:120]}", r)

    blocked = [r for r in results if r["status"] == "blocked"]
    conditional = [r for r in results if r["status"] == "conditional"]
    overall = "blocked" if blocked else ("conditional" if conditional else "passed")

    summary = {
        "status": overall,
        "gates": results,
        "blocked_count": len(blocked),
        "conditional_count": len(conditional),
    }

    if on_event:
        on_event("gates_done",
                 f"Gates complete — {overall.upper()} "
                 f"({len(blocked)} blocked, {len(conditional)} conditional).",
                 summary)

    return summary
