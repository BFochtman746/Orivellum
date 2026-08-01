"""System routes — /api/system/*"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

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


@router.get("/system/models")
def system_models():
    """Return the models configured in the serving section.

    The frontend uses this to populate the model picker.
    Each entry includes a short role label so the UI can show friendly names.
    """
    cfg = get_config()
    seen: set[str] = set()
    models = []
    for role, model_id in [
        ("workhorse", cfg.serving.workhorse_model),
        ("reasoner",  cfg.serving.reasoner_model),
        ("coder",     cfg.serving.coder_model),
    ]:
        if model_id and model_id not in seen:
            seen.add(model_id)
            models.append({
                "id": model_id,
                "role": role,
                "label": role.capitalize(),
                "description": {
                    "workhorse": "Default · fast, capable",
                    "reasoner":  "Deeper reasoning · slower",
                    "coder":     "Code generation · analysis",
                }.get(role, ""),
            })
    return {"models": models, "default": cfg.serving.workhorse_model}


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


@router.get("/system/settings/ai-extraction")
def get_ai_extraction_setting():
    """Return whether LLM-powered knowledge extraction is enabled."""
    db = get_db()
    enabled = db.get_setting("ai_extraction_enabled", "false").lower() == "true"
    return {"enabled": enabled}


class AiExtractionUpdate(BaseModel):
    enabled: bool


@router.put("/system/settings/ai-extraction")
def set_ai_extraction_setting(body: AiExtractionUpdate):
    """Enable or disable LLM-powered knowledge extraction for future document imports."""
    db = get_db()
    db.set_setting("ai_extraction_enabled", "true" if body.enabled else "false")
    return {"enabled": body.enabled, "ok": True}


@router.get("/briefing")
def get_briefing():
    db = get_db()
    summary = db.dashboard_summary()
    return {
        "date": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).date().isoformat(),
        "summary": summary,
        "greeting": "Good day. Here's what's happening across your works.",
    }
