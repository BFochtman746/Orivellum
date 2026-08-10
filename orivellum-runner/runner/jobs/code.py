"""CODE job — take a zip or directory, come back with a function-level map,
scanner findings, and a list of what is missing.

DIVISION OF LABOUR, and it is the whole design:
  deterministic tools FIND things   (AST, call graph, bandit, secret regexes)
  the model EXPLAINS them           (what this function does, its failure modes)
Never the reverse. LLM detection has weak inter-statement reasoning, so it is
the wrong instrument for dataflow; the AST is right and it is checkable.

A unit is ONE FUNCTION. Its sub-agent sees that function plus its signature
context and nothing else, so parent context stays flat across thousands of
units.
"""
import ast, json, os, re, shutil, subprocess, zipfile
from collections import defaultdict
from pathlib import Path
from ..config import CFG
from .. import store, llm, shield

SKIP_DIR_GLOBS = ("pytest-of-", "pytest-", ".tox", "htmlcov")
SKIP_DIRS = {".git","node_modules",".venv","venv","__pycache__","dist","build",
             ".next",".nuxt","target","site-packages",".mypy_cache",".pytest_cache",
             ".ruff_cache","coverage","vendor",".idea",".vscode",".tox","egg-info"}
SKIP_EXT  = {".pyc",".pyo",".so",".dll",".exe",".bin",".zip",".gz",".tar",".whl",
             ".png",".jpg",".jpeg",".gif",".ico",".pdf",".woff",".woff2",".ttf",
             ".mp3",".mp4",".wav",".lock",".map",".min.js"}
CODE_EXT  = {".py",".js",".jsx",".ts",".tsx",".ps1",".sh",".java",".cs",".go",".rb",".sql"}
MAX_FILE_BYTES = 400_000

SECRETS = [
    (r"(?i)(aws_secret_access_key|aws_access_key_id)\s*[=:]\s*['\"][A-Za-z0-9/+=]{16,}", "aws credential"),
    (r"(?i)\b(api[_-]?key|apikey|secret|password|passwd|token)\s*[=:]\s*['\"][^'\"\s]{12,}['\"]", "hardcoded credential"),
    (r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----", "private key"),
    (r"(?i)\bBearer\s+[A-Za-z0-9\-_.]{20,}", "bearer token"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}", "github token"),
]
RISKY_PY = [
    (r"\beval\s*\(", "HIGH", "CODE-EVAL", "eval() on possibly external input"),
    (r"\bexec\s*\(", "HIGH", "CODE-EXEC", "exec() executes constructed code"),
    (r"subprocess\.[a-z_]+\([^)]*shell\s*=\s*True", "HIGH", "CODE-SHELL", "subprocess with shell=True"),
    (r"\bos\.system\s*\(", "HIGH", "CODE-OSSYS", "os.system() passes a string to a shell"),
    (r"pickle\.loads?\s*\(", "HIGH", "CODE-PICKLE", "pickle deserialises arbitrary objects"),
    (r"yaml\.load\s*\((?![^)]*Loader)", "MEDIUM", "CODE-YAML", "yaml.load without a safe Loader"),
    (r"verify\s*=\s*False", "HIGH", "CODE-TLS", "TLS verification disabled"),
    (r"except\s*:\s*(#.*)?$", "MEDIUM", "CODE-BAREEXC", "bare except swallows every error"),
    (r"\bassert\b", "LOW", "CODE-ASSERT", "assert is stripped under python -O"),
    (r"(?i)\btodo\b|\bfixme\b|\bhack\b|\bxxx\b", "INFO", "CODE-TODO", "unfinished marker in source"),
]

# ── discovery ───────────────────────────────────────────────────────────────
def prepare(target, workdir):  # noqa: D401
    """Unzip if needed. Returns the directory to analyse."""
    t = Path(target)
    if t.is_dir(): return t
    if t.suffix.lower() in (".zip",):
        dest = Path(workdir) / "src"
        if dest.exists(): shutil.rmtree(dest)
        dest.mkdir(parents=True)
        skipped = []
        with zipfile.ZipFile(t) as z:
            for m in z.namelist():
                if m.endswith("/") or ".." in m or Path(m).is_absolute(): continue
                parts = Path(m).parts
                if any(p in SKIP_DIRS for p in parts): continue
                if any(p.startswith(g) for p in parts for g in SKIP_DIR_GLOBS): continue
                if Path(m).suffix.lower() in SKIP_EXT: continue
                if len(m) > 200: skipped.append((m, "path too long")); continue
                try:
                    z.extract(m, dest)
                except Exception as e:                      # noqa: BLE001
                    # One unextractable member must never abort the run. Record
                    # it so the report can say the analysis was incomplete
                    # rather than implying full coverage.
                    skipped.append((m, f"{type(e).__name__}: {e}"[:120]))
        prepare.skipped = skipped
        return dest
    raise ValueError(f"unsupported target: {target}")

def walk_code(root):
    out = []
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for fn in fns:
            p = Path(dp) / fn
            if p.suffix.lower() in SKIP_EXT: continue
            if p.suffix.lower() not in CODE_EXT: continue
            try:
                if p.stat().st_size > MAX_FILE_BYTES: continue
            except OSError: continue
            out.append(p)
    return sorted(out)

# ── structure: real AST for Python, signature scan elsewhere ────────────────
FUNC_RE = {
    ".js":  re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", re.M),
    ".ts":  re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)|^\s*(?:export\s+)?const\s+(\w+)\s*=\s*(?:async\s*)?\(", re.M),
    ".ps1": re.compile(r"^\s*function\s+([\w-]+)", re.M | re.I),
    ".sh":  re.compile(r"^\s*(?:function\s+)?(\w+)\s*\(\)\s*\{", re.M),
    ".java":re.compile(r"^\s*(?:public|private|protected).*?\s(\w+)\s*\([^)]*\)\s*\{", re.M),
    ".cs":  re.compile(r"^\s*(?:public|private|protected|internal).*?\s(\w+)\s*\([^)]*\)\s*\{", re.M),
    ".go":  re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(\w+)\s*\(", re.M),
}
FUNC_RE[".jsx"] = FUNC_RE[".js"]; FUNC_RE[".tsx"] = FUNC_RE[".ts"]

def py_units(path, rel, text):
    """Real structure: one record per function/method, with callers resolved."""
    units, calls = [], defaultdict(set)
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        return [], {}, f"SyntaxError line {e.lineno}"
    parents = {}
    for node in ast.walk(tree):
        for ch in ast.iter_child_nodes(node): parents[ch] = node

    def qual(fn):
        bits = [fn.name]; p = parents.get(fn)
        while p is not None:
            if isinstance(p, ast.ClassDef): bits.append(p.name)
            p = parents.get(p)
        return ".".join(reversed(bits))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)): continue
        name = qual(node)
        seg = ast.get_source_segment(text, node) or ""
        callees = set()
        for n in ast.walk(node):
            if isinstance(n, ast.Call):
                f = n.func
                if isinstance(f, ast.Name): callees.add(f.id)
                elif isinstance(f, ast.Attribute): callees.add(f.attr)
        calls[name] = callees
        decorators = [ast.unparse(d) for d in node.decorator_list] if hasattr(ast,"unparse") else []
        args = [a.arg for a in node.args.args]
        units.append({
            "kind": "function", "ref": f"{rel}::{name}",
            "payload": {"file": rel, "name": name, "lineno": node.lineno,
                        "end": getattr(node, "end_lineno", node.lineno),
                        "args": args, "decorators": decorators,
                        "doc": (ast.get_docstring(node) or "")[:400],
                        "is_async": isinstance(node, ast.AsyncFunctionDef),
                        "source": seg[:6000], "lang": "python",
                        "has_try": "try:" in seg, "returns": "return " in seg,
                        "loc": seg.count("\n") + 1}})
    return units, calls, None

def other_units(path, rel, text):
    rx = FUNC_RE.get(path.suffix.lower())
    if not rx: return []
    out = []
    for m in rx.finditer(text):
        name = next((g for g in m.groups() if g), None)
        if not name: continue
        start = text.count("\n", 0, m.start()) + 1
        seg = text[m.start(): m.start() + 4000]
        out.append({"kind": "function", "ref": f"{rel}::{name}",
                    "payload": {"file": rel, "name": name, "lineno": start,
                                "source": seg, "lang": path.suffix.lstrip("."),
                                "doc": "", "args": [], "decorators": [],
                                "loc": seg.count("\n") + 1,
                                "has_try": "try" in seg, "returns": "return" in seg}})
    return out

# ── scanners ────────────────────────────────────────────────────────────────
def run_bandit(root, run_id):
    if not CFG.use_bandit: return False
    if not shutil.which("bandit"): return False
    try:
        p = subprocess.run(["bandit","-r",str(root),"-f","json","-q"],
                           capture_output=True, text=True, timeout=900)
        data = json.loads(p.stdout or "{}")
        for r in data.get("results", []):
            sev = {"HIGH":"HIGH","MEDIUM":"MEDIUM","LOW":"LOW"}.get(r.get("issue_severity","LOW"),"LOW")
            store.add_finding(run_id, sev, f"BANDIT-{r.get('test_id')}",
                              f"{Path(r.get('filename','')).name}:{r.get('line_number')}",
                              r.get("issue_text","")[:200],
                              detail=(r.get("code") or "")[:300],
                              fix="See bandit rule " + str(r.get("test_id")),
                              source="bandit")
        return True
    except Exception:
        return False

def run_semgrep(root, run_id):
    if not CFG.use_semgrep or not shutil.which("semgrep"): return False
    try:
        p = subprocess.run(["semgrep","--config","auto","--json","--quiet",str(root)],
                           capture_output=True, text=True, timeout=1800)
        data = json.loads(p.stdout or "{}")
        for r in data.get("results", []):
            ex = r.get("extra", {})
            sev = {"ERROR":"HIGH","WARNING":"MEDIUM","INFO":"LOW"}.get(ex.get("severity","INFO"),"LOW")
            store.add_finding(run_id, sev, f"SEMGREP-{r.get('check_id','')[:40]}",
                              f"{Path(r.get('path','')).name}:{r.get('start',{}).get('line')}",
                              (ex.get("message") or "")[:200], source="semgrep")
        return True
    except Exception:
        return False

# ── plan / unit worker / final pass ─────────────────────────────────────────
def plan(target, run_dir):
    root = prepare(target, run_dir)
    files = walk_code(root)
    units, calls, parse_errors, meta = [], {}, [], {"files": len(files), "loc": 0}
    for p in files:
        rel = str(p.relative_to(root))
        try: text = p.read_text(encoding="utf-8", errors="replace")
        except Exception: continue
        meta["loc"] += text.count("\n") + 1
        if p.suffix.lower() == ".py":
            u, c, err = py_units(p, rel, text)
            if err: parse_errors.append({"file": rel, "err": err})
            units += u; calls.update(c)
        else:
            units += other_units(p, rel, text)
    skipped = getattr(prepare, "skipped", []) or []
    unavailable = []
    if skipped: unavailable.append(f"{len(skipped)} archive member(s) could not be extracted")
    if not shutil.which("bandit"): unavailable.append("bandit (pip install bandit)")
    if not shutil.which("semgrep"): unavailable.append("semgrep (pip install semgrep)")
    return {"root": str(root), "units": units, "calls": {k: sorted(v) for k,v in calls.items()},
            "meta": meta, "parse_errors": parse_errors, "unavailable": unavailable,
            "skipped_members": skipped[:50]}

DIGEST_SYS = ("You describe ONE function for an engineer auditing a codebase he did "
              "not write. Reply as JSON with keys: purpose (one sentence), inputs "
              "(what it trusts), failure_modes (list, max 3), unvalidated (list of "
              "inputs used without checking, max 3), hardening (list, max 3 concrete "
              "suggestions). Describe only what the code does. If you cannot tell, "
              "say so in that field rather than guessing.")

def unit_worker(run_id, unit):
    p = unit["payload"]
    src = p.get("source") or ""
    ref = unit["ref"]

    # deterministic first
    hits = shield.screen(src, where=ref)
    for h in hits:
        store.add_finding(run_id, "HIGH", "INJECT-SRC", ref,
                          f"Injection-shaped text in source: {h['kind']}",
                          detail=h["match"], source="shield",
                          fix="Treat this file's comments/strings as hostile input; "
                              "do not let a digest of it drive any action.")
    if p.get("lang") == "python":
        for rx, sev, code, title in RISKY_PY:
            for m in re.finditer(rx, src, re.M):
                line = p.get("lineno",0) + src.count("\n",0,m.start())
                store.add_finding(run_id, sev, code, f"{p['file']}:{line}", title,
                                  detail=m.group(0)[:160], source="pattern")
    for rx, what in SECRETS:
        if re.search(rx, src):
            store.add_finding(run_id, "CRITICAL", "SECRET", ref,
                              f"Possible {what} in source", source="pattern",
                              fix="Rotate it, then move it to an environment variable.")
    # structural gaps — computed, not guessed
    if p.get("loc",0) > 80:
        store.add_finding(run_id, "LOW", "SIZE", ref,
                          f"{p['loc']} lines in one function", source="metric",
                          fix="Split it; long functions hide their own failure modes.")
    if not p.get("doc") and p.get("lang") == "python" and not p["name"].startswith("_"):
        store.add_finding(run_id, "INFO", "NODOC", ref, "Public function has no docstring",
                          source="metric")

    digest = {"ref": ref, "file": p.get("file"), "name": p.get("name"),
              "loc": p.get("loc"), "has_try": p.get("has_try"),
              "decorators": p.get("decorators", [])}
    out = llm.as_json(llm.chat(DIGEST_SYS, shield.wrap(src, ref),
                               max_tokens=400, model=CFG.coder_model or None))
    if out:
        digest.update({k: out.get(k) for k in
                       ("purpose","inputs","failure_modes","unvalidated","hardening")})
        digest["by"] = "model"
        for u in (out.get("unvalidated") or [])[:3]:
            store.add_finding(run_id, "MEDIUM", "UNVALIDATED", ref,
                              f"Unvalidated input: {str(u)[:90]}", source="model",
                              fix="Validate or reject at the boundary.")
    else:
        digest["purpose"] = None; digest["by"] = "structure-only"
    return digest

def final_pass(run_id):
    run = store.get_run(run_id)
    pl = run["plan"]; root = Path(pl["root"])
    ds = store.digests(run_id, kind="function")
    calls = pl.get("calls", {})

    # entry points and dead code, from the call graph
    called = set()
    for callees in calls.values(): called |= set(callees)
    defined = {d["digest"].get("name") for d in ds if d["digest"].get("name")}
    leaf_names = {n.split(".")[-1] for n in defined if n}
    orphans = sorted(n for n in leaf_names
                     if n and n not in called and not n.startswith("_")
                     and n not in ("main","app"))
    # tests
    test_files = [p for p in walk_code(root) if "test" in p.name.lower()]
    test_text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                          for p in test_files[:200])
    untested = [d["digest"]["name"] for d in ds
                if d["digest"].get("name") and
                d["digest"]["name"].split(".")[-1] not in test_text]
    if not test_files:
        store.add_finding(run_id, "HIGH", "NOTESTS", "(repository)",
                          "No test files found at all", source="metric",
                          fix="One test per entry point is the minimum that makes "
                              "refactoring safe.", unique=True)
    elif untested:
        store.add_finding(run_id, "MEDIUM", "UNTESTED", "(repository)",
                          f"{len(untested)} of {len(ds)} functions are never named in a test",
                          detail=", ".join(untested[:25]), source="metric", unique=True)
    # env vars read but undocumented
    env_used = set()
    for p in walk_code(root):
        t = p.read_text(encoding="utf-8", errors="replace")
        env_used |= set(re.findall(r"(?:os\.getenv|os\.environ(?:\.get)?\()\s*\(?\s*['\"]([A-Z0-9_]{3,})['\"]", t))
    documented = set()
    for cand in ("README.md",".env.example","README.txt"):
        f = root / cand
        if f.exists():
            documented |= set(re.findall(r"\b([A-Z][A-Z0-9_]{2,})\b",
                                         f.read_text(encoding="utf-8", errors="replace")))
    undocumented = sorted(env_used - documented)
    if undocumented:
        store.add_finding(run_id, "MEDIUM", "ENVDOC", "(repository)",
                          f"{len(undocumented)} env vars read but not documented",
                          detail=", ".join(undocumented[:25]), source="metric",
                          fix="Every variable the code reads belongs in .env.example.",
                          unique=True)
    for sm in (pl.get("skipped_members") or [])[:20]:
        store.add_finding(run_id, "INFO", "SKIPPED", sm[0][-60:],
                          f"Not analysed: {sm[1]}", source="extract",
                          fix="Coverage is incomplete for this path.")
    for pe in pl.get("parse_errors", []):
        store.add_finding(run_id, "HIGH", "PARSE", pe["file"],
                          f"File would not parse: {pe['err']}", source="ast")
    bandit_ok = run_bandit(root, run_id)
    semgrep_ok = run_semgrep(run_id and root, run_id) if CFG.use_semgrep else False

    sections = [("Structure", "\n".join([
        f"- Files scanned: **{pl['meta']['files']}**, ~{pl['meta']['loc']:,} lines",
        f"- Functions mapped: **{len(ds)}**",
        f"- Never called inside this repo (entry points or dead code): "
        f"{len(orphans)} — {', '.join(orphans[:20]) or 'none'}",
        f"- Test files: {len(test_files)}",
        f"- Scanners: bandit {'ran' if bandit_ok else 'UNAVAILABLE'}, "
        f"semgrep {'ran' if semgrep_ok else 'not run'}",
    ]))]
    return {"sections": sections, "functions": len(ds), "orphans": orphans[:50],
            "untested": untested[:100], "undocumented_env": undocumented}

def plan_items(run_id):
    f = store.findings(run_id)
    codes = {x["code"].split("-")[0] for x in f}
    items = []
    def ev(prefix):
        return [x["ref"] for x in f if x["code"].startswith(prefix)][:6]
    if "SECRET" in codes:
        items.append(dict(topic="Secrets handling", why="Credentials appear in source.",
            evidence=ev("SECRET"), read="12-factor config; your own .env.example pattern",
            check="Grep the repo for the flagged strings and confirm each is rotated.",
            question="Where does every secret this program needs come from at runtime?"))
    if any(c in codes for c in ("CODE","BANDIT")):
        items.append(dict(topic="Injection and unsafe execution", why="Patterns that hand input to an interpreter or shell.",
            evidence=ev("CODE"), read="OWASP command-injection cheat sheet",
            check="For each flagged call, trace where its argument comes from.",
            question="For each of these, can an outside value reach the interpreter?"))
    if "UNVALIDATED" in codes or "PARSE" in codes:
        items.append(dict(topic="Input validation at boundaries", why="Values are used before being checked.",
            evidence=ev("UNVALIDATED"), read="Parse-don't-validate; schema at the edge",
            check="Pick one entry point and list every value it trusts.",
            question="What is the boundary in this program, and what is validated at it?"))
    if "NOTESTS" in codes or "UNTESTED" in codes:
        items.append(dict(topic="A minimum test floor", why="Most functions are never named in a test.",
            evidence=ev("UNTESTED"), read="Test pyramid; characterisation tests for legacy code",
            check="Write one characterisation test for the largest untested function.",
            question="Which change to this code would break silently today?"))
    if "ENVDOC" in codes:
        items.append(dict(topic="Configuration surface", why="The code reads variables nobody documented.",
            evidence=ev("ENVDOC"), read="Your own §A.8 configuration reference",
            check="Run it with an empty environment and see what breaks.",
            question="What is the complete set of inputs this program takes from its environment?"))
    return items
