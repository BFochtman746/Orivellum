"""Honest completeness reporting for Works (THE RE-PROJECTION Phases 7-8).

The old report multiplied assumed denominators (10 chapters, 50,000 words)
into percentage bars and a weighted "overall" score — a number that looked
like measurement but was a guess.  This module refuses to guess:

* **Predicates** — facts that are true or false, never a percentage:
  chapter structure ratified (GENESIS G8), canonical manuscript designated
  by the author.
* **Counts** — plain observed numbers with observed denominators:
  open critical findings, knowledge items reviewed of total.
* **Progress** — raw word/chapter counts.  A target appears ONLY when the
  author set one on the Work (``meta.completeness_targets``); otherwise the
  target is absent and no ratio is computed.
* **Coverage** — where a genuine coverage figure is wanted, the Chao1 /
  Good–Turing estimator (``coverage_estimate``) supplies an honest upper
  bound with its own framing.

No overall score, no readiness label, no default denominator anywhere.
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)


def _read_targets(work: dict) -> tuple[int | None, int | None]:
    """Return the author-set (word_target, chapter_target) or (None, None).

    Only ``work.meta.completeness_targets`` counts.  There is NO default —
    a Work without an author-set target has no denominator, and the report
    shows raw counts instead of a ratio.
    """
    meta = work.get("meta") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    targets = (meta.get("completeness_targets") or {}) if isinstance(meta, dict) else {}

    def _pos_int(value) -> int | None:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None

    return _pos_int(targets.get("word_target")), _pos_int(targets.get("chapter_target"))


def calculate_work_completeness(work_id: str, db: OrivellumDB) -> dict:
    """Build the honest completeness report for a Work.

    Returns a dict (the route serialises it verbatim):
      work_id, work_title, predicates[], counts[], progress{}, coverage{},
      evaluated_at.
    """
    from orivellum.capabilities.coverage_estimate import estimate_coverage
    from orivellum.capabilities.readiness import (
        author_canonical_manuscript,
        chapter_structure_ratified,
        manuscript_document_count,
    )

    work = db.get_work(work_id) or {}
    work_title = work.get("title") or work_id[:8]
    word_target, chapter_target = _read_targets(work)

    # ── Observed raw data ────────────────────────────────────────────────────
    with db._lock:
        doc_rows = db._conn.execute(
            "SELECT id, word_count FROM documents WHERE work_id=? AND readiness='ready'",
            (work_id,),
        ).fetchall()

        doc_ids = [r["id"] for r in doc_rows]
        total_chapters = 0
        if doc_ids:
            placeholders = ",".join("?" * len(doc_ids))
            row = db._conn.execute(
                f"SELECT COUNT(*) AS n FROM book_chapters bc "
                f"WHERE bc.source_doc_id IN ({placeholders})",
                doc_ids,
            ).fetchone()
            total_chapters = int(row["n"]) if row else 0

        kn_row = db._conn.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN review_status IN ('approved','rejected')
                          THEN 1 ELSE 0 END) AS reviewed
               FROM knowledge WHERE work_id=?
                 AND review_status != 'quarantined_reprojection'""",
            (work_id,),
        ).fetchone()

    total_words = sum(r["word_count"] or 0 for r in doc_rows)
    total_docs = len(doc_rows)
    total_kn = int(kn_row["total"] or 0)
    reviewed_kn = int(kn_row["reviewed"] or 0)

    # Open critical/high findings on the Work and (if any) its pipeline —
    # these are the blockers a reader actually needs to know about.
    finding_ids = [work_id]
    pipeline = db.get_book_pipeline_for_work(work_id)
    if pipeline:
        finding_ids.append(pipeline["id"])
    open_critical = 0
    for oid in finding_ids:
        open_critical += len(
            db.list_findings(
                object_id=oid, state="open", min_severity=("high", "critical"), limit=100
            )
        )

    # ── Predicates (true/false facts, never percentages) ─────────────────────
    ratified = chapter_structure_ratified(db, work_id)
    canonical = author_canonical_manuscript(db, work_id)
    ms_count = manuscript_document_count(db, work_id)

    predicates = [
        {
            "name": "manuscript_document",
            "label": "Manuscript document present",
            "value": ms_count > 0,
            "detail": f"{ms_count} manuscript document(s) in this Work.",
        },
        {
            "name": "chapter_structure_ratified",
            "label": "Chapter structure ratified",
            "value": ratified,
            "detail": (
                "GENESIS Chapter Blueprint gate (G8) is signed PASSED."
                if ratified
                else "GENESIS Chapter Blueprint gate (G8) has not been passed — "
                "extracted chapters alone are not ratification."
            ),
        },
        {
            "name": "canonical_by_author",
            "label": "Canonical version set by author",
            "value": canonical,
            "detail": (
                "An author-designated canonical manuscript exists."
                if canonical
                else "No manuscript has been designated canonical by the author."
            ),
        },
    ]

    counts = [
        {
            "name": "open_critical_findings",
            "label": "Open critical findings",
            "value": open_critical,
            "detail": (
                "No open high/critical findings."
                if open_critical == 0
                else f"{open_critical} open high/critical finding(s) on this Work and its pipeline."
            ),
        },
        {
            "name": "knowledge_reviewed",
            "label": "Knowledge reviewed",
            "current": reviewed_kn,
            "total": total_kn,
            "detail": f"{reviewed_kn} of {total_kn} knowledge item(s) reviewed.",
        },
    ]

    # ── Progress: raw counts; targets only when the author set them ──────────
    progress = {
        "words": total_words,
        "word_target": word_target,  # None when the author has not set one
        "chapters": total_chapters,
        "chapter_target": chapter_target,  # None when the author has not set one
        "documents": total_docs,
        "note": (
            None
            if (word_target or chapter_target)
            else "No author-set targets — raw counts only. Set targets on the Work "
            "to see progress against them."
        ),
    }

    return {
        "work_id": work_id,
        "work_title": work_title,
        "predicates": predicates,
        "counts": counts,
        "progress": progress,
        "coverage": estimate_coverage(db, work_id),
        "evaluated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
