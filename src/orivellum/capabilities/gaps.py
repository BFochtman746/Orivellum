"""Research gap detection for Works.

Identifies what knowledge is missing from a Work so users know what to
research next.  Operates entirely on existing data — no LLM call needed
for the basic analysis.  LLM can be invoked optionally for richer
gap descriptions, but the core function is always fast.

Gap categories:
  uncovered_chapters  — chapters that have no knowledge items
  undocumented_docs   — documents with no chapters extracted
  weak_coverage       — chapters with fewer than MIN_ITEMS items
  missing_sources     — knowledge items that cite no source document
  suggested_queries   — simple keyword suggestions to fill gaps (rule-based)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

_MIN_ITEMS_PER_CHAPTER = 3   # below this is "weak coverage"


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


def detect_gaps(work_id: str, db: "OrivellumDB") -> GapReport:
    """Analyse a Work's chapters and knowledge items to surface research gaps."""
    import datetime

    with db._lock:
        # Documents linked to this work
        doc_rows = db._conn.execute(
            "SELECT id, title FROM documents WHERE work_id=? AND readiness='ready'",
            (work_id,),
        ).fetchall()

        doc_ids = [r["id"] for r in doc_rows]
        doc_titles = {r["id"]: r["title"] for r in doc_rows}

        chapters: list[dict] = []
        if doc_ids:
            placeholders = ",".join("?" * len(doc_ids))
            chapter_rows = db._conn.execute(
                f"""SELECT id, seq, title, source_doc_id FROM book_chapters
                    WHERE source_doc_id IN ({placeholders}) ORDER BY seq""",
                doc_ids,
            ).fetchall()
            chapters = [dict(r) for r in chapter_rows]

        # Knowledge items per document (source_doc_id)
        kn_rows = db._conn.execute(
            "SELECT source_doc_id, count(*) as cnt FROM knowledge WHERE work_id=? GROUP BY source_doc_id",
            (work_id,),
        ).fetchall()

    kn_by_doc: dict[str, int] = {r["source_doc_id"]: r["cnt"] for r in kn_rows if r["source_doc_id"]}
    docs_with_chapters: set[str] = {ch["source_doc_id"] for ch in chapters}

    gaps: list[Gap] = []

    # Gap 1: documents with no chapters extracted
    for doc_id in doc_ids:
        if doc_id not in docs_with_chapters:
            title = doc_titles.get(doc_id, doc_id[:8])
            gaps.append(Gap(
                kind="undocumented_doc",
                title=f'No structure detected in "{title}"',
                description=(
                    f'The document "{title}" has been extracted but no chapter or '
                    "section headings were found.  Consider adding headings to improve "
                    "structure, or check if the extraction captured the right text."
                ),
                severity="medium",
                metadata={"doc_id": doc_id, "doc_title": title},
            ))

    # Gap 2: chapters with no or weak knowledge-item coverage
    uncovered = 0
    weak = 0
    for ch in chapters:
        doc_id = ch["source_doc_id"]
        kn_count = kn_by_doc.get(doc_id, 0)
        if kn_count == 0:
            uncovered += 1
            gaps.append(Gap(
                kind="uncovered_chapter",
                title=f"No research for \"{ch['title']}\"",
                description=(
                    f"The chapter \"{ch['title']}\" has no knowledge items linked to "
                    "its source document.  Run extraction or add notes to fill this gap."
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
                    f"Aim for at least {_MIN_ITEMS_PER_CHAPTER} to consider it covered."
                ),
                severity="low",
                metadata={"chapter_title": ch["title"], "kn_count": kn_count, "doc_id": doc_id},
            ))

    # Compute coverage %
    total = len(chapters)
    covered = total - uncovered - weak if total > 0 else 0
    coverage_pct = round(covered / total * 100) if total > 0 else 0

    # Gap 3: if there are zero chapters at all
    if total == 0 and doc_ids:
        gaps.append(Gap(
            kind="no_structure",
            title="No chapter structure found in this Work",
            description=(
                "None of the documents in this Work have extractable section headings.  "
                "Re-extract documents or upload files with clear headings (DOCX, Markdown) "
                "to enable completeness scoring and gap analysis."
            ),
            severity="high",
            metadata={},
        ))

    # Suggested search queries — simple rule-based extraction from chapter titles
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
        evaluated_at=datetime.datetime.utcnow().isoformat() + "Z",
    )
