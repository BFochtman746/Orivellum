"""Knowledge domain routes — /api/knowledge/*"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from orivellum.api._deps import get_db

router = APIRouter(prefix="/api")


@router.get("/knowledge")
def list_knowledge(work_id: str | None = None, kind: str | None = None, limit: int = 100):
    db = get_db()
    items = db.list_knowledge(work_id=work_id, kind=kind, limit=min(limit, 500))
    return {"knowledge": items, "count": len(items)}


@router.get("/knowledge/search")
def search_knowledge(q: str, work_id: str | None = None, limit: int = 20):
    if not q:
        raise HTTPException(400, "q parameter required")
    db = get_db()
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
