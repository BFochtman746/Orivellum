"""System routes — /api/system/*"""
from __future__ import annotations

from fastapi import APIRouter

from orivellum.api._deps import get_db, get_config

router = APIRouter(prefix="/api")


@router.get("/system/health")
def system_health():
    db = get_db()
    cfg = get_config()
    db_health = db.health()

    # Check AI service availability
    ai_status = "unknown"
    try:
        import httpx
        r = httpx.get(f"{cfg.serving.base_url}/models", timeout=2.0)
        ai_status = "ok" if r.status_code == 200 else "degraded"
    except Exception:
        ai_status = "unavailable"

    overall = "ok" if db_health["status"] == "ok" else "degraded"
    if ai_status == "unavailable":
        overall = "degraded"  # AI unavailable = degraded, not down

    return {
        "status": overall,
        "services": {
            "database": db_health,
            "ai": {"status": ai_status, "endpoint": cfg.serving.base_url},
        },
    }


@router.get("/system/tools")
def system_tools():
    """List registered capability tools (stub — populated as capabilities load)."""
    return {"tools": [], "count": 0}


@router.get("/system/capabilities")
def system_capabilities():
    """List capability modules and their status."""
    capabilities = [
        {"id": "pdf", "name": "PDF", "status": "available"},
        {"id": "docx", "name": "DOCX", "status": "available"},
        {"id": "excel", "name": "Excel", "status": "available"},
        {"id": "ocr", "name": "OCR", "status": "available"},
        {"id": "knowledge", "name": "Knowledge", "status": "available"},
        {"id": "voice", "name": "Voice/TTS", "status": "requires_lemonade"},
        {"id": "audiobook", "name": "Audiobook", "status": "requires_lemonade"},
        {"id": "imagegen", "name": "Image Generation", "status": "requires_lemonade"},
        {"id": "code", "name": "Code Execution", "status": "available"},
        {"id": "math", "name": "Math", "status": "available"},
    ]
    return {"capabilities": capabilities, "count": len(capabilities)}


@router.get("/suggestions")
def get_suggestions(work_id: str | None = None, limit: int = 5):
    db = get_db()
    q = "SELECT * FROM suggestions WHERE 1=1"
    args: list = []
    if work_id:
        q += " AND (work_id=? OR work_id IS NULL)"
        args.append(work_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(min(limit, 20))
    with db._lock:
        rows = db._conn.execute(q, args).fetchall()
    return {"suggestions": [dict(r) for r in rows]}


@router.get("/briefing")
def get_briefing():
    db = get_db()
    summary = db.dashboard_summary()
    return {
        "date": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).date().isoformat(),
        "summary": summary,
        "greeting": "Good day. Here's what's happening across your works.",
    }
