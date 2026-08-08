"""Forge Website Factory routes — /api/forge/*

Pipeline: PLAN → DESIGN (human selects concept) → BUILD → VERIFY → RELEASE

Jobs run on the shared thread executor (same as other background tasks).
Events stream from the forge_events table via SSE so the UI can tail them
in real-time without a websocket.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/forge")

# ── Request bodies ─────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    brief: str = ""
    work_id: str | None = None

class JobCreate(BaseModel):
    type: str                          # PLAN | DESIGN | BUILD | VERIFY | REPAIR
    instruction: str | None = None
    plan_job_id: str | None = None
    design_job_id: str | None = None
    target_job_id: str | None = None

class ApproveBody(BaseModel):
    selected_concept_id: str | None = None  # for DESIGN jobs

# ── Background runner ──────────────────────────────────────────────────────────

def _run_job_bg(project_id: str, job_id: str) -> None:
    """Called in background thread — imports lazily to avoid circular deps."""
    try:
        from orivellum.capabilities.forge import run_forge_job
        db = get_db()
        cfg = get_config()
        run_forge_job(db, cfg, project_id, job_id)
    except Exception as exc:
        logger.exception("Forge background job %s failed: %s", job_id, exc)


# ── Projects ───────────────────────────────────────────────────────────────────

@router.get("/projects")
def list_forge_projects(
    work_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    db = get_db()
    projects = db.list_forge_projects(work_id=work_id, limit=limit)
    return {"projects": projects}


@router.post("/projects", status_code=201)
def create_forge_project(body: ProjectCreate):
    db = get_db()
    project = db.create_forge_project(
        name=body.name.strip(),
        brief=body.brief.strip(),
        work_id=body.work_id or None,
    )
    return {"project": project}


@router.get("/projects/{project_id}")
def get_forge_project(project_id: str):
    db = get_db()
    project = db.get_forge_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    jobs = db.list_forge_jobs(project_id)
    return {"project": project, "jobs": jobs}


@router.delete("/projects/{project_id}", status_code=204)
def delete_forge_project(project_id: str):
    db = get_db()
    if not db.get_forge_project(project_id):
        raise HTTPException(404, "Project not found")
    db.delete_forge_project(project_id)
    return None


# ── Jobs ───────────────────────────────────────────────────────────────────────

VALID_TYPES = {"PLAN", "DESIGN", "BUILD", "VERIFY", "REPAIR"}


@router.post("/projects/{project_id}/jobs", status_code=202)
def start_forge_job(project_id: str, body: JobCreate, background_tasks: BackgroundTasks):
    db = get_db()
    project = db.get_forge_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")

    jtype = body.type.upper()
    if jtype not in VALID_TYPES:
        raise HTTPException(400, f"Invalid job type. Allowed: {sorted(VALID_TYPES)}")

    # Guard: DESIGN requires an approved PLAN
    if jtype == "DESIGN":
        plan_job_id = body.plan_job_id or project.get("config_data", {}).get("plan_job_id")
        if not plan_job_id:
            raise HTTPException(400, "DESIGN requires an approved plan_job_id.")
        plan_job = db.get_forge_job(plan_job_id)
        if not plan_job or plan_job.get("status") != "passed":
            raise HTTPException(400, "The referenced PLAN job must be in 'passed' state.")

    # Guard: BUILD requires approved PLAN + DESIGN
    if jtype == "BUILD":
        plan_job_id   = body.plan_job_id   or project.get("config_data", {}).get("plan_job_id")
        design_job_id = body.design_job_id or project.get("config_data", {}).get("design_job_id")
        if not plan_job_id or not design_job_id:
            raise HTTPException(400, "BUILD requires plan_job_id and design_job_id.")

    job = db.create_forge_job(
        project_id=project_id,
        type=jtype,
        instruction=body.instruction,
        plan_job_id=body.plan_job_id,
        design_job_id=body.design_job_id,
        target_job_id=body.target_job_id,
    )

    background_tasks.add_task(_run_job_bg, project_id, job["id"])
    return {"job": job}


@router.get("/projects/{project_id}/jobs/{job_id}")
def get_forge_job(project_id: str, job_id: str):
    db = get_db()
    job = db.get_forge_job(job_id)
    if not job or job.get("project_id") != project_id:
        raise HTTPException(404, "Job not found")
    # Attach artifact list
    try:
        from orivellum.database.db import Database as _DB  # noqa
        with db._lock:
            arts = db._conn.execute(
                "SELECT id, artifact_type, sha256, created_at FROM forge_artifacts WHERE job_id=?",
                (job_id,),
            ).fetchall()
        job["artifacts"] = [dict(a) for a in arts]
    except Exception:
        job["artifacts"] = []
    return {"job": job}


@router.post("/projects/{project_id}/jobs/{job_id}/approve")
def approve_forge_job(project_id: str, job_id: str, body: ApproveBody):
    """
    Mark a PLAN or DESIGN job as approved ('passed'), unblocking the next stage.
    For DESIGN jobs, optionally record the selected_concept_id.
    """
    db = get_db()
    job = db.get_forge_job(job_id)
    if not job or job.get("project_id") != project_id:
        raise HTTPException(404, "Job not found")

    if job.get("status") != "awaiting_approval":
        raise HTTPException(400, f"Job is not awaiting approval (current: {job.get('status')})")

    meta_update: dict[str, Any] = {}
    if body.selected_concept_id:
        meta_update["selected_concept_id"] = body.selected_concept_id

    if meta_update:
        existing_meta: dict = {}
        try:
            existing_meta = json.loads(job.get("meta") or "{}")
        except Exception:
            pass
        existing_meta.update(meta_update)
        db.update_forge_job(job_id, status="passed", meta=json.dumps(existing_meta))
    else:
        db.update_forge_job(job_id, status="passed")

    db.append_forge_event(job_id, "approved", "Job approved — ready for next stage.")
    return {"ok": True, "job_id": job_id}


@router.post("/projects/{project_id}/jobs/{job_id}/reject")
def reject_forge_job(project_id: str, job_id: str):
    db = get_db()
    job = db.get_forge_job(job_id)
    if not job or job.get("project_id") != project_id:
        raise HTTPException(404, "Job not found")
    if job.get("status") not in ("awaiting_approval", "conditional"):
        raise HTTPException(400, f"Job cannot be rejected in state: {job.get('status')}")
    db.update_forge_job(job_id, status="rejected")
    db.append_forge_event(job_id, "rejected", "Job rejected.")
    return {"ok": True}


# ── SSE event stream ───────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/jobs/{job_id}/events")
async def stream_forge_events(
    project_id: str,
    job_id: str,
    after_id: str | None = Query(None),
):
    """
    SSE stream — tails forge_events for this job.
    Sends all historical events (after after_id if supplied), then polls
    every 0.75 s for new events until the job reaches a terminal state.
    """
    db = get_db()
    job = db.get_forge_job(job_id)
    if not job or job.get("project_id") != project_id:
        raise HTTPException(404, "Job not found")

    TERMINAL = {"passed", "failed", "blocked", "conditional", "rejected", "awaiting_approval"}

    async def event_generator():
        last_id = after_id
        # How many consecutive empty polls we've seen in a terminal state
        quiet_rounds = 0

        while True:
            try:
                events = db.list_forge_events(job_id, after_id=last_id, limit=100)
            except Exception as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
                break

            for ev in events:
                last_id = ev["id"]
                payload = {
                    "id":      ev["id"],
                    "phase":   ev["phase"],
                    "message": ev["message"],
                    "data":    ev.get("data"),
                    "ts":      ev["created_at"],
                }
                yield f"data: {json.dumps(payload)}\n\n"

            # Check if job is done
            try:
                current = db.get_forge_job(job_id)
                status = current.get("status") if current else "unknown"
            except Exception:
                status = "unknown"

            if status in TERMINAL:
                if not events:
                    quiet_rounds += 1
                    if quiet_rounds >= 2:
                        # Drain done — send terminal sentinel and close
                        yield f"data: {json.dumps({'phase': '__done__', 'status': status})}\n\n"
                        break
                else:
                    quiet_rounds = 0
            else:
                quiet_rounds = 0

            await asyncio.sleep(0.75)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


# ── Artifact retrieval ─────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/jobs/{job_id}/artifact/{artifact_type}")
def get_forge_artifact(project_id: str, job_id: str, artifact_type: str):
    db = get_db()
    job = db.get_forge_job(job_id)
    if not job or job.get("project_id") != project_id:
        raise HTTPException(404, "Job not found")
    art = db.get_forge_artifact(job_id, artifact_type)
    if not art:
        raise HTTPException(404, "Artifact not found")
    try:
        content = json.loads(art["content_json"])
    except Exception:
        content = art["content_json"]
    return {"artifact_type": artifact_type, "content": content, "sha256": art.get("sha256")}


# ── Build preview ──────────────────────────────────────────────────────────────

@router.get("/projects/{project_id}/jobs/{job_id}/preview/{file_path:path}")
def preview_build_file(project_id: str, job_id: str, file_path: str):
    """Serve a static file from the job's build directory."""
    db = get_db()
    job = db.get_forge_job(job_id)
    if not job or job.get("project_id") != project_id:
        raise HTTPException(404, "Job not found")
    build_dir_str = job.get("build_dir")
    if not build_dir_str:
        raise HTTPException(404, "Build directory not set")
    build_dir = pathlib.Path(build_dir_str)
    target = (build_dir / (file_path or "index.html")).resolve()
    # Jail check
    if not str(target).startswith(str(build_dir.resolve())):
        raise HTTPException(403, "Path outside build directory")
    if not target.exists():
        # Try index.html fallback
        target = build_dir / "index.html"
        if not target.exists():
            raise HTTPException(404, "File not found")
    return FileResponse(str(target))
