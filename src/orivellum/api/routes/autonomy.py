"""AUTONOMY — /api/works/{work_id}/autonomy/* and /api/autonomy/*

Unattended draft → check → revise runs (M12).  Runs claim via the run row
and dispatch in the background; every pre-dispatch failure finishes the row
as 'error' — never a leaked 'running' row.  The runner NEVER signs a gate:
signature requirements halt the run and land in the review queue.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.api.executor import submit_bg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

_BOOL_KEYS = ("autonomy_enabled", "autonomy_nightshift_enabled")


class RunBody(BaseModel):
    max_chapters: int | None = Field(default=None, ge=1, le=50)
    max_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    max_tokens: int | None = Field(default=None, ge=0)
    halt_policy: str | None = Field(default=None, pattern="^(stop|continue)$")


@router.post("/works/{work_id}/autonomy/run", status_code=202)
def start_autonomy_run(work_id: str, body: RunBody):
    db = get_db()
    cfg = get_config()
    from orivellum.capabilities import autonomy  # noqa: PLC0415

    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    if not autonomy.enabled(db):
        raise HTTPException(409, "autonomy is disabled — enable it in settings first (kill switch)")
    budget = autonomy.budget_from_settings(db, body.model_dump(exclude_none=True))
    try:
        run_id = db.create_autonomy_run(work_id, budget)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e

    # We hold the claim from here on: every failure before a successful
    # dispatch must finish the row as 'error'.
    try:
        dispatched = submit_bg(
            autonomy.run_autonomy,
            db,
            cfg,
            run_id=run_id,
            work_id=work_id,
            kind="autonomy_run",
            label=f"autonomy:{work_id}",
        )
    except Exception as exc:
        db.finish_autonomy_run(run_id, status="error", stop_reason=str(exc))
        raise
    if not dispatched:
        db.finish_autonomy_run(run_id, status="error", stop_reason="background dispatch refused")
        raise HTTPException(503, "run could not be dispatched; try again")
    return {"run_id": run_id, "status": "running", "budget": budget}


@router.get("/works/{work_id}/autonomy")
def autonomy_overview(work_id: str):
    db = get_db()
    from orivellum.capabilities import autonomy  # noqa: PLC0415

    work = db.get_work(work_id)
    if work is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    try:
        meta = (
            work.get("meta")
            if isinstance(work.get("meta"), dict)
            else json.loads(work.get("meta") or "{}")
        )
    except (TypeError, ValueError):
        meta = {}
    return {
        "enabled": autonomy.enabled(db),
        "optin": meta.get("autonomy_optin") is True,
        "runs": db.list_autonomy_runs(work_id),
    }


class OptinBody(BaseModel):
    enabled: bool


@router.put("/works/{work_id}/autonomy/optin")
def set_autonomy_optin(work_id: str, body: OptinBody):
    db = get_db()
    work = db.get_work(work_id)
    if work is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    try:
        meta = (
            work.get("meta")
            if isinstance(work.get("meta"), dict)
            else json.loads(work.get("meta") or "{}")
        )
    except (TypeError, ValueError):
        meta = {}
    meta["autonomy_optin"] = bool(body.enabled)
    db.update_work(work_id, meta=meta)
    return {"work_id": work_id, "optin": bool(body.enabled)}


@router.get("/autonomy/runs/{run_id}")
def get_autonomy_run(run_id: str):
    db = get_db()
    run = db.get_autonomy_run(run_id)
    if run is None:
        raise HTTPException(404, f"autonomy run {run_id!r} not found")
    return run


@router.get("/autonomy/settings")
def get_autonomy_settings():
    db = get_db()
    from orivellum.capabilities.autonomy import SETTING_KEYS  # noqa: PLC0415

    out = {}
    for key, default in SETTING_KEYS.items():
        value = db.get_setting(key, default)
        out[key] = (value.lower() == "true") if key in _BOOL_KEYS else value
    return out


class SettingsBody(BaseModel):
    autonomy_enabled: bool | None = None
    autonomy_nightshift_enabled: bool | None = None
    autonomy_max_chapters: int | None = Field(default=None, ge=1, le=50)
    autonomy_max_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    autonomy_max_tokens: int | None = Field(default=None, ge=0)
    autonomy_halt_policy: str | None = Field(default=None, pattern="^(stop|continue)$")


@router.patch("/autonomy/settings")
def patch_autonomy_settings(body: SettingsBody):
    db = get_db()
    for key, value in body.model_dump(exclude_none=True).items():
        if key in _BOOL_KEYS:
            db.set_setting(key, "true" if value else "false")
        else:
            db.set_setting(key, str(value))
    return get_autonomy_settings()
