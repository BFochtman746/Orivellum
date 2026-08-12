"""UI consumer evidence scan — does the PWA actually call this operation?

Used by scripts/check_capability_manifest.py to verify that every operation
classified 'pwa' has at least one machine-detectable consumer in the UI
sources, not merely an existing route.  Three evidence passes:

  A. literal path — the operation's path (minus the /api prefix) appears in a
     UI source file, with path params matched against template-literal
     interpolations or concrete segments.
  B. BASE-constant path — pages commonly build URLs from a module-scoped
     constant (e.g. BASE = `${...}api/finishing`) plus a relative path; the
     remainder of the path (first segment stripped) must appear in a file
     that also references api/<first-segment>.
  C. typed-client hook — for operations in the public contract, the Orval
     generated react-query hook (use<PascalCase(operationId)>) is referenced.

Limitations (documented, deliberate): path evidence is method-insensitive
(a page that GETs /api/works also counts as evidence for POST /api/works),
and a very short path could in principle match a same-shaped string.  The
scan is an existence check that catches the real failure mode — endpoints no
UI code references at all — not a call-graph proof.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
UI_SRC = ROOT / "artifacts" / "orivellum-ui" / "src"
SPEC = ROOT / "lib" / "api-spec" / "openapi.yaml"

_PARAM_SEG = r"(?:\$\{[^}]*\}|[^/`\"']+)"


def load_ui_sources() -> dict[str, str]:
    """Every UI source file's text, keyed by path relative to src/."""
    sources: dict[str, str] = {}
    for f in UI_SRC.rglob("*.ts"):
        sources[str(f.relative_to(UI_SRC))] = f.read_text(encoding="utf-8", errors="ignore")
    for f in UI_SRC.rglob("*.tsx"):
        sources[str(f.relative_to(UI_SRC))] = f.read_text(encoding="utf-8", errors="ignore")
    return sources


def load_spec_hook_names() -> dict[tuple[str, str], str]:
    """(METHOD, normalized path) -> Orval hook name, from the typed contract."""
    import yaml

    param = re.compile(r"\{[^}]+\}")
    spec = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    base = (spec.get("servers") or [{}])[0].get("url", "").rstrip("/")
    hooks: dict[tuple[str, str], str] = {}
    for path, item in (spec.get("paths") or {}).items():
        for method, op in item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            op_id = (op or {}).get("operationId")
            if op_id:
                pascal = op_id[0].upper() + op_id[1:]
                hooks[(method.upper(), param.sub("{}", base + path))] = f"use{pascal}"
    return hooks


def _path_regex(path: str) -> re.Pattern:
    p = path[4:] if path.startswith("/api") else path
    segs = [_PARAM_SEG if s.startswith("{") else re.escape(s) for s in p.strip("/").split("/")]
    return re.compile("/" + "/".join(segs) + r"(?![\w-])")


def _base_regex(path: str) -> tuple[str, re.Pattern] | None:
    p = path[4:] if path.startswith("/api") else path
    segs = p.strip("/").split("/")
    if len(segs) < 2:
        return None
    rest = [_PARAM_SEG if s.startswith("{") else re.escape(s) for s in segs[1:]]
    return segs[0], re.compile("`?/" + "/".join(rest) + r"(?![\w-])")


def has_ui_consumer(
    method: str,
    path: str,
    sources: dict[str, str],
    hook_names: dict[tuple[str, str], str],
) -> bool:
    """True when any evidence pass finds a consumer for this operation."""
    param = re.compile(r"\{[^}]+\}")
    hook = hook_names.get((method, param.sub("{}", path)))
    hook_rx = re.compile(rf"\b{re.escape(hook)}\b") if hook else None
    primary = _path_regex(path)
    based = _base_regex(path)
    for text in sources.values():
        if primary.search(text):
            return True
        if hook_rx and hook_rx.search(text):
            return True
        if based:
            first, rx = based
            if f"api/{first}" in text and rx.search(text):
                return True
    return False
