"""CI floor checks for security, permission, and unattended code paths.

Two mechanical rules from the August 2026 cross-cutting review (Class 4) and
the retrieval audit (Part 4).  Both are AST-based import/reference-graph
tests, not coverage percentages and not raw text greps (comments, docstrings,
and string literals never satisfy a rule):

1. THE FLOOR RULE — no module may be imported by a permission decision, a
   security path, or an unattended job unless at least one test file
   actually imports it.  Roots:
     * unattended  — nightshift, custodian, autonomy (run with nobody watching)
     * security    — the mail package (credentials, outbound actions), shield
     * permission  — action_policy, auth_keys, api/_deps (require_auth)
   The check walks every ``orivellum.*`` import (including function-local
   imports) in the root files and requires some file under tests/ to import
   the same module.

2. THE ZERO-CALLER RULE — a built-but-never-connected feature must fail the
   build (websearch, training_plan, and rerank_candidates all shipped that
   way).  Two levels:
     a. every capabilities module must be imported by production code
        outside itself — a module nothing imports is an unfinished wire;
     b. every public entry point must appear as a real identifier
        (``ast.Name`` / ``ast.Attribute`` / import alias) outside its own
        module in production code OR tests — a name neither wired nor
        tested is dead code.

Additions to either allowlist must carry a date and a reason, and the
allowlists are RATCHETED — they may only shrink (enforced below).
"""

from __future__ import annotations

import ast
import re
from functools import cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "orivellum"
TESTS = REPO / "tests"
CAPABILITIES = SRC / "capabilities"

# ── Rule 1 roots ──────────────────────────────────────────────────────────────

_UNATTENDED_ROOTS = [
    CAPABILITIES / "nightshift.py",
    CAPABILITIES / "custodian.py",
    CAPABILITIES / "autonomy.py",
]
_SECURITY_ROOTS = [
    *sorted((CAPABILITIES / "mail").glob("*.py")),
    CAPABILITIES / "shield.py",
]
_PERMISSION_ROOTS = [
    CAPABILITIES / "mail" / "action_policy.py",
    SRC / "api" / "auth_keys.py",  # the only credential path (repo hygiene rule)
    SRC / "api" / "_deps.py",  # hosts require_auth — every router's gate
]

# Modules imported by a root but temporarily exempt from the floor.
# Format: dotted module -> "YYYY-MM-DD — reason".  Every entry is debt.
FLOOR_ALLOWLIST: dict[str, str] = {}

# Rule 2: (module path relative to src/orivellum, public name) -> dated reason.
# _G = grandfathered when the rule was introduced.  These are public names with
# no reference outside their own module — either rename them _private, wire
# them, delete them, or write a test that uses them; then remove the entry
# AND lower the ratchet constant.
_G = (
    "2026-08-12 — grandfathered at zero-caller rule introduction; "
    "public name used only inside its own module"
)
ZERO_CALLER_ALLOWLIST: dict[tuple[str, str], str] = {
    ("capabilities/chapters.py", "ExtractedChapter"): _G,
    ("capabilities/bench.py", "stream_probe"): _G,
    ("capabilities/bench.py", "run_generation_bench"): _G,
    ("capabilities/bench.py", "run_cache_probe"): _G,
    ("capabilities/shield.py", "Screening"): _G,
    ("capabilities/shield.py", "is_trusted_recipient"): _G,
    ("capabilities/completeness.py", "CompletenessReport"): _G,
    ("capabilities/diagnostics.py", "render_markdown"): _G,
    ("capabilities/embeddings.py", "embed_with_late_chunking"): _G,
    ("capabilities/enums.py", "FindingSeverity"): _G,
    ("capabilities/folder_watch.py", "stop_watcher"): _G,
    ("capabilities/intake.py", "SuggestedAction"): _G,
    ("capabilities/mcos.py", "run_prompt_benchmark"): _G,
    ("capabilities/retrieval.py", "QueryType"): _G,
    ("capabilities/websearch.py", "SearchProfile"): _G,
    ("capabilities/websearch.py", "ResearchDiagnostics"): _G,
    ("capabilities/notes.py", "vault_root"): _G,
    ("capabilities/notes.py", "classify_block"): _G,
    ("capabilities/enhancement.py", "setup_in_progress"): _G,
    ("capabilities/enhancement.py", "get_setup_progress"): _G,
    ("capabilities/wa_decompose.py", "ParsedDoc"): _G,
    ("capabilities/wa_decompose.py", "parse_docx_bytes"): _G,
    ("capabilities/wa_decompose.py", "defer_reason_for"): _G,
    ("capabilities/wa_decompose.py", "build_engine_contract"): _G,
    ("capabilities/wa_decompose.py", "engine_index_statuses"): _G,
    ("capabilities/wa_decompose.py", "proposals_from_doc"): _G,
    ("capabilities/wa_decompose.py", "distill_voice_envelope"): _G,
    ("capabilities/wa_decompose.py", "build_position_spec"): _G,
    ("capabilities/wa_decompose.py", "build_generic_payload"): _G,
    ("capabilities/context_compiler.py", "render_genesis"): _G,
    ("capabilities/context_compiler.py", "render_canon"): _G,
    ("capabilities/context_compiler.py", "render_contracts"): _G,
    ("capabilities/context_compiler.py", "render_chapter_text"): _G,
    ("capabilities/context_compiler.py", "render_documents"): _G,
    ("capabilities/context_compiler.py", "render_knowledge"): _G,
    ("capabilities/context_compiler.py", "render_prior"): _G,
    ("capabilities/loom.py", "drafting_model"): _G,
    ("capabilities/loom.py", "critic_model"): _G,
    ("capabilities/loom.py", "assemble_context"): _G,
    ("capabilities/position.py", "PositionError"): _G,
    ("capabilities/position.py", "completion_plan"): _G,
    ("capabilities/workbench_analyze.py", "build_report"): _G,
    ("capabilities/workbench.py", "archives_dir"): _G,
    ("capabilities/corpus_hygiene.py", "HygieneFinding"): _G,
    ("capabilities/corpus_hygiene.py", "HygieneReport"): _G,
    ("capabilities/domain_model.py", "normalize_node_key"): _G,
    ("capabilities/domain_model.py", "corpus_evidence_count"): _G,
    ("capabilities/pcwa.py", "candidates_functional_closure"): _G,
    ("capabilities/pklos/capture_stamp.py", "extract_claims_from_text"): _G,
    ("capabilities/pklos/claim_verifier.py", "values_agree"): _G,
    ("capabilities/pklos/output_validator.py", "ClaimRef"): _G,
    ("capabilities/trailer/io_orivellum.py", "slugify"): _G,
    ("capabilities/trailer/util.py", "load_yaml"): _G,
    ("capabilities/finishing/gateway.py", "EpigraphResult"): _G,
    ("capabilities/finishing/gateway.py", "CoverVersion"): _G,
    ("capabilities/mail/models.py", "ActionRequest"): _G,
    ("capabilities/mail/models.py", "AuditEvent"): _G,
    ("capabilities/mail/action_policy.py", "PolicyDecision"): _G,
    ("capabilities/operations/scheduler.py", "system_busy"): _G,
    ("capabilities/operations/store.py", "get_operation_state"): _G,
    ("capabilities/assay/metrics.py", "imagery_density"): _G,
    ("capabilities/assay/metrics.py", "dialogue_spans"): _G,
    ("capabilities/assay/drift.py", "sentences_count"): _G,
    ("capabilities/assay/promotion.py", "promotion_bar"): _G,
    ("capabilities/assay/force.py", "build_profiles"): _G,
    ("capabilities/assay/force.py", "detect_structural_enforcement"): _G,
    ("capabilities/assay/force.py", "detect_narrative_physics"): _G,
    ("capabilities/assay/force.py", "detect_pressure_curve"): _G,
    ("capabilities/assay/force.py", "detect_conflict_escalation"): _G,
    ("capabilities/assay/force.py", "detect_scene_purpose"): _G,
    ("capabilities/assay/force.py", "detect_story_momentum"): _G,
    ("capabilities/assay/force.py", "detect_theme_integrity"): _G,
    # Surfaced when the rule moved from text-grep to AST identifiers
    # (docstring/string mentions no longer count as references):
    ("capabilities/trailer/util.py", "<module>"): _G,
    ("capabilities/classify.py", "Classification"): _G,
    ("capabilities/completeness.py", "Dimension"): _G,
    ("capabilities/embeddings.py", "embed_text"): _G,
    ("capabilities/retrieval.py", "RetrievalConfig"): _G,
    ("capabilities/wa_decompose.py", "Section"): _G,
    ("capabilities/pipeline_workers.py", "compile_stage_context"): _G,
    ("capabilities/position.py", "reconstruct"): _G,
    ("capabilities/trailer/method.py", "select"): _G,
    ("capabilities/finishing/gateway.py", "Gateway"): _G,
    ("capabilities/mail/models.py", "MailRecord"): _G,
    ("capabilities/assay/metrics.py", "diction_fingerprints"): _G,
    # Surfaced when the rule became symbol-aware (attribute references now
    # resolve to the owning module; monkeypatch string targets don't count):
    ("capabilities/constory.py", "is_running"): _G,
    ("capabilities/position.py", "claimed_stage"): _G,
    ("capabilities/corpus_hygiene.py", "finding_key"): _G,
    ("capabilities/pklos/authority_resolver.py", "is_prohibited_source"): _G,
    ("capabilities/finishing/atelier.py", "list_series"): _G,
    ("capabilities/finishing/atelier.py", "get_series"): _G,
    ("capabilities/finishing/atelier.py", "list_books"): _G,
    ("capabilities/finishing/atelier.py", "get_spec"): _G,
    ("capabilities/finishing/press.py", "output_dir"): _G,
    ("capabilities/finishing/press.py", "list_distribution"): _G,
    ("capabilities/finishing/press.py", "get_ledger"): _G,
}
# Tamper-evident snapshot of the grandfathered baseline.  The hygiene test
# recomputes the hash of the live allowlist keys and compares: ANY addition,
# removal, or substitution forces an edit of this literal — a loud,
# deliberate act the diff makes obvious.  Shrinking is encouraged (delete the
# entry, recompute with scripts: see the test's failure message); growing or
# swapping requires justification in review.
_ZERO_CALLER_BASELINE_SHA256 = (
    "e870af3c7b2ef18b35071db02afe9a2e3b8cbb250abcc1ab6dbd014184101edb"  # 95 entries
)
_FLOOR_BASELINE_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # empty


# ── AST helpers ───────────────────────────────────────────────────────────────


@cache
def _parsed(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8", errors="replace"))


def _dotted_for(path: Path) -> str | None:
    """Fully-qualified module name for a file under src/orivellum, else None."""
    try:
        rel = path.relative_to(SRC)
    except ValueError:
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["orivellum", *parts]) if parts else "orivellum"


def _module_file(dotted: str) -> Path | None:
    rel = dotted.replace("orivellum.", "", 1).replace(".", "/")
    for candidate in (SRC / f"{rel}.py", SRC / rel / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imports_of(path: Path) -> set[str]:
    """Every orivellum.* dotted module imported anywhere in a file.

    Handles ``import orivellum.x``, ``from orivellum.x import y`` (where y may
    itself be a module), and RELATIVE imports resolved against the file's
    package — sibling imports inside capabilities packages are relative.
    """
    found: set[str] = set()
    self_dotted = _dotted_for(path)
    for node in ast.walk(_parsed(path)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "orivellum" or alias.name.startswith("orivellum."):
                    found.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from(node, path, self_dotted)
            if module is None:
                continue
            found.add(module)
            for alias in node.names:
                candidate = f"{module}.{alias.name}"
                if _module_file(candidate) is not None:
                    found.add(candidate)
    return found


def _resolve_import_from(node: ast.ImportFrom, path: Path, self_dotted: str | None) -> str | None:
    """Dotted orivellum module an ImportFrom targets, or None if external."""
    if node.level and self_dotted:
        # Resolve `from ..x import y` relative to this file's package.
        base_parts = self_dotted.split(".")
        if path.name != "__init__.py":
            base_parts = base_parts[:-1]
        if node.level > 1:
            base_parts = base_parts[: -(node.level - 1)]
        if not base_parts:
            return None
        return ".".join(base_parts + (node.module.split(".") if node.module else []))
    if node.module and (node.module == "orivellum" or node.module.startswith("orivellum.")):
        return node.module
    return None


def _references_of(path: Path) -> set[tuple[str, str]]:
    """Symbol-aware references: {(dotted module, name)} a file actually uses.

    A reference counts ONLY when it provably resolves to the target module:
      * ``from orivellum.x.y import name``            → (orivellum.x.y, name)
      * ``import orivellum.x.y`` + ``orivellum.x.y.name(...)``
      * ``import orivellum.x.y as z`` + ``z.name(...)``
      * ``from orivellum.x import y`` (y a module) + ``y.name(...)``
    An unrelated local variable or ``other_object.name`` with the same
    spelling never satisfies the rule; nor do comments, docstrings, or
    string literals (so ``monkeypatch.setattr(mod, "name", ...)`` does not
    count — patching is not calling).
    """
    refs: set[tuple[str, str]] = set()
    aliases: dict[str, str] = {}  # local binding -> dotted module
    tree = _parsed(path)
    for node in ast.walk(tree):
        _collect_import_bindings(node, path, aliases, refs)
    for node in ast.walk(tree):
        _collect_attribute_ref(node, aliases, refs)
    return refs


def _collect_import_bindings(
    node: ast.AST, path: Path, aliases: dict[str, str], refs: set[tuple[str, str]]
) -> None:
    if isinstance(node, ast.Import):
        for alias in node.names:
            if not (alias.name == "orivellum" or alias.name.startswith("orivellum.")):
                continue
            if alias.asname:
                aliases[alias.asname] = alias.name
            else:
                # `import orivellum.x.y` binds the ROOT name; attribute
                # chains starting at `orivellum` are resolved separately.
                aliases.setdefault("orivellum", "orivellum")
    elif isinstance(node, ast.ImportFrom):
        module = _resolve_import_from(node, path, _dotted_for(path))
        if module is None:
            return
        for alias in node.names:
            local = alias.asname or alias.name
            if _module_file(f"{module}.{alias.name}") is not None:
                aliases[local] = f"{module}.{alias.name}"  # imported a module
            else:
                refs.add((module, alias.name))  # imported a symbol


def _collect_attribute_ref(
    node: ast.AST, aliases: dict[str, str], refs: set[tuple[str, str]]
) -> None:
    if not isinstance(node, ast.Attribute):
        return
    chain = _attr_chain(node)
    if not chain or chain[0] not in aliases:
        return
    parts = aliases[chain[0]].split(".") + chain[1:]
    # Longest prefix that is a real module; next component is the name.
    for i in range(len(parts) - 1, 0, -1):
        dotted = ".".join(parts[:i])
        if _module_file(dotted) is not None:
            refs.add((dotted, parts[i]))
            return


def _attr_chain(node: ast.Attribute) -> list[str] | None:
    """Flatten ``a.b.c`` to ['a','b','c']; None when the base isn't a Name."""
    parts = [node.attr]
    cur = node.value
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return list(reversed(parts))


def _production_files() -> list[Path]:
    files = [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]
    files += list((REPO / "scripts").glob("*.py"))
    return files


def _test_files() -> list[Path]:
    return [p for p in TESTS.rglob("*.py") if "__pycache__" not in p.parts]


def _capability_files() -> list[Path]:
    return [
        p
        for p in CAPABILITIES.rglob("*.py")
        if p.name != "__init__.py" and "__pycache__" not in p.parts
    ]


# ── Rule 1: the floor ─────────────────────────────────────────────────────────


def test_floor_rule_security_permission_unattended_modules_have_tests():
    roots = _UNATTENDED_ROOTS + _SECURITY_ROOTS + _PERMISSION_ROOTS
    imported: set[str] = set()
    for root in roots:
        assert root.is_file(), f"floor root missing: {root} — update the root list"
        imported |= _imports_of(root)
        dotted = _dotted_for(root)
        if dotted:
            imported.add(dotted)  # the roots themselves are on the floor too

    # Modules the test suite actually imports (AST, not text).
    tested: set[str] = set()
    for tf in _test_files():
        tested |= _imports_of(tf)

    untested: list[str] = []
    for dotted in sorted(imported):
        mod_file = _module_file(dotted)
        if mod_file is None or mod_file.name == "__init__.py":
            continue  # package prefix — its concrete modules are checked
        if dotted in FLOOR_ALLOWLIST:
            continue
        if dotted not in tested:
            untested.append(dotted)

    assert not untested, (
        "Modules on a security/permission/unattended path that NO test file "
        f"imports (the floor rule): {untested}. Write tests, or add a dated "
        "FLOOR_ALLOWLIST entry with a reason."
    )


# ── Allowlist hygiene: dated entries + shrink-only ratchet ───────────────────


def test_allowlist_entries_are_dated_and_ratcheted():
    for key, reason in FLOOR_ALLOWLIST.items():
        assert re.match(r"^\d{4}-\d{2}-\d{2} — ", reason), (
            f"FLOOR_ALLOWLIST[{key!r}] must start with 'YYYY-MM-DD — reason'"
        )
    for key, reason in ZERO_CALLER_ALLOWLIST.items():
        assert re.match(r"^\d{4}-\d{2}-\d{2} — ", reason), (
            f"ZERO_CALLER_ALLOWLIST[{key!r}] must start with 'YYYY-MM-DD — reason'"
        )
    # Tamper-evident baseline: hash of the live allowlist keys must match the
    # hardcoded snapshot.  Any addition, removal, OR substitution changes the
    # hash and forces an edit of the baseline literal — nobody can quietly
    # swap one exemption for another under a stable count.
    floor_hash = _allowlist_hash(sorted(FLOOR_ALLOWLIST))
    assert floor_hash == _FLOOR_BASELINE_SHA256, (
        f"FLOOR_ALLOWLIST changed (hash {floor_hash}). If you SHRANK it, "
        "update _FLOOR_BASELINE_SHA256 to this value. Growing it needs "
        "review justification — prefer writing the missing tests."
    )
    zc_hash = _allowlist_hash(sorted(f"{a}::{b}" for a, b in ZERO_CALLER_ALLOWLIST))
    assert zc_hash == _ZERO_CALLER_BASELINE_SHA256, (
        f"ZERO_CALLER_ALLOWLIST changed (hash {zc_hash}). If you SHRANK it, "
        "update _ZERO_CALLER_BASELINE_SHA256 to this value. Growing or "
        "swapping entries needs review justification — prefer wiring, "
        "deleting, or testing the name."
    )


def _allowlist_hash(keys) -> str:
    import hashlib

    return hashlib.sha256("\n".join(keys).encode("utf-8")).hexdigest()


# ── Rule 2: zero callers ──────────────────────────────────────────────────────


def _public_entry_points(path: Path) -> list[str]:
    return [
        node.name
        for node in _parsed(path).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("_")
    ]


def test_zero_caller_rule_every_capability_module_is_imported():
    """Level (a): a capabilities module no production code imports is an
    unfinished wire — the websearch failure mode — and fails the build."""
    prod_imports: dict[Path, set[str]] = {p: _imports_of(p) for p in _production_files()}
    orphans: list[str] = []
    for mod in _capability_files():
        rel = str(mod.relative_to(SRC))
        if (rel, "<module>") in ZERO_CALLER_ALLOWLIST:
            continue
        dotted = _dotted_for(mod)
        # The package's own __init__.py counts as a caller: registry-style
        # packages (e.g. the actions framework) wire their modules there.
        if not any(dotted in imps for p, imps in prod_imports.items() if p != mod):
            orphans.append(rel)
    assert not orphans, (
        f"Capability modules imported by NO production code: {orphans}. "
        "Wire them, delete them, or add a dated ZERO_CALLER_ALLOWLIST entry "
        "keyed (module, '<module>')."
    )


def test_zero_caller_rule_every_public_capability_entry_point_is_referenced():
    """Level (b): a public entry point that no production code or test
    provably imports/uses (symbol-aware, not same-spelling) is dead code
    and fails the build."""
    all_files = _production_files() + _test_files()
    refs: set[tuple[str, str]] = set()
    for p in all_files:
        for module, name in _references_of(p):
            if p != _module_file(module):  # self-references don't count
                refs.add((module, name))

    orphans: list[str] = []
    for mod in _capability_files():
        rel = str(mod.relative_to(SRC))
        dotted = _dotted_for(mod)
        for name in _public_entry_points(mod):
            if (rel, name) in ZERO_CALLER_ALLOWLIST:
                continue
            if (dotted, name) not in refs:
                orphans.append(f"{rel}:{name}")

    assert not orphans, (
        "Public capability entry points with ZERO resolved references outside "
        f"their own module (dead code or an unfinished wire): {orphans}. "
        "Wire them, delete them, or add a dated ZERO_CALLER_ALLOWLIST entry."
    )


# ── The rule checks itself: adversarial reference-resolution tests ───────────


def _refs_from_source(tmp_path: Path, source: str) -> set[tuple[str, str]]:
    f = tmp_path / "sample_ref_probe.py"
    f.write_text(source, encoding="utf-8")
    _parsed.cache_clear()
    try:
        return _references_of(f)
    finally:
        _parsed.cache_clear()


def test_reference_resolution_counts_only_the_real_module(tmp_path):
    target = ("orivellum.capabilities.cluster", "run_clustering")
    # Direct symbol import counts.
    assert target in _refs_from_source(
        tmp_path, "from orivellum.capabilities.cluster import run_clustering\n"
    )
    # Module alias attribute counts.
    assert target in _refs_from_source(
        tmp_path,
        "import orivellum.capabilities.cluster as cl\ncl.run_clustering(None)\n",
    )
    # Full dotted chain counts.
    assert target in _refs_from_source(
        tmp_path,
        "import orivellum.capabilities.cluster\n"
        "orivellum.capabilities.cluster.run_clustering(None)\n",
    )
    # `from package import module` then attribute counts.
    assert target in _refs_from_source(
        tmp_path,
        "from orivellum.capabilities import cluster\ncluster.run_clustering(None)\n",
    )


def test_reference_resolution_rejects_same_spelled_impostors(tmp_path):
    target = ("orivellum.capabilities.cluster", "run_clustering")
    # A local variable with the same name is NOT a reference.
    assert target not in _refs_from_source(tmp_path, "run_clustering = 1\nrun_clustering\n")
    # An attribute on an unrelated object is NOT a reference.
    assert target not in _refs_from_source(tmp_path, "class Other: pass\nOther().run_clustering\n")
    # The same attribute reached through a DIFFERENT orivellum module is not
    # a reference to cluster.
    assert target not in _refs_from_source(
        tmp_path,
        "import orivellum.capabilities.custodian as c\nc.run_clustering\n",
    )
    # A string literal / patch target is NOT a reference.
    assert target not in _refs_from_source(
        tmp_path,
        "import orivellum.capabilities.cluster as cl\n"
        "x = 'cl.run_clustering'\n"
        "def patch(o, n, v): pass\n"
        "patch(cl, 'run_clustering', None)\n",
    )
