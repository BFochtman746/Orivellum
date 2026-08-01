"""System routes — /api/system/*"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config

logger = logging.getLogger(__name__)

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


@router.post("/suggestions/generate")
def generate_suggestions(work_id: str | None = Body(None), limit: int = Body(6)):
    """Generate personalised study/research suggestions from the knowledge base.

    Fetches a sample of knowledge items, groups them by topic, then either
    asks the configured LLM to propose the next study directions or falls back
    to a deterministic algorithm when the AI endpoint is unavailable.
    Clears suggestions older than 7 days before writing new ones.
    """
    db  = get_db()
    cfg = get_config()
    now = datetime.now(timezone.utc).isoformat()

    # ── Gather knowledge context ───────────────────────────────────────────────
    with db._lock:
        # Sample up to 40 recent/diverse knowledge items
        k_rows = db._conn.execute(
            """SELECT k.kind, k.text, w.title AS work_title
               FROM knowledge k
               LEFT JOIN works w ON w.id = k.work_id
               WHERE k.review_status != 'rejected'
               ORDER BY k.created_at DESC
               LIMIT 40""",
        ).fetchall()
        # Grab work titles for context
        work_rows = db._conn.execute(
            "SELECT id, title FROM works ORDER BY created_at DESC LIMIT 10"
        ).fetchall()

    if not k_rows:
        return {"suggestions": [], "generated": 0,
                "message": "Upload and process some documents first — your library is empty."}

    knowledge_lines = [
        f"[{r['kind']}] ({r['work_title'] or 'Library'}) {r['text'][:200]}"
        for r in k_rows
    ]
    works_list = ", ".join(r["title"] for r in work_rows) or "none yet"
    knowledge_block = "\n".join(knowledge_lines)

    # ── Try LLM ───────────────────────────────────────────────────────────────
    llm_suggestions: list[dict] | None = None
    try:
        import httpx
        probe = httpx.get(f"{cfg.serving.base_url}/models", timeout=2.0)
        if probe.status_code == 200:
            payload = {
                "model": cfg.serving.workhorse_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a research advisor. Given a user's knowledge base, "
                            "suggest specific topics they should study or research next. "
                            "Return ONLY valid JSON — a list of objects with keys: "
                            "title (short, ≤60 chars), rationale (1-2 sentences citing their existing "
                            "knowledge), effort (e.g. '1 hour', '2-3 hours'), kind (one of: "
                            "explore, deep_dive, practice, connect, gap). No markdown, no prose."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"My active works: {works_list}\n\n"
                            f"Sample of my knowledge base ({len(k_rows)} items):\n"
                            f"{knowledge_block}\n\n"
                            f"Suggest {limit} specific things I should study or explore next. "
                            "Focus on gaps, connections between topics, and logical next steps."
                        ),
                    },
                ],
                "temperature": 0.7,
                "max_tokens": 1200,
            }
            r = httpx.post(
                f"{cfg.serving.base_url}/chat/completions",
                json=payload, timeout=30,
            )
            if r.status_code == 200:
                raw = r.json()["choices"][0]["message"]["content"].strip()
                # Strip markdown code fences if present
                raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
                llm_suggestions = json.loads(raw)
    except Exception as exc:
        logger.warning("LLM suggestion generation failed, using fallback: %s", exc)

    # ── Fallback: deterministic extraction from knowledge ─────────────────────
    if not llm_suggestions:
        # Group by work/topic and propose exploration of less-covered areas
        from collections import Counter
        work_counts: Counter = Counter(
            r["work_title"] or "Library" for r in k_rows
        )
        kind_counts: Counter = Counter(r["kind"] for r in k_rows)
        seen_topics = set()
        fallback = []
        for r in k_rows:
            topic = (r["text"][:80]).split(".")[0].strip()
            if topic not in seen_topics and len(fallback) < limit:
                seen_topics.add(topic)
                fallback.append({
                    "title": f"Explore: {topic[:55]}",
                    "rationale": (
                        f"From your {r['work_title'] or 'Library'} documents — "
                        f"this concept appears in your knowledge base and has connections worth exploring."
                    ),
                    "effort": "1-2 hours",
                    "kind": "explore",
                })
        llm_suggestions = fallback

    # ── Prune stale suggestions ───────────────────────────────────────────────
    with db._lock:
        db._conn.execute(
            "DELETE FROM suggestions WHERE created_at < datetime('now','-7 days')"
        )
        db._conn.commit()

    # ── Persist new suggestions ───────────────────────────────────────────────
    new_rows: list[dict] = []
    with db._lock:
        for item in (llm_suggestions or [])[:limit]:
            sid = str(uuid.uuid4())
            meta = json.dumps({
                "rationale": item.get("rationale", ""),
                "effort":    item.get("effort", ""),
                "kind":      item.get("kind", "explore"),
            })
            db._conn.execute(
                "INSERT INTO suggestions(id,work_id,kind,text,meta,created_at) VALUES(?,?,?,?,?,?)",
                (sid, work_id, item.get("kind","explore"), item.get("title",""), meta, now),
            )
            new_rows.append({
                "id": sid, "work_id": work_id, "kind": item.get("kind","explore"),
                "text": item.get("title",""), "meta": json.loads(meta), "created_at": now,
            })
        db._conn.commit()

    return {"suggestions": new_rows, "generated": len(new_rows)}


@router.get("/system/jobs")
def system_jobs():
    """Return documents currently in-progress (not ready/error/no_text) and the last nightshift run."""
    db = get_db()
    with db._lock:
        docs = db._conn.execute(
            """SELECT d.id, d.title, d.source, d.readiness, d.work_id,
                      w.title AS work_title
               FROM documents d
               LEFT JOIN works w ON w.id = d.work_id
               WHERE d.readiness NOT IN ('ready', 'error', 'no_text')
               ORDER BY d.created_at DESC
               LIMIT 50"""
        ).fetchall()
        try:
            nightshift = db._conn.execute(
                "SELECT * FROM nightshift_runs ORDER BY ran_at DESC LIMIT 1"
            ).fetchone()
        except Exception:
            nightshift = None
    return {
        "jobs": [dict(d) for d in docs],
        "total": len(docs),
        "nightshift": dict(nightshift) if nightshift else None,
    }


@router.get("/system/user-memory")
def list_user_memory():
    db = get_db()
    try:
        with db._lock:
            rows = db._conn.execute(
                "SELECT id, key, value, source_conv_id, created_at FROM user_memory ORDER BY created_at DESC"
            ).fetchall()
        return {"memories": [dict(r) for r in rows]}
    except Exception:
        return {"memories": []}


@router.delete("/system/user-memory/{memory_id}")
def delete_user_memory(memory_id: str):
    db = get_db()
    try:
        with db._lock:
            db._conn.execute("DELETE FROM user_memory WHERE id=?", (memory_id,))
            db._conn.commit()
        return {"deleted": memory_id}
    except Exception as exc:
        raise HTTPException(500, f"Could not delete memory: {exc}")


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
    import datetime
    db = get_db()
    summary = db.dashboard_summary()
    now = datetime.datetime.now(datetime.timezone.utc)
    hour = now.hour
    if hour < 12:
        time_of_day = "morning"
    elif hour < 17:
        time_of_day = "afternoon"
    else:
        time_of_day = "evening"
    work_count = summary.get("work_count", 0)
    pending = summary.get("pending_task_count", 0)
    if work_count == 0:
        greeting = f"Good {time_of_day}. Your workspace is ready."
    elif pending > 0:
        greeting = f"Good {time_of_day}. You have {pending} task{'s' if pending != 1 else ''} pending across {work_count} work{'s' if work_count != 1 else ''}."
    else:
        greeting = f"Good {time_of_day}. Here's what's happening across your works."
    return {
        "date": now.date().isoformat(),
        "summary": summary,
        "greeting": greeting,
    }
