"""Works domain routes — /api/works/*"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db

router = APIRouter(prefix="/api")

WORK_TYPES = [
    {"id": "research", "label": "Research", "description": "Deep research and knowledge synthesis"},
    {"id": "writing", "label": "Writing", "description": "Books, essays, articles"},
    {"id": "learning", "label": "Learning", "description": "Structured learning and mastery"},
    {"id": "project", "label": "Project", "description": "Goals and deliverables"},
    {"id": "reference", "label": "Reference", "description": "Reference material and notes"},
]


class WorkCreate(BaseModel):
    title: str
    work_type: str = "research"
    description: str | None = None
    meta: dict[str, Any] = {}


class WorkUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    meta: dict[str, Any] | None = None


class TaskCreate(BaseModel):
    text: str
    priority: int = 0


class TaskUpdate(BaseModel):
    status: str | None = None
    text: str | None = None


@router.get("/works/types")
def works_list_types():
    return {"types": WORK_TYPES}


@router.get("/works")
def works_list(status: str | None = None, work_type: str | None = None):
    db = get_db()
    return {"works": db.list_works(status=status, work_type=work_type)}


@router.post("/works")
def works_create(body: WorkCreate):
    db = get_db()
    work = db.create_work(
        title=body.title,
        work_type=body.work_type,
        description=body.description,
        meta=body.meta,
    )
    return {"work": work}


@router.get("/works/{work_id}")
def works_get(work_id: str):
    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    return {"work": work}


@router.patch("/works/{work_id}")
def works_update(work_id: str, body: WorkUpdate):
    db = get_db()
    work = db.update_work(work_id, title=body.title, description=body.description,
                          status=body.status, meta=body.meta)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    return {"work": work}


@router.delete("/works/{work_id}")
def works_delete(work_id: str):
    db = get_db()
    ok = db.delete_work(work_id)
    if not ok:
        raise HTTPException(404, f"Work {work_id!r} not found")
    return {"ok": True}


@router.get("/works/{work_id}/documents")
def works_documents(work_id: str):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    docs = db.list_documents(work_id=work_id)
    return {"documents": docs, "count": len(docs)}


@router.get("/works/{work_id}/knowledge")
def works_knowledge(work_id: str, kind: str | None = None):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    items = db.list_knowledge(work_id=work_id, kind=kind)
    return {"knowledge": items, "count": len(items)}


@router.get("/works/{work_id}/tasks")
def works_tasks(work_id: str, status: str | None = None):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    tasks = db.list_tasks(work_id=work_id, status=status)
    return {"tasks": tasks, "count": len(tasks)}


@router.post("/works/{work_id}/tasks")
def works_create_task(work_id: str, body: TaskCreate):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    task = db.create_task(work_id, body.text, body.priority)
    return {"task": task}


@router.patch("/works/{work_id}/tasks/{task_id}")
def works_update_task(work_id: str, task_id: str, body: TaskUpdate):
    db = get_db()
    task = db.update_task(task_id, status=body.status, text=body.text)
    if not task:
        raise HTTPException(404, f"Task {task_id!r} not found")
    return {"task": task}


@router.get("/works/{work_id}/conversations")
def works_conversations(work_id: str):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    convs = db.list_conversations(work_id=work_id)
    return {"conversations": convs}


@router.get("/works/{work_id}/stats")
def works_stats(work_id: str):
    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    with db._lock:
        doc_by_kind = db._conn.execute(
            "SELECT kind, COUNT(*) as n FROM documents WHERE work_id=? GROUP BY kind",
            (work_id,)
        ).fetchall()
        knowledge_by_kind = db._conn.execute(
            "SELECT kind, COUNT(*) as n FROM knowledge WHERE work_id=? GROUP BY kind",
            (work_id,)
        ).fetchall()
        task_by_status = db._conn.execute(
            "SELECT status, COUNT(*) as n FROM tasks WHERE work_id=? GROUP BY status",
            (work_id,)
        ).fetchall()
        conv_count = db._conn.execute(
            "SELECT COUNT(*) as n FROM conversations WHERE work_id=?",
            (work_id,)
        ).fetchone()["n"]
        doc_by_readiness = db._conn.execute(
            "SELECT readiness, COUNT(*) as n FROM documents WHERE work_id=? GROUP BY readiness",
            (work_id,)
        ).fetchall()
    return {
        "work_id": work_id,
        "documents_by_kind": {r["kind"] or "unknown": r["n"] for r in doc_by_kind},
        "documents_by_readiness": {r["readiness"] or "unknown": r["n"] for r in doc_by_readiness},
        "knowledge_by_kind": {r["kind"]: r["n"] for r in knowledge_by_kind},
        "tasks_by_status": {r["status"]: r["n"] for r in task_by_status},
        "conversation_count": conv_count,
    }
