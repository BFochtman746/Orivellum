"""Per-stage context compiler for the book pipeline (B-stage workers).

Every pipeline stage declares a *context recipe*: which sources it needs and
how many characters of each it may consume.  The compiler assembles exactly
that package — GENESIS seal artifacts, canon facts, chapter contracts,
chapter prose, library documents, harvested knowledge, and prior-stage
artifacts — and returns it together with a machine-checkable
``context_report`` stating, per source, how much was delivered against the
declared budget and whether truncation occurred.

Doctrine (context-budget-controller / lore-delivery, M0 extraction):
- A worker never receives an unbounded dump; every source is clipped to its
  stage budget with the highest-priority material surviving first.
- A stage that does not declare a source receives none of it (budget 0).
- The report makes context delivery testable by assertion, not by eyeball.

Entry point: ``compile_context(pipeline_id, stage, db)``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

# ── Recipes ────────────────────────────────────────────────────────────────────

#: All source names a recipe may budget for.
SOURCE_NAMES = (
    "genesis",
    "canon",
    "contracts",
    "chapter_text",
    "documents",
    "knowledge",
    "prior",
)

#: Character budgets per stage and source.  0 = source excluded for the stage.
STAGE_RECIPES: dict[str, dict[str, int]] = {
    # B0 Intake: the brief is derived from the seal — GENESIS gets the bulk.
    "B0": {
        "genesis": 24_000,
        "canon": 6_000,
        "contracts": 0,
        "chapter_text": 0,
        "documents": 3_000,
        "knowledge": 3_000,
        "prior": 0,
    },
    # B1 Outline: reconcile to the G8 blueprint; contracts if scaffolded.
    "B1": {
        "genesis": 16_000,
        "canon": 6_000,
        "contracts": 6_000,
        "chapter_text": 0,
        "documents": 3_000,
        "knowledge": 2_000,
        "prior": 4_000,
    },
    # B2 Research: canon + knowledge are the ground truth being gap-checked.
    "B2": {
        "genesis": 8_000,
        "canon": 8_000,
        "contracts": 4_000,
        "chapter_text": 0,
        "documents": 4_000,
        "knowledge": 6_000,
        "prior": 6_000,
    },
    # B3 Architecture: blueprint + outline drive the dependency design.
    "B3": {
        "genesis": 12_000,
        "canon": 4_000,
        "contracts": 6_000,
        "chapter_text": 0,
        "documents": 2_000,
        "knowledge": 2_000,
        "prior": 8_000,
    },
    # B6 Continuity: operates on prose — chapter text gets the bulk.
    "B6": {
        "genesis": 4_000,
        "canon": 6_000,
        "contracts": 4_000,
        "chapter_text": 24_000,
        "documents": 0,
        "knowledge": 2_000,
        "prior": 6_000,
    },
    # B7 Fact check: prose against the canon record.
    "B7": {
        "genesis": 4_000,
        "canon": 10_000,
        "contracts": 2_000,
        "chapter_text": 16_000,
        "documents": 2_000,
        "knowledge": 4_000,
        "prior": 4_000,
    },
}

#: Which GENESIS artifacts matter most, per stage (first = survives clipping).
_GENESIS_PRIORITY: dict[str, list[str]] = {
    "B0": ["G9", "G1", "G2", "G5", "G8", "G3", "G4", "G6", "G7", "G0"],
    "B1": ["G8", "G5", "G1", "G9", "G3"],
    "B2": ["G3", "G8", "G1", "G2"],
    "B3": ["G8", "G5", "G1", "G9"],
    "B6": ["G8", "G5", "G4"],
    "B7": ["G3", "G8", "G1"],
}
_GENESIS_DEFAULT_ORDER = [f"G{i}" for i in range(10)]

_STAGE_ORDER = ["B0", "B1", "B2", "B3", "B4", "B5", "B6", "B7"]


def _clip(text: str, budget: int) -> tuple[str, bool]:
    """Return (clipped_text, truncated?)."""
    if budget <= 0:
        return "", bool(text)
    if len(text) <= budget:
        return text, False
    return text[:budget], True


# ── Source adapters ────────────────────────────────────────────────────────────


def _genesis_source(db: OrivellumDB, work_id: str, stage: str, budget: int) -> dict:
    """Sealed origination package: manifest + G-stage artifacts, clipped by priority."""
    empty = {
        "present": False,
        "sealed": False,
        "state": None,
        "manifest": None,
        "blueprint_chapter_count": None,
        "artifacts": {},
    }
    with db._lock:
        book = db._conn.execute(
            "SELECT * FROM genesis_books WHERE work_id=?", (work_id,)
        ).fetchone()
        if not book:
            return empty
        art_rows = db._conn.execute(
            "SELECT stage_code, content FROM genesis_artifacts WHERE book_id=? ORDER BY stage_code",
            (book["id"],),
        ).fetchall()

    sealed = book["state"] == "READY_FOR_B0"
    manifest = None
    if book["manifest_json"]:
        try:
            manifest = json.loads(book["manifest_json"])
        except ValueError:
            manifest = None

    by_code = {r["stage_code"]: (r["content"] or "") for r in art_rows}
    priority = _GENESIS_PRIORITY.get(stage, _GENESIS_DEFAULT_ORDER)
    order = [c for c in priority if c in by_code] + [
        c for c in _GENESIS_DEFAULT_ORDER if c in by_code and c not in priority
    ]

    artifacts: dict[str, str] = {}
    remaining = budget
    truncated = False
    for code in order:
        if remaining <= 0:
            truncated = True
            break
        text, was_cut = _clip(by_code[code], remaining)
        truncated = truncated or was_cut
        if text:
            artifacts[code] = text
            remaining -= len(text)

    return {
        "present": True,
        "sealed": sealed,
        "state": book["state"],
        "manifest": manifest,
        # The declared target chapter count is authoritative once sealed.
        "blueprint_chapter_count": int(book["length"]) if sealed else None,
        "artifacts": artifacts,
        "_truncated": truncated,
        "_available_codes": sorted(by_code.keys()),
    }


def _canon_source(db: OrivellumDB, work_id: str, budget: int) -> tuple[list[dict], bool]:
    """Active canon facts (work-scoped + series-scoped), clipped to budget."""
    from orivellum.database.canon_store import CanonStore

    rows = CanonStore(db).list_facts(work_id=work_id, include_series=True, status="active")
    facts: list[dict] = []
    used = 0
    truncated = False
    for r in rows:
        item = {
            "id": r["id"],
            "statement": r["statement"],
            "classification": r["classification"],
            "source_ref": r.get("source_ref") or "",
        }
        cost = len(item["statement"]) + len(item["source_ref"]) + 24
        if used + cost > budget:
            truncated = True
            break
        facts.append(item)
        used += cost
    return facts, truncated


def _contracts_source(db: OrivellumDB, work_id: str, budget: int) -> tuple[list[dict], bool]:
    """Per-chapter contracts (from book_chapters.meta), clipped to budget."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT seq, title, meta FROM book_chapters WHERE work_id=? ORDER BY seq",
            (work_id,),
        ).fetchall()
    out: list[dict] = []
    used = 0
    truncated = False
    for r in rows:
        try:
            meta = json.loads(r["meta"] or "{}")
        except ValueError:
            meta = {}
        contract = meta.get("contract") or {}
        item = {"seq": r["seq"], "title": r["title"] or "", "contract": contract}
        cost = len(json.dumps(contract, ensure_ascii=False)) + len(item["title"]) + 16
        if used + cost > budget:
            truncated = True
            break
        out.append(item)
        used += cost
    return out, truncated


def _chapter_text_source(db: OrivellumDB, work_id: str, budget: int) -> tuple[list[dict], bool]:
    """Chapter prose, budget split fairly across chapters (bounded per chapter)."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT seq, title, text FROM book_chapters WHERE work_id=? "
            "AND text IS NOT NULL AND text != '' ORDER BY seq",
            (work_id,),
        ).fetchall()
    if not rows or budget <= 0:
        return [], bool(rows) and budget <= 0
    share = max(200, budget // len(rows))
    out: list[dict] = []
    used = 0
    truncated = False
    for r in rows:
        if used >= budget:
            truncated = True
            break
        text = r["text"] or ""
        excerpt, was_cut = _clip(text, min(share, budget - used))
        truncated = truncated or was_cut
        out.append(
            {
                "seq": r["seq"],
                "title": r["title"] or "",
                "excerpt": excerpt,
                "words": len(text.split()),
            }
        )
        used += len(excerpt)
    return out, truncated


def _documents_source(db: OrivellumDB, work_id: str, budget: int) -> tuple[list[dict], bool]:
    with db._lock:
        rows = db._conn.execute(
            """SELECT d.title, d.source,
                      substr(coalesce(d.extracted_text,''), 1, 600) as summary
               FROM documents d JOIN objects o ON o.id=d.id
               WHERE d.work_id=? AND o.lifecycle != 'deleted'
               ORDER BY d.created_at DESC LIMIT 5""",
            (work_id,),
        ).fetchall()
    out: list[dict] = []
    used = 0
    truncated = False
    for r in rows:
        title = r["title"] or (r["source"] or "").split("/")[-1] or "Document"
        summary = (r["summary"] or "").strip()
        cost = len(title) + len(summary) + 8
        if used + cost > budget:
            truncated = True
            break
        out.append({"title": title, "summary": summary})
        used += cost
    return out, truncated


def _knowledge_source(db: OrivellumDB, work_id: str, budget: int) -> tuple[list[dict], bool]:
    with db._lock:
        rows = db._conn.execute(
            """SELECT kind, text, subject FROM knowledge
               WHERE work_id=? AND review_status NOT IN ('rejected','quarantined_reprojection')
               ORDER BY confidence DESC LIMIT 20""",
            (work_id,),
        ).fetchall()
    out: list[dict] = []
    used = 0
    truncated = False
    for r in rows:
        item = {"kind": r["kind"], "text": r["text"], "subject": r["subject"] or ""}
        cost = len(item["text"]) + len(item["subject"]) + 12
        if used + cost > budget:
            truncated = True
            break
        out.append(item)
        used += cost
    return out, truncated


def _prior_source(
    db: OrivellumDB, pipeline_id: str, stage: str, budget: int
) -> tuple[dict[str, Any], bool]:
    prior: dict[str, Any] = {}
    if stage not in _STAGE_ORDER or budget <= 0:
        return prior, False
    used = 0
    truncated = False
    idx = _STAGE_ORDER.index(stage)
    for s in _STAGE_ORDER[:idx]:
        art = db.get_pipeline_artifact(pipeline_id, s)
        if not (art and art.get("status") == "done"):
            continue
        cost = len(json.dumps(art["content"], ensure_ascii=False))
        if used + cost > budget:
            truncated = True
            continue
        prior[s] = art["content"]
        used += cost
    return prior, truncated


# ── Block renderers ────────────────────────────────────────────────────────────
# The compiler budgets the EXACT strings a worker prompt will receive.  Each
# source is rendered here and hard-clipped to its budget; the report counts
# the rendered block, so "within budget" is true of what is delivered, not of
# an approximation.


def render_genesis(genesis: dict) -> str:
    if not genesis.get("artifacts"):
        return ""
    parts = []
    manifest = genesis.get("manifest") or {}
    if manifest:
        parts.append(
            f"SEAL MANIFEST: package_sha256={manifest.get('package_sha256', '?')} "
            f"sealed_at={manifest.get('sealed_at', '?')} "
            f"author={manifest.get('author_signoff', '?')}"
        )
    for code, content in genesis["artifacts"].items():
        parts.append(f"=== {code} ===\n{content}")
    return "\n\n".join(parts)


def render_canon(facts: list[dict]) -> str:
    lines = []
    for f in facts:
        src = f" [source: {f['source_ref']}]" if f["source_ref"] else ""
        lines.append(f"- ({f['classification']}) {f['statement']}{src}")
    return "\n".join(lines)


def render_contracts(contracts: list[dict]) -> str:
    lines = []
    for c in contracts:
        detail = json.dumps(c["contract"], ensure_ascii=False) if c["contract"] else "{}"
        lines.append(f"{c['seq']}. {c['title']}: {detail}")
    return "\n".join(lines)


def render_chapter_text(chapters: list[dict]) -> str:
    parts = []
    for c in chapters:
        parts.append(
            f"--- Chapter {c['seq']}: {c['title']} ({c['words']} words) ---\n{c['excerpt']}"
        )
    return "\n\n".join(parts)


def render_documents(documents: list[dict]) -> str:
    lines = []
    for i, d in enumerate(documents, 1):
        summary = d["summary"].replace("\n", " ") if d["summary"] else "(no text extracted)"
        lines.append(f"{i}. {d['title']}: {summary}")
    return "\n".join(lines)


def render_knowledge(knowledge: list[dict]) -> str:
    lines = []
    for k in knowledge:
        subj = f"[{k['subject']}] " if k["subject"] else ""
        lines.append(f"- {k['kind'].upper()}: {subj}{k['text']}")
    return "\n".join(lines)


def render_prior(prior: dict) -> str:
    parts = []
    for stage, content in prior.items():
        parts.append(f"[{stage}] " + json.dumps(content, ensure_ascii=False))
    return "\n\n".join(parts)


_RENDERERS = {
    "genesis": render_genesis,
    "canon": render_canon,
    "contracts": render_contracts,
    "chapter_text": render_chapter_text,
    "documents": render_documents,
    "knowledge": render_knowledge,
    "prior": render_prior,
}


# ── Compiler ───────────────────────────────────────────────────────────────────


def compile_context(pipeline_id: str, stage: str, db: OrivellumDB) -> dict:
    """Assemble the declared context package for *stage*.

    Returns a dict with the work header, one key per source, and a
    ``context_report`` mapping each source to
    ``{"budget", "chars", "items", "truncated"}`` so tests can assert exact
    delivery.  Raises ``ValueError`` for unknown pipelines or stages without
    a recipe.
    """
    recipe = STAGE_RECIPES.get(stage)
    if recipe is None:
        raise ValueError(f"No context recipe declared for stage {stage!r}")

    with db._lock:
        row = db._conn.execute(
            """SELECT bp.work_id, w.title, w.description
               FROM book_pipelines bp JOIN works w ON w.id=bp.work_id
               WHERE bp.id=?""",
            (pipeline_id,),
        ).fetchone()
    if not row:
        raise ValueError(f"Pipeline {pipeline_id!r} not found")
    work_id = row["work_id"]

    report: dict[str, dict] = {}
    blocks: dict[str, str] = {}

    def _finalize(name: str, items: int, rendered: str, pre_truncated: bool) -> None:
        """Hard-clip the rendered block to its budget; report what is DELIVERED."""
        budget = recipe.get(name, 0)
        block, clipped = _clip(rendered, budget)
        blocks[name] = block
        report[name] = {
            "budget": budget,
            "chars": len(block),
            "items": items if block else 0,
            "truncated": pre_truncated or clipped,
        }

    # GENESIS
    if recipe.get("genesis", 0) > 0:
        genesis = _genesis_source(db, work_id, stage, recipe["genesis"])
        pre_cut = genesis.pop("_truncated", False)
        genesis.pop("_available_codes", None)
    else:
        genesis = {
            "present": False,
            "sealed": False,
            "state": None,
            "manifest": None,
            "blueprint_chapter_count": None,
            "artifacts": {},
        }
        pre_cut = False
    _finalize("genesis", len(genesis["artifacts"]), render_genesis(genesis), pre_cut)

    # Canon facts
    if recipe.get("canon", 0) > 0:
        canon_facts, cut = _canon_source(db, work_id, recipe["canon"])
    else:
        canon_facts, cut = [], False
    _finalize("canon", len(canon_facts), render_canon(canon_facts), cut)

    # Chapter contracts
    if recipe.get("contracts", 0) > 0:
        contracts, cut = _contracts_source(db, work_id, recipe["contracts"])
    else:
        contracts, cut = [], False
    _finalize("contracts", len(contracts), render_contracts(contracts), cut)

    # Chapter prose
    if recipe.get("chapter_text", 0) > 0:
        chapters, cut = _chapter_text_source(db, work_id, recipe["chapter_text"])
    else:
        chapters, cut = [], False
    _finalize("chapter_text", len(chapters), render_chapter_text(chapters), cut)

    # Documents
    if recipe.get("documents", 0) > 0:
        documents, cut = _documents_source(db, work_id, recipe["documents"])
    else:
        documents, cut = [], False
    _finalize("documents", len(documents), render_documents(documents), cut)

    # Knowledge
    if recipe.get("knowledge", 0) > 0:
        knowledge, cut = _knowledge_source(db, work_id, recipe["knowledge"])
    else:
        knowledge, cut = [], False
    _finalize("knowledge", len(knowledge), render_knowledge(knowledge), cut)

    # Prior artifacts
    prior_artifacts, cut = _prior_source(db, pipeline_id, stage, recipe.get("prior", 0))
    _finalize("prior", len(prior_artifacts), render_prior(prior_artifacts), cut)

    return {
        "stage": stage,
        "recipe": dict(recipe),
        "work_id": work_id,
        "work_title": row["title"] or "Untitled",
        "work_description": row["description"] or "",
        "genesis": genesis,
        "canon_facts": canon_facts,
        "chapter_contracts": contracts,
        "chapters": chapters,
        "documents": documents,
        "knowledge": knowledge,
        "prior_artifacts": prior_artifacts,
        "blocks": blocks,
        "context_report": report,
    }
