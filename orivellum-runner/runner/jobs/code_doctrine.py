"""Doctrine checks — the defect classes no off-the-shelf scanner looks for.

Each check enforces a numbered rule in PROGRAMMING_DOCTRINE.md (repo root).
All checks are deterministic AST/text analysis: every finding carries
file:line evidence, and the model never votes here. Counts are honest lower
bounds — a check that cannot decide stays silent rather than guessing.

  D1 DOCTRINE-FAILOPEN    except handlers that return approval / None-as-pass
  D2 DOCTRINE-PCTGATE     percentage-typed values compared in gate logic
  D3 DOCTRINE-NOCALLER    public functions / exports nothing references
  D4 DOCTRINE-DEFAULTOFF  security-named settings that default to off
  D5 DOCTRINE-TESTGAP     per-function coverage-by-name, worst offenders named
  D6 DOCTRINE-ENVDOC      env vars read but documented nowhere
"""

import ast
import re
from pathlib import Path

from .. import store

GATE_NAME = re.compile(
    r"(?i)(verify|check|gate|allow|permit|valid|approve|grant|auth|can_|is_|has_)"
)
PCT_NAME = re.compile(r"(?i)(pct|percent|ratio|rate|coverage|share|fraction)")
SEC_NAME = re.compile(
    r"(?i)(verify|tls|ssl|auth|secure|csrf|sandbox|shield|quarantine|encrypt|sign)"
)
TS_EXT = (".ts", ".tsx", ".js", ".jsx")
GENERIC_NAMES = {"main", "app", "run", "setup", "init", "cli", "test", "index", "default"}
WELL_KNOWN_ENV = {
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "TZ",
    "PWD",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOSTNAME",
    "CI",
    "SHELL",
    "TERM",
    "NODE_ENV",
}
MAX_ITEMIZED = 15


# ── shared loaders ──────────────────────────────────────────────────────────
def _load(root, files):
    py, ts = [], []
    for p in files:
        try:
            rel = str(Path(p).relative_to(root))
        except ValueError:
            rel = str(p)
        try:
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sfx = Path(p).suffix.lower()
        if sfx == ".py":
            py.append((rel, text))
        elif sfx in TS_EXT:
            ts.append((rel, text))
    return py, ts


def _trees(py):
    out = []
    for rel, text in py:
        try:
            out.append((rel, ast.parse(text), text))
        except SyntaxError:
            continue  # already a PARSE finding from plan()
    return out


def _is_test(rel):
    name = Path(rel).name.lower()
    return "test" in name or ".spec." in name or "conftest" in name


def _parents(tree):
    par = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node):
            par[ch] = node
    return par


def _enclosing_function(node, par):
    p = par.get(node)
    while p is not None:
        if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return p
        p = par.get(p)
    return None


def _returns_under(stmts):
    """Return nodes under these statements, NOT descending into nested defs."""
    out, stack = [], list(stmts)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(n, ast.Return):
            out.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return out


# ── D1: fail-open except handlers ───────────────────────────────────────────
def _truthy_const(node):
    return isinstance(node, ast.Constant) and bool(node.value)


def _is_none_return(r):
    return r.value is None or (isinstance(r.value, ast.Constant) and r.value.value is None)


def _flag_handler(run_id, rel, h, fname, gate):
    hits = 0
    for r in _returns_under(h.body):
        if _truthy_const(r.value):
            store.add_finding(
                run_id,
                "HIGH",
                "DOCTRINE-FAILOPEN",
                f"{rel}:{r.lineno}",
                f"except handler returns approval ({ast.unparse(r.value)})"
                + (f" in {fname}" if fname else ""),
                detail="Any exception here silently converts failure into a pass (doctrine D1).",
                fix="Fail closed: re-raise, or return the explicit rejection value.",
                source="doctrine",
            )
            hits += 1
        elif gate and _is_none_return(r):
            store.add_finding(
                run_id,
                "MEDIUM",
                "DOCTRINE-FAILOPEN",
                f"{rel}:{r.lineno}",
                f"gate '{fname}' returns None from an except handler",
                detail="None-as-pass: callers using truthiness or 'is not None' read this "
                "however they need (doctrine D1).",
                fix="Raise, or return an explicit fail value the caller must handle.",
                source="doctrine",
            )
            hits += 1
    if gate and len(h.body) == 1 and isinstance(h.body[0], ast.Pass):
        store.add_finding(
            run_id,
            "MEDIUM",
            "DOCTRINE-FAILOPEN",
            f"{rel}:{h.lineno}",
            f"gate '{fname}' swallows an exception with a bare pass",
            detail="The gate proceeds as if the check ran (doctrine D1).",
            fix="Fail closed, or record the skip so the caller can see coverage dropped.",
            source="doctrine",
        )
        hits += 1
    return hits


def _check_fail_open(run_id, trees):
    n = 0
    for rel, tree, _text in trees:
        if _is_test(rel):
            continue
        par = _parents(tree)
        for h in (x for x in ast.walk(tree) if isinstance(x, ast.ExceptHandler)):
            fn = _enclosing_function(h, par)
            fname = fn.name if fn else ""
            n += _flag_handler(run_id, rel, h, fname, bool(GATE_NAME.search(fname)))
    return n


# ── D2: percentage-typed values in gate comparisons ─────────────────────────
def _pct_compare_names(test):
    names = []
    for c in (x for x in ast.walk(test) if isinstance(x, ast.Compare)):
        sides = [c.left, *c.comparators]
        if not any(
            isinstance(s, ast.Constant) and isinstance(s.value, (int, float)) for s in sides
        ):
            continue
        for s in sides:
            ident = s.id if isinstance(s, ast.Name) else getattr(s, "attr", "")
            if ident and PCT_NAME.search(ident):
                names.append(ident)
    return names


def _check_pct_gate(run_id, trees):
    n = 0
    for rel, tree, text in trees:
        if _is_test(rel):
            continue
        for node in (x for x in ast.walk(tree) if isinstance(x, ast.If)):
            names = _pct_compare_names(node.test)
            if not names:
                continue
            if not any(isinstance(x, (ast.Return, ast.Raise)) for x in ast.walk(node)):
                continue
            seg = (ast.get_source_segment(text, node.test) or "")[:120]
            store.add_finding(
                run_id,
                "MEDIUM",
                "DOCTRINE-PCTGATE",
                f"{rel}:{node.lineno}",
                f"percentage '{names[0]}' gates a branch: `{seg}`",
                detail="A percentage in gate logic is only as honest as its denominator "
                "(doctrine D2).",
                fix="Trace the denominator's origin and prove it is independent of the "
                "numerator's filter — a self-referential denominator measures itself.",
                source="doctrine",
            )
            n += 1
    return n


# ── D3: public functions / exports with zero callers ────────────────────────
TS_EXPORT = re.compile(r"^export\s+(?:async\s+)?(?:function|const|class)\s+([A-Za-z_]\w{2,})", re.M)


def _py_public(trees):
    out = []
    for rel, tree, _text in trees:
        if _is_test(rel) or Path(rel).name == "__init__.py":
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_") or node.name in GENERIC_NAMES or len(node.name) < 3:
                continue
            if node.decorator_list:
                continue  # framework-wired (routes, fixtures, CLI commands)
            out.append((rel, node.name, node.lineno, "python"))
    return out


def _ts_exports(ts):
    out = []
    for rel, text in ts:
        if _is_test(rel) or rel.endswith(".d.ts") or ".stories." in rel:
            continue
        for m in TS_EXPORT.finditer(text):
            name = m.group(1)
            if name in GENERIC_NAMES:
                continue
            out.append((rel, name, text.count("\n", 0, m.start()) + 1, "ts"))
    return out


def _referenced(name, defrel, texts):
    rx = re.compile(rf"\b{re.escape(name)}\b")
    for rel, text in texts.items():
        hits = len(rx.findall(text))
        if rel == defrel:
            if hits > 1:  # anything beyond its own definition
                return True
        elif hits:
            return True
    return False


def _check_no_caller(run_id, py, ts, trees):
    texts = dict(py)
    texts.update(dict(ts))
    total = 0
    for rel, name, lineno, lang in _py_public(trees) + _ts_exports(ts):
        if _referenced(name, rel, texts):
            continue
        total += 1
        if total <= MAX_ITEMIZED:
            store.add_finding(
                run_id,
                "MEDIUM",
                "DOCTRINE-NOCALLER",
                f"{rel}::{name}",
                f"public {'export' if lang == 'ts' else 'function'} '{name}' has zero callers "
                f"(line {lineno})",
                detail="Nothing in this repository imports, calls, or mentions it — built "
                "with no wire (doctrine D3).",
                fix="Wire it to a caller, or delete it; unreachable code is an unverified claim.",
                source="doctrine",
            )
    if total > MAX_ITEMIZED:
        store.add_finding(
            run_id,
            "INFO",
            "DOCTRINE-NOCALLER",
            "(repository)",
            f"{total - MAX_ITEMIZED} further zero-caller symbols not itemized (total {total})",
            source="doctrine",
            unique=True,
        )
    return total


# ── D4: security controls that default to off ───────────────────────────────
def _falsy_default(node):
    if not isinstance(node, ast.Constant):
        return False
    v = node.value
    if isinstance(v, str):
        return v.strip().lower() in ("", "0", "false", "no", "off", "none")
    return not v


def _flag_env_default(run_id, rel, call):
    try:
        fs = ast.unparse(call.func)
    except Exception:  # noqa: BLE001 — unparse can fail on exotic nodes
        return 0
    if not (fs.endswith("getenv") or "environ" in fs):
        return 0
    if not (
        call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)
    ):
        return 0
    key = call.args[0].value
    default = call.args[1] if len(call.args) > 1 else None
    if default is None or not SEC_NAME.search(key) or not _falsy_default(default):
        return 0
    store.add_finding(
        run_id,
        "MEDIUM",
        "DOCTRINE-DEFAULTOFF",
        f"{rel}:{call.lineno}",
        f"security setting {key} defaults to {ast.unparse(default)} (off)",
        detail="A control that ships disabled protects the demo, not the user (doctrine D4).",
        fix=f"Default {key} to ON; require an explicit opt-out.",
        source="doctrine",
    )
    return 1


def _flag_param_defaults(run_id, rel, fn):
    hits = 0
    pos = (
        list(zip(fn.args.args[-len(fn.args.defaults) :], fn.args.defaults, strict=False))
        if fn.args.defaults
        else []
    )
    kw = [
        (a, d)
        for a, d in zip(fn.args.kwonlyargs, fn.args.kw_defaults, strict=False)
        if d is not None
    ]
    for a, d in pos + kw:
        if SEC_NAME.search(a.arg) and isinstance(d, ast.Constant) and not d.value:
            store.add_finding(
                run_id,
                "LOW",
                "DOCTRINE-DEFAULTOFF",
                f"{rel}:{fn.lineno}",
                f"parameter '{a.arg}={ast.unparse(d)}' in '{fn.name}' defaults a security "
                "control to off",
                detail="Callers who forget the argument get the unsafe path (doctrine D4).",
                fix="Default it to the safe value; make disabling explicit at the call site.",
                source="doctrine",
            )
            hits += 1
    return hits


def _check_default_off(run_id, trees):
    n = 0
    for rel, tree, _text in trees:
        if _is_test(rel):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                n += _flag_env_default(run_id, rel, node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                n += _flag_param_defaults(run_id, rel, node)
    return n


# ── D5: per-function coverage-by-name ───────────────────────────────────────
def _check_test_gap(run_id, py, ts, fn_digests):
    test_blob = "\n".join(t for rel, t in py + ts if _is_test(rel))
    if not test_blob:
        store.add_finding(
            run_id,
            "HIGH",
            "NOTESTS",
            "(repository)",
            "No test files found at all",
            fix="One test per entry point is the minimum that makes refactoring safe.",
            source="doctrine",
            unique=True,
        )
        return {"untested": len(fn_digests), "total": len(fn_digests), "no_tests": True}
    untested = [
        d
        for d in fn_digests
        if (d.get("name") or "").split(".")[-1]
        and (d.get("name") or "").split(".")[-1] not in test_blob
    ]
    if untested:
        store.add_finding(
            run_id,
            "MEDIUM",
            "DOCTRINE-TESTGAP",
            "(repository)",
            f"{len(untested)} of {len(fn_digests)} functions are never named in a test",
            detail=", ".join(sorted({d.get("name") or "" for d in untested})[:25]),
            fix="Name-mention floor: every public function appears in at least one test "
            "(doctrine D5).",
            source="doctrine",
            unique=True,
        )
        worst = sorted(
            (d for d in untested if not (d.get("name") or "_").split(".")[-1].startswith("_")),
            key=lambda d: -(d.get("loc") or 0),
        )[:10]
        for d in worst:
            store.add_finding(
                run_id,
                "LOW",
                "DOCTRINE-TESTGAP",
                f"{d.get('file')}::{d.get('name')}",
                f"never named in any test ({d.get('loc') or '?'} lines)",
                fix="Write one characterisation test that calls it by name.",
                source="doctrine",
                unique=True,
            )
    return {"untested": len(untested), "total": len(fn_digests), "no_tests": False}


# ── D6: env vars read but documented nowhere ────────────────────────────────
PY_ENV = re.compile(
    r"(?:os\.getenv|os\.environ(?:\.get)?)\s*[\(\[]\s*['\"]([A-Z][A-Z0-9_]{2,})['\"]"
)
TS_ENV = re.compile(
    r"(?:process\.env|import\.meta\.env)"
    r"(?:\.([A-Z][A-Z0-9_]{2,})|\[['\"]([A-Z][A-Z0-9_]{2,})['\"]\])"
)
DOC_SOURCES = ("README.md", "README.txt", "README.rst", ".env.example", ".env.sample")


def _documented_env(root):
    docs = set()
    cands = [root / c for c in DOC_SOURCES]
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        cands += sorted(docs_dir.glob("*.md"))[:50]
    for f in cands:
        if f.exists():
            docs |= set(
                re.findall(
                    r"\b([A-Z][A-Z0-9_]{2,})\b", f.read_text(encoding="utf-8", errors="replace")
                )
            )
    return docs


def _env_readers(py, ts):
    readers = {}
    for rel, text in py:
        for v in PY_ENV.findall(text):
            readers.setdefault(v, set()).add(rel)
    for rel, text in ts:
        for a, b in TS_ENV.findall(text):
            readers.setdefault(a or b, set()).add(rel)
    return readers


def _check_env_undoc(run_id, root, py, ts):
    readers = _env_readers(py, ts)
    undoc = sorted(set(readers) - _documented_env(root) - WELL_KNOWN_ENV)
    for v in undoc[:25]:
        files = sorted(readers[v])
        store.add_finding(
            run_id,
            "MEDIUM",
            "DOCTRINE-ENVDOC",
            f"{files[0]}::{v}",
            f"env var {v} is read but documented nowhere",
            detail="read in: " + ", ".join(files[:5]),
            fix=f"Add {v} to .env.example with a comment saying what it does (doctrine D6).",
            source="doctrine",
            unique=True,
        )
    if len(undoc) > 25:
        store.add_finding(
            run_id,
            "INFO",
            "DOCTRINE-ENVDOC",
            "(repository)",
            f"{len(undoc) - 25} further undocumented env vars not itemized (total {len(undoc)})",
            source="doctrine",
            unique=True,
        )
    return len(undoc)


# ── entry point ─────────────────────────────────────────────────────────────
def audit(run_id, root, files, fn_digests):
    """Run every doctrine check. fn_digests: [{name, file, loc}, ...]."""
    root = Path(root)
    py, ts = _load(root, files)
    trees = _trees(py)
    return {
        "fail_open": _check_fail_open(run_id, trees),
        "pct_gate": _check_pct_gate(run_id, trees),
        "no_caller": _check_no_caller(run_id, py, ts, trees),
        "default_off": _check_default_off(run_id, trees),
        "env_undoc": _check_env_undoc(run_id, root, py, ts),
        "test_gap": _check_test_gap(run_id, py, ts, fn_digests),
    }
