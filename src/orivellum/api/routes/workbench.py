"""Project Workbench routes — /api/workbench/*

Create a project (xlsx or code) from a plain-language brief; the local AI
builds v1. Every further instruction builds the next version in the
background. Versions are immutable; revert copies an old version forward.
Completing a project archives every version + hash manifest as a zip.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.api.errors import internal_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/workbench", dependencies=[Depends(require_auth)])


# ── Bodies ────────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    kind: str                       # 'xlsx' | 'code'
    brief: str = Field(min_length=1, max_length=8000)


class IterateBody(BaseModel):
    instruction: str = Field(min_length=1, max_length=8000)


class RevertBody(BaseModel):
    version_no: int = Field(ge=1)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _project_out(p: dict, versions: list[dict] | None = None) -> dict:
    out = dict(p)
    out["building"] = bool(p.get("building"))
    if versions is not None:
        out["versions"] = [_version_out(v) for v in versions]
        out["version_count"] = len(versions)
    return out


def _version_out(v: dict) -> dict:
    return {
        "id": v["id"], "version_no": v["version_no"],
        "instruction": v["instruction"], "note": v.get("note") or "",
        "verdict": v.get("verdict"), "created_at": v["created_at"],
        "files": json.loads(v.get("files_json") or "[]"),
        "checks": json.loads(v.get("checks_json") or "{}"),
    }


def _get_or_404(db, project_id: str) -> dict:
    proj = db.get_wb_project(project_id)
    if not proj:
        raise HTTPException(404, "project not found")
    return proj


def _require_active(proj: dict) -> None:
    if proj["status"] != "active":
        raise HTTPException(409, "project is archived — archived projects are read-only")


def _start_build(project_id: str, instruction: str) -> None:
    from orivellum.api.executor import submit_bg

    def _run() -> None:
        from orivellum.capabilities.workbench import run_build
        run_build(get_db(), get_config(), project_id, instruction)

    submit_bg(_run, kind="workbench.build", label=f"workbench {project_id[:8]}")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/projects")
def list_projects(status: str | None = None):
    db = get_db()
    return {"projects": [_project_out(p) for p in db.list_wb_projects(status=status)]}


@router.post("/projects")
def create_project(body: ProjectCreate):
    from orivellum.capabilities.workbench import KINDS
    if body.kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {KINDS}")
    db = get_db()
    try:
        proj = db.create_wb_project(body.title.strip(), body.kind, body.brief.strip())
    except Exception as exc:                                      # noqa: BLE001
        raise internal_error(logger, exc, "workbench create") from exc
    db.claim_wb_build(proj["id"])  # fresh project — always succeeds
    _start_build(proj["id"], "Build the first version of this project from the brief.")
    proj = db.get_wb_project(proj["id"])
    return _project_out(proj, [])


@router.get("/projects/{project_id}")
def get_project(project_id: str):
    db = get_db()
    proj = _get_or_404(db, project_id)
    return _project_out(proj, db.list_wb_versions(project_id))


@router.post("/projects/{project_id}/iterate")
def iterate_project(project_id: str, body: IterateBody):
    db = get_db()
    proj = _get_or_404(db, project_id)
    _require_active(proj)
    if not db.claim_wb_build(project_id):
        raise HTTPException(409, "a build is already running for this project")
    _start_build(project_id, body.instruction.strip())
    return _project_out(db.get_wb_project(project_id))


@router.post("/projects/{project_id}/revert")
def revert_project(project_id: str, body: RevertBody):
    db, cfg = get_db(), get_config()
    proj = _get_or_404(db, project_id)
    _require_active(proj)
    if not db.get_wb_version(project_id, body.version_no):
        raise HTTPException(404, f"version v{body.version_no} not found")
    if not db.claim_wb_build(project_id):
        raise HTTPException(409, "a build is already running for this project")
    try:
        from orivellum.capabilities.workbench import revert_to
        row = revert_to(db, cfg, project_id, body.version_no)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except Exception as exc:                                      # noqa: BLE001
        raise internal_error(logger, exc, "workbench revert") from exc
    finally:
        db.update_wb_project(project_id, building=0)
    return _version_out(row)


@router.post("/projects/{project_id}/complete")
def complete_project(project_id: str):
    db, cfg = get_db(), get_config()
    proj = _get_or_404(db, project_id)
    _require_active(proj)
    if not db.claim_wb_build(project_id):
        raise HTTPException(409, "wait for the running build to finish first")
    try:
        from orivellum.capabilities.workbench import archive_project
        path = archive_project(db, cfg, project_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:                                      # noqa: BLE001
        raise internal_error(logger, exc, "workbench archive") from exc
    finally:
        db.update_wb_project(project_id, building=0)
    return {"archived": True, "archive_path": path}


@router.get("/projects/{project_id}/versions/{version_no}/download")
def download_version(project_id: str, version_no: int):
    db, cfg = get_db(), get_config()
    proj = _get_or_404(db, project_id)
    if not db.get_wb_version(project_id, version_no):
        raise HTTPException(404, f"version v{version_no} not found")
    try:
        from orivellum.capabilities.workbench import make_version_zip
        path = make_version_zip(cfg, project_id, version_no)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in proj["title"])[:40]
    return FileResponse(path, media_type="application/zip",
                        filename=f"{safe or 'project'}_v{version_no}.zip")


@router.get("/projects/{project_id}/archive/download")
def download_archive(project_id: str):
    db = get_db()
    proj = _get_or_404(db, project_id)
    path = proj.get("archive_path")
    if not path:
        raise HTTPException(404, "project has not been archived")
    import pathlib
    p = pathlib.Path(path)
    if not p.is_file():
        raise HTTPException(404, "archive file missing on disk")
    return FileResponse(p, media_type="application/zip", filename=p.name)


@router.delete("/projects/{project_id}")
def delete_project_route(project_id: str):
    db, cfg = get_db(), get_config()
    _get_or_404(db, project_id)
    if not db.claim_wb_build(project_id, require_active=False):
        raise HTTPException(409, "wait for the running build to finish first")
    from orivellum.capabilities.workbench import delete_project
    delete_project(db, cfg, project_id)
    return {"deleted": True}
