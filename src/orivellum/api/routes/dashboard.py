"""Dashboard summary endpoints — /api/dashboard/*"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from orivellum.api._deps import get_db, require_auth

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


@router.get("/dashboard/summary")
def dashboard_summary():
    db = get_db()
    return db.dashboard_summary()


@router.get("/dashboard/activity")
def dashboard_activity(limit: int = 20):
    db = get_db()
    return {"activity": db.recent_activity(limit=min(limit, 100))}


# ── Proactive custodian nudges ─────────────────────────────────────────────────


@router.get("/dashboard/nudges")
def get_nudges(limit: int = 5):
    """Return top unresolved work-staleness nudges for the dashboard."""
    db = get_db()
    try:
        from orivellum.capabilities.custodian import get_top_nudges

        nudges = get_top_nudges(db, limit=min(limit, 20))
    except Exception:
        nudges = []
    return {"nudges": nudges, "total": len(nudges)}


class ResolveBody(BaseModel):
    nudge_id: str


@router.post("/dashboard/nudges/resolve")
def resolve_nudge(body: ResolveBody):
    """Mark a nudge as resolved so it no longer appears on the dashboard."""
    db = get_db()
    try:
        from orivellum.capabilities.custodian import resolve_nudge

        ok = resolve_nudge(db, body.nudge_id)
    except Exception:
        ok = False
    return {"ok": ok}


@router.post("/dashboard/nudges/rebuild")
def rebuild_nudges():
    """Trigger a synchronous custodian pass (for manual testing / refresh)."""
    db = get_db()
    try:
        from orivellum.capabilities.custodian import run_custodian

        result = run_custodian(db)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, **result}
