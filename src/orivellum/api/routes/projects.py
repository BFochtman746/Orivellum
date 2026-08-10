"""Projects (mastery learning) routes — /api/projects/*"""

from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db, require_auth

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


class ProjectCreate(BaseModel):
    name: str
    description: str | None = None
    work_id: str | None = None


@router.get("/projects")
def list_projects():
    db = get_db()
    with db._lock:
        rows = db._conn.execute(
            """SELECT lc.*, COUNT(lm.id) as review_count,
                      MAX(lm.created_at) as last_review
               FROM learning_concepts lc
               LEFT JOIN learning_mastery lm ON lm.concept_id = lc.id
               GROUP BY lc.id
               ORDER BY lc.created_at DESC"""
        ).fetchall()
    return {"projects": [dict(r) for r in rows]}


@router.post("/projects")
def create_project(body: ProjectCreate):
    db = get_db()
    import uuid
    from datetime import datetime

    pid = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    with db._lock:
        db._conn.execute(
            """INSERT INTO learning_concepts(id, work_id, name, description, mastery, meta, created_at)
               VALUES(?, ?, ?, ?, 0.0, '{}', ?)""",
            (pid, body.work_id, body.name, body.description, now),
        )
        db._conn.commit()
    db.audit(
        "learning.concept_created",
        object_id=pid,
        object_type="learning_concept",
        actor="user",
        detail=body.name[:120] if body.name else None,
    )
    return {
        "project": {
            "id": pid,
            "name": body.name,
            "description": body.description,
            "work_id": body.work_id,
            "mastery": 0.0,
            "created_at": now,
        }
    }


@router.get("/projects/{project_id}")
def get_project(project_id: str):
    db = get_db()
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM learning_concepts WHERE id=?", (project_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, f"Project {project_id!r} not found")
    return {"project": dict(row)}


@router.get("/projects/{project_id}/concepts")
def get_concepts(project_id: str):
    db = get_db()
    with db._lock:
        rows = db._conn.execute(
            """SELECT lc.*, 
                      (SELECT COUNT(*) FROM learning_prerequisites WHERE concept_id=lc.id) as prereq_count
               FROM learning_concepts lc WHERE lc.id=? OR lc.work_id=(
                   SELECT work_id FROM learning_concepts WHERE id=?
               ) ORDER BY lc.created_at""",
            (project_id, project_id),
        ).fetchall()
    return {"concepts": [dict(r) for r in rows]}
