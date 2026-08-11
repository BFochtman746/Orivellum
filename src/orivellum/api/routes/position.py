"""POSITION — /api/works/{work_id}/position/*

Derive where an inherited manuscript truly stands.  The audit row is created
'running' under the write lock (the row IS the claim) before the background
dispatch; if the dispatch is refused the row is finished as 'error'
immediately — never a leaked 'running' row.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.api.executor import submit_bg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


@router.post("/works/{work_id}/position/run", status_code=202)
def start_position_audit(work_id: str):
    """Kick off the seven-step position audit in the background."""
    db = get_db()
    cfg = get_config()
    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    try:
        audit_id = db.create_position_audit(work_id)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e

    from orivellum.capabilities.position import run_position_audit  # noqa: PLC0415

    dispatched = submit_bg(
        run_position_audit, db, cfg,
        audit_id=audit_id, work_id=work_id,
        kind="position_audit", label=f"position:{work_id}",
    )
    if not dispatched:
        # We hold the claim — release it as an explicit failure.
        db.finish_position_audit(audit_id, status="error",
                                 error="background dispatch refused")
        raise HTTPException(503, "audit could not be dispatched; try again")
    return {"audit_id": audit_id, "status": "running"}


@router.get("/works/{work_id}/position")
def get_position(work_id: str):
    """Latest audit (with evidence + completion plan) and open proposals."""
    db = get_db()
    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    audits = db.list_position_audits(work_id, limit=1)
    proposals = db.list_position_proposals(work_id=work_id)
    return {
        "audit": audits[0] if audits else None,
        "proposals": proposals,
        "proposal_counts": _counts(proposals),
    }


def _counts(proposals: list[dict]) -> dict:
    counts: dict[str, dict[str, int]] = {}
    for p in proposals:
        bucket = counts.setdefault(p["kind"], {})
        bucket[p["status"]] = bucket.get(p["status"], 0) + 1
    return counts


@router.get("/works/{work_id}/position/audits")
def list_position_audits(work_id: str, limit: int = 20):
    db = get_db()
    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    return {"audits": db.list_position_audits(work_id, limit=limit)}


@router.get("/position/audits/{audit_id}")
def get_position_audit(audit_id: str):
    db = get_db()
    audit = db.get_position_audit(audit_id)
    if audit is None:
        raise HTTPException(404, f"audit {audit_id!r} not found")
    return {"audit": audit}
