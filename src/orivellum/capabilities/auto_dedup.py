"""Automatic near-duplicate resolution.

When ``auto_dedup_enabled`` is ``true`` in the settings table this module
applies rule-based resolution to unresolved ``doc_dupes`` rows so the user
never has to touch a Review-Queue item just to tidy up obvious duplicates.

Resolution rules (applied in order):
  1. Lifecycle priority wins.  ``canonical > reference > draft > superseded``.
     A document the user already set to ``canonical`` is never auto-superseded.
  2. Newer ``created_at`` wins when lifecycle priority is equal.
  3. Higher ``word_count`` wins when both lifecycle and date are equal.
  4. If all tie the pair is left for the human queue.

Action mapping by similarity tier:
  * near_duplicate  (≥ 0.85) → ``mark_superseded``   (loser archived)
  * likely_revision (0.60–0.84) → ``mark_versions``  (DERIVED_FROM chain)

Safety guards:
  * Both docs canonical → skip (human decision required).
  * Either doc has ``lifecycle == 'deleted'`` → skip.
  * Configurable cap: ``auto_dedup_max_pairs`` (default 50/run).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# Lifecycle ordering — higher number = higher survivability.
_LIFECYCLE_PRIORITY: dict[str, int] = {
    "canonical": 4,
    "reference":  3,
    "draft":      2,
    "active":     2,   # legacy alias
    "superseded": 1,
    "deleted":    0,
}

_DEFAULT_MAX_PAIRS = 50


def _pick_canonical(doc_a: dict, doc_b: dict) -> str | None:
    """Return the doc_id that should survive as canonical, or None to skip.

    Returns None when the pair should be left for the human queue (e.g. both
    already canonical, either deleted, or all fields are exactly tied).
    """
    lc_a = doc_a.get("lifecycle") or "draft"
    lc_b = doc_b.get("lifecycle") or "draft"

    # Never touch deleted documents.
    if lc_a == "deleted" or lc_b == "deleted":
        return None

    # Both canonical → human call needed.
    if lc_a == "canonical" and lc_b == "canonical":
        return None

    pri_a = _LIFECYCLE_PRIORITY.get(lc_a, 1)
    pri_b = _LIFECYCLE_PRIORITY.get(lc_b, 1)

    if pri_a != pri_b:
        return doc_a["id"] if pri_a > pri_b else doc_b["id"]

    # Equal lifecycle priority → prefer newer.
    ts_a = doc_a.get("created_at") or ""
    ts_b = doc_b.get("created_at") or ""
    if ts_a != ts_b:
        return doc_a["id"] if ts_a > ts_b else doc_b["id"]

    # Equal date → prefer richer (more words).
    wc_a = doc_a.get("word_count") or 0
    wc_b = doc_b.get("word_count") or 0
    if wc_a != wc_b:
        return doc_a["id"] if wc_a > wc_b else doc_b["id"]

    # Truly tied — leave for human.
    return None


def auto_resolve_duplicates(
    db: "OrivellumDB",
    max_pairs: int | None = None,
) -> dict:
    """Process unresolved ``doc_dupes`` rows and apply automatic resolution.

    Parameters
    ----------
    db:
        Live database handle.
    max_pairs:
        Maximum number of pairs to process in one call.  Reads
        ``auto_dedup_max_pairs`` from settings when *None* (default 50).

    Returns
    -------
    dict
        Summary: ``{processed, superseded, versioned, skipped, errors}``.
    """
    if max_pairs is None:
        try:
            max_pairs = int(db.get_setting("auto_dedup_max_pairs", str(_DEFAULT_MAX_PAIRS)))
        except (TypeError, ValueError):
            max_pairs = _DEFAULT_MAX_PAIRS

    # Fetch the oldest unresolved pairs first so the queue drains in stable order.
    try:
        with db._lock:
            rows = db._conn.execute(
                """SELECT dd.id, dd.doc_a_id, dd.doc_b_id, dd.similarity, dd.kind
                   FROM doc_dupes dd
                   WHERE dd.resolved = 0
                   ORDER BY dd.created_at ASC
                   LIMIT ?""",
                (max_pairs,),
            ).fetchall()
    except Exception as exc:
        logger.warning("auto_dedup: could not query doc_dupes: %s", exc)
        return {"processed": 0, "superseded": 0, "versioned": 0, "skipped": 0, "errors": 1}

    counters = {"processed": 0, "superseded": 0, "versioned": 0, "skipped": 0, "errors": 0}

    for row in rows:
        dupe_id   = row[0]
        doc_a_id  = row[1]
        doc_b_id  = row[2]
        kind      = row[4]  # near_duplicate | likely_revision

        counters["processed"] += 1
        try:
            doc_a = db.get_document(doc_a_id)
            doc_b = db.get_document(doc_b_id)
            if not doc_a or not doc_b:
                logger.debug("auto_dedup: skipping %s — one doc missing", dupe_id)
                counters["skipped"] += 1
                continue

            # Decide action based on similarity tier.
            if kind == "near_duplicate":
                canonical_id = _pick_canonical(doc_a, doc_b)
                if canonical_id is None:
                    logger.debug("auto_dedup: skipping %s — cannot auto-pick canonical", dupe_id)
                    counters["skipped"] += 1
                    continue
                result = db.resolve_near_duplicate(
                    dupe_id, "mark_superseded",
                    canonical_doc_id=canonical_id,
                    actor="system",
                )
                if result and not result.get("already_resolved"):
                    superseded_id = doc_b_id if canonical_id == doc_a_id else doc_a_id
                    logger.info(
                        "auto_dedup: superseded doc %s (kept %s) — similarity %.2f",
                        superseded_id[:8], canonical_id[:8], row[3],
                    )
                    counters["superseded"] += 1
                else:
                    counters["skipped"] += 1

            elif kind == "likely_revision":
                result = db.resolve_near_duplicate(dupe_id, "mark_versions", actor="system")
                if result and not result.get("already_resolved"):
                    logger.info(
                        "auto_dedup: version-linked docs %s ↔ %s — similarity %.2f",
                        doc_a_id[:8], doc_b_id[:8], row[3],
                    )
                    counters["versioned"] += 1
                else:
                    counters["skipped"] += 1

            else:
                counters["skipped"] += 1

        except Exception as exc:
            logger.warning("auto_dedup: error processing pair %s: %s", dupe_id, exc)
            counters["errors"] += 1

    return counters


def auto_resolve_import_hits(
    new_doc_id: str,
    hits: list[tuple[str, float, str]],
    db: "OrivellumDB",
) -> dict:
    """Immediately resolve near-duplicate hits found during document import.

    Called from the pipeline (step 4.6) when ``auto_dedup_enabled=true``.
    *hits* is the list returned by ``find_and_record_near_duplicates``:
    ``[(other_doc_id, similarity, kind), ...]``.

    Only processes pairs that were just inserted — i.e., the ``doc_dupes``
    row for (new_doc_id, other_doc_id) must exist and be unresolved.
    """
    counters = {"superseded": 0, "versioned": 0, "skipped": 0, "errors": 0}

    for (other_id, similarity, kind) in hits:
        try:
            # Look up the dupe row (either ordering).
            with db._lock:
                row = db._conn.execute(
                    """SELECT id FROM doc_dupes
                       WHERE resolved=0
                         AND ((doc_a_id=? AND doc_b_id=?) OR (doc_a_id=? AND doc_b_id=?))
                       LIMIT 1""",
                    (new_doc_id, other_id, other_id, new_doc_id),
                ).fetchone()

            if not row:
                counters["skipped"] += 1
                continue

            dupe_id = row[0]

            if kind == "near_duplicate":
                new_doc  = db.get_document(new_doc_id)
                other_doc = db.get_document(other_id)
                if not new_doc or not other_doc:
                    counters["skipped"] += 1
                    continue
                canonical_id = _pick_canonical(new_doc, other_doc)
                if canonical_id is None:
                    counters["skipped"] += 1
                    continue
                result = db.resolve_near_duplicate(
                    dupe_id, "mark_superseded",
                    canonical_doc_id=canonical_id,
                    actor="system",
                )
                if result and not result.get("already_resolved"):
                    counters["superseded"] += 1
                else:
                    counters["skipped"] += 1

            elif kind == "likely_revision":
                result = db.resolve_near_duplicate(dupe_id, "mark_versions", actor="system")
                if result and not result.get("already_resolved"):
                    counters["versioned"] += 1
                else:
                    counters["skipped"] += 1
            else:
                counters["skipped"] += 1

        except Exception as exc:
            logger.warning("auto_dedup import: error for pair (%s, %s): %s",
                           new_doc_id[:8], other_id[:8], exc)
            counters["errors"] += 1

    return counters
