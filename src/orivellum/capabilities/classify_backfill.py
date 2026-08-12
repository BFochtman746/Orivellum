"""Classification backfill — THE RE-PROJECTION Phase 3.

Walks every existing document and gives it the two classification
dimensions that new imports get at creation:

* ``collection_id`` — docs with no collection are assigned to a single
  "Manual imports" collection (provenance assignment, not interpretation,
  so it applies directly).
* ``doc_type`` — deterministic rules apply directly with provenance
  ``rule:<name>``.  Ambiguous residue stays ``unknown`` (which refuses
  harvest) and may receive a MODEL PROPOSAL routed through the review
  queue — the model never applies a classification.
* ``tier`` — NEVER mutated directly here.  Where the deterministic tier
  classifier disagrees with the stored tier, a ``pending_reclassify``
  proposal is created for ratification in the Review Queue.

Every applied change is auditable; every interpretive change is a proposal.
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from orivellum.capabilities.classify import (
    VALID_DOC_TYPES,
    DocType,
    classify_doc_type,
    classify_object,
)

logger = logging.getLogger("orivellum.classify_backfill")


def _now() -> str:
    return datetime.now(UTC).isoformat()


MANUAL_COLLECTION_REF = "manual:legacy-backfill"
MANUAL_COLLECTION_LABEL = "Manual imports"

# Deterministic rules below this confidence are treated as residue.
_RULE_CONFIDENCE_FLOOR = 0.8

_MODEL_PROMPT = (
    "You classify a document into exactly one type. Reply with ONLY one of "
    "these words and nothing else: manuscript, reference, doctrine, "
    "test_catalog, code, workbook, correspondence, generated, unknown.\n\n"
    "manuscript = chapter prose / book drafts. reference = handbooks, "
    "lexica, commentaries. doctrine = specs, policies, engine contracts. "
    "test_catalog = test case catalogs. code = source code. workbook = "
    "spreadsheets/tabular data. correspondence = mail or chat exports. "
    "generated = machine-produced reports/exports. unknown = cannot tell."
)


def _ensure_manual_collection(db: Any) -> str:
    coll = db.find_collection_by_source_ref(MANUAL_COLLECTION_REF)
    if coll:
        return coll["id"]
    coll = db.create_collection(
        label=MANUAL_COLLECTION_LABEL,
        source_kind="manual",
        source_ref=MANUAL_COLLECTION_REF,
        meta={"origin": "classification-backfill"},
    )
    return coll["id"]


def _sample_text(row: Any) -> str | None:
    if "extracted_text" not in row.keys():  # noqa: SIM118 — sqlite3.Row, not a dict
        return None
    text = row["extracted_text"]
    return text[:20000] if text else None


def _meta_dict(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def backfill_classification(db: Any) -> dict:
    """Deterministic backfill pass.  Returns a report dict.

    Idempotent: only touches docs with NULL collection_id / NULL doc_type,
    and upserts (never duplicates) tier proposals.
    """
    report = {
        "collections_assigned": 0,
        "doc_type_applied": 0,
        "doc_type_residue": 0,
        "tier_proposals": 0,
        "by_type": {},
    }

    # 1. Collection home for orphaned manual imports.
    with db._lock:
        orphans = db._conn.execute(
            "SELECT id FROM documents WHERE collection_id IS NULL"
        ).fetchall()
    if orphans:
        manual_id = _ensure_manual_collection(db)
        with db._lock:
            db._conn.execute(
                "UPDATE documents SET collection_id=? WHERE collection_id IS NULL",
                (manual_id,),
            )
            db._conn.commit()
        db.refresh_collection_count(manual_id)
        report["collections_assigned"] = len(orphans)
        with contextlib.suppress(Exception):
            db.audit(
                "documents.collection_backfilled",
                object_id=manual_id,
                object_type="collection",
                actor="system",
                detail=f"{len(orphans)} manual import(s) assigned",
            )

    # 2. doc_type — deterministic rules, applied directly with rule provenance.
    # Re-scans rule-classified 'unknown' residue too, so improved rules pick
    # up documents an earlier run couldn't place.  Author/model-ratified
    # classifications are never touched.
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, title, source, kind, meta, doc_type, doc_type_by,
                      substr(extracted_text, 1, 20000) AS extracted_text
               FROM documents
               WHERE doc_type IS NULL
                  OR (doc_type = 'unknown' AND doc_type_by LIKE 'rule:%')"""
        ).fetchall()
    for row in rows:
        cls = classify_doc_type(
            row["title"] or "",
            kind=row["kind"],
            sample_text=_sample_text(row),
            source_path=row["source"],
            meta=_meta_dict(row["meta"]),
        )
        if cls.confidence >= _RULE_CONFIDENCE_FLOOR:
            doc_type, by = cls.doc_type.value, f"rule:{cls.rule}"
        else:
            # Residue: unknown refuses harvest until a human (or a ratified
            # model proposal) says otherwise.
            doc_type, by = DocType.UNKNOWN.value, f"rule:{cls.rule}"
        if doc_type == row["doc_type"] and by == row["doc_type_by"]:
            continue  # unchanged — keeps re-runs cheap and reports honest
        if cls.confidence >= _RULE_CONFIDENCE_FLOOR:
            report["doc_type_applied"] += 1
        else:
            report["doc_type_residue"] += 1
        with db._lock:
            db._conn.execute(
                "UPDATE documents SET doc_type=?, doc_type_by=? WHERE id=?",
                (doc_type, by, row["id"]),
            )
            db._conn.commit()
        report["by_type"][doc_type] = report["by_type"].get(doc_type, 0) + 1

    # 3. Tier disagreements → pending_reclassify PROPOSALS, never mutation.
    with db._lock:
        rows = db._conn.execute("SELECT id, title, source, kind, tier FROM documents").fetchall()
    for row in rows:
        tc = classify_object(row["title"] or "", source_path=row["source"], kind=row["kind"])
        if tc.confidence < _RULE_CONFIDENCE_FLOOR:
            continue
        if tc.tier.value == (row["tier"] or "source"):
            continue
        reason = (
            f"Tier backfill: deterministic rule says {tc.tier.value!r} "
            f"(currently {row['tier']!r}) — {tc.reason}"
        )
        with db._lock:
            # Per-field provenance/reason: a doc_type proposal already on the
            # row (from the model) must never lose its origin to a tier upsert.
            cur = db._conn.execute(
                """INSERT INTO pending_reclassify(id, doc_id, reason, created_at,
                                                  proposed_tier, proposed_tier_by)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(doc_id) DO UPDATE SET
                     reason=CASE WHEN pending_reclassify.proposed_doc_type IS NULL
                                 THEN excluded.reason
                                 ELSE pending_reclassify.reason END,
                     proposed_tier=excluded.proposed_tier,
                     proposed_tier_by=excluded.proposed_tier_by""",
                (str(uuid.uuid4()), row["id"], reason, _now(), tc.tier.value, f"rule:{tc.reason}"),
            )
            db._conn.commit()
        if cur.rowcount:
            report["tier_proposals"] += 1

    logger.info("Classification backfill: %s", report)
    return report


def propose_doc_types_via_model(db: Any, cfg: Any, limit: int = 100) -> dict:
    """Ask the local model to PROPOSE doc_types for unknown residue.

    Proposals land in pending_reclassify with proposed_doc_type_by='model' and are
    ratified (or rejected) in the Review Queue — the model never applies a
    classification.  Returns {"proposed": n, "skipped": n, "remaining": n}.
    """
    from orivellum.capabilities.llm import llm_call

    with db._lock:
        rows = db._conn.execute(
            """SELECT d.id, d.title, d.kind,
                      substr(d.extracted_text, 1, 4000) AS sample
               FROM documents d
               WHERE d.doc_type = 'unknown'
                 AND NOT EXISTS (SELECT 1 FROM pending_reclassify pr
                                 WHERE pr.doc_id = d.id AND pr.proposed_doc_type IS NOT NULL)
               LIMIT ?""",
            (limit + 1,),
        ).fetchall()
    remaining_flag = len(rows) > limit
    rows = rows[:limit]

    proposed = skipped = 0
    for row in rows:
        sample = (row["sample"] or "").strip()
        user = (
            f"Filename: {row['title']}\nKind: {row['kind'] or 'unknown'}\n"
            f"First text:\n{sample[:3000] if sample else '(no extracted text)'}"
        )
        result = llm_call(
            [{"role": "system", "content": _MODEL_PROMPT}, {"role": "user", "content": user}],
            cfg=cfg,
            db=db,
            purpose="doc_type_proposal",
            timeout=25,
            temperature=0.0,
            max_tokens=8,
        )
        answer = (result.text or "").strip().lower().split()[0] if result.ok and result.text else ""
        answer = answer.strip(".,\"'")
        if answer not in VALID_DOC_TYPES or answer == "unknown":
            skipped += 1
            continue
        reason = f"Model proposes doc_type {answer!r} for unclassified document"
        with db._lock:
            # Mirror of the tier upsert: never clobber a tier proposal's
            # reason/provenance already on the row.
            db._conn.execute(
                """INSERT INTO pending_reclassify(id, doc_id, reason, created_at,
                                                  proposed_doc_type, proposed_doc_type_by)
                   VALUES(?,?,?,?,?,'model')
                   ON CONFLICT(doc_id) DO UPDATE SET
                     reason=CASE WHEN pending_reclassify.proposed_tier IS NULL
                                 THEN excluded.reason
                                 ELSE pending_reclassify.reason END,
                     proposed_doc_type=excluded.proposed_doc_type,
                     proposed_doc_type_by='model'""",
                (str(uuid.uuid4()), row["id"], reason, _now(), answer),
            )
            db._conn.commit()
        proposed += 1

    return {"proposed": proposed, "skipped": skipped, "more_remaining": remaining_flag}
