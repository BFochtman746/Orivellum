"""System routes — /api/system/*"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
from fastapi.responses import JSONResponse
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
    """List all registered capability tools from the capabilities registry.

    Returns every entry in ``CAPABILITY_REGISTRY`` enriched with a live
    ``status`` field that reflects whether required dependencies are reachable.
    """
    from orivellum.capabilities import CAPABILITY_REGISTRY
    cfg = get_config()
    lemonade_up = bool(getattr(getattr(cfg, "serving", None), "base_url", None))
    tavily_up = bool(os.getenv("TAVILY_API_KEY"))

    def _status(cap: dict) -> str:
        reqs = cap.get("requires", [])
        if not reqs:
            return "available"
        if "lemonade" in reqs and not lemonade_up:
            return "requires_lemonade"
        if "tavily" in reqs and not tavily_up:
            return "requires_api_key"
        if "external_api" in reqs:
            return "available"
        return "available"

    tools = [
        {**cap, "status": _status(cap)}
        for cap in CAPABILITY_REGISTRY
    ]
    return {"tools": tools, "count": len(tools)}


@router.get("/system/capabilities")
def system_capabilities():
    """List capability modules grouped by category with live status.

    Replaces the former hardcoded list.  Reads from the same
    ``CAPABILITY_REGISTRY`` as ``/system/tools``.
    """
    from orivellum.capabilities import CAPABILITY_REGISTRY
    cfg = get_config()
    lemonade_up = bool(getattr(getattr(cfg, "serving", None), "base_url", None))
    tavily_up = bool(os.getenv("TAVILY_API_KEY"))

    def _status(cap: dict) -> str:
        reqs = cap.get("requires", [])
        if not reqs:
            return "available"
        if "lemonade" in reqs and not lemonade_up:
            return "requires_lemonade"
        if "tavily" in reqs and not tavily_up:
            return "requires_api_key"
        return "available"

    capabilities = [
        {**cap, "status": _status(cap)}
        for cap in CAPABILITY_REGISTRY
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
        from orivellum.capabilities.llm import llm_call
        probe = httpx.get(f"{cfg.serving.base_url}/models", timeout=2.0)
        if probe.status_code == 200:
            messages = [
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
            ]
            result = llm_call(
                messages,
                base_url=cfg.serving.base_url, model=cfg.serving.workhorse_model,
                temperature=0.7, max_tokens=1200,
                timeout=30, purpose="system", db=db,
            )
            if result.ok and result.text is not None:
                raw = result.text.strip()
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
        _prune_cur = db._conn.execute(
            "DELETE FROM suggestions WHERE created_at < datetime('now','-7 days')"
        )
        _pruned = _prune_cur.rowcount
        db._conn.commit()
    if _pruned > 0:
        db.audit("system.suggestions_pruned", object_id=None, object_type="suggestion",
                 actor="system", detail=f"{_pruned} stale suggestions removed")

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
    if new_rows:
        db.audit("system.suggestions_generated", object_id=None, object_type="suggestion",
                 actor="system", detail=f"{len(new_rows)} suggestions")

    return {"suggestions": new_rows, "generated": len(new_rows)}


@router.post("/system/nightshift/run")
async def trigger_nightshift(background_tasks: BackgroundTasks):
    """Manually trigger a nightshift pass in the background."""
    from orivellum.capabilities.nightshift import run_nightshift
    db = get_db()
    cfg = get_config()
    background_tasks.add_task(run_nightshift, db, cfg)
    return {"ok": True, "message": "Nightshift started in background"}


def _last_nightshift_run(db) -> dict | None:
    """Return the newest nightshift_runs row as a dict, or None."""
    try:
        with db._lock:
            row = db._conn.execute(
                "SELECT * FROM nightshift_runs ORDER BY ran_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


@router.post("/system/nightshift/run-now")
def nightshift_run_now():
    """Trigger a nightshift pass on a daemon thread. 409 if one is running.

    The run slot is reserved atomically via ``try_start()`` BEFORE returning 200
    and before the worker thread is scheduled, so two near-simultaneous requests
    (or a request racing the 3AM daemon) can never both start a run.
    """
    import threading
    from orivellum.capabilities.nightshift import run_nightshift, try_start

    if not try_start():
        raise HTTPException(409, "Nightshift is already running")

    db = get_db()
    cfg = get_config()

    def _worker():
        try:
            run_nightshift(db, cfg, _preacquired=True)
        except Exception:
            logger.exception("Nightshift run-now worker crashed")

    try:
        from orivellum.api.executor import _tracked_submit as _ts_ns
        _ts_ns(_worker, kind="nightshift", label="nightshift_run_now")
    except Exception as _exc_ns:
        logger.warning("Executor unavailable for nightshift run-now, falling back to thread: %s",
                       _exc_ns)
        threading.Thread(target=_worker, name="nightshift-run-now", daemon=True).start()
    return {"started": True}


@router.get("/system/nightshift/status")
def nightshift_status():
    """Return the current run state plus a summary of the last recorded run."""
    from orivellum.capabilities.nightshift import get_status

    st = get_status()
    db = get_db()
    last = _last_nightshift_run(db)
    last_run = None
    if last:
        last_run = {
            "ran_at": last.get("ran_at"),
            "docs_processed": last.get("docs_processed"),
            "items_added": last.get("items_added"),
        }
    return {
        "running": bool(st.get("running")),
        "started_at": st.get("started_at"),
        "last_run": last_run,
    }


@router.get("/system/nightshift/last-report")
def nightshift_last_report():
    """Return the newest night report's markdown body plus run metadata."""
    db = get_db()
    last = _last_nightshift_run(db)
    if not last:
        return {"report_markdown": None}

    report_markdown: str | None = None
    report_path = last.get("report_path")
    if report_path:
        try:
            from pathlib import Path
            p = Path(report_path)
            if p.exists():
                report_markdown = p.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Could not read night report %s: %s", report_path, exc)

    return {
        "ran_at": last.get("ran_at"),
        "docs_processed": last.get("docs_processed"),
        "items_added": last.get("items_added"),
        "report_markdown": report_markdown,
    }


@router.get("/system/document-queue")
def system_document_queue():
    """Return documents currently in-progress (not ready/error/no_text), recently completed, and the last nightshift run."""
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
        # Recently completed: docs that became ready/errored in the last 30 min
        # Use the objects table timestamp (works share the same id via objects)
        try:
            recent = db._conn.execute(
                """SELECT d.id, d.title, d.source, d.readiness, d.work_id,
                          w.title AS work_title,
                          o.created_at AS completed_at
                   FROM documents d
                   LEFT JOIN works w ON w.id = d.work_id
                   LEFT JOIN objects o ON o.id = d.id
                   WHERE d.readiness IN ('ready', 'error', 'no_text')
                   AND o.created_at > datetime('now', '-30 minutes')
                   ORDER BY o.created_at DESC
                   LIMIT 10"""
            ).fetchall()
        except Exception:
            recent = []
        try:
            nightshift = db._conn.execute(
                "SELECT * FROM nightshift_runs ORDER BY ran_at DESC LIMIT 1"
            ).fetchone()
        except Exception:
            nightshift = None
    return {
        "jobs": [dict(d) for d in docs],
        "total": len(docs),
        "recently_done": [dict(d) for d in recent],
        "nightshift": dict(nightshift) if nightshift else None,
    }


class JobStateUpdateBody(BaseModel):
    from_state: str
    to_state: str
    actor: str = "system"
    detail: str | None = None


@router.patch("/system/jobs/{job_id}/state")
def update_job_state(job_id: str, body: JobStateUpdateBody):
    """Advance or return a job's lifecycle state via JOB_SM.

    The server enforces the declared state graph (queued→running→done/failed/
    cancelled; queued→cancelled).  Undeclared transitions return 422; open
    high/critical findings on the job return 409 with the blocker list.

    Body:
        from_state: current state (client's view, used for idempotency check)
        to_state:   desired new state
        actor:      who is requesting the change (default: "system")
        detail:     optional reason for the audit log
    """
    from orivellum.capabilities.state_machine import (
        InvalidTransitionError, BlockedTransitionError,
    )
    db = get_db()
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    # Idempotent: if already in to_state, return success without error.
    if job["state"] == body.to_state:
        return {"id": job_id, "state": job["state"], "changed": False}
    # Let update_job_state raise the typed exceptions — app.py maps them to HTTP.
    db.update_job_state(
        job_id,
        from_state=body.from_state,
        to_state=body.to_state,
        actor=body.actor,
        detail=body.detail,
    )
    updated = db.get_job(job_id)
    return {"id": job_id, "state": updated["state"] if updated else body.to_state, "changed": True}


@router.get("/system/jobs/{job_id}")
def get_job(job_id: str):
    """Return a single job by id."""
    db = get_db()
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


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
        _existed = False
        with db._lock:
            _row = db._conn.execute("SELECT id FROM user_memory WHERE id=?", (memory_id,)).fetchone()
            _existed = _row is not None
            db._conn.execute("DELETE FROM user_memory WHERE id=?", (memory_id,))
            db._conn.commit()
        if _existed:
            db.audit("user_memory.deleted", object_id=memory_id, object_type="user_memory", actor="user")
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
    db.set_setting("ai_extraction_enabled", "true" if body.enabled else "false", actor="user")
    return {"enabled": body.enabled, "ok": True}


# ── AI Re-ranking setting ──────────────────────────────────────────────────────

@router.get("/system/settings/ai-reranking")
def get_ai_reranking_setting():
    """Return whether LLM-powered listwise re-ranking is enabled for chat retrieval."""
    db = get_db()
    enabled = db.get_setting("ai_reranking_enabled", "false").lower() == "true"
    return {"enabled": enabled}


class AiRerankingUpdate(BaseModel):
    enabled: bool


@router.put("/system/settings/ai-reranking")
def set_ai_reranking_setting(body: AiRerankingUpdate):
    """Enable or disable LLM-powered re-ranking of retrieved passages before chat injection."""
    db = get_db()
    db.set_setting("ai_reranking_enabled", "true" if body.enabled else "false", actor="user")
    return {"enabled": body.enabled, "ok": True}


# ── Late-chunking setting ──────────────────────────────────────────────────────

@router.get("/system/settings/late-chunking")
def get_late_chunking_setting():
    """Return the late-chunking enable flag and last-known probe status.

    ``probe_status`` is one of:
    - ``"supported"``  — endpoint confirmed to return per-token embeddings
    - ``"not_supported"`` — endpoint confirmed to return flat (standard) embeddings
    - ``"untested"`` — no probe has been run since the last server start
    """
    from orivellum.capabilities.embeddings import _late_chunking_probe_cache
    db = get_db()
    enabled = db.get_setting("use_late_chunking", "false").lower() == "true"
    if _late_chunking_probe_cache is True:
        probe_status = "supported"
    elif _late_chunking_probe_cache is False:
        probe_status = "not_supported"
    else:
        probe_status = "untested"
    return {"enabled": enabled, "probe_status": probe_status}


class LateChunkingUpdate(BaseModel):
    enabled: bool


@router.put("/system/settings/late-chunking")
def set_late_chunking_setting(body: LateChunkingUpdate):
    """Enable or disable late-chunking for new document imports.

    When enabled, new documents are embedded using full-document token pooling
    (Jina AI late chunking, 2024) instead of independent per-chunk embedding.
    Each chunk's vector inherits its surrounding document context, improving
    semantic similarity search for short or ambiguous passages.

    Falls back silently to standard per-chunk embedding when the configured
    embeddings endpoint does not support per-token output.
    """
    db = get_db()
    db.set_setting("use_late_chunking", "true" if body.enabled else "false", actor="user")
    return {"enabled": body.enabled, "ok": True}


@router.post("/system/settings/late-chunking/probe")
def probe_late_chunking():
    """Run a live probe against the embeddings endpoint and cache the result.

    Sends a small request with ``return_token_embeddings=true`` and checks
    whether the response contains per-token (2-D) embeddings.  Use this after
    switching to a model that supports late chunking to confirm it works.

    Returns the probe result immediately; the cached value is also updated so
    ``GET /system/settings/late-chunking`` reflects it without another probe.
    """
    from orivellum.capabilities.embeddings import probe_late_chunking_support
    supported = probe_late_chunking_support(force=True)
    return {
        "supported": supported,
        "probe_status": "supported" if supported else "not_supported",
    }


# ── Vision model settings + probe ─────────────────────────────────────────────

@router.get("/system/settings/vision-model")
def get_vision_model_setting():
    """Return the configured vision model name (empty = use workhorse fallback)."""
    db  = get_db()
    cfg = get_config()
    stored = db.get_setting("vision_model", "")
    effective = stored or cfg.serving.vision_model or ""
    return {"model": effective, "stored": stored, "config_default": cfg.serving.vision_model}


class VisionModelUpdate(BaseModel):
    model: str  # empty string = use config default / workhorse fallback


@router.put("/system/settings/vision-model")
def set_vision_model_setting(body: VisionModelUpdate):
    """Persist a custom vision model name.  Empty string removes the override."""
    db = get_db()
    db.set_setting("vision_model", body.model.strip(), actor="user")
    return {"model": body.model.strip(), "ok": True}


# ── Context-window settings ────────────────────────────────────────────────────

@router.get("/system/settings/context-window")
def get_context_window_setting():
    """Return the effective context-window size (tokens) used for prompt trimming.

    Uses the same validation as the runtime resolver:
      effective = validated DB value (integer ≥ 512) → config default.
    Sub-512 or non-integer stored values are reported as invalid (stored=None)
    and the config default is used as the effective value, matching exactly
    what chat construction applies.
    """
    db  = get_db()
    cfg = get_config()
    stored_raw = db.get_setting("context_window", "")
    stored: int | None = None
    if stored_raw:
        try:
            val = int(stored_raw)
            if val >= 512:
                stored = val
        except ValueError:
            pass
    effective = stored if stored is not None else cfg.serving.context_window
    return {
        "context_window": effective,
        "stored": stored,
        "config_default": cfg.serving.context_window,
    }


class ContextWindowUpdate(BaseModel):
    context_window: int  # token count; must be a positive integer


@router.put("/system/settings/context-window")
def set_context_window_setting(body: ContextWindowUpdate):
    """Persist a custom context-window size (in tokens) for prompt trimming.

    This overrides the YAML / env-var default at runtime without a restart.
    The value is validated to be ≥ 512 tokens (below that, useful context
    would be impossible to include).
    """
    from fastapi import HTTPException as _HTTP
    if body.context_window < 512:
        raise _HTTP(422, "context_window must be ≥ 512 tokens")
    db = get_db()
    db.set_setting("context_window", str(body.context_window), actor="user")
    return {"context_window": body.context_window, "ok": True}


@router.get("/system/embeddings/status")
def embeddings_status():
    """Return the circuit-breaker state for the embeddings endpoint.

    Does not make a network call — just reads the in-process cooldown
    timestamp so the UI can show whether semantic search is degraded.
    """
    from orivellum.capabilities.embeddings import _unavailable_until
    import time
    circuit_open = _unavailable_until > time.monotonic()
    return {
        "circuit_open": circuit_open,
        "available_at": _unavailable_until if circuit_open else None,
    }


@router.post("/system/embeddings/probe")
def probe_embeddings():
    """Run a live test embed call and return ok/fail + dimensions.

    A successful probe resets the circuit breaker so subsequent searches
    immediately benefit from semantic ranking again.
    """
    from orivellum.capabilities.embeddings import embed_texts, _reset_circuit_breaker
    try:
        vecs = embed_texts(["semantic search health probe"], timeout=8)
        if vecs and len(vecs) > 0:
            _reset_circuit_breaker()
            return {"ok": True, "dims": len(vecs[0]),
                    "detail": f"Embedding returned {len(vecs[0])}-dimensional vector — semantic search is active."}
        return {"ok": False, "status": "empty",
                "detail": "Embedding call returned no vectors. Semantic search falls back to keyword-only."}
    except Exception as exc:
        return {"ok": False, "status": "error", "detail": str(exc)}


@router.get("/system/stats")
def system_stats():
    """Return high-level database statistics for the System settings page."""
    import os
    db = get_db()
    summary = db.dashboard_summary()
    try:
        db_size = os.path.getsize(db._path) if db._path else 0
    except Exception:
        db_size = 0
    return {
        "document_count":  summary.get("document_count", 0),
        "knowledge_count": summary.get("knowledge_count", 0),
        "work_count":      summary.get("work_count", 0),
        "db_size_bytes":   db_size,
    }


@router.post("/system/vision/probe")
def probe_vision_model():
    """Test whether the configured vision model accepts image inputs.

    Sends a 1×1 white JPEG (the smallest possible valid image) with a simple
    "What colour is this?" question.  The model must respond with something
    containing "white" or any colour word — we accept any non-empty reply as
    proof of vision support since returning *anything* from an image message
    confirms the model can process vision input.

    Returns:
        ok          — True when the model responded
        model       — model name that was tested
        response    — first 200 chars of the model's reply
        error       — error message if ok=False
    """
    import base64
    import io

    db  = get_db()
    cfg = get_config()

    # Resolve model (DB override → config vision_model → workhorse fallback)
    stored  = db.get_setting("vision_model", "")
    model   = stored or cfg.serving.vision_model or cfg.serving.workhorse_model

    # Build a 1×1 white JPEG — smallest meaningful vision payload
    try:
        from PIL import Image as _PIL
        buf = io.BytesIO()
        img = _PIL.new("RGB", (1, 1), color=(255, 255, 255))
        img.save(buf, format="JPEG")
        b64 = base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        # Pillow not available — use a hardcoded 1×1 white JPEG (from spec)
        _TINY_WHITE_JPEG = (
            "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
            "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgN"
            "DRgyIRwhMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIy"
            "MjIyMjL/wAARCAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAA"
            "AAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/EABQQAQAAAAAAAAAAAAAAAA"
            "AAAAP/2gAMAwEAAhEDEQA/AJAA/9k="
        )
        b64 = _TINY_WHITE_JPEG

    from orivellum.capabilities.llm import llm_call

    try:
        result = llm_call(
            [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What colour is this image? Reply in one word."},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
            base_url=cfg.serving.base_url,
            model=model,
            timeout=20,
            purpose="system.vision_probe",
            db=db,
        )
        if result.ok and result.text and result.text.strip():
            return {"ok": True, "model": model, "response": result.text.strip()[:200]}
        return {
            "ok": False,
            "model": model,
            "error": result.error or "Model returned an empty response — vision may not be supported",
        }
    except Exception as exc:
        return {"ok": False, "model": model, "error": str(exc)}


@router.get("/system/settings/image-gen")
def get_image_gen_setting():
    """Return the configured image generation URL (empty = use auto-detection)."""
    db = get_db()
    return {"url": db.get_setting("image_gen_url", "")}


class ImageGenUrlUpdate(BaseModel):
    url: str  # empty string = auto-detect


@router.put("/system/settings/image-gen")
def set_image_gen_setting(body: ImageGenUrlUpdate):
    """Set a custom image generation endpoint URL.  Empty string restores auto-detect."""
    db = get_db()
    db.set_setting("image_gen_url", body.url.strip(), actor="user")
    return {"url": body.url.strip(), "ok": True}


@router.get("/governance/audit-chain")
def governance_audit_chain():
    """Verify the integrity of the hash-chained audit ledger.

    Walks every chained audit row (those with ``row_hash IS NOT NULL``) in
    insertion order and recomputes the hash chain from scratch.  Returns
    immediately — the walk is O(n) in the number of chained rows.

    Response:
        ``{"ok": true, "checked_rows": N}`` when the chain is intact.
        ``{"ok": false, "reason": "...", "checked_rows": N}`` when any
        link is broken or a hash does not match.
    """
    db = get_db()
    # Count chained rows for the response.
    with db._lock:
        row = db._conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE row_hash IS NOT NULL"
        ).fetchone()
    checked = row[0] if row else 0
    ok, reason = db.verify_audit_chain()
    if ok:
        return {"ok": True, "checked_rows": checked, "status": "intact"}
    return JSONResponse(
        {"ok": False, "checked_rows": checked, "status": "broken", "reason": reason},
        status_code=200,   # 200 so the UI can render the broken state without an error
    )


@router.get("/governance/outbox")
def governance_outbox(pending_only: bool = True, limit: int = 100):
    """Return transactional outbox events.

    By default returns only undispatched (pending) events so the governance
    page can show backlog depth.  Pass ``pending_only=false`` to see all
    events including already-dispatched ones.
    """
    db = get_db()
    events = db.list_outbox(pending_only=pending_only, limit=min(limit, 500))
    return {"events": events, "count": len(events), "pending_only": pending_only}


@router.get("/governance/findings")
def governance_list_findings(
    object_id: str | None = None,
    state: str | None = None,
    severity: str | None = None,
    limit: int = 100,
):
    """List governance findings, optionally filtered by object, state, or severity.

    Query params:
        object_id: restrict to findings on this object
        state:     "open" or "resolved" (default: both)
        severity:  comma-separated severities, e.g. "high,critical"
        limit:     max rows (capped at 500)
    """
    db = get_db()
    min_severity: tuple | None = None
    if severity:
        min_severity = tuple(s.strip() for s in severity.split(",") if s.strip())
    findings = db.list_findings(
        object_id=object_id,
        state=state,
        min_severity=min_severity or None,
        limit=min(limit, 500),
    )
    return {"findings": findings, "count": len(findings)}


class FindingCreateBody(BaseModel):
    object_id: str
    object_type: str = "unknown"
    description: str
    kind: str = "issue"
    severity: str = "high"


@router.post("/governance/findings", status_code=201)
def governance_create_finding(body: FindingCreateBody):
    """Create a governance finding (blocker) on an object.

    A finding with severity ``high`` or ``critical`` will block forward
    state-machine transitions on *object_id* until it is resolved.
    """
    db = get_db()
    valid_severities = {"info", "warning", "high", "critical"}
    if body.severity not in valid_severities:
        raise HTTPException(400, f"severity must be one of {sorted(valid_severities)}")
    fid = db.create_finding(
        object_id=body.object_id,
        object_type=body.object_type,
        description=body.description,
        kind=body.kind,
        severity=body.severity,
    )
    finding = db.get_finding(fid)
    return finding


@router.get("/governance/findings/{finding_id}")
def governance_get_finding(finding_id: str):
    """Return a single finding by id."""
    db = get_db()
    finding = db.get_finding(finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found")
    return finding


@router.patch("/governance/findings/{finding_id}/resolve")
def governance_resolve_finding(finding_id: str):
    """Resolve (close) an open governance finding.

    Once resolved, the finding no longer blocks state-machine transitions.
    Returns the updated finding.
    """
    db = get_db()
    finding = db.get_finding(finding_id)
    if not finding:
        raise HTTPException(404, "Finding not found")
    if finding["state"] == "resolved":
        return finding  # idempotent — already resolved
    db.resolve_finding(finding_id, resolved_by="user")
    return db.get_finding(finding_id)


@router.get("/governance/pending")
def governance_pending(limit: int = 100):
    """Return AI-auto knowledge items awaiting human review, across all Works."""
    db = get_db()
    with db._lock:
        rows = db._conn.execute(
            """SELECT k.id, k.work_id, k.kind, k.text, k.subject, k.predicate, k.object,
                      k.confidence, k.review_status, k.source_doc_id, k.created_at,
                      w.title as work_title, d.title as doc_title
               FROM knowledge k
               LEFT JOIN works w ON w.id = k.work_id
               LEFT JOIN documents d ON d.id = k.source_doc_id
               WHERE k.review_status = 'ai_auto'
               ORDER BY k.created_at DESC
               LIMIT ?""",
            (min(limit, 500),),
        ).fetchall()
    items = [dict(r) for r in rows]
    return {"items": items, "count": len(items)}


@router.get("/governance/stats")
def governance_stats():
    """Return aggregate review-status counts across all knowledge items."""
    db = get_db()
    with db._lock:
        rows = db._conn.execute(
            "SELECT review_status, COUNT(*) as cnt FROM knowledge GROUP BY review_status"
        ).fetchall()
    stats = {r["review_status"]: r["cnt"] for r in rows}
    return {
        "pending":  stats.get("ai_auto", 0),
        "approved": stats.get("approved", 0),
        "rejected": stats.get("rejected", 0),
        "auto":     stats.get("auto", 0),
        "total":    sum(stats.values()),
    }


@router.get("/governance/conflicts")
def governance_conflicts(resolved: bool = False, limit: int = 100):
    """Return detected knowledge conflicts (contradiction pairs) for adjudication."""
    db = get_db()
    conflicts = db.list_conflicts(resolved=resolved, limit=limit)
    return {"conflicts": conflicts, "count": len(conflicts)}


class ConflictResolveBody(BaseModel):
    resolution: str  # "keep_a" | "keep_b" | "keep_both"


@router.post("/governance/conflicts/{conflict_id}/resolve")
def governance_resolve_conflict(conflict_id: str, body: ConflictResolveBody):
    """Resolve a conflict. keep_a/keep_b rejects the losing claim; keep_both dismisses."""
    if body.resolution not in ("keep_a", "keep_b", "keep_both"):
        raise HTTPException(400, "resolution must be keep_a, keep_b, or keep_both")
    db = get_db()
    ok = db.resolve_conflict(conflict_id, body.resolution)
    if not ok:
        raise HTTPException(404, f"Conflict {conflict_id!r} not found or already resolved")
    return {"ok": True, "id": conflict_id, "resolution": body.resolution}


@router.post("/governance/rescore")
def governance_rescore():
    """Manually trigger evidence re-scoring + contradiction detection across all active Works."""
    from orivellum.capabilities.evidence import rescore_work, detect_contradictions
    db = get_db()
    rescored = conflicts = 0
    for work in db.list_works(status="active")[:50]:
        try:
            rescored += rescore_work(work["id"], db)
            conflicts += detect_contradictions(work["id"], db)
        except Exception:
            pass
    return {"rescored": rescored, "new_conflicts": conflicts}


class BatchReviewBody(BaseModel):
    item_ids: list[str]
    status: str  # "approved" | "rejected"


@router.post("/governance/batch-review")
def governance_batch_review(body: BatchReviewBody):
    """Approve or reject multiple AI-auto knowledge items in one atomic operation.

    Only items currently in ``review_status = 'ai_auto'`` are affected —
    already-reviewed items are silently skipped.  Returns the count actually
    updated so the client can surface an accurate success message.
    """
    if body.status not in ("approved", "rejected"):
        raise HTTPException(400, f"Invalid status {body.status!r}. Must be 'approved' or 'rejected'.")
    if not body.item_ids:
        return {"updated": 0, "status": body.status, "total_requested": 0}

    db = get_db()
    updated = 0

    with db._lock:
        for item_id in body.item_ids[:500]:   # safety cap
            result = db._conn.execute(
                "UPDATE knowledge SET review_status=? WHERE id=? AND review_status='ai_auto'",
                (body.status, item_id),
            )
            updated += result.rowcount
        db._conn.commit()

    # Audit the batch operation so it appears in the audit log
    try:
        db.audit(
            f"knowledge.batch_{body.status}",
            object_id="batch",
            object_type="knowledge",
            actor="user",
            detail=f"batch_{body.status}: {updated}/{len(body.item_ids)} items",
        )
    except Exception:
        pass

    # Invalidate the in-process knowledge vector cache so the next semantic
    # search reflects the updated review_status values (approved items become
    # eligible; rejected items are filtered out).  This is necessary because
    # this endpoint writes review_status directly via raw SQL and bypasses the
    # update_knowledge_review_status helper that would otherwise bump the cache.
    if updated:
        try:
            from orivellum.capabilities.embeddings import bump_vector_cache_version
            bump_vector_cache_version(db._path, "knowledge")
        except Exception:
            pass

    return {"updated": updated, "status": body.status, "total_requested": len(body.item_ids)}


@router.get("/search")
def global_search(q: str, limit: int = 20, work_id: str | None = None):
    """Hybrid global search across knowledge items, document chunks, and chapters.

    Uses SQLite FTS for keyword matching plus recency boosting.
    Results are ranked and deduplicated by source.
    """
    db = get_db()
    if not q or len(q.strip()) < 2:
        raise HTTPException(400, "Query must be at least 2 characters")

    results: list[dict] = []

    # 1 — Knowledge items (FTS)
    try:
        with db._lock:
            q_sql = q.replace('"', "").strip()
            kn_base = """SELECT k.id, k.kind, k.text, k.confidence, k.work_id,
                               w.title as work_title
                        FROM knowledge_fts f
                        JOIN knowledge k ON k.id = f.item_id
                        LEFT JOIN works w ON w.id = k.work_id
                        WHERE knowledge_fts MATCH ?"""
            args = [q_sql]
            if work_id:
                kn_base += " AND k.work_id=?"
                args.append(work_id)
            kn_base += f" LIMIT {min(limit, 50)}"
            kn_rows = db._conn.execute(kn_base, args).fetchall()
        for r in kn_rows:
            results.append({
                "kind": "knowledge",
                "id": r["id"],
                "title": r["text"][:120],
                "snippet": r["text"],
                "work_id": r["work_id"],
                "work_title": r["work_title"],
                "confidence": r["confidence"],
                "item_kind": r["kind"],
            })
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).debug("knowledge FTS search failed: %s", exc)

    # 2 — Document chunks (FTS)
    try:
        chunk_results = db.search_chunks(q, work_id=work_id, limit=min(limit, 20))
        for r in chunk_results:
            results.append({
                "kind": "chunk",
                "id": r["id"],
                "title": r.get("doc_title") or r.get("id", "")[:20],
                "snippet": (r.get("text") or "")[:200],
                "work_id": r.get("work_id"),
                "doc_id": r.get("doc_id"),
            })
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).debug("chunk FTS search failed: %s", exc)

    # 3 — Document titles (LIKE fallback)
    try:
        with db._lock:
            doc_q = f"%{q}%"
            doc_args = [doc_q, doc_q]
            doc_sql = "SELECT id, title, kind, work_id FROM documents WHERE title LIKE ? OR source LIKE ? ORDER BY created_at DESC LIMIT 10"
            doc_rows = db._conn.execute(doc_sql, doc_args).fetchall()
        for r in doc_rows:
            results.append({
                "kind": "document",
                "id": r["id"],
                "title": r["title"] or r["id"][:20],
                "snippet": f"{r['kind']} document",
                "work_id": r["work_id"],
                "doc_id": r["id"],
            })
    except Exception as exc:
        import logging as _log
        _log.getLogger(__name__).debug("doc title search failed: %s", exc)

    # Deduplicate and cap
    seen: set[str] = set()
    unique: list[dict] = []
    for item in results:
        key = f"{item['kind']}:{item['id']}"
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return {
        "results": unique[:limit],
        "count": len(unique),
        "query": q,
    }


@router.get("/system/audit-log")
def get_audit_log(
    limit: int = 100,
    object_id: str | None = None,
    object_type: str | None = None,
    actor: str | None = None,
    operation: str | None = None,
    since: str | None = None,
):
    """Return recent audit-log entries, newest first.

    Query params:
      limit       — max rows (default 100, max 1000)
      object_id   — filter to a specific object UUID
      object_type — filter by type (document, work, knowledge, conversation)
      actor       — filter by actor (system, pipeline, user)
      operation   — substring match against operation name
      since       — ISO-8601 lower-bound timestamp (inclusive)
    """
    db = get_db()
    entries = db.list_audit_log(
        limit=limit,
        object_id=object_id,
        object_type=object_type,
        actor=actor,
        operation=operation,
        since=since,
    )
    return {"entries": entries, "count": len(entries)}


@router.get("/system/diagnostics")
async def run_diagnostics(vacuum: bool = False):
    """Run a full system diagnostic and return a structured report.

    Checks database integrity, orphaned records, stuck documents, configuration,
    service connectivity, data quality, nightshift status, and pipeline health.

    Pass ``?vacuum=true`` to also run SQLite VACUUM (compacts the database;
    takes a few seconds on large databases but is safe).

    The response includes a ``markdown_report`` field — a pre-formatted Markdown
    document you can copy and send to an AI assistant for a complete evaluation.
    """
    from starlette.concurrency import run_in_threadpool
    from orivellum.capabilities.diagnostics import run_full_diagnostic

    db = get_db()
    cfg = get_config()

    result = await run_in_threadpool(run_full_diagnostic, db, cfg, vacuum)
    return result


@router.get("/system/jobs")
def system_jobs(limit: int = 50):
    """Return recent background job status for the job dashboard."""
    from orivellum.api.executor import get_recent_jobs
    jobs = get_recent_jobs(limit=min(limit, 200))
    running = sum(1 for j in jobs if j["state"] == "running")
    failed  = sum(1 for j in jobs if j["state"] == "failed")
    return {"jobs": jobs, "running": running, "failed": failed, "total": len(jobs)}


@router.post("/system/jobs/{job_id}/retry")
def retry_job(job_id: str):
    """Re-queue a failed background job.

    Looks up the job in the in-memory dashboard registry and re-submits it
    using the original callable and arguments stored at submission time.

    Returns:
        200 — job re-queued; new_job_id is the id of the replacement entry.
        404 — no job with that id found in the dashboard registry.
        409 — job is not in state 'failed' (already running or done).
        501 — job pre-dates retry support (no stored callable).
    """
    from orivellum.api.executor import retry_job as _retry
    try:
        _retry(job_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    except RuntimeError as exc:
        raise HTTPException(501, str(exc))
    return {"ok": True, "retried_job_id": job_id}


@router.get("/system/llm-health")
def system_llm_health():
    """Probe the configured LLM server and configured models.

    Returns health status for the primary and (if configured) fallback models.
    Used by the UI to show a clear error instead of a broken spinner when the
    AI server is down.
    """
    cfg = get_config()
    db = get_db()

    def _probe_model(base_url: str, model_id: str) -> dict:
        """Send a minimal /chat/completions request and measure latency."""
        import time as _t, httpx as _hx
        t0 = _t.monotonic()
        try:
            r = _hx.post(
                f"{base_url}/chat/completions",
                json={"model": model_id, "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 1, "stream": False},
                timeout=5.0,
            )
            ok = r.status_code in (200, 201)
            return {"ok": ok, "status_code": r.status_code,
                    "latency_ms": int((_t.monotonic() - t0) * 1000),
                    "model": model_id, "error": None if ok else r.text[:200]}
        except Exception as exc:
            return {"ok": False, "status_code": None,
                    "latency_ms": int((_t.monotonic() - t0) * 1000),
                    "model": model_id, "error": str(exc)[:200]}

    primary = _probe_model(cfg.serving.base_url, cfg.serving.workhorse_model)

    # Fallback model — currently configured as the reasoner; in the future this
    # could be a dedicated smaller model.  Only probe if different from primary.
    fallback_model = db.get_setting("fallback_model", cfg.serving.reasoner_model)
    if fallback_model and fallback_model != cfg.serving.workhorse_model:
        fallback = _probe_model(cfg.serving.base_url, fallback_model)
    else:
        fallback = None

    overall = "ok" if primary["ok"] else ("degraded" if (fallback and fallback["ok"]) else "down")
    return {
        "overall": overall,
        "primary": primary,
        "fallback": fallback,
        "base_url": cfg.serving.base_url,
    }


@router.get("/system/hardware")
def system_hardware():
    """Return server hardware telemetry: CPU, RAM, disk, GPU (if available)."""
    import time as _time
    result: dict = {}

    # ── CPU / RAM / Disk (psutil) ─────────────────────────────────────────────
    try:
        import psutil
        result["cpu_percent"] = psutil.cpu_percent(interval=0.1)
        result["cpu_count"] = psutil.cpu_count(logical=True) or 1
        mem = psutil.virtual_memory()
        result["ram"] = {
            "used_gb": round(mem.used / 1e9, 2),
            "total_gb": round(mem.total / 1e9, 2),
            "percent": mem.percent,
        }
        try:
            import sys as _sys
            _disk_path = "C:\\" if _sys.platform == "win32" else "/"
            disk = psutil.disk_usage(_disk_path)
            result["disk"] = {
                "used_gb": round(disk.used / 1e9, 2),
                "total_gb": round(disk.total / 1e9, 2),
                "percent": disk.percent,
            }
        except Exception:
            result["disk"] = {"error": "unavailable"}
        # Uptime via boot time
        try:
            boot = psutil.boot_time()
            result["uptime_seconds"] = int(_time.time() - boot)
        except Exception:
            pass
    except ImportError:
        result["error"] = "psutil not installed"

    # ── GPU — nvidia-smi ──────────────────────────────────────────────────────
    gpu_info: list[dict] = []
    try:
        import subprocess as _sp
        r = _sp.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3:
                    gpu_info.append({
                        "name": parts[0],
                        "vram_used_mb": _safe_int(parts[1]),
                        "vram_total_mb": _safe_int(parts[2]),
                        "utilization_percent": _safe_int(parts[3]) if len(parts) > 3 else None,
                        "temp_c": _safe_int(parts[4]) if len(parts) > 4 else None,
                    })
    except Exception:
        pass

    # ── GPU — rocm-smi (AMD) ──────────────────────────────────────────────────
    if not gpu_info:
        try:
            import subprocess as _sp, json as _json
            r = _sp.run(["rocm-smi", "--json"], capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                data = _json.loads(r.stdout)
                for _k, v in data.items():
                    if isinstance(v, dict):
                        gpu_info.append({
                            "name": v.get("Card series", "AMD GPU"),
                            "vram_used_mb": None,
                            "vram_total_mb": None,
                            "utilization_percent": _safe_int(v.get("GPU use (%)", None)),
                            "temp_c": _safe_int(v.get("Temperature (Sensor edge) (°C)", None)),
                        })
        except Exception:
            pass

    result["gpus"] = gpu_info
    result["gpu_available"] = len(gpu_info) > 0
    return result


def _safe_int(v) -> int | None:
    try:
        return int(str(v).strip())
    except Exception:
        return None


@router.get("/system/access-log")
def get_access_log(limit: int = 200):
    """Return recent API request log entries."""
    db = get_db()
    try:
        rows = db.get_access_log(limit=limit)
        return {"entries": rows, "count": len(rows)}
    except Exception as exc:
        logger.warning("Access log unavailable: %s", exc)
        return {"entries": [], "count": 0, "error": str(exc)}


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


# ─── Watch directories CRUD ───────────────────────────────────────────────────


class _WatchDirEntry(BaseModel):
    path: str
    work_id: str | None = None
    enabled: bool = True


@router.get("/system/watch-dirs")
def list_watch_dirs():
    """Return the configured watch directories and last scan status."""
    from orivellum.capabilities.folder_watch import get_watch_dirs, get_watch_status
    db = get_db()
    dirs = get_watch_dirs(db)
    status = get_watch_status(db)
    # Merge per-dir status into the dir list
    status_by_path = {d.get("path"): d for d in status.get("dirs", [])}
    enriched = []
    for entry in dirs:
        ds = status_by_path.get(entry.get("path"), {})
        enriched.append({
            **entry,
            "last_scan_files_imported": ds.get("files_imported", 0),
            "last_scan_error": ds.get("error"),
        })
    return {
        "dirs": enriched,
        "scanned_at": status.get("scanned_at"),
    }


@router.post("/system/watch-dirs")
def add_watch_dir(entry: _WatchDirEntry):
    """Append a new watch directory to the list."""
    from orivellum.capabilities.folder_watch import get_watch_dirs, set_watch_dirs
    db = get_db()
    dirs = get_watch_dirs(db)
    # Reject duplicate paths
    if any(d.get("path") == entry.path for d in dirs):
        raise HTTPException(409, f"Directory already watched: {entry.path}")
    dirs.append({"path": entry.path, "work_id": entry.work_id, "enabled": entry.enabled})
    set_watch_dirs(dirs, db)
    return {"ok": True, "dirs": dirs}


@router.put("/system/watch-dirs/{index}")
def update_watch_dir(index: int, entry: _WatchDirEntry):
    """Update a watch directory entry by index."""
    from orivellum.capabilities.folder_watch import get_watch_dirs, set_watch_dirs
    db = get_db()
    dirs = get_watch_dirs(db)
    if index < 0 or index >= len(dirs):
        raise HTTPException(404, "Watch dir index out of range")
    dirs[index] = {"path": entry.path, "work_id": entry.work_id, "enabled": entry.enabled}
    set_watch_dirs(dirs, db)
    return {"ok": True, "dirs": dirs}


@router.delete("/system/watch-dirs/{index}")
def delete_watch_dir(index: int):
    """Remove a watch directory by index."""
    from orivellum.capabilities.folder_watch import get_watch_dirs, set_watch_dirs
    db = get_db()
    dirs = get_watch_dirs(db)
    if index < 0 or index >= len(dirs):
        raise HTTPException(404, "Watch dir index out of range")
    dirs.pop(index)
    set_watch_dirs(dirs, db)
    return {"ok": True, "dirs": dirs}


# ── Extraction templates ───────────────────────────────────────────────────────

_VALID_DOC_KINDS = {
    "pdf", "docx", "excel", "csv", "markdown", "text",
    "image", "audio", "pptx", "html", "json", "code", "file",
}


class ExtractionTemplateCreate(BaseModel):
    name: str
    system_prompt: str
    kind_label: str | None = None   # None = applies to any document kind
    field_hints: list[str] = []     # optional extraction hints shown to the model
    work_id: str | None = None      # None = applies to any Work


class ExtractionTemplateUpdate(BaseModel):
    name: str | None = None
    system_prompt: str | None = None
    kind_label: str | None = None
    field_hints: list[str] | None = None
    work_id: str | None = None
    clear_work_id: bool = False       # explicitly set work_id to NULL
    clear_kind_label: bool = False    # explicitly set kind_label to NULL


@router.get("/system/web-search-status")
def get_web_search_status():
    """Return whether the Tavily web-search integration is configured.

    The UI uses this to gate the per-conversation web-search toggle: when
    ``configured`` is false, the globe button is hidden and a setup prompt
    is shown instead so users know what they need to do.
    """
    import os
    configured = bool(os.environ.get("TAVILY_API_KEY", "").strip())
    return {"configured": configured, "provider": "tavily" if configured else None}


@router.get("/system/extraction-templates")
def list_extraction_templates(kind_label: str | None = None, work_id: str | None = None):
    """List all extraction templates, optionally filtered by kind or work."""
    db = get_db()
    templates = db.list_extraction_templates(kind_label=kind_label, work_id=work_id)
    return {"templates": templates, "count": len(templates)}


@router.post("/system/extraction-templates")
def create_extraction_template(body: ExtractionTemplateCreate):
    """Create a new named extraction template."""
    if body.kind_label and body.kind_label not in _VALID_DOC_KINDS:
        raise HTTPException(
            422,
            f"Unknown kind_label '{body.kind_label}'. "
            f"Valid values: {sorted(_VALID_DOC_KINDS)}",
        )
    if not body.name.strip():
        raise HTTPException(422, "name must not be blank")
    if not body.system_prompt.strip():
        raise HTTPException(422, "system_prompt must not be blank")
    db = get_db()
    template = db.create_extraction_template(
        name=body.name.strip(),
        system_prompt=body.system_prompt,
        kind_label=body.kind_label or None,
        field_hints=body.field_hints,
        work_id=body.work_id or None,
    )
    db.audit(
        "extraction_template.created",
        object_id=template["id"],
        object_type="extraction_template",
        actor="user",
        detail=f"name={template['name']!r} kind={template['kind_label']}",
    )
    return template


@router.get("/system/extraction-templates/{template_id}")
def get_extraction_template(template_id: str):
    """Return a single extraction template."""
    db = get_db()
    t = db.get_extraction_template(template_id)
    if not t:
        raise HTTPException(404, "Template not found")
    return t


@router.put("/system/extraction-templates/{template_id}")
def update_extraction_template(template_id: str, body: ExtractionTemplateUpdate):
    """Update an extraction template's fields."""
    db = get_db()
    existing = db.get_extraction_template(template_id)
    if not existing:
        raise HTTPException(404, "Template not found")
    kl = body.kind_label
    if kl is not None and kl not in _VALID_DOC_KINDS:
        raise HTTPException(
            422,
            f"Unknown kind_label '{kl}'. Valid values: {sorted(_VALID_DOC_KINDS)}",
        )
    updated = db.update_extraction_template(
        template_id,
        name=body.name,
        kind_label=kl,
        system_prompt=body.system_prompt,
        field_hints=body.field_hints,
        work_id=body.work_id,
        _clear_work_id=body.clear_work_id,
        _clear_kind_label=body.clear_kind_label,
    )
    db.audit(
        "extraction_template.updated",
        object_id=template_id,
        object_type="extraction_template",
        actor="user",
    )
    return updated


@router.delete("/system/extraction-templates/{template_id}")
def delete_extraction_template(template_id: str):
    """Delete an extraction template."""
    db = get_db()
    deleted = db.delete_extraction_template(template_id)
    if not deleted:
        raise HTTPException(404, "Template not found")
    db.audit(
        "extraction_template.deleted",
        object_id=template_id,
        object_type="extraction_template",
        actor="user",
    )
    return {"deleted": template_id, "ok": True}


@router.post("/system/extraction-templates/{template_id}/reharvest")
def reharvest_with_template(template_id: str, background_tasks: BackgroundTasks):
    """Re-run LLM harvest for all documents matching this template's kind/work scope.

    Queues background jobs for each matching document so the response returns
    immediately. Only runs when AI extraction is currently enabled.
    """
    import threading
    db = get_db()
    t = db.get_extraction_template(template_id)
    if not t:
        raise HTTPException(404, "Template not found")

    enabled = db.get_setting("ai_extraction_enabled", "false").lower() == "true"
    if not enabled:
        raise HTTPException(
            409,
            "AI extraction is disabled — enable it first under System → AI Extraction",
        )

    # Find documents that match this template's scope
    kind_label = t.get("kind_label")
    work_id = t.get("work_id")

    q = "SELECT d.id, d.source, d.kind, d.work_id, d.title FROM documents d WHERE d.readiness='ready'"
    args: list = []
    if kind_label:
        q += " AND d.kind=?"
        args.append(kind_label)
    if work_id:
        q += " AND d.work_id=?"
        args.append(work_id)
    q += " LIMIT 200"

    with db._lock:
        doc_rows = db._conn.execute(q, args).fetchall()

    docs = [dict(r) for r in doc_rows]
    if not docs:
        return {"queued": 0, "message": "No matching documents found"}

    def _run_reharvest(doc: dict) -> None:
        try:
            from orivellum.capabilities.knowledge_harvest import llm_harvest
            from orivellum.capabilities.extraction import ExtractionResult, PageSegment
            # Rebuild a minimal ExtractionResult from the stored extracted_text.
            with db._lock:
                row = db._conn.execute(
                    "SELECT extracted_text FROM documents WHERE id=?", (doc["id"],)
                ).fetchone()
            text = (row["extracted_text"] if row else "") or ""
            if not text:
                logger.debug(
                    "reharvest: doc %s has no extracted_text — skipping", doc["id"][:8]
                )
                return
            # Segment the text into ~2000-char chunks mirroring the normal pipeline.
            chunk_size = 2000
            segments = [
                PageSegment(page=i, text=text[i * chunk_size:(i + 1) * chunk_size])
                for i in range(0, max(1, (len(text) + chunk_size - 1) // chunk_size))
            ]
            er = ExtractionResult(
                kind=doc.get("kind") or "text",
                full_text=text,
                word_count=len(text.split()),
                pages=segments,
            )
            llm_harvest(
                er,
                doc_id=doc["id"],
                work_id=doc.get("work_id"),
                doc_title=doc.get("title") or doc["id"][:8],
                db=db,
                kind=doc.get("kind"),
            )
        except Exception as exc:
            logger.warning("reharvest failed for doc %s: %s", doc.get("id", "?"), exc)

    queued = 0
    for doc in docs:
        try:
            from orivellum.api.executor import _tracked_submit as _ts
            _ts(
                _run_reharvest, doc,
                kind="pipeline",
                label=f"reharvest:{doc['id'][:8]}",
            )
        except Exception:
            threading.Thread(target=_run_reharvest, args=(doc,), daemon=True).start()
        queued += 1

    db.audit(
        "extraction_template.reharvest",
        object_id=template_id,
        object_type="extraction_template",
        actor="user",
        detail=f"queued {queued} docs, kind={kind_label}, work_id={work_id}",
    )
    return {"queued": queued, "template_id": template_id, "ok": True}
