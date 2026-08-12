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

import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db, require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
_DEFER_DAYS = 7
_VALID_DECISIONS = {"approve", "reject", "defer", "reclassify"}
_VALID_TYPES = {
    "knowledge",
    "reclassify",
    "suggestion",
    "duplicate",
    "quarantine",
    "noteblock",
    "canon_fact",
    "position",
    "loom_persona",
    "domain_node",
    "work_proposal",
}


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


def _work_proposal_items(db, deferred: set[str]) -> list[dict]:
    """Build review-queue entries for content-derived Work proposals."""
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, suggested_name, name_source, size, dominant_doc_type,
                      collection_spread, cluster_stats, exemplar_doc_ids, created_at
               FROM work_proposals
               WHERE status='proposed'
               ORDER BY size DESC, created_at ASC LIMIT 300""",
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        key = f"work_proposal:{r['id']}"
        if key in deferred:
            continue
        spread = _jload(r["collection_spread"], {})
        stats = _jload(r["cluster_stats"], {})
        exemplar_ids = _jload(r["exemplar_doc_ids"], [])
        exemplar_titles: list[str] = []
        if exemplar_ids:
            ph = ",".join(["?"] * len(exemplar_ids))
            with db._lock:
                trows = db._conn.execute(
                    f"SELECT id, title FROM documents WHERE id IN ({ph})", exemplar_ids
                ).fetchall()
            title_by_id = {t["id"]: t["title"] for t in trows}
            exemplar_titles = [title_by_id.get(e, e) for e in exemplar_ids]
        cohesion = stats.get("cohesion")
        out.append(
            {
                "id": key,
                "item_type": "work_proposal",
                "title": f"Proposed Work: {r['suggested_name']}",
                "description": (
                    f"{r['size']} documents cluster around one subject across "
                    f"{len(spread)} collection(s). Ratifying creates the Work, "
                    "assigns these documents to it, and records which "
                    "collections contributed. Nothing happens until you sign."
                ),
                # Higher cohesion = less uncertain (sorted later in the queue).
                "confidence": round(float(cohesion), 3) if cohesion is not None else 0.5,
                "work_id": None,
                "work_title": None,
                "evidence": {
                    "size": r["size"],
                    "dominant_doc_type": r["dominant_doc_type"],
                    "name_source": r["name_source"],
                    "collection_spread": spread,
                    "cohesion": cohesion,
                    "exemplar_titles": exemplar_titles,
                },
                "created_at": r["created_at"],
            }
        )
    return out


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
        items.append(
            {
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
            }
        )

    # 2. Documents flagged for reclassification
    with db._lock:
        rows = db._conn.execute(
            """SELECT pr.id, pr.doc_id, pr.reason, pr.created_at,
                      pr.proposed_tier, pr.proposed_doc_type,
                      pr.proposed_tier_by, pr.proposed_doc_type_by,
                      d.title AS doc_title, d.kind, d.classification, d.work_id,
                      d.tier AS current_tier, d.doc_type AS current_doc_type,
                      w.title AS work_title
               FROM pending_reclassify pr
               JOIN documents d ON d.id = pr.doc_id
               LEFT JOIN works w ON w.id = d.work_id""",
        ).fetchall()
    for r in rows:
        key = f"reclassify:{r['id']}"
        if key in deferred:
            continue
        items.append(
            {
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
                    "current_tier": r["current_tier"],
                    "current_doc_type": r["current_doc_type"],
                    "proposed_tier": r["proposed_tier"],
                    "proposed_doc_type": r["proposed_doc_type"],
                    "proposed_tier_by": r["proposed_tier_by"],
                    "proposed_doc_type_by": r["proposed_doc_type_by"],
                },
                "created_at": r["created_at"],
            }
        )

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
        items.append(
            {
                "id": key,
                "item_type": "suggestion",
                "title": (r["kind"] or "suggestion").replace("_", " ").capitalize(),
                "description": r["text"],
                "confidence": meta.get("confidence", 0.5),
                "work_id": r["work_id"],
                "work_title": r["work_title"],
                "evidence": {"kind": r["kind"], **meta},
                "created_at": r["created_at"],
            }
        )

    # 4. Unresolved near-duplicate pairs
    for p in db.list_near_duplicates(resolved=False):
        key = f"duplicate:{p['id']}"
        if key in deferred:
            continue
        items.append(
            {
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
            }
        )

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
        items.append(
            {
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
            }
        )

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
        items.append(
            {
                "id": key,
                "item_type": "noteblock",
                "title": proposal.get("title")
                or (r["text"][:70] + ("…" if len(r["text"]) > 70 else "")),
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
            }
        )

    # 7. Machine-proposed canon facts awaiting author ratification
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, fact_title, fact_text, classification, scope,
                      source_path, source_location, created_at
               FROM wa_canon_proposals WHERE status='proposed'
               ORDER BY created_at ASC LIMIT 300""",
        ).fetchall()
    for r in rows:
        key = f"canon_fact:{r['id']}"
        if key in deferred:
            continue
        items.append(
            {
                "id": key,
                "item_type": "canon_fact",
                "title": f"Canon fact ({r['classification']}): "
                + (r["fact_title"] or (r["fact_text"] or "")[:60]),
                "description": r["fact_text"],
                # Canon is authority — surface it near the top (after security).
                "confidence": 0.1,
                "work_id": None,
                "work_title": None,
                "evidence": {
                    "classification": r["classification"],
                    "scope": r["scope"],
                    "source_path": r["source_path"],
                    "source_location": r["source_location"],
                },
                "created_at": r["created_at"],
            }
        )

    # 8. POSITION reconstruction proposals awaiting ratification
    with db._lock:
        rows = db._conn.execute(
            """SELECT p.id, p.work_id, p.kind, p.title, p.created_at,
                      w.title AS work_title
               FROM position_proposal p
               LEFT JOIN works w ON w.id = p.work_id
               WHERE p.status='proposed'
               ORDER BY p.created_at ASC LIMIT 300""",
        ).fetchall()
    for r in rows:
        key = f"position:{r['id']}"
        if key in deferred:
            continue
        items.append(
            {
                "id": key,
                "item_type": "position",
                "title": r["title"],
                "description": (
                    "Reconstruction derived from existing prose — evidence, "
                    "not authority, until you ratify it."
                ),
                # Reconstruction feeds authority — surface near the top.
                "confidence": 0.15,
                "work_id": r["work_id"],
                "work_title": r["work_title"],
                "evidence": {"kind": r["kind"]},
                "created_at": r["created_at"],
            }
        )

    # 9. LOOM personas awaiting approval (drafting uses ONLY approved ones)
    with db._lock:
        rows = db._conn.execute(
            """SELECT p.id, p.work_id, p.name, p.created_at, w.title AS work_title
               FROM loom_persona p
               LEFT JOIN works w ON w.id = p.work_id
               WHERE p.status='proposed'
               ORDER BY p.created_at ASC LIMIT 300""",
        ).fetchall()
    for r in rows:
        key = f"loom_persona:{r['id']}"
        if key in deferred:
            continue
        items.append(
            {
                "id": key,
                "item_type": "loom_persona",
                "title": f"Persona: {r['name']}",
                "description": (
                    "Character persona for the drafting engine — diction profile "
                    "and knowledge horizon. Drafting refuses until you approve it."
                ),
                "confidence": 0.15,
                "work_id": r["work_id"],
                "work_title": r["work_title"],
                "evidence": {"name": r["name"]},
                "created_at": r["created_at"],
            }
        )

    # 10. Domain Model node proposals (interpretive frame — proposal-only)
    with db._lock:
        rows = db._conn.execute(
            """SELECT n.id, n.work_id, n.domain, n.label, n.node_class,
                      n.agreement, n.source_count, n.created_at,
                      w.title AS work_title
               FROM domain_node n
               LEFT JOIN works w ON w.id = n.work_id
               WHERE n.status='proposed'
               ORDER BY n.agreement DESC, n.created_at ASC LIMIT 300""",
        ).fetchall()
    for r in rows:
        key = f"domain_node:{r['id']}"
        if key in deferred:
            continue
        items.append(
            {
                "id": key,
                "item_type": "domain_node",
                "title": f"Domain node: {r['label']}",
                "description": (
                    f"Harvested from {r['agreement']} of {r['source_count']} "
                    f"reference structures in domain '{r['domain']}' "
                    f"({r['node_class']}). Generates no gap until you ratify "
                    "it with a signature."
                ),
                # Consensus across more sources = less uncertain.
                "confidence": min(0.9, 0.2 + 0.15 * r["agreement"]),
                "work_id": r["work_id"],
                "work_title": r["work_title"],
                "evidence": {
                    "domain": r["domain"],
                    "node_class": r["node_class"],
                    "agreement": r["agreement"],
                    "source_count": r["source_count"],
                },
                "created_at": r["created_at"],
            }
        )

    # 11. Work proposals — content-derived subject clusters (RE-PROJECTION
    # Phase 4).  No Work exists until the author ratifies with a signature
    # and picks an ontology domain.
    items.extend(_work_proposal_items(db, deferred))

    # Most uncertain first; None confidence treated as 0.5
    items.sort(key=lambda i: i["confidence"] if i["confidence"] is not None else 0.5)
    counts: dict[str, int] = {}
    for i in items:
        counts[i["item_type"]] = counts.get(i["item_type"], 0) + 1
    return {"items": items[: min(limit, 500)], "count": len(items), "counts_by_type": counts}


# ── Resolve ───────────────────────────────────────────────────────────────────


class ResolveBody(BaseModel):
    decision: str  # approve | reject | defer | reclassify (canon only)
    reason: str = ""
    canonical_doc_id: str | None = None  # duplicates: which doc survives on approve
    # canon_fact ratification: the author may edit/reclassify while approving.
    author: str = ""
    classification: str | None = None  # HISTORICAL | INFERRED | INVENTED
    statement: str | None = None
    source_ref: str | None = None
    work_id: str | None = None
    parent_ids: list[str] | None = None
    # work_proposal ratification: the author picks the ontology domain.
    domain: str | None = None


_PENDING_SQL = {
    "knowledge": "SELECT 1 FROM knowledge WHERE id=? AND review_status='ai_auto'",
    "reclassify": "SELECT 1 FROM pending_reclassify WHERE id=?",
    "duplicate": "SELECT 1 FROM doc_dupes WHERE id=? AND resolved=0",
    "quarantine": "SELECT 1 FROM documents WHERE id=? AND quarantined=1",
    "noteblock": "SELECT 1 FROM note_blocks WHERE id=? AND status='proposed'",
    "canon_fact": "SELECT 1 FROM wa_canon_proposals WHERE id=? AND status='proposed'",
    "position": "SELECT 1 FROM position_proposal WHERE id=? AND status='proposed'",
    "loom_persona": "SELECT 1 FROM loom_persona WHERE id=? AND status='proposed'",
    "domain_node": "SELECT 1 FROM domain_node WHERE id=? AND status='proposed'",
    "work_proposal": "SELECT 1 FROM work_proposals WHERE id=? AND status='proposed'",
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
def review_resolve(
    item_type: str, item_id: str, body: ResolveBody, background_tasks: BackgroundTasks
):
    """Resolve one review item. decision: approve | reject | defer."""
    if body.decision not in _VALID_DECISIONS:
        raise HTTPException(400, f"decision must be one of: {', '.join(sorted(_VALID_DECISIONS))}")
    if item_type not in _VALID_TYPES:
        raise HTTPException(400, f"unknown item type {item_type!r}")
    if body.decision == "reclassify" and item_type != "canon_fact":
        raise HTTPException(400, "reclassify only applies to canon_fact items")

    db = get_db()

    resolvers = {
        "canon_fact": lambda: _resolve_canon_fact(db, item_id, body),
        "knowledge": lambda: _resolve_knowledge(db, item_id, body),
        "reclassify": lambda: _resolve_reclassify(db, item_id, body, background_tasks),
        "suggestion": lambda: _resolve_suggestion(db, item_id, body),
        "quarantine": lambda: _resolve_quarantine(db, item_id, body, background_tasks),
        "noteblock": lambda: _resolve_noteblock(db, item_id, body),
        "duplicate": lambda: _resolve_duplicate(db, item_id, body),
        "position": lambda: _resolve_position(db, item_id, body),
        "loom_persona": lambda: _resolve_loom_persona(db, item_id, body),
        "domain_node": lambda: _resolve_domain_node(db, item_id, body),
        "work_proposal": lambda: _resolve_work_proposal(db, item_id, body),
    }
    if body.decision == "defer":
        result = _defer(db, item_type, item_id, body.reason)
    else:
        result = resolvers[item_type]()

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
    result = db.update_knowledge_review_status(item_id, status, expected_status=("ai_auto",))
    if result == "not_found":
        raise HTTPException(404, f"Knowledge item {item_id!r} not found")
    if result == "conflict":
        raise HTTPException(409, "Knowledge item was already resolved")
    return {"ok": True, "decision": body.decision, "review_status": status}


def _resolve_reclassify(
    db, item_id: str, body: ResolveBody, background_tasks: BackgroundTasks
) -> dict:
    with db._lock:
        row = db._conn.execute("SELECT * FROM pending_reclassify WHERE id=?", (item_id,)).fetchone()
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
    applied: dict = {}
    if body.decision == "approve" and (row["proposed_tier"] or row["proposed_doc_type"]):
        # Proposal ratification (RE-PROJECTION Phase 3): the classification
        # backfill and the model only ever PROPOSE — approval here is the one
        # place a proposed tier/doc_type is applied.  Applied doc_type carries
        # provenance 'author' because a human ratified it.
        sets, params = [], []
        if row["proposed_tier"]:
            sets.append("tier=?")
            params.append(row["proposed_tier"])
            applied["tier"] = row["proposed_tier"]
        if row["proposed_doc_type"]:
            sets.extend(["doc_type=?", "doc_type_by='author'"])
            params.append(row["proposed_doc_type"])
            applied["doc_type"] = row["proposed_doc_type"]
        params.append(doc_id)
        with db._lock:
            db._conn.execute(f"UPDATE documents SET {', '.join(sets)} WHERE id=?", params)
            db._conn.commit()
        _by = ", ".join(
            f"{field}: {row[col] or 'system'}"
            for field, col, present in (
                ("tier", "proposed_tier_by", row["proposed_tier"]),
                ("doc_type", "proposed_doc_type_by", row["proposed_doc_type"]),
            )
            if present
        )
        with contextlib.suppress(Exception):
            db.audit(
                "document.reclassified",
                object_id=doc_id,
                object_type="document",
                actor="user",
                detail=f"ratified {applied} (proposed by {_by})",
            )
        return {"ok": True, "decision": body.decision, "applied": applied}

    if body.decision == "approve":
        # Approving means: yes, re-extract/classify this document.
        doc = db.get_document(doc_id)
        if doc:
            from orivellum.capabilities.pipeline import process_document, resolve_file_path

            file_path = resolve_file_path(doc.get("source") or "", doc_id, db)
            if file_path:
                db.delete_extraction_warnings(doc_id)
                db.update_document_extracted(
                    doc_id, "", 0, readiness="imported", error_message=None
                )
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


def _resolve_quarantine(
    db, item_id: str, body: ResolveBody, background_tasks: BackgroundTasks
) -> dict:
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
                db.update_document_extracted(
                    item_id, "", 0, readiness="imported", error_message=None
                )
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
    return {"ok": True, "decision": body.decision, "reprocess_queued": reprocess_queued}


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
    db.audit("review.quarantine_reopened", object_id=item_id, object_type="document", actor="user")
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
        return {
            "resolved": True,
            "decision": "approve",
            "pending": True,
            "detail": "Approved. Filing hit an error and will be retried automatically.",
        }

    return {
        "resolved": True,
        "decision": "approve",
        "filed_paths": out["filed_paths"],
        "tasks_created": out["tasks_created"],
    }


def _resolve_canon_fact(db, item_id: str, body: ResolveBody) -> dict:
    """Ratify a machine-proposed canon fact.

    approve/reclassify → write a signed canon_fact (reclassify lets the
    author override the proposed classification); reject → dismiss it.
    The proposal row is claimed first, so no fact enters canon twice.
    """
    from orivellum.database.canon_store import CanonFactError, CanonStore

    # Canon decisions are author acts — a real signature is mandatory (the
    # store enforces this too; checking here gives a clean 422 up front).
    author = (body.author or "").strip()
    if not author:
        raise HTTPException(422, "Canon ratification requires your signature (author)")
    if body.decision == "reject":
        result = CanonStore(db).ratify_proposal(item_id, decision="reject", author=author)
        if result["result"] == "not_found":
            raise HTTPException(404, f"Canon proposal {item_id!r} not found")
        if result["result"] == "conflict":
            raise HTTPException(409, "Proposal was already resolved")
        return {"ok": True, "decision": "reject"}

    # approve or reclassify — both write a fact; reclassify supplies a new class.
    classification = body.classification
    if body.decision == "reclassify" and not classification:
        raise HTTPException(400, "reclassify requires a target classification")
    try:
        result = CanonStore(db).ratify_proposal(
            item_id,
            decision="approve",
            author=author,
            classification=classification,
            statement=body.statement,
            source_ref=body.source_ref,
            work_id=body.work_id,
            parent_ids=body.parent_ids,
        )
    except CanonFactError as e:
        raise HTTPException(422, str(e)) from e
    if result["result"] == "not_found":
        raise HTTPException(404, f"Canon proposal {item_id!r} not found")
    if result["result"] == "conflict":
        raise HTTPException(409, "Proposal was already resolved")
    return {"ok": True, "decision": body.decision, "fact": result["fact"]}


def _resolve_position(db, item_id: str, body: ResolveBody) -> dict:
    """Resolve a POSITION reconstruction proposal (persona / de-facto
    blueprint / de-facto voice spec).

    Reconstruction becomes authority only through this signature: the
    proposal row is atomically claimed first, then — for a voice spec — the
    approved metrics are installed as the work's ASSAY voice baseline (the
    manuscript is its own voice authority, but only once ratified).
    """
    author = (body.author or "").strip()
    if not author:
        raise HTTPException(422, "Ratifying a reconstruction requires your signature (author)")
    proposal = db.get_position_proposal(item_id)
    if proposal is None:
        raise HTTPException(404, f"Position proposal {item_id!r} not found")

    decision = "approved" if body.decision == "approve" else "rejected"
    result = db.resolve_position_proposal(
        item_id, decision=decision, author=author, note=body.reason or ""
    )
    if result == "not_found":
        raise HTTPException(404, f"Position proposal {item_id!r} not found")
    if result == "conflict":
        raise HTTPException(409, "Proposal was already resolved")

    installed = None
    if decision == "approved" and proposal["kind"] == "voice_spec":
        # Side effect strictly AFTER the successful atomic claim.  If the
        # install fails, the approval is rolled back (compensating update
        # guarded by our own signature) so the author can simply retry —
        # never an approved proposal with no baseline and no retry path.
        try:
            db.set_assay_baseline(proposal["work_id"], "voice_envelope", proposal["payload"])
        except Exception as exc:
            db.reopen_position_proposal(item_id, expected_resolved_by=author)
            raise HTTPException(
                500,
                "voice baseline install failed; the proposal was returned "
                "to the review queue — retry the approval",
            ) from exc
        installed = "voice_envelope baseline"
    return {"ok": True, "decision": decision, "kind": proposal["kind"], "installed": installed}


def _resolve_loom_persona(db, item_id: str, body: ResolveBody) -> dict:
    """Approve/reject a LOOM persona.  An approved persona grounds every
    character agent, so APPROVAL requires the author signature (LAW 4);
    rejection grants no authority and needs none.  The resolution is an
    atomic claim."""
    author = (body.author or "").strip()
    decision = "approved" if body.decision == "approve" else "rejected"
    if decision == "approved" and not author:
        raise HTTPException(422, "Approving a persona requires your signature (author)")
    result = db.resolve_loom_persona(item_id, decision=decision, author=author)
    if result == "not_found":
        raise HTTPException(404, f"Persona {item_id!r} not found")
    if result == "conflict":
        raise HTTPException(409, "Persona was already resolved")
    return {"ok": True, "decision": decision}


def _resolve_domain_node(db, item_id: str, body: ResolveBody) -> dict:
    """Ratify/reject a Domain Model node.  A ratified node becomes part of
    the interpretive frame and can generate G2/G4 gaps, so BOTH decisions
    require the author signature — a rejection is also a signed ruling the
    harvest must never overturn.  Claim is atomic in the db layer."""
    author = (body.author or "").strip()
    if not author:
        raise HTTPException(422, "Resolving a domain node requires your signature (author)")
    node_class = (body.classification or "").strip() or None
    try:
        result = db.resolve_domain_node(
            item_id,
            body.decision,
            signed_by=author,
            reason=body.reason,
            node_class=node_class,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result == "not_found":
        raise HTTPException(404, f"Domain node {item_id!r} not found")
    if result == "conflict":
        raise HTTPException(409, "Domain node was already resolved")
    status = "ratified" if body.decision == "approve" else "rejected"
    return {"ok": True, "decision": body.decision, "status": status}


def _gate_work_proposal_resolution(body: ResolveBody) -> tuple[str, str]:
    """Pre-claim validation for Work-proposal resolutions.

    Contract: only approve/reject are meaningful (defer is handled upstream) —
    any other decision, a missing signature, or a bad domain raises a 4xx
    BEFORE any claim, so the proposal always stays queued.  Returns the
    stripped ``(author, domain)``.
    """
    from orivellum.capabilities.work_proposals import VALID_DOMAINS

    if body.decision not in ("approve", "reject"):
        raise HTTPException(400, "work_proposal decisions must be approve or reject")
    author = (body.author or "").strip()
    if not author:
        raise HTTPException(422, "Ratifying a Work proposal requires your signature (author)")
    domain = (body.domain or "").strip()
    if body.decision == "approve":
        if not domain:
            raise HTTPException(
                422,
                f"Ratifying a Work proposal requires a domain ({', '.join(sorted(VALID_DOMAINS))})",
            )
        if domain not in VALID_DOMAINS:
            raise HTTPException(
                422, f"Unknown domain {domain!r} — one of: {', '.join(sorted(VALID_DOMAINS))}"
            )
    return author, domain


def _resolve_work_proposal(db, item_id: str, body: ResolveBody) -> dict:
    """Ratify or reject a content-derived Work proposal (RE-PROJECTION Phase 4).

    A Work only ever comes into existence through this signed ratification.
    Validation (signature, domain) runs BEFORE the atomic claim, so a 422
    always leaves the proposal queued.  Reject touches nothing but the
    proposal row.  On approve: create the Work (with the chosen ontology
    domain), re-point still-eligible member docs, and record which
    collections contributed (provenance shown on the Work detail page).
    """
    author, domain = _gate_work_proposal_resolution(body)

    if db.get_work_proposal(item_id) is None:
        raise HTTPException(404, f"Work proposal {item_id!r} not found")

    if body.decision == "reject":
        if not db.claim_work_proposal(item_id, "rejected", author):
            raise HTTPException(409, "Work proposal was already resolved")
        return {"ok": True, "decision": "reject"}

    # ── Approve: claim + all side effects in ONE transaction ───────────────
    # The claim, Work creation, member re-points, provenance, and finalize
    # commit together or not at all.  Any failure rolls the whole thing back,
    # so there is never a ratified proposal with an orphaned Work or a
    # half-assigned membership — the author simply retries.
    try:
        with db.atomic():
            if not db.claim_work_proposal(item_id, "ratified", author):
                raise _ProposalAlreadyResolved
            # Snapshot the CLAIMED row inside this transaction — never a
            # pre-claim read.  A concurrent generation pass could refresh a
            # still-proposed row (same fingerprint) right up to the claim;
            # reading here guarantees the Work is built from exactly the row
            # that was ratified, so the persisted proposal and the Work it
            # produced can never disagree.
            proposal = db.get_work_proposal(item_id)
            result = _apply_work_proposal_approval(db, item_id, proposal, author, domain)
    except _ProposalAlreadyResolved:
        raise HTTPException(409, "Work proposal was already resolved") from None
    except HTTPException:
        raise
    except Exception as exc:
        # db.atomic() already rolled everything back — claim included — so the
        # proposal is still queued and the author can simply retry.
        raise HTTPException(500, f"Ratification failed and was rolled back: {exc}") from exc

    if result.get("linked"):
        # Post-commit: the chunk vector cache carries d.work_id from its JOIN
        # on documents, so work-scoped semantic search would keep seeing the
        # re-pointed members as unassigned until invalidated.  One bump for
        # the whole batch (update_document_work does this per call).
        try:
            from orivellum.capabilities.embeddings import bump_vector_cache_version

            bump_vector_cache_version(db._path, "chunk")
        except Exception:  # pragma: no cover
            pass
    return result


class _ProposalAlreadyResolved(Exception):
    """Internal sentinel: the atomic claim lost the race (rolls back the txn)."""


def _apply_work_proposal_approval(
    db, item_id: str, proposal: dict, author: str, domain: str
) -> dict:
    """Create the Work + re-point still-eligible members + record provenance.

    Runs inside the caller's ``db.atomic()`` block, so every write here shares
    one transaction with the claim.
    """
    work = db.create_work(
        title=proposal["suggested_name"],
        work_type="research",
        description=(
            f"Ratified from a content-derived subject cluster of "
            f"{proposal['size']} documents (signed by {author})."
        ),
        domain=domain,
    )
    linked = 0
    skipped = 0
    collection_counts: dict[str, int] = {}
    for did in proposal["member_doc_ids"]:
        # Re-check eligibility and re-point in ONE atomic conditional write: a
        # doc that gained a work_id / got re-tiered / was quarantined since the
        # proposal was generated is skipped, never stolen.
        assigned, cid = db.assign_document_to_work_if_eligible(did, work["id"])
        if not assigned:
            skipped += 1
            continue
        linked += 1
        if cid:
            collection_counts[cid] = collection_counts.get(cid, 0) + 1
    for cid, count in collection_counts.items():
        db.add_work_collection(work["id"], cid, count)
    db.finalize_work_proposal(item_id, work["id"], domain)
    return {
        "ok": True,
        "decision": "approve",
        "work_id": work["id"],
        "linked": linked,
        "skipped": skipped,
        "collections": len(collection_counts),
    }


def _gate_work_assignment_approval(db, decision: str, kind: str, meta: dict) -> None:
    """Tier gate (RE-PROJECTION Phase 3): an ARTIFACT/SYSTEM archive may never
    produce a Work.  Enforced at approval too, so stale or hand-crafted
    suggestions can't slip past the creation-time check.  Runs BEFORE the
    atomic claim so a 422 leaves the suggestion queued."""
    if decision != "approve" or kind != "work_assignment":
        return
    from orivellum.capabilities.classify import assert_tier_may_become_work

    archive_doc_id = meta.get("archive_doc_id")
    doc_ids = meta.get("doc_ids") or []
    for gid in [i for i in ([archive_doc_id] + list(doc_ids)) if i]:
        gdoc = db.get_document(gid)
        if not gdoc:
            continue
        try:
            assert_tier_may_become_work(gdoc.get("tier"), "be assigned to a Work")
        except ValueError as tier_exc:
            raise HTTPException(422, str(tier_exc)) from tier_exc


def _resolve_suggestion(db, item_id: str, body: ResolveBody) -> dict:
    with db._lock:
        row = db._conn.execute("SELECT * FROM suggestions WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Suggestion {item_id!r} not found")
    kind = row["kind"]
    meta = _jload(row["meta"], {})

    # Validation BEFORE the claim: a rejected approval (422) must leave the
    # suggestion queued so it can be corrected or reviewed — validation
    # failures never resolve an item.
    _gate_work_assignment_approval(db, body.decision, kind, meta)

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
                work = db.create_work(
                    title=title, work_type="research", description=meta.get("proposed_description")
                )
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
    if (
        body.decision == "approve"
        and body.canonical_doc_id
        and body.canonical_doc_id not in pair_ids
    ):
        raise HTTPException(
            400,
            "canonical_doc_id must be one of the pair's documents "
            f"({row['doc_a_id']}, {row['doc_b_id']})",
        )

    # Claim-first resolution lives in the shared db primitive, so this route
    # and the legacy /library/duplicates route can never both apply effects.
    result = db.resolve_near_duplicate(item_id, action, canonical_doc_id=body.canonical_doc_id)
    if result is None:
        raise HTTPException(404, f"Duplicate pair {item_id!r} not found")
    if result.get("already_resolved"):
        raise HTTPException(409, "Duplicate pair was already resolved")
    canonical = body.canonical_doc_id if body.canonical_doc_id in pair_ids else row["doc_a_id"]
    return {
        "ok": True,
        "decision": body.decision,
        "action": action,
        "canonical_doc_id": canonical if action == "mark_superseded" else None,
    }
