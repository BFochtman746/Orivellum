"""Multi-dimensional completeness scoring for Works.

Implements the MONARCH spec requirement: every completeness score must
trace to explicit criteria, calculation rules, and evidence.  A single
unexplained percentage is prohibited.

Dimensions:
  structural   — chapters / sections present relative to expected
  content      — total word count relative to a baseline
  research     — chapters that have ≥ N knowledge items
  editorial    — knowledge items that have been reviewed
  source       — distinct source documents feeding the work

Overall readiness label (never a bare number):
  Draft | Developing | Substantial | Near-Complete | Ready
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────

_CONTENT_BASELINE_WORDS = 50_000    # typical non-fiction manuscript
_RESEARCH_MIN_ITEMS = 3             # items per chapter to be "covered"
_EXPECTED_CHAPTERS_DEFAULT = 10     # assumed when no chapters extracted yet


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class Dimension:
    name: str
    label: str
    score: int                  # 0-100
    current: int | float
    target: int | float
    unit: str
    rule: str                   # plain-language calculation rule
    evidence: list[str] = field(default_factory=list)


@dataclass
class CompletenessReport:
    work_id: str
    work_title: str
    dimensions: list[Dimension]
    overall: int                # weighted average 0-100
    readiness: str              # "Draft" … "Ready"
    summary: str
    evaluated_at: str


# ── Weights ───────────────────────────────────────────────────────────────────

_WEIGHTS = {
    "structural": 0.25,
    "content":    0.25,
    "research":   0.25,
    "editorial":  0.15,
    "source":     0.10,
}


# ── Public API ────────────────────────────────────────────────────────────────

def calculate_work_completeness(work_id: str, db: "OrivellumDB") -> CompletenessReport:
    """Calculate a multi-dimensional completeness report for a Work."""
    import datetime

    work = db.get_work(work_id) or {}
    work_title = work.get("title") or work_id[:8]

    # ── Gather raw data ──────────────────────────────────────────────────────

    with db._lock:
        # Documents linked to this work
        doc_rows = db._conn.execute(
            "SELECT id, word_count FROM documents WHERE work_id=? AND readiness='ready'",
            (work_id,),
        ).fetchall()

        # Chapters across all documents
        doc_ids = [r["id"] for r in doc_rows]
        chapters: list[dict] = []
        if doc_ids:
            placeholders = ",".join("?" * len(doc_ids))
            chapter_rows = db._conn.execute(
                f"""SELECT bc.id, bc.source_doc_id,
                       (length(coalesce(bc.text,'')) - length(replace(coalesce(bc.text,''),' ','')) + 1) as wc
                    FROM book_chapters bc WHERE bc.source_doc_id IN ({placeholders})""",
                doc_ids,
            ).fetchall()
            chapters = [dict(r) for r in chapter_rows]

        # Knowledge items
        kn_rows = db._conn.execute(
            """SELECT review_status, source_doc_id
               FROM knowledge WHERE work_id=?""",
            (work_id,),
        ).fetchall()

    total_words = sum(r["word_count"] or 0 for r in doc_rows)
    total_docs = len(doc_rows)
    total_chapters = len(chapters)
    total_kn = len(kn_rows)
    reviewed_kn = sum(1 for r in kn_rows if r["review_status"] in ("approved", "rejected"))

    # Distinct source docs in knowledge
    sourced_docs = len({r["source_doc_id"] for r in kn_rows if r["source_doc_id"]})

    # Chapters that have at least _RESEARCH_MIN_ITEMS knowledge items via source_doc_id
    kn_by_doc: dict[str, int] = {}
    for r in kn_rows:
        sid = r["source_doc_id"]
        if sid:
            kn_by_doc[sid] = kn_by_doc.get(sid, 0) + 1
    chapters_with_research = sum(
        1 for ch in chapters if kn_by_doc.get(ch["source_doc_id"], 0) >= _RESEARCH_MIN_ITEMS
    )

    # ── Dimension calculations ───────────────────────────────────────────────

    # 1 — Structural
    expected_ch = max(total_chapters, _EXPECTED_CHAPTERS_DEFAULT)
    struct_score = min(100, round(total_chapters / expected_ch * 100))
    structural = Dimension(
        name="structural",
        label="Structure",
        score=struct_score,
        current=total_chapters,
        target=expected_ch,
        unit="chapters",
        rule=f"chapters extracted ÷ expected ({expected_ch}) × 100",
        evidence=[f"{total_chapters} chapter(s) found across {total_docs} document(s)"],
    )

    # 2 — Content
    content_score = min(100, round(total_words / _CONTENT_BASELINE_WORDS * 100))
    content = Dimension(
        name="content",
        label="Content",
        score=content_score,
        current=total_words,
        target=_CONTENT_BASELINE_WORDS,
        unit="words",
        rule=f"total words ÷ {_CONTENT_BASELINE_WORDS:,} baseline × 100",
        evidence=[f"{total_words:,} words across {total_docs} document(s)"],
    )

    # 3 — Research coverage
    research_score = min(100, round(chapters_with_research / max(total_chapters, 1) * 100)) if total_chapters else 0
    research = Dimension(
        name="research",
        label="Research",
        score=research_score,
        current=chapters_with_research,
        target=max(total_chapters, 1),
        unit="chapters covered",
        rule=f"chapters with ≥{_RESEARCH_MIN_ITEMS} knowledge items ÷ total chapters × 100",
        evidence=[
            f"{chapters_with_research} of {total_chapters} chapter(s) have "
            f"≥{_RESEARCH_MIN_ITEMS} knowledge items",
        ],
    )

    # 4 — Editorial review
    editorial_score = min(100, round(reviewed_kn / max(total_kn, 1) * 100)) if total_kn else 0
    editorial = Dimension(
        name="editorial",
        label="Editorial",
        score=editorial_score,
        current=reviewed_kn,
        target=total_kn,
        unit="items reviewed",
        rule="reviewed knowledge items ÷ total knowledge items × 100",
        evidence=[f"{reviewed_kn} of {total_kn} knowledge item(s) reviewed"],
    )

    # 5 — Source diversity
    source_score = min(100, round(sourced_docs / max(total_docs, 1) * 100)) if total_docs else 0
    source = Dimension(
        name="source",
        label="Sources",
        score=source_score,
        current=sourced_docs,
        target=total_docs,
        unit="source docs",
        rule="distinct source documents cited in knowledge ÷ total documents × 100",
        evidence=[f"{sourced_docs} document(s) cited in knowledge items"],
    )

    # ── Overall weighted score ───────────────────────────────────────────────

    dims = [structural, content, research, editorial, source]
    dim_map = {d.name: d for d in dims}
    overall = round(sum(_WEIGHTS[d.name] * d.score for d in dims))

    readiness = (
        "Ready"         if overall >= 80 else
        "Near-Complete" if overall >= 60 else
        "Substantial"   if overall >= 40 else
        "Developing"    if overall >= 20 else
        "Draft"
    )

    summary = (
        f"{work_title} is at '{readiness}' stage ({overall}% overall). "
        f"{structural.current} chapters, {content.current:,} words, "
        f"{research.current} chapters with research coverage."
    )

    return CompletenessReport(
        work_id=work_id,
        work_title=work_title,
        dimensions=dims,
        overall=overall,
        readiness=readiness,
        summary=summary,
        evaluated_at=datetime.datetime.utcnow().isoformat() + "Z",
    )
