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
    """Return available models for the model picker.

    Fetches the live model list from the configured AI endpoint
    (GET /v1/models — standard OpenAI-compat format).  Falls back to the
    models declared in config if the endpoint is unreachable.

    Config-declared models (workhorse / reasoner / coder) are annotated with
    friendly role labels and descriptions so the UI can display them nicely.
    Any additional models returned by the live endpoint are included with a
    generic "available" label.
    """
    cfg = get_config()

    # Build role metadata from config so we can annotate live results
    role_meta: dict[str, dict] = {}
    for role, model_id in [
        ("workhorse", cfg.serving.workhorse_model),
        ("reasoner",  cfg.serving.reasoner_model),
        ("coder",     cfg.serving.coder_model),
    ]:
        if model_id and model_id not in role_meta:
            role_meta[model_id] = {
                "role": role,
                "label": role.capitalize(),
                "description": {
                    "workhorse": "Default · fast, capable",
                    "reasoner":  "Deeper reasoning · slower",
                    "coder":     "Code generation · analysis",
                }.get(role, ""),
            }

    # Try to fetch live models from the AI endpoint
    live_model_ids: list[str] = []
    try:
        import httpx
        r = httpx.get(f"{cfg.serving.base_url}/models", timeout=2.0)
        if r.status_code == 200:
            data = r.json()
            # OpenAI format: {"data": [{"id": "...", ...}, ...]}
            # Some servers return {"models": [...]} or a plain list
            raw_list = (
                data.get("data")
                or data.get("models")
                or (data if isinstance(data, list) else [])
            )
            for entry in raw_list:
                mid = entry.get("id") or entry.get("name") or str(entry)
                if mid:
                    live_model_ids.append(mid)
    except Exception:
        pass  # fall back to config-only below

    # If we got live models, build the final list from them
    if live_model_ids:
        seen: set[str] = set()
        models = []
        for mid in live_model_ids:
            if mid in seen:
                continue
            seen.add(mid)
            meta = role_meta.get(mid, {})
            models.append({
                "id": mid,
                "role": meta.get("role", "available"),
                "label": meta.get("label", mid.split("/")[-1]),
                "description": meta.get("description", ""),
            })
        return {"models": models, "default": cfg.serving.workhorse_model}

    # Fall back to config-declared models when AI endpoint is unavailable
    seen2: set[str] = set()
    models_fallback = []
    for model_id, meta in role_meta.items():
        if model_id in seen2:
            continue
        seen2.add(model_id)
        models_fallback.append({"id": model_id, **meta})
    return {"models": models_fallback, "default": cfg.serving.workhorse_model}


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
