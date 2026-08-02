"""System routes — /api/system/*"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException
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

    threading.Thread(
        target=_worker, name="nightshift-run-now", daemon=True,
    ).start()
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


@router.get("/system/jobs")
def system_jobs():
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
