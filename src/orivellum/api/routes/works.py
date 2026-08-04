"""Works domain routes — /api/works/*"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from orivellum.api._deps import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

WORK_TYPES = [
    {"id": "research", "label": "Research", "description": "Deep research and knowledge synthesis"},
    {"id": "writing", "label": "Writing", "description": "Books, essays, articles"},
    {"id": "learning", "label": "Learning", "description": "Structured learning and mastery"},
    {"id": "project", "label": "Project", "description": "Goals and deliverables"},
    {"id": "reference", "label": "Reference", "description": "Reference material and notes"},
]


class WorkCreate(BaseModel):
    title: str
    work_type: str = "research"
    description: str | None = None
    meta: dict[str, Any] = {}


class WorkUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    meta: dict[str, Any] | None = None


class TaskCreate(BaseModel):
    text: str
    priority: int = 0


class TaskUpdate(BaseModel):
    status: str | None = None
    text: str | None = None
    priority: int | None = None


@router.get("/works/types")
def works_list_types():
    return {"types": WORK_TYPES}


@router.get("/works")
def works_list(status: str | None = None, work_type: str | None = None):
    db = get_db()
    return {"works": db.list_works(status=status, work_type=work_type)}


@router.post("/works")
def works_create(body: WorkCreate):
    db = get_db()
    work = db.create_work(
        title=body.title,
        work_type=body.work_type,
        description=body.description,
        meta=body.meta,
    )
    return {"work": work}


@router.get("/works/{work_id}")
def works_get(work_id: str):
    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    return {"work": work}


@router.patch("/works/{work_id}")
def works_update(work_id: str, body: WorkUpdate):
    db = get_db()
    work = db.update_work(work_id, title=body.title, description=body.description,
                          status=body.status, meta=body.meta)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    return {"work": work}


@router.delete("/works/{work_id}")
def works_delete(work_id: str):
    db = get_db()
    ok = db.delete_work(work_id)
    if not ok:
        raise HTTPException(404, f"Work {work_id!r} not found")
    return {"ok": True}


@router.get("/works/{work_id}/documents")
def works_documents(work_id: str):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    docs = db.list_documents(work_id=work_id)
    return {"documents": docs, "count": len(docs)}


@router.get("/works/{work_id}/duplicates")
def works_duplicates(work_id: str, resolved: bool = False):
    """Return near-duplicate document pairs where at least one doc belongs to this Work."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    pairs = db.list_near_duplicates(resolved=resolved, work_id=work_id)
    return {"pairs": pairs, "count": len(pairs)}


@router.get("/works/{work_id}/knowledge")
def works_knowledge(work_id: str, kind: str | None = None):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    items = db.list_knowledge(work_id=work_id, kind=kind)
    return {"knowledge": items, "count": len(items)}


@router.post("/works/{work_id}/quiz")
async def generate_quiz(work_id: str, count: int = 5):
    """Generate multiple-choice quiz questions from a Work's knowledge base using the AI."""
    import asyncio, json, logging
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    items = db.list_knowledge(work_id=work_id, limit=20)
    if not items:
        raise HTTPException(422, "This Work has no knowledge items yet — import and process some documents first.")

    knowledge_text = "\n".join(
        f"- {it.get('kind','fact').upper()}: {it.get('text','')}" for it in items[:20]
    )
    work = db.get_work(work_id)
    title = (work.get("title") or "this topic") if work else "this topic"

    prompt = (
        f'You are an expert quiz generator. Based on the following knowledge items about "{title}", '
        f'generate exactly {count} multiple-choice questions that test real understanding. '
        'Each question must have exactly 4 options (A–D), one correct answer index (0-based), '
        'and a short explanation of why the correct answer is right.\n\n'
        'Return ONLY valid JSON with no markdown, no commentary, no code fences. '
        'Format:\n'
        '{"questions":[{"q":"Question?","options":["A text","B text","C text","D text"],"answer":0,"explanation":"..."}]}\n\n'
        f'Knowledge items:\n{knowledge_text}'
    )

    from orivellum.config import get_config
    from starlette.concurrency import run_in_threadpool
    from orivellum.capabilities.llm import llm_call
    cfg = get_config()
    try:
        result = await run_in_threadpool(
            llm_call,
            [{"role": "user", "content": prompt}],
            base_url=cfg.serving.base_url, model=cfg.serving.workhorse_model,
            timeout=60, purpose="works", db=db,
        )
        if not result.ok or result.text is None:
            raise HTTPException(503, "AI is unavailable. Start Lemonade or Ollama to generate quizzes.")
        content = result.text
        # Strip markdown fences if the model added them
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        parsed = json.loads(content)
        return {"questions": parsed["questions"][:count], "work_id": work_id}
    except HTTPException:
        raise
    except json.JSONDecodeError as exc:
        raise HTTPException(502, f"AI returned invalid JSON: {exc}")
    except Exception as exc:
        logging.getLogger("orivellum").warning("Quiz generation failed: %s", exc)
        raise HTTPException(503, "AI is unavailable. Start Lemonade or Ollama to generate quizzes.")


@router.get("/knowledge/ask")
def knowledge_ask(
    q: str = Query(..., description="Search query"),
    work_id: str | None = Query(None, description="Limit to a specific work"),
    limit: int = Query(12, le=50),
):
    """Cross-work knowledge and chunk search. Pass work_id to scope to one Work."""
    db = get_db()
    if not q.strip():
        return {"knowledge": [], "chunks": [], "query": q}
    try:
        knowledge = db.search_knowledge(q, work_id=work_id, limit=limit)
        chunks    = db.search_chunks(q,    work_id=work_id, limit=limit)
    except Exception as exc:
        raise HTTPException(500, f"Search failed: {exc}")
    return {
        "knowledge": [dict(r) for r in knowledge],
        "chunks":    [dict(r) for r in chunks],
        "query":     q,
        "total":     len(knowledge) + len(chunks),
        "work_id":   work_id,
    }


@router.get("/works/{work_id}/search")
def works_search(work_id: str, q: str, limit: int = 20):
    """Full-text search across a Work's knowledge items and document chunks."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    if not q.strip():
        return {"knowledge": [], "chunks": [], "query": q}
    try:
        knowledge = db.search_knowledge(q, work_id=work_id, limit=limit)
        chunks = db.search_chunks(q, work_id=work_id, limit=limit)
    except Exception as exc:
        raise HTTPException(500, f"Search failed: {exc}")
    return {
        "knowledge": knowledge,
        "chunks": chunks,
        "query": q,
        "total": len(knowledge) + len(chunks),
    }


@router.get("/works/{work_id}/tasks")
def works_tasks(work_id: str, status: str | None = None):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    tasks = db.list_tasks(work_id=work_id, status=status)
    return {"tasks": tasks, "count": len(tasks)}


@router.post("/works/{work_id}/tasks")
def works_create_task(work_id: str, body: TaskCreate):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    task = db.create_task(work_id, body.text, body.priority)
    return {"task": task}


@router.patch("/works/{work_id}/tasks/{task_id}")
def works_update_task(work_id: str, task_id: str, body: TaskUpdate):
    db = get_db()
    task = db.update_task(task_id, status=body.status, text=body.text, priority=body.priority)
    if not task:
        raise HTTPException(404, f"Task {task_id!r} not found")
    return {"task": task}


class KnowledgeCreate(BaseModel):
    text: str
    kind: str = "claim"
    subject: str | None = None
    predicate: str | None = None
    obj: str | None = None


@router.post("/works/{work_id}/knowledge")
def works_create_knowledge(work_id: str, body: KnowledgeCreate):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    item_id = db.create_knowledge_item(
        work_id=work_id,
        kind=body.kind,
        text=body.text.strip(),
        subject=body.subject,
        predicate=body.predicate,
        obj=body.obj,
        confidence=1.0,
        review_status="approved",
    )
    with db._lock:
        row = db._conn.execute("SELECT * FROM knowledge WHERE id=?", (item_id,)).fetchone()
    return {"item": dict(row) if row else {"id": item_id}}


@router.delete("/works/{work_id}/tasks/{task_id}", status_code=204)
def works_delete_task(work_id: str, task_id: str):
    db = get_db()
    ok = db.delete_task(task_id)
    if not ok:
        raise HTTPException(404, f"Task {task_id!r} not found")


@router.get("/works/{work_id}/conversations")
def works_conversations(work_id: str):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    convs = db.list_conversations(work_id=work_id)
    return {"conversations": convs}


@router.get("/works/{work_id}/stats")
def works_stats(work_id: str):
    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    with db._lock:
        doc_by_kind = db._conn.execute(
            "SELECT kind, COUNT(*) as n FROM documents WHERE work_id=? GROUP BY kind",
            (work_id,)
        ).fetchall()
        knowledge_by_kind = db._conn.execute(
            "SELECT kind, COUNT(*) as n FROM knowledge WHERE work_id=? GROUP BY kind",
            (work_id,)
        ).fetchall()
        task_by_status = db._conn.execute(
            "SELECT status, COUNT(*) as n FROM tasks WHERE work_id=? GROUP BY status",
            (work_id,)
        ).fetchall()
        conv_count = db._conn.execute(
            "SELECT COUNT(*) as n FROM conversations WHERE work_id=?",
            (work_id,)
        ).fetchone()["n"]
        doc_by_readiness = db._conn.execute(
            "SELECT readiness, COUNT(*) as n FROM documents WHERE work_id=? GROUP BY readiness",
            (work_id,)
        ).fetchall()
        try:
            mastery_row = db._conn.execute(
                "SELECT AVG(mastery) as avg_m, COUNT(*) as cnt FROM learning_concepts WHERE work_id=?",
                (work_id,)
            ).fetchone()
            avg_mastery = mastery_row["avg_m"] or 0.0
            concept_count = mastery_row["cnt"] or 0
        except Exception:
            avg_mastery, concept_count = 0.0, 0
    return {
        "work_id": work_id,
        "documents_by_kind": {r["kind"] or "unknown": r["n"] for r in doc_by_kind},
        "documents_by_readiness": {r["readiness"] or "unknown": r["n"] for r in doc_by_readiness},
        "knowledge_by_kind": {r["kind"]: r["n"] for r in knowledge_by_kind},
        "tasks_by_status": {r["status"]: r["n"] for r in task_by_status},
        "pending_task_count": sum(r["n"] for r in task_by_status if r["status"] not in ("completed", "done", "complete")),
        "conversation_count": conv_count,
        "avg_mastery_pct": round(avg_mastery * 100),
        "concept_count": concept_count,
    }


@router.get("/works/{work_id}/book-intelligence")
def works_book_intelligence(work_id: str):
    """Unified Knowledge Object view of a Work: canonical manuscript,
    manuscript versions, merged outline with per-chapter status and research
    counts, completeness dimensions, gaps, and the next recommended action.

    All data derives from existing extracted text, knowledge items, and
    book_chapters records — nothing is recomputed from source files.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.book_intelligence import build_book_intelligence
    try:
        return build_book_intelligence(work_id, db)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/works/{work_id}/chapters")
def works_chapters(work_id: str):
    """Return all book chapters extracted from documents linked to this Work.

    Results are grouped by document and ordered by document title then
    chapter sequence number.  Each chapter record includes ``word_count``
    (approximated from text), ``status``, and ``extraction_method``.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    with db._lock:
        rows = db._conn.execute(
            """SELECT bc.id, bc.seq, COALESCE(bc.level, 1) as level, bc.title,
                      (length(coalesce(bc.text,'')) - length(replace(coalesce(bc.text,''), ' ', '')) + 1) as word_count,
                      bc.status, bc.extraction_method, bc.created_at,
                      bc.source_doc_id,
                      d.title as doc_title
               FROM book_chapters bc
               JOIN documents d ON d.id = bc.source_doc_id
               WHERE bc.work_id = ?
               ORDER BY d.title, bc.seq""",
            (work_id,),
        ).fetchall()

    by_doc: dict[str, dict] = {}
    for r in rows:
        doc_id = r["source_doc_id"]
        if doc_id not in by_doc:
            by_doc[doc_id] = {"doc_id": doc_id, "doc_title": r["doc_title"] or "Untitled", "chapters": []}
        ch = dict(r)
        ch.pop("doc_title", None)
        by_doc[doc_id]["chapters"].append(ch)

    return {
        "work_id": work_id,
        "total_chapters": len(rows),
        "documents": list(by_doc.values()),
    }


# ─── Project Compass ───────────────────────────────────────────────────────────

class CompassUpdate(BaseModel):
    focus: str | None = None
    last_reasoning: str | None = None
    next_step: str | None = None


@router.get("/works/{work_id}/compass")
def get_compass(work_id: str):
    """Return the Project Compass state for a Work."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.cognition import read_compass
    compass = read_compass(db, work_id)
    return {"work_id": work_id, "compass": compass}


@router.get("/entities")
def list_entities(kind: str | None = None, limit: int = 200):
    """Return all entities across the workspace with mention counts."""
    db = get_db()
    entities = db.list_entities(kind=kind, limit=min(limit, 1000))
    return {"entities": entities, "count": len(entities)}


@router.get("/entities/{entity_id}")
def get_entity(entity_id: str):
    """Return a single entity with its document mention list."""
    db = get_db()
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM entities WHERE id=?", (entity_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, f"Entity {entity_id!r} not found")
    import json as _json
    entity = dict(row)
    try:
        entity["meta"] = _json.loads(entity.get("meta") or "{}")
    except Exception:
        entity["meta"] = {}
    with db._lock:
        mention_rows = db._conn.execute(
            """SELECT d.id, d.title, d.kind, d.work_id
               FROM relationships r
               JOIN documents d ON d.id = r.target_id
               WHERE r.source_id=? AND r.kind='MENTIONS'
               LIMIT 50""",
            (entity_id,),
        ).fetchall()
    entity["mentions"] = [dict(r) for r in mention_rows]
    entity["mention_count"] = len(entity["mentions"])
    return entity


@router.get("/works/{work_id}/graph")
def works_graph(work_id: str, limit: int = 100):
    """Return entity graph nodes and edges for a Work.

    Uses real entity/edge tables when populated, falls back to a
    knowledge-item projection for works processed before graph support.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    graph = db.get_work_graph(work_id, limit=min(limit, 200))
    return {"work_id": work_id, **graph}


@router.get("/gaps/top")
def workspace_top_gaps(limit: int = 3, refresh: bool = False):
    """Return the highest-severity research gaps across all active Works.

    Strategy (v50 cache):
    1. Read all non-stale (< 1 h) gap cache rows for active Works.
    2. For Works with no cache entry (or when ``refresh=True``), run detection
       now and write the results to the cache — capped at 10 Works to stay fast.
    3. Sort all results by severity and return the top ``limit`` entries.

    This makes the dashboard load in milliseconds on repeat visits while still
    providing fresh data when the cache is cold or the caller forces a refresh.
    """
    db = get_db()
    from orivellum.capabilities.gaps import detect_gaps

    works = db.list_works(status="active")
    work_by_id = {w["id"]: w for w in works}

    sev_order = {"high": 0, "medium": 1, "low": 2, "critical": -1}

    # ── 1. Load cached rows ────────────────────────────────────────────────────
    all_gaps: list[dict] = []
    cached_work_ids: set[str] = set()

    if not refresh:
        for cached in db.get_all_cached_gaps(max_age_seconds=3600):
            wid = cached["work_id"]
            if wid not in work_by_id:
                continue  # Work was deleted or deactivated
            cached_work_ids.add(wid)
            title = work_by_id[wid].get("title", "")
            for g in cached["gaps"]:
                all_gaps.append({
                    "work_id":    wid,
                    "work_title": title,
                    **{k: g.get(k, "") for k in ("kind", "title", "description", "severity", "metadata")},
                })

    # ── 2. Detect for uncached / stale Works (cap at 10 to stay fast) ─────────
    stale_works = [w for w in works if refresh or w["id"] not in cached_work_ids]
    for work in stale_works[:10]:
        try:
            report = detect_gaps(work["id"], db)
            gap_dicts = [
                {
                    "kind": g.kind, "title": g.title, "description": g.description,
                    "severity": g.severity, "metadata": g.metadata,
                }
                for g in report.gaps
            ]
            db.cache_work_gaps(work["id"], gap_dicts, report.coverage_pct)
            for g in gap_dicts:
                all_gaps.append({"work_id": work["id"], "work_title": work.get("title", ""), **g})
        except Exception as exc:
            logger.warning("Gap detection failed for work %s: %s", work.get("id"), exc)

    all_gaps.sort(key=lambda x: sev_order.get(x.get("severity", ""), 3))

    return {
        "gaps": all_gaps[:max(1, limit)],
        "total_works_analyzed": len(works),
        "cache_hits": len(cached_work_ids),
    }


@router.get("/works/{work_id}/gaps")
def works_gaps(work_id: str, refresh: bool = False):
    """Return research gap analysis for a Work.

    Uses the cache (max 1 h staleness) unless ``refresh=True``.
    Always writes fresh results to the cache after detection.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    from orivellum.capabilities.gaps import detect_gaps

    # Try cache first
    if not refresh:
        cached = db.get_cached_gaps(work_id, max_age_seconds=3600)
        if cached is not None:
            return {
                "work_id":         work_id,
                "coverage_pct":    cached["coverage_pct"],
                "total_chapters":  None,
                "suggested_queries": [],
                "evaluated_at":    cached["evaluated_at"],
                "gaps":            cached["gaps"],
                "from_cache":      True,
            }

    report = detect_gaps(work_id, db)
    gap_dicts = [
        {
            "kind": g.kind, "title": g.title, "description": g.description,
            "severity": g.severity, "metadata": g.metadata,
        }
        for g in report.gaps
    ]
    # Write back to cache
    try:
        db.cache_work_gaps(work_id, gap_dicts, report.coverage_pct)
    except Exception as exc:
        logger.debug("Gap cache write failed: %s", exc)

    return {
        "work_id":           report.work_id,
        "coverage_pct":      report.coverage_pct,
        "total_chapters":    report.total_chapters,
        "suggested_queries": report.suggested_queries,
        "evaluated_at":      report.evaluated_at,
        "gaps":              gap_dicts,
        "from_cache":        False,
    }


@router.get("/works/{work_id}/completeness")
def works_completeness(work_id: str):
    """Return multi-dimensional completeness scoring for a Work."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.completeness import calculate_work_completeness
    report = calculate_work_completeness(work_id, db)
    return {
        "work_id": report.work_id,
        "work_title": report.work_title,
        "overall": report.overall,
        "readiness": report.readiness,
        "summary": report.summary,
        "evaluated_at": report.evaluated_at,
        "dimensions": [
            {
                "name": d.name,
                "label": d.label,
                "score": d.score,
                "current": d.current,
                "target": d.target,
                "unit": d.unit,
                "rule": d.rule,
                "evidence": d.evidence,
            }
            for d in report.dimensions
        ],
    }


@router.patch("/works/{work_id}/compass")
def patch_compass(work_id: str, body: CompassUpdate):
    """Partial-update the Project Compass state for a Work.

    Only fields explicitly provided in the request body are written;
    omitted fields retain their current values.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.cognition import update_compass, read_compass
    # Pass keyword args so only non-None fields are set
    update_compass(
        db, work_id,
        focus=body.focus,
        reasoning=body.last_reasoning,
        next_step=body.next_step,
    )
    return {"work_id": work_id, "compass": read_compass(db, work_id)}


# ─── Book Pipeline ──────────────────────────────────────────────────────────────

class PipelineCreateRequest(BaseModel):
    title: str | None = None


@router.post("/works/{work_id}/pipeline")
def create_pipeline(work_id: str, body: PipelineCreateRequest = Body(default=PipelineCreateRequest())):
    """Create (or return existing) book pipeline for a Work, initialised at B0.

    Idempotent — calling multiple times returns the same pipeline.
    Orphan book_chapters already extracted for this Work are linked
    to the new pipeline automatically.
    """
    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    title = (body.title or "").strip() or work.get("title") or "Book Pipeline"
    pipeline = db.create_book_pipeline(work_id, title)
    return {"pipeline": pipeline}


@router.get("/works/{work_id}/pipeline")
def get_pipeline(work_id: str):
    """Return the current book pipeline state for a Work, or null if none exists.

    Enriches the DB row with computed ``stage_label``, ``next_status``, and
    ``chapters_total`` so clients don't need to hard-code the B-stage list.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    pipeline = db.get_book_pipeline_for_work(work_id)
    if pipeline:
        from orivellum.capabilities.state_machine import BOOK_SM, BOOK_STAGE_LABELS
        status = pipeline.get("status", "")
        pipeline["stage_label"] = BOOK_STAGE_LABELS.get(status, status)
        allowed = BOOK_SM.allowed_from(status)
        pipeline["next_status"] = next(iter(allowed)) if allowed else None
        pipeline["chapters_total"] = pipeline.get("chapter_count", 0)
    return {"pipeline": pipeline}


@router.post("/works/{work_id}/pipeline/advance")
def advance_pipeline(work_id: str):
    """Advance the book pipeline one stage forward through the B0–B17 lifecycle.

    Uses the M0.2 BOOK_SM state machine.  Returns 409 if open high/critical
    findings block the transition, with a ``blockers`` list in the body.
    Returns 422 if the pipeline is already at a terminal state.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    pipeline = db.get_book_pipeline_for_work(work_id)
    if not pipeline:
        raise HTTPException(404, "No pipeline for this Work — call POST /pipeline first")

    from orivellum.capabilities.state_machine import (
        BOOK_SM, apply_transition, InvalidTransitionError, BlockedTransitionError,
    )

    current = pipeline["status"]
    allowed = BOOK_SM.allowed_from(current)
    if not allowed:
        raise HTTPException(422, f"Pipeline is at terminal state {current!r} — no further transitions")

    # BOOK_SM is strictly sequential; exactly one next state
    next_state = next(iter(allowed))

    try:
        apply_transition(
            db,
            BOOK_SM,
            object_id=pipeline["id"],
            object_type="book_pipeline",
            table="book_pipelines",
            state_col="status",
            from_state=current,
            to_state=next_state,
            actor="user",
            detail=f"Manual advance: {current}→{next_state}",
        )
    except InvalidTransitionError as exc:
        raise HTTPException(422, str(exc))
    except BlockedTransitionError as exc:
        raise HTTPException(409, {"detail": str(exc), "blockers": exc.blockers})

    return {"pipeline": db.get_book_pipeline_for_work(work_id)}
