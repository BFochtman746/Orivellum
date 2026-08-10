"""Research gap detection for Works.

Identifies what knowledge is missing from a Work so users know what to
research next.  Operates entirely on existing data — no LLM call needed
for the basic analysis.

Gap categories (ranked high → low within each type):
  undocumented_doc    — document with no chapter/section structure extracted
  uncovered_chapter   — chapter heading with 0 knowledge items in its source doc
  weak_coverage       — chapter with fewer than MIN_ITEMS knowledge items
  missing_sources     — knowledge items with no source document (no citation)
  orphaned_research   — knowledge items whose source doc is no longer in this work
  stale_source        — documents older than one year
  duplicate_research  — near-duplicate knowledge-item pairs (Jaccard ≥ 0.8)
  no_structure        — no chapter structure at all when docs do exist
"""
from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

_MIN_ITEMS_PER_CHAPTER = 3    # below this is "weak coverage"
_DUPLICATE_THRESHOLD   = 0.80  # Jaccard similarity threshold
_DUP_SAMPLE_LIMIT      = 150   # max knowledge items to compare pairwise
_DUP_PAIR_CAP          = 5     # stop after finding this many duplicate pairs
_STALE_DAYS            = 365   # days before a source is considered stale


@dataclass
class Gap:
    kind: str
    title: str
    description: str
    severity: str  # "high" | "medium" | "low"
    metadata: dict = field(default_factory=dict)


@dataclass
class GapReport:
    work_id: str
    gaps: list[Gap]
    suggested_queries: list[str]
    coverage_pct: int    # 0-100 — chapters with sufficient coverage
    total_chapters: int
    evaluated_at: str


def _jaccard(a: str, b: str) -> float:
    """Word-set Jaccard similarity between two strings."""
    wa, wb = set(a.lower().split()), set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def detect_gaps(work_id: str, db: OrivellumDB) -> GapReport:
    """Analyse a Work's chapters and knowledge items to surface research gaps."""

    # ── Gather all data in one lock acquisition ────────────────────────────────
    with db._lock:
        doc_rows = db._conn.execute(
            "SELECT id, title, created_at FROM documents "
            "WHERE work_id=? AND readiness='ready'",
            (work_id,),
        ).fetchall()

        doc_ids    = [r["id"] for r in doc_rows]
        doc_titles = {r["id"]: r["title"] for r in doc_rows}

        chapters: list[dict] = []
        if doc_ids:
            placeholders = ",".join("?" * len(doc_ids))
            chapter_rows = db._conn.execute(
                f"SELECT id, seq, title, source_doc_id FROM book_chapters "
                f"WHERE source_doc_id IN ({placeholders}) ORDER BY seq",
                doc_ids,
            ).fetchall()
            chapters = [dict(r) for r in chapter_rows]

        # Knowledge counts per source document
        kn_rows = db._conn.execute(
            "SELECT source_doc_id, COUNT(*) AS cnt FROM knowledge "
            "WHERE work_id=? GROUP BY source_doc_id",
            (work_id,),
        ).fetchall()

        # Missing sources — knowledge items with no source doc
        n_missing_src = db._conn.execute(
            "SELECT COUNT(*) AS n FROM knowledge "
            "WHERE work_id=? AND source_doc_id IS NULL",
            (work_id,),
        ).fetchone()["n"]

        # Orphaned research — knowledge items from docs not in this work
        if doc_ids:
            n_orphaned = db._conn.execute(
                "SELECT COUNT(*) AS n FROM knowledge k "
                "WHERE k.work_id=? "
                "  AND k.source_doc_id IS NOT NULL "
                "  AND k.source_doc_id NOT IN ({})".format(
                    ",".join("?" * len(doc_ids))
                ),
                (work_id, *doc_ids),
            ).fetchone()["n"]
        else:
            # No docs at all — every knowledge item with a source_doc_id is orphaned
            n_orphaned = db._conn.execute(
                "SELECT COUNT(*) AS n FROM knowledge k "
                "WHERE k.work_id=? AND k.source_doc_id IS NOT NULL",
                (work_id,),
            ).fetchone()["n"]

        # Sample for duplicate detection (outside-lock computation below)
        kn_sample = db._conn.execute(
            "SELECT id, text FROM knowledge "
            "WHERE work_id=? AND LENGTH(text) > 20 LIMIT ?",
            (work_id, _DUP_SAMPLE_LIMIT),
        ).fetchall()

    # ── Derived values (computed outside lock) ────────────────────────────────

    kn_by_doc: dict[str, int] = {
        r["source_doc_id"]: r["cnt"] for r in kn_rows if r["source_doc_id"]
    }
    docs_with_chapters: set[str] = {ch["source_doc_id"] for ch in chapters}

    # Stale-source check
    cutoff_date = (
        datetime.datetime.now(datetime.UTC)
        - datetime.timedelta(days=_STALE_DAYS)
    ).isoformat()[:10]
    stale_docs = [
        r for r in doc_rows
        if r["created_at"] and str(r["created_at"])[:10] < cutoff_date
    ]

    # Duplicate-research detection (pairwise Jaccard on text sample)
    dup_pairs = 0
    kn_texts = [(r["id"], r["text"]) for r in kn_sample]
    outer_done = False
    for i in range(len(kn_texts)):
        if outer_done:
            break
        for j in range(i + 1, len(kn_texts)):
            if _jaccard(kn_texts[i][1], kn_texts[j][1]) >= _DUPLICATE_THRESHOLD:
                dup_pairs += 1
                if dup_pairs >= _DUP_PAIR_CAP:
                    outer_done = True
                    break

    # ── Build gap list ────────────────────────────────────────────────────────

    gaps: list[Gap] = []

    # 1. Documents with no chapter structure
    for doc_id in doc_ids:
        if doc_id not in docs_with_chapters:
            title = doc_titles.get(doc_id, doc_id[:8])
            gaps.append(Gap(
                kind="undocumented_doc",
                title=f'No structure detected in "{title}"',
                description=(
                    f'The document "{title}" has been extracted but no chapter or '
                    "section headings were found. Consider adding headings to improve "
                    "structure, or check if the extraction captured the right text."
                ),
                severity="medium",
                metadata={"doc_id": doc_id, "doc_title": title},
            ))

    # 2. Chapters with zero or thin knowledge coverage
    uncovered = 0
    weak = 0
    for ch in chapters:
        doc_id   = ch["source_doc_id"]
        kn_count = kn_by_doc.get(doc_id, 0)
        if kn_count == 0:
            uncovered += 1
            gaps.append(Gap(
                kind="uncovered_chapter",
                title=f"No research for \"{ch['title']}\"",
                description=(
                    f"The chapter \"{ch['title']}\" has no knowledge items linked to "
                    "its source document. Run extraction or add notes to fill this gap."
                ),
                severity="high",
                metadata={"chapter_title": ch["title"], "doc_id": doc_id},
            ))
        elif kn_count < _MIN_ITEMS_PER_CHAPTER:
            weak += 1
            gaps.append(Gap(
                kind="weak_coverage",
                title=f"Thin coverage for \"{ch['title']}\"",
                description=(
                    f"Only {kn_count} knowledge item(s) for \"{ch['title']}\". "
                    f"Aim for at least {_MIN_ITEMS_PER_CHAPTER} to consider it well covered."
                ),
                severity="low",
                metadata={"chapter_title": ch["title"], "kn_count": kn_count, "doc_id": doc_id},
            ))

    # 3. No chapter structure at all when docs exist
    total = len(chapters)
    if total == 0 and doc_ids:
        gaps.append(Gap(
            kind="no_structure",
            title="No chapter structure found in this Work",
            description=(
                "None of the documents in this Work have extractable section headings. "
                "Re-extract documents or upload files with clear headings (DOCX, Markdown) "
                "to enable gap analysis and completeness scoring."
            ),
            severity="high",
            metadata={},
        ))

    # 4. Knowledge items with no source document (missing citation)
    if n_missing_src > 0:
        s  = "s" if n_missing_src != 1 else ""
        are = "are" if n_missing_src > 1 else "is"
        gaps.append(Gap(
            kind="missing_sources",
            title=f"{n_missing_src} knowledge item{s} without a source document",
            description=(
                f"{n_missing_src} knowledge item{s} {are} not linked to any source document. "
                "These may be AI-generated facts with no citation. Review them, link to "
                "source documents, or remove unsupported claims."
            ),
            severity="medium",
            metadata={"count": n_missing_src},
        ))

    # 5. Orphaned knowledge items (source doc no longer in this work)
    if n_orphaned > 0:
        s   = "s" if n_orphaned != 1 else ""
        ref = "reference" if n_orphaned == 1 else "reference"
        gaps.append(Gap(
            kind="orphaned_research",
            title=f"{n_orphaned} knowledge item{s} from unlinked documents",
            description=(
                f"{n_orphaned} knowledge item{s} {ref} a source document no longer linked "
                "to this Work. They may be stale. Consider relinking the source documents "
                "or removing the orphaned items."
            ),
            severity="low",
            metadata={"count": n_orphaned},
        ))

    # 6. Stale source documents (older than _STALE_DAYS days)
    if stale_docs:
        n = len(stale_docs)
        s   = "s" if n != 1 else ""
        were = "were" if n > 1 else "was"
        gaps.append(Gap(
            kind="stale_source",
            title=f"{n} source document{s} older than one year",
            description=(
                f"{n} document{s} {were} imported more than a year ago. "
                "Consider checking for newer editions, updated research, or "
                "more recent primary sources."
            ),
            severity="low",
            metadata={"count": n, "doc_ids": [r["id"] for r in stale_docs[:5]]},
        ))

    # 7. Near-duplicate knowledge items
    if dup_pairs > 0:
        s = "s" if dup_pairs != 1 else ""
        gaps.append(Gap(
            kind="duplicate_research",
            title="Near-duplicate knowledge items detected",
            description=(
                f"Found {dup_pairs} pair{s} of knowledge items with highly similar text. "
                "Duplicates inflate research metrics and reduce clarity. "
                "Review and merge or remove redundant items."
            ),
            severity="medium" if dup_pairs > 2 else "low",
            metadata={"duplicate_pairs": dup_pairs},
        ))

    # ── Coverage % and query suggestions ──────────────────────────────────────

    covered     = max(0, total - uncovered - weak)
    coverage_pct = round(covered / total * 100) if total > 0 else 0

    suggestions: list[str] = []
    for ch in chapters:
        t = ch["title"].strip()
        if t and kn_by_doc.get(ch["source_doc_id"], 0) < _MIN_ITEMS_PER_CHAPTER:
            suggestions.append(f"research on {t}")
        if len(suggestions) >= 8:
            break

    return GapReport(
        work_id=work_id,
        gaps=gaps,
        suggested_queries=suggestions[:8],
        coverage_pct=coverage_pct,
        total_chapters=total,
        evaluated_at=datetime.datetime.now(datetime.UTC).isoformat(),
    )
