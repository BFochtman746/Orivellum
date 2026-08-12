"""Promotion-readiness predicates for Works (THE RE-PROJECTION Phases 7-8).

Promote-to-Book is a gated act.  A Work is eligible only when:

  1. It has at least one ``manuscript`` document — a Work of research notes
     is not a book.
  2. Its chapter structure is ratified — the GENESIS Chapter Blueprint gate
     (G8) has been signed PASSED for the Work's book.
  3. The author has designated a canonical manuscript version — lifecycle
     ``canonical`` with ``lifecycle_by='author'``; a system-picked survivor
     does not count.

Every refusal names the specific unmet predicate.  These helpers are shared
by the promote route, the Books UI (via the eligibility endpoint), and the
honest-completeness report so all three surfaces tell the same story.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

# The GENESIS stage whose PASSED status ratifies the chapter structure.
_CHAPTER_BLUEPRINT_STAGE = "G8"


def manuscript_document_count(db: OrivellumDB, work_id: str) -> int:
    """Count non-deleted documents of doc_type='manuscript' in this Work."""
    row = (
        db.read_conn()
        .execute(
            """SELECT COUNT(*) AS n
               FROM documents d
               LEFT JOIN objects o ON o.id = d.id
               WHERE d.work_id = ?
                 AND d.doc_type = 'manuscript'
                 AND COALESCE(o.lifecycle, 'draft') != 'deleted'""",
            (work_id,),
        )
        .fetchone()
    )
    return int(row["n"]) if row else 0


def chapter_structure_ratified(db: OrivellumDB, work_id: str) -> bool:
    """True when the Work's GENESIS Chapter Blueprint gate (G8) is PASSED.

    The G8 gate is the only ratification machinery for chapter structure —
    a signed, ledger-chained author decision.  Extracted chapters existing
    in the database is NOT ratification.
    """
    row = (
        db.read_conn()
        .execute(
            """SELECT 1
               FROM genesis_books gb
               JOIN genesis_stages gs ON gs.book_id = gb.id
               WHERE gb.work_id = ?
                 AND gs.stage_code = ?
                 AND gs.status = 'PASSED'
               LIMIT 1""",
            (work_id, _CHAPTER_BLUEPRINT_STAGE),
        )
        .fetchone()
    )
    return row is not None


def author_canonical_manuscript(db: OrivellumDB, work_id: str) -> bool:
    """True when an author-designated canonical manuscript version exists.

    Requires lifecycle='canonical' AND lifecycle_by='author' on a
    doc_type='manuscript' document.  Legacy canonical rows (lifecycle_by
    NULL) and system-picked survivors do not count — unknown provenance is
    not author provenance.
    """
    row = (
        db.read_conn()
        .execute(
            """SELECT 1
               FROM documents d
               JOIN objects o ON o.id = d.id
               WHERE d.work_id = ?
                 AND d.doc_type = 'manuscript'
                 AND o.lifecycle = 'canonical'
                 AND d.lifecycle_by = 'author'
               LIMIT 1""",
            (work_id,),
        )
        .fetchone()
    )
    return row is not None


def promotion_eligibility(db: OrivellumDB, work_id: str) -> dict:
    """Evaluate all promote-to-Book predicates for a Work.

    Returns ``{"eligible": bool, "checks": [...], "reasons": [...]}`` where
    ``reasons`` lists the specific unmet requirement for every failed check —
    a refusal always says WHY.
    """
    ms_count = manuscript_document_count(db, work_id)
    ratified = chapter_structure_ratified(db, work_id)
    canonical = author_canonical_manuscript(db, work_id)

    checks = [
        {
            "rule": "manuscript_document",
            "label": "At least one manuscript document",
            "ok": ms_count > 0,
            "reason": (
                None
                if ms_count > 0
                else "No manuscript document — classify a document as doc_type "
                "'manuscript' (a Work of research notes is not a book)."
            ),
        },
        {
            "rule": "chapter_structure_ratified",
            "label": "Chapter structure ratified",
            "ok": ratified,
            "reason": (
                None
                if ratified
                else "Chapter structure not ratified — pass the GENESIS Chapter "
                "Blueprint gate (G8) for this Work."
            ),
        },
        {
            "rule": "canonical_by_author",
            "label": "Canonical version designated by the author",
            "ok": canonical,
            "reason": (
                None
                if canonical
                else "No author-designated canonical manuscript — set a manuscript "
                "document's lifecycle to 'canonical' yourself (a system-picked "
                "survivor does not count)."
            ),
        },
    ]
    reasons = [c["reason"] for c in checks if not c["ok"]]
    return {"eligible": not reasons, "checks": checks, "reasons": reasons}
