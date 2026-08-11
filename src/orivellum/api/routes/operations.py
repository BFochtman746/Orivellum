"""Operations API — run a whole multi-step job with one button.

Routes
------
GET  /api/operations                      → recent operation runs
GET  /api/operations/actions              → registered step actions
GET  /api/operations/playbooks            → starter + saved custom playbooks
POST /api/operations/playbooks            → save a validated custom playbook
DELETE /api/operations/playbooks/{id}     → delete a custom playbook
POST /api/operations/plan                 → plain-words job → proposed plan
POST /api/operations/start                → create + start an operation
GET  /api/operations/{op_id}              → one run with its steps
POST /api/operations/{op_id}/pause        → pause at the next checkpoint
POST /api/operations/{op_id}/resume      → resume (also retries a failed run)
POST /api/operations/{op_id}/cancel       → cancel the run

Route order matters: the literal /actions, /playbooks, and /plan paths must
be declared before the /{op_id} catch-all.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from orivellum.api import notifications
from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.api.executor import submit_bg
from orivellum.api.routes import studio
from orivellum.capabilities.operations import hooks as _op_hooks

logger = logging.getLogger("orivellum.api.operations")
router = APIRouter(
    prefix="/api/operations", tags=["operations"], dependencies=[Depends(require_auth)]
)

# Inject the api-layer services the operations capability needs. The layering
# contract forbids capabilities importing orivellum.api, so the dependency is
# inverted here (routes → capabilities is the allowed direction).
_op_hooks.configure(notify=notifications.emit, submit_bg=submit_bg, studio=studio)


class StepIn(BaseModel):
    action_id: str
    label: str | None = None
    params: dict = Field(default_factory=dict)


class StartOperationRequest(BaseModel):
    """Start from a playbook (playbook_id) or from explicit custom steps."""

    playbook_id: str | None = None
    title: str | None = None
    steps: list[StepIn] | None = None
    work_id: str | None = None
    params: dict = Field(default_factory=dict)


@router.get("")
def list_operations(limit: int = Query(30, ge=1, le=200), work_id: str | None = Query(None)):
    from orivellum.capabilities.operations import store

    ops = store.list_operations(get_db(), limit=limit, work_id=work_id)
    return {"operations": ops, "count": len(ops)}


@router.get("/actions")
def list_op_actions():
    from orivellum.capabilities.operations.registry import get_op_registry

    return {"actions": [a.to_dict() for a in get_op_registry().values()]}


@router.get("/playbooks")
def list_playbooks():
    from orivellum.capabilities.operations.playbooks import PLAYBOOKS, list_custom_playbooks

    return {"playbooks": PLAYBOOKS + list_custom_playbooks(get_db())}


class SavePlaybookRequest(BaseModel):
    title: str
    description: str = ""
    steps: list[StepIn]


@router.post("/playbooks")
def save_playbook(body: SavePlaybookRequest):
    from orivellum.capabilities.operations.playbooks import save_custom_playbook

    try:
        pb = save_custom_playbook(
            get_db(),
            body.title,
            [s.model_dump() for s in body.steps],
            description=body.description,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"playbook": pb}


@router.delete("/playbooks/{playbook_id}")
def delete_playbook(playbook_id: str):
    from orivellum.capabilities.operations.playbooks import PLAYBOOKS, delete_custom_playbook

    if any(p["id"] == playbook_id for p in PLAYBOOKS):
        raise HTTPException(409, "Built-in playbooks cannot be deleted.")
    if not delete_custom_playbook(get_db(), playbook_id):
        raise HTTPException(404, "Playbook not found")
    return {"ok": True}


class PlanRequest(BaseModel):
    job: str


@router.post("/plan")
def plan_operation(body: PlanRequest):
    """Plain-words job → proposed step plan (or a clarifying question).

    Always answers 200 with a ``status`` field of ``ok`` / ``clarify`` /
    ``error`` — the three outcomes are normal results for the UI, not HTTP
    failures. The plan is strictly validated against the action registry;
    the model gets one repair retry, never a silent guess.
    """
    from orivellum.capabilities.operations.planner import plan_job

    job = body.job.strip()
    if not job:
        raise HTTPException(422, "Describe the job first.")
    return plan_job(get_db(), get_config(), job)


@router.post("/start")
def start_operation(body: StartOperationRequest):
    from orivellum.capabilities.operations import store
    from orivellum.capabilities.operations.playbooks import get_playbook
    from orivellum.capabilities.operations.registry import get_op_registry
    from orivellum.capabilities.operations.runner import start_operation_run

    db = get_db()

    if body.playbook_id:
        pb = get_playbook(body.playbook_id, db)
        if not pb:
            raise HTTPException(404, f"Unknown playbook '{body.playbook_id}'")
        title = body.title or pb["title"]
        steps = pb["steps"]
        playbook_id = pb["id"]
    else:
        if not body.steps:
            raise HTTPException(422, "Provide either playbook_id or steps")
        title = body.title or "Custom operation"
        steps = [s.model_dump() for s in body.steps]
        playbook_id = None

    registry = get_op_registry()
    unknown = [s["action_id"] for s in steps if s["action_id"] not in registry]
    if unknown:
        raise HTTPException(422, f"Unknown action(s): {', '.join(unknown)}")

    params = dict(body.params)
    if body.work_id:
        params.setdefault("work_id", body.work_id)

    op_id = store.create_operation(
        db,
        title=title,
        steps=steps,
        work_id=body.work_id,
        playbook_id=playbook_id,
        params=params,
    )
    if not start_operation_run(db, get_config(), op_id):
        raise HTTPException(503, "The server is too busy to start the operation — try again.")
    return {"operation_id": op_id, "state": "running"}


@router.get("/{op_id}")
def get_operation(op_id: str):
    from orivellum.capabilities.operations import store

    db = get_db()
    op = store.get_operation(db, op_id)
    if not op:
        raise HTTPException(404, "Operation not found")
    return {**op, "steps": store.list_steps(db, op_id)}


@router.post("/{op_id}/pause")
def pause_operation(op_id: str):
    from orivellum.capabilities.operations import store

    db = get_db()
    if not store.get_operation(db, op_id):
        raise HTTPException(404, "Operation not found")
    if not store.request_pause(db, op_id):
        raise HTTPException(409, "Only a pending or running operation can be paused")
    return {"operation_id": op_id, "state": "paused"}


@router.post("/{op_id}/resume")
def resume_operation(op_id: str):
    from orivellum.capabilities.operations import store
    from orivellum.capabilities.operations.runner import start_operation_run

    db = get_db()
    op = store.get_operation(db, op_id)
    if not op:
        raise HTTPException(404, "Operation not found")
    if op["state"] == "running":
        return {"operation_id": op_id, "state": "running"}
    if op["state"] in ("done", "cancelled"):
        raise HTTPException(409, f"A {op['state']} operation cannot be resumed")
    # A failed run's steps are reset atomically inside the claim itself.
    if not start_operation_run(db, get_config(), op_id):
        raise HTTPException(503, "The server is too busy to resume the operation — try again.")
    return {"operation_id": op_id, "state": "running"}


@router.post("/{op_id}/cancel")
def cancel_operation(op_id: str):
    from orivellum.capabilities.operations import store

    db = get_db()
    if not store.get_operation(db, op_id):
        raise HTTPException(404, "Operation not found")
    if not store.request_cancel(db, op_id):
        raise HTTPException(409, "The operation already finished")
    return {"operation_id": op_id, "state": "cancelled"}
