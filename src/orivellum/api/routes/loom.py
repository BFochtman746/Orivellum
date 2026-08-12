"""LOOM — /api/works/{work_id}/loom/*

Chapter drafting engine (B5).  Personas are review-gated (created 'proposed';
only an author signature approves them, through the review queue).  Drafting
runs claim via the run row and dispatch in the background; every pre-dispatch
failure finishes the row as 'error' — never a leaked 'running' row.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.api.executor import submit_bg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


# ── Personas ──────────────────────────────────────────────────────────────────


class PersonaBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: str = ""
    personality: str = ""
    goals: str = ""
    description: str = ""
    relationships: dict = Field(default_factory=dict)
    diction_profile: dict = Field(default_factory=dict)
    # {"1": [canon_fact_id, ...], "2": [...]} — what the character can know,
    # per act boundary.
    knowledge_horizon: dict = Field(default_factory=dict)


@router.post("/works/{work_id}/loom/personas", status_code=201)
def create_persona(work_id: str, body: PersonaBody):
    db = get_db()
    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    payload = body.model_dump(exclude={"name"})
    try:
        pid = db.create_loom_persona(work_id, body.name, payload)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"persona_id": pid, "status": "proposed"}


@router.get("/works/{work_id}/loom/personas")
def list_personas(work_id: str, status: str | None = None):
    db = get_db()
    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    return {"personas": db.list_loom_personas(work_id, status=status)}


# ── Drafting ──────────────────────────────────────────────────────────────────


class DraftBody(BaseModel):
    chapter_id: str = Field(min_length=1)


@router.post("/works/{work_id}/loom/draft", status_code=202)
def start_draft(work_id: str, body: DraftBody):
    db = get_db()
    cfg = get_config()
    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    try:
        run_id = db.create_loom_run(work_id, body.chapter_id)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e

    # We hold the claim from here on: every failure before a successful
    # dispatch must finish the row as 'error'.
    try:
        from orivellum.capabilities.loom import run_loom_draft  # noqa: PLC0415

        dispatched = submit_bg(
            run_loom_draft,
            db,
            cfg,
            run_id=run_id,
            work_id=work_id,
            chapter_id=body.chapter_id,
            kind="loom_draft",
            label=f"loom:{work_id}",
        )
    except Exception as exc:
        db.finish_loom_run(run_id, status="error", error=str(exc))
        raise
    if not dispatched:
        db.finish_loom_run(run_id, status="error", error="background dispatch refused")
        raise HTTPException(503, "draft could not be dispatched; try again")
    return {"run_id": run_id, "status": "running"}


@router.get("/works/{work_id}/loom")
def loom_overview(work_id: str):
    db = get_db()
    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    state = db.get_world_state(work_id)
    return {
        "runs": db.list_loom_runs(work_id, limit=20),
        "personas": db.list_loom_personas(work_id),
        "world_state_keys": len(state),
        "world_state": state,
    }


@router.get("/loom/runs/{run_id}")
def get_loom_run(run_id: str):
    db = get_db()
    run = db.get_loom_run(run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    return {"run": run}


@router.get("/loom/chapters/{chapter_id}/revisions")
def list_revisions(chapter_id: str):
    db = get_db()
    return {"revisions": db.list_chapter_revisions(chapter_id)}
