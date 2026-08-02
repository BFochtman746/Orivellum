"""Knowledge domain routes — /api/knowledge/*"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db

router = APIRouter(prefix="/api")


class KnowledgeReview(BaseModel):
    review_status: str  # "approved" | "rejected" | "auto" | "ai_auto"
    force: bool = False  # override an already-finalized decision (deliberate flip)


@router.get("/knowledge")
def list_knowledge(work_id: str | None = None, kind: str | None = None, limit: int = 100):
    db = get_db()
    items = db.list_knowledge(work_id=work_id, kind=kind, limit=min(limit, 500))
    return {"knowledge": items, "count": len(items)}


@router.get("/knowledge/search")
def search_knowledge(q: str, work_id: str | None = None, limit: int = 20,
                     semantic: bool = True):
    """Hybrid keyword + semantic search over knowledge items.

    Falls back to pure keyword (FTS) search automatically when the embeddings
    endpoint is unavailable, or when ``semantic=false`` is passed.
    """
    if not q:
        raise HTTPException(400, "q parameter required")
    db = get_db()
    if semantic:
        from orivellum.capabilities.embeddings import hybrid_search_knowledge
        items = hybrid_search_knowledge(q.strip(), db, limit=min(limit, 50),
                                        work_id=work_id)
    else:
        items = db.search_knowledge(q.strip(), work_id=work_id, limit=min(limit, 50))
    return {"query": q, "knowledge": items, "count": len(items)}


@router.get("/knowledge/{item_id}")
def get_knowledge(item_id: str):
    db = get_db()
    with db._lock:
        row = db._conn.execute("SELECT * FROM knowledge WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Knowledge item {item_id!r} not found")
    import json
    d = dict(row)
    d["meta"] = json.loads(d.get("meta") or "{}")
    return {"item": d}


@router.delete("/knowledge/{item_id}")
def delete_knowledge(item_id: str):
    """Permanently delete a knowledge item."""
    db = get_db()
    with db._lock:
        row = db._conn.execute("SELECT id FROM knowledge WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise HTTPException(404, f"Knowledge item {item_id!r} not found")
        db._conn.execute("DELETE FROM knowledge WHERE id=?", (item_id,))
        db._conn.commit()
    db.audit("knowledge.deleted", object_id=item_id, object_type="knowledge", actor="user")
    return {"ok": True, "id": item_id}


@router.patch("/knowledge/{item_id}/review")
def review_knowledge(item_id: str, body: KnowledgeReview):
    """Approve or dismiss a knowledge item.

    By default this is claim-first: it only transitions items still awaiting
    review ('auto'/'ai_auto'), returning 409 when the item was already
    finalized elsewhere (stale card / concurrent request). Setting the same
    status again is treated as an idempotent success. A deliberate flip of a
    finalized decision must pass ``force: true``.
    """
    db = get_db()
    expected = None if body.force else ("auto", "ai_auto")
    try:
        result = db.update_knowledge_review_status(item_id, body.review_status,
                                                   expected_status=expected)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    if result == "not_found":
        raise HTTPException(404, f"Knowledge item {item_id!r} not found")
    if result == "conflict":
        with db._lock:
            row = db._conn.execute(
                "SELECT review_status FROM knowledge WHERE id=?", (item_id,)
            ).fetchone()
        current = row["review_status"] if row else None
        if current == body.review_status:
            return {"ok": True, "id": item_id, "review_status": current}
        raise HTTPException(
            409,
            f"Item already resolved (status={current}); pass force=true to override",
        )
    return {"ok": True, "id": item_id, "review_status": body.review_status}
