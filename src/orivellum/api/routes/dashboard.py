"""Dashboard summary endpoints — /api/dashboard/*"""
from __future__ import annotations

from fastapi import APIRouter

from orivellum.api._deps import get_db

router = APIRouter(prefix="/api")


@router.get("/dashboard/summary")
def dashboard_summary():
    db = get_db()
    return db.dashboard_summary()


@router.get("/dashboard/activity")
def dashboard_activity(limit: int = 20):
    db = get_db()
    return {"activity": db.recent_activity(limit=min(limit, 100))}
