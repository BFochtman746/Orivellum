"""Governance review queue — /api/review/*

A single inbox aggregating every pending item that needs a human decision
before the system treats it as fact:

- knowledge   : AI-extracted knowledge awaiting approval (review_status='ai_auto')
- reclassify  : documents flagged for reclassification (pending_reclassify)
- suggestion  : system suggestions (version relationships, work assignments, …)
- duplicate   : unresolved near-duplicate document pairs (doc_dupes)

Item ids are namespaced: "<type>:<row id>", e.g. "knowledge:3fa2…".
Resolving accepts decision approve | reject | defer (+ optional reason);
defer snoozes the item for 7 days via the review_deferrals table.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db, require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
_DEFER_DAYS = 7
_VALID_DECISIONS = {"approve", "reject", "defer"}
_VALID_TYPES = {"knowledge", "reclassify", "suggestion", "duplicate", "quarantine", "noteblock"}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _active_deferrals(db) -> set[str]:
    now = _now_iso()
    with db._lock:
        rows = db._conn.execute(
            "SELECT item_key FROM review_deferrals WHERE deferred_until > ?", (now,)
        ).fetchall()
    return {r["item_key"] for r in rows}


def _jload(s, default=None):
    try:
        return json.loads(s) if s else (default if default is not None else {})
    except Exception:
        return default if default is not None else {}


# ── Queue ─────────────────────────────────────────────────────────────────────

@router.get("/review/queue")
def review_queue(limit: int = 200):
    """Unified review inbox, most-uncertain first (confidence ASC)."""
    db = get_db()
    deferred = _active_deferrals(db)
    items: list[dict] = []

    # 1. AI-extracted knowledge awaiting review
    with db._lock:
        rows = db._conn.execute(
            """SELECT k.id, k.work_id, k.kind, k.text, k.subject, k.predicate,
                      k.object, k.confidence, k.created_at, k.source_doc_id,
                      w.title AS work_title, d.title AS doc_title
               FROM knowledge k
               LEFT JOIN works w ON w.id = k.work_id
               LEFT JOIN documents d ON d.id = k.source_doc_id
               WHERE k.review_status='ai_auto'
               ORDER BY k.confidence ASC
               LIMIT 500""",
        ).fetchall()
    for r in rows:
        key = f"knowledge:{r['id']}"
        if key in deferred:
            continue
        items.append({
            "id": key,
            "item_type": "knowledge",
            "title": f"AI-extracted {r['kind']}",
            "description": r["text"],
            "confidence": r["confidence"],
            "work_id": r["work_id"],
            "work_title": r["work_title"],
            "evidence": {
                "source_doc": r["doc_title"],
                "source_doc_id": r["source_doc_id"],
                "subject": r["subject"],
                "predicate": r["predicate"],
                "object": r["object"],
            },
            "created_at": r["created_at"],
        })

    # 2. Documents flagged for reclassification
    with db._lock:
        rows = db._conn.execute(
            """SELECT pr.id, pr.doc_id, pr.reason, pr.created_at,
                      d.title AS doc_title, d.kind, d.classification, d.work_id,
                      w.title AS work_title
               FROM pending_reclassify pr
               JOIN documents d ON d.id = pr.doc_id
               LEFT JOIN works w ON w.id = d.work_id""",
        ).fetchall()
    for r in rows:
        key = f"reclassify:{r['id']}"
        if key in deferred:
            continue
        items.append({
            "id": key,
            "item_type": "reclassify",
            "title": f"Reclassify \u201c{r['doc_title'] or r['doc_id'][:8]}\u201d?",
            "description": r["reason"] or "Document flagged for reclassification",
            "confidence": 0.4,
            "work_id": r["work_id"],
            "work_title": r["work_title"],
            "evidence": {
                "doc_id": r["doc_id"],
                "doc_title": r["doc_title"],
                "current_kind": r["kind"],
                "current_classification": r["classification"],
            },
            "created_at": r["created_at"],
        })

    # 3. Active suggestions (unexpired)
    now = _now_iso()
    with db._lock:
        rows = db._conn.execute(
            """SELECT s.*, w.title AS work_title
               FROM suggestions s
               LEFT JOIN works w ON w.id = s.work_id
               WHERE s.expires_at IS NULL OR s.expires_at > ?
               ORDER BY s.created_at DESC LIMIT 200""",
            (now,),
        ).fetchall()
    for r in rows:
        key = f"suggestion:{r['id']}"
        if key in deferred:
            continue
        meta = _jload(r["meta"], {})
        items.append({
            "id": key,
            "item_type": "suggestion",
            "title": (r["kind"] or "suggestion").replace("_", " ").capitalize(),
            "description": r["text"],
            "confidence": meta.get("confidence", 0.5),
            "work_id": r["work_id"],
            "work_title": r["work_title"],
            "evidence": {"kind": r["kind"], **meta},
            "created_at": r["created_at"],
        })

    # 4. Unresolved near-duplicate pairs
    for p in db.list_near_duplicates(resolved=False):
        key = f"duplicate:{p['id']}"
        if key in deferred:
            continue
        items.append({
            "id": key,
            "item_type": "duplicate",
            "title": "Possible duplicate documents",
            "description": (
                f"\u201c{p.get('doc_a_title') or p['doc_a_id'][:8]}\u201d and "
                f"\u201c{p.get('doc_b_title') or p['doc_b_id'][:8]}\u201d are "
                f"{round((p.get('similarity') or 0) * 100)}% similar"
            ),
            # Higher similarity = more certain it's a dupe; invert so the most
            # uncertain (lowest-similarity) pairs surface first like the rest.
            "confidence": p.get("similarity"),
            "work_id": p.get("doc_a_work_id") or p.get("doc_b_work_id"),
            "work_title": None,
            "evidence": {
                "dupe_id": p["id"],
                "doc_a_id": p["doc_a_id"],
                "doc_b_id": p["doc_b_id"],
                "doc_a_title": p.get("doc_a_title"),
                "doc_b_title": p.get("doc_b_title"),
                "similarity": p.get("similarity"),
                "kind": p.get("kind"),
            },
            "created_at": p.get("created_at") or "",
        })

    # 5. Quarantined documents (ingestion shield tripped at import)
    with db._lock:
        rows = db._conn.execute(
            """SELECT d.id, d.title, d.kind, d.work_id, d.meta, d.created_at,
                      w.title AS work_title
               FROM documents d
               LEFT JOIN works w ON w.id = d.work_id
               WHERE d.quarantined = 1
               ORDER BY d.created_at DESC LIMIT 100""",
        ).fetchall()
    for r in rows:
        key = f"quarantine:{r['id']}"
        if key in deferred:
            continue
        shield_meta = _jload(r["meta"], {}).get("shield") or {}
        findings = shield_meta.get("findings") or []
        kinds = sorted({f.get("kind", "?") for f in findings})
        items.append({
            "id": key,
            "item_type": "quarantine",
            "title": f"Quarantined: \u201c{r['title'] or r['id'][:8]}\u201d",
            "description": (
                "The import safety screen found "
                f"{len(findings)} suspicious pattern(s) in this document"
                + (f" ({', '.join(kinds[:4])})" if kinds else "")
                + ". It is stored but hidden from search, chat, and AI "
                  "processing until you release it."
            ),
            # Security items should surface first in the queue.
            "confidence": 0.0,
            "work_id": r["work_id"],
            "work_title": r["work_title"],
            "evidence": {
                "doc_id": r["id"],
                "doc_title": r["title"],
                "doc_kind": r["kind"],
                "findings": findings[:10],
            },
            "created_at": r["created_at"],
        })

    # 6. Captured notes with an AI filing proposal awaiting approval
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, day, text, source, proposal, created_at
               FROM note_blocks WHERE status='proposed'
               ORDER BY created_at ASC LIMIT 200""",
        ).fetchall()
    for r in rows:
        key = f"noteblock:{r['id']}"
        if key in deferred:
            continue
        proposal = _jload(r["proposal"], {})
        cats = proposal.get("categories") or []
        actions = proposal.get("actions") or []
        items.append({
            "id": key,
            "item_type": "noteblock",
            "title": proposal.get("title") or (r["text"][:70] + ("…" if len(r["text"]) > 70 else "")),
            "description": (
                (proposal.get("summary") or r["text"][:200])
                + f" — file under {', '.join(cats) if cats else 'unsorted'}"
                + (f"; {len(actions)} action(s)" if actions else "")
            ),
            "confidence": proposal.get("confidence"),
            "work_id": None,
            "work_title": None,
            "evidence": {
                "day": r["day"],
                "text": r["text"][:1000],
                "categories": cats,
                "kind": proposal.get("kind"),
                "actions": actions,
                "open_questions": proposal.get("open_questions") or [],
                "warnings": proposal.get("warnings") or [],
            },
            "created_at": r["created_at"],
        })

    # Most uncertain first; None confidence treated as 0.5
    items.sort(key=lambda i: i["confidence"] if i["confidence"] is not None else 0.5)
    counts: dict[str, int] = {}
    for i in items:
        counts[i["item_type"]] = counts.get(i["item_type"], 0) + 1
    return {"items": items[:min(limit, 500)], "count": len(items), "counts_by_type": counts}


# ── Resolve ───────────────────────────────────────────────────────────────────

class ResolveBody(BaseModel):
    decision: str                       # approve | reject | defer
    reason: str = ""
    canonical_doc_id: str | None = None  # duplicates: which doc survives on approve


_PENDING_SQL = {
    "knowledge": "SELECT 1 FROM knowledge WHERE id=? AND review_status='ai_auto'",
    "reclassify": "SELECT 1 FROM pending_reclassify WHERE id=?",
    "duplicate": "SELECT 1 FROM doc_dupes WHERE id=? AND resolved=0",
    "quarantine": "SELECT 1 FROM documents WHERE id=? AND quarantined=1",
    "noteblock": "SELECT 1 FROM note_blocks WHERE id=? AND status='proposed'",
}


def _defer(db, item_type: str, item_id: str, reason: str) -> dict:
    """Snooze a still-pending item. Validation + insert run under one lock so a
    concurrent resolution cannot leave an orphaned deferral."""
    until = (datetime.now(UTC) + timedelta(days=_DEFER_DAYS)).isoformat()
    key = f"{item_type}:{item_id}"
    now = _now_iso()
    with db._lock:
        if item_type == "suggestion":
            pending = db._conn.execute(
                "SELECT 1 FROM suggestions WHERE id=? AND (expires_at IS NULL OR expires_at > ?)",
                (item_id, now),
            ).fetchone()
        else:
            pending = db._conn.execute(_PENDING_SQL[item_type], (item_id,)).fetchone()
        if not pending:
            raise HTTPException(404, f"No pending {item_type} item {item_id!r}")
        db._conn.execute(
            """INSERT INTO review_deferrals(item_key, deferred_until, reason, created_at)
               VALUES(?,?,?,?)
               ON CONFLICT(item_key) DO UPDATE SET
                 deferred_until=excluded.deferred_until, reason=excluded.reason""",
            (key, until, reason or None, now),
        )
        db._conn.commit()
    return {"ok": True, "decision": "defer", "deferred_until": until}


@router.post("/review/{item_type}:{item_id}/resolve")
def review_resolve(item_type: str, item_id: str, body: ResolveBody,
                   background_tasks: BackgroundTasks):
    """Resolve one review item. decision: approve | reject | defer."""
    if body.decision not in _VALID_DECISIONS:
        raise HTTPException(400, f"decision must be one of: {', '.join(sorted(_VALID_DECISIONS))}")
    if item_type not in _VALID_TYPES:
        raise HTTPException(400, f"unknown item type {item_type!r}")

    db = get_db()

    if body.decision == "defer":
        result = _defer(db, item_type, item_id, body.reason)
    elif item_type == "knowledge":
        result = _resolve_knowledge(db, item_id, body)
    elif item_type == "reclassify":
        result = _resolve_reclassify(db, item_id, body, background_tasks)
    elif item_type == "suggestion":
        result = _resolve_suggestion(db, item_id, body)
    elif item_type == "quarantine":
        result = _resolve_quarantine(db, item_id, body, background_tasks)
    elif item_type == "noteblock":
        result = _resolve_noteblock(db, item_id, body)
    else:  # duplicate
        result = _resolve_duplicate(db, item_id, body)

    db.audit(
        "review.resolved",
        object_id=item_id,
        object_type=f"review_{item_type}",
        actor="user",
        detail=f"decision={body.decision}"
               + (f" reason={body.reason[:120]}" if body.reason else ""),
    )
    return result


def _resolve_knowledge(db, item_id: str, body: ResolveBody) -> dict:
    status = "approved" if body.decision == "approve" else "rejected"
    # Claim-first via the shared db primitive: only items still awaiting
    # review can be flipped, so a stale card or a concurrent request through
    # any surface (this route or PATCH /api/knowledge/{id}/review) cannot
    # overturn a decision already made.
    result = db.update_knowledge_review_status(item_id, status,
                                               expected_status=("ai_auto",))
    if result == "not_found":
        raise HTTPException(404, f"Knowledge item {item_id!r} not found")
    if result == "conflict":
        raise HTTPException(409, "Knowledge item was already resolved")
    return {"ok": True, "decision": body.decision, "review_status": status}


def _resolve_reclassify(db, item_id: str, body: ResolveBody,
                        background_tasks: BackgroundTasks) -> dict:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM pending_reclassify WHERE id=?", (item_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, f"Reclassify item {item_id!r} not found")
    doc_id = row["doc_id"]

    # Atomic claim: only the caller whose DELETE removes the row applies effects.
    with db._lock:
        cur = db._conn.execute("DELETE FROM pending_reclassify WHERE id=?", (item_id,))
        claimed = cur.rowcount
        db._conn.commit()
    if not claimed:
        raise HTTPException(409, "Item was already resolved by another request")

    reprocess_queued = False
    if body.decision == "approve":
        # Approving means: yes, re-extract/classify this document.
        doc = db.get_document(doc_id)
        if doc:
            from orivellum.capabilities.pipeline import process_document, resolve_file_path
            file_path = resolve_file_path(doc.get("source") or "", doc_id, db)
            if file_path:
                db.delete_extraction_warnings(doc_id)
                db.update_document_extracted(doc_id, "", 0, readiness="imported",
                                             error_message=None)
                background_tasks.add_task(
                    process_document,
                    doc_id=doc_id,
                    file_path=str(file_path),
                    kind=doc.get("kind") or "file",
                    work_id=doc.get("work_id"),
                    title=doc.get("title", ""),
                    db=db,
                )
                reprocess_queued = True
    return {"ok": True, "decision": body.decision, "reprocess_queued": reprocess_queued}


def _resolve_quarantine(db, item_id: str, body: ResolveBody,
                        background_tasks: BackgroundTasks) -> dict:
    """approve = release the document (marks it safe and reprocesses it so it
    finally gets chunked/indexed/harvested); reject = keep it isolated.
    The release mark means a reprocess will not re-quarantine it."""
    # Atomic claim: only the caller whose UPDATE flips quarantined=1 away
    # applies side effects.
    new_state = 0 if body.decision == "approve" else 2
    with db._lock:
        cur = db._conn.execute(
            "UPDATE documents SET quarantined=? WHERE id=? AND quarantined=1",
            (new_state, item_id),
        )
        claimed = cur.rowcount
        db._conn.commit()
    if not claimed:
        raise HTTPException(409, "Item was already resolved by another request")

    reprocess_queued = False
    if body.decision == "approve":
        # Record the human release (so reprocess skips the screen) and queue
        # the full pipeline — the doc was never chunked/harvested/embedded.
        db.set_document_quarantine(item_id, 0, released=True)
        doc = db.get_document(item_id)
        if doc:
            from orivellum.capabilities.pipeline import process_document, resolve_file_path
            file_path = resolve_file_path(doc.get("source") or "", item_id, db)
            if file_path:
                db.update_document_extracted(item_id, "", 0, readiness="imported",
                                             error_message=None)
                background_tasks.add_task(
                    process_document,
                    doc_id=item_id,
                    file_path=str(file_path),
                    kind=doc.get("kind") or "file",
                    work_id=doc.get("work_id"),
                    title=doc.get("title", ""),
                    db=db,
                )
                reprocess_queued = True
    return {"ok": True, "decision": body.decision,
            "reprocess_queued": reprocess_queued}


@router.post("/review/quarantine:{item_id}/reopen")
def review_quarantine_reopen(item_id: str):
    """Move a kept-quarantined document (state 2) back to pending review
    (state 1) so it reappears in the queue and can be released."""
    db = get_db()
    with db._lock:
        cur = db._conn.execute(
            "UPDATE documents SET quarantined=1 WHERE id=? AND quarantined=2",
            (item_id,),
        )
        claimed = cur.rowcount
        db._conn.commit()
    if not claimed:
        raise HTTPException(404, "Document is not in kept-quarantine state")
    db.audit("review.quarantine_reopened", object_id=item_id,
             object_type="document", actor="user")
    return {"ok": True}


def _resolve_noteblock(db, item_id: str, body: ResolveBody) -> dict:
    block = db.get_note_block(item_id)
    if not block:
        raise HTTPException(404, f"Note {item_id!r} not found")
    if block["status"] != "proposed":
        raise HTTPException(409, "This note was already resolved.")

    if body.decision == "reject":
        if not db.claim_note_block(item_id, "rejected", expected="proposed"):
            raise HTTPException(409, "This note was already resolved.")
        return {"resolved": True, "decision": "reject"}

    # Approve: atomic claim first; only the winner files and creates side effects.
    if not db.claim_note_block(item_id, "approved", expected="proposed"):
        raise HTTPException(409, "This note was already resolved.")

    from orivellum.api._deps import get_config
    from orivellum.capabilities import notes as notes_cap

    # complete_approval is fully idempotent; if anything fails here the block
    # stays 'approved' and the nightshift recovery pass replays it — the
    # decision is never lost and filing is never left permanently partial.
    try:
        out = notes_cap.complete_approval(db, get_config(), db.get_note_block(item_id))
    except Exception as exc:
        logger.warning("noteblock %s: filing failed, queued for retry: %s", item_id, exc)
        db.set_note_block_error(item_id, f"Filing failed; will retry automatically: {exc}")
        return {"resolved": True, "decision": "approve", "pending": True,
                "detail": "Approved. Filing hit an error and will be retried automatically."}

    return {"resolved": True, "decision": "approve",
            "filed_paths": out["filed_paths"], "tasks_created": out["tasks_created"]}


def _resolve_suggestion(db, item_id: str, body: ResolveBody) -> dict:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM suggestions WHERE id=?", (item_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, f"Suggestion {item_id!r} not found")
    kind = row["kind"]
    meta = _jload(row["meta"], {})

    # Atomic claim: delete first; only the caller whose DELETE removes the row
    # applies side effects, so concurrent approvals cannot both create a Work.
    with db._lock:
        cur = db._conn.execute("DELETE FROM suggestions WHERE id=?", (item_id,))
        claimed = cur.rowcount
        db._conn.commit()
    if not claimed:
        raise HTTPException(409, "Suggestion was already resolved by another request")

    applied: dict = {}

    if body.decision == "approve":
        if kind == "version_relationship":
            doc_a, doc_b = meta.get("doc_a_id"), meta.get("doc_b_id")
            if doc_a and doc_b:
                rel_oid = db._create_object("relationship")
                with db._lock:
                    db._conn.execute(
                        """INSERT OR IGNORE INTO relationships
                           (id, source_id, target_id, kind, weight, meta, created_at)
                           VALUES(?,?,?,'DERIVED_FROM',1.0,'{}',?)""",
                        (rel_oid, doc_b, doc_a, _now_iso()),
                    )
                    db._conn.commit()
                applied = {"relationship": "DERIVED_FROM", "doc_a": doc_a, "doc_b": doc_b}
        elif kind == "work_assignment":
            doc_ids = meta.get("doc_ids") or []
            target_work_id = meta.get("work_id")
            if not target_work_id:
                title = meta.get("proposed_title") or row["text"][:80] or "Imported archive"
                work = db.create_work(title=title, work_type="research",
                                      description=meta.get("proposed_description"))
                target_work_id = work["id"]
                applied["work_created"] = target_work_id
            linked = 0
            for did in doc_ids:
                try:
                    db.update_document_work(did, target_work_id)
                    linked += 1
                except Exception as exc:  # noqa: BLE001
                    logger.debug("work_assignment link failed for %s: %s", did, exc)
            applied.update({"work_id": target_work_id, "linked": linked})
        # Other suggestion kinds: approval simply acknowledges + removes them.

    return {"ok": True, "decision": body.decision, "applied": applied}


def _resolve_duplicate(db, item_id: str, body: ResolveBody) -> dict:
    # approve = confirm duplication → mark the non-canonical doc superseded
    # reject  = not duplicates → keep both, dismiss the pair
    action = "mark_superseded" if body.decision == "approve" else "keep_both"

    # Validate canonical_doc_id against the pair before attempting the claim.
    with db._lock:
        row = db._conn.execute(
            "SELECT doc_a_id, doc_b_id FROM doc_dupes WHERE id=?", (item_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, f"Duplicate pair {item_id!r} not found")
    pair_ids = {row["doc_a_id"], row["doc_b_id"]}
    if (body.decision == "approve" and body.canonical_doc_id
            and body.canonical_doc_id not in pair_ids):
        raise HTTPException(
            400,
            "canonical_doc_id must be one of the pair's documents "
            f"({row['doc_a_id']}, {row['doc_b_id']})",
        )

    # Claim-first resolution lives in the shared db primitive, so this route
    # and the legacy /library/duplicates route can never both apply effects.
    result = db.resolve_near_duplicate(item_id, action,
                                       canonical_doc_id=body.canonical_doc_id)
    if result is None:
        raise HTTPException(404, f"Duplicate pair {item_id!r} not found")
    if result.get("already_resolved"):
        raise HTTPException(409, "Duplicate pair was already resolved")
    canonical = (body.canonical_doc_id
                 if body.canonical_doc_id in pair_ids else row["doc_a_id"])
    return {"ok": True, "decision": body.decision, "action": action,
            "canonical_doc_id": canonical if action == "mark_superseded" else None}
