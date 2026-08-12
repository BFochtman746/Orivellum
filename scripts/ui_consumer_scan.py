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

Path evidence is VERB-AWARE: each match derives its HTTP method from the
surrounding call context (an explicit `method: "POST"` option within the same
call, else the fetch default GET), so a page that only GETs /api/works does
NOT count as evidence for POST /api/works.  Hook evidence is verb-exact by
construction (mapped from the spec's method+path).

Limitations (documented, deliberate): a URL built in one statement and
fetched with a non-GET verb far away may be attributed to GET, and a very
short path could in principle match a same-shaped string.  The scan is an
existence check that catches the real failure mode — endpoints no UI code
calls with that verb — not a call-graph proof.
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


# `method:` option value — may be a literal or an expression like a ternary
# (method: editing ? "PUT" : "POST"); collect every verb literal in it.
_METHOD_OPT = re.compile(r"method\s*:\s*([^,}\n]*)")
_VERB_LIT = re.compile(r"[\"'](GET|POST|PUT|PATCH|DELETE)[\"']", re.IGNORECASE)
# A new URL literal starting (quote/backtick then / or ${) ends the look-ahead:
# any `method:` past it belongs to a different request.
_NEXT_URL = re.compile(r"[\"'`](?:\$\{|/)")
# ...unless it is the sibling branch of the same ternary (`cond ? urlA : urlB`)
# — then both branches share the eventual request's verb, so look past it.
_TERNARY_CONT = re.compile(r"^\s*[\"'`]?\s*[:?]")


def _cut_ahead(ahead: str) -> str:
    """Trim the look-ahead window at the next unrelated URL literal."""
    hits = list(_NEXT_URL.finditer(ahead))
    for hit in hits:
        if _TERNARY_CONT.match(ahead[: hit.start()]):
            continue  # sibling ternary branch, same request — keep looking
        return ahead[: hit.start()]
    return ahead


# Verb carried by the call the URL sits in: xhr.open("POST", url), verb-named
# helpers (apiGet/apiPost/..., or page-local post()/put()/patch()/del()).
_XHR_OPEN = re.compile(r"\.open\(\s*[\"'](GET|POST|PUT|PATCH|DELETE)[\"']", re.IGNORECASE)
_HELPER = re.compile(r"\b(?:api)?(get|post|put|patch|del|delete)\s*(?:<[^<>]*>)?\(", re.IGNORECASE)
_HELPER_VERBS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH"}


def _verb_before(window: str) -> str | None:
    """Verb implied by the call syntax immediately preceding the URL."""
    best: tuple[int, str] | None = None
    for m in _XHR_OPEN.finditer(window):
        best = (m.start(), m.group(1).upper())
    for m in _HELPER.finditer(window):
        verb = _HELPER_VERBS.get(m.group(1).lower(), "DELETE")
        if best is None or m.start() > best[0]:
            best = (m.start(), verb)
    return best[1] if best else None


def _match_verbs(text: str, rx: re.Pattern) -> set[str]:
    """HTTP verbs the file uses with URLs matching rx.

    Per match, in priority order: an explicit `method:` option ahead of the
    URL (look-ahead cut at the next URL literal, so a later request's option
    is never attributed to this one; every verb literal in the option's
    expression counts, covering ternaries); the verb implied by the
    immediately preceding call syntax (xhr.open verb, verb-named helper);
    else the fetch default GET.
    """
    verbs: set[str] = set()
    for m in rx.finditer(text):
        ahead = _cut_ahead(text[m.end() : m.end() + 300])
        opt = _METHOD_OPT.search(ahead)
        found = _VERB_LIT.findall(opt.group(1)) if opt else []
        if found:
            verbs.update(v.upper() for v in found)
            continue
        before = _verb_before(text[max(0, m.start() - 80) : m.start()])
        verbs.add(before or "GET")
    return verbs


def has_ui_consumer(
    method: str,
    path: str,
    sources: dict[str, str],
    hook_names: dict[tuple[str, str], str],
) -> bool:
    """True when any evidence pass finds a consumer calling this exact verb."""
    param = re.compile(r"\{[^}]+\}")
    hook = hook_names.get((method, param.sub("{}", path)))
    hook_rx = re.compile(rf"\b{re.escape(hook)}\b") if hook else None
    primary = _path_regex(path)
    based = _base_regex(path)
    for text in sources.values():
        if method in _match_verbs(text, primary):
            return True
        if hook_rx and hook_rx.search(text):
            return True
        if based:
            first, rx = based
            if f"api/{first}" in text and method in _match_verbs(text, rx):
                return True
    return False
