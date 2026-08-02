"""Works domain routes — /api/works/*"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from orivellum.api._deps import get_db

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
    cfg = get_config()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{cfg.serving.base_url}/chat/completions",
                json={"model": cfg.serving.model, "messages": [{"role": "user", "content": prompt}], "stream": False},
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            # Strip markdown fences if the model added them
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            parsed = json.loads(content)
            return {"questions": parsed["questions"][:count], "work_id": work_id}
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


@router.get("/works/{work_id}/graph")
def works_graph(work_id: str, limit: int = 50):
    """Return entity graph nodes and edges for a Work.

    Entities are sourced from knowledge items (kind=entity or relationship).
    This is a lightweight projection — not a full knowledge graph extraction.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    with db._lock:
        # Source entities from knowledge items
        kn_rows = db._conn.execute(
            """SELECT id, kind, text, subject, predicate, object, confidence
               FROM knowledge WHERE work_id=? AND kind IN ('entity','relationship') LIMIT ?""",
            (work_id, limit * 2),
        ).fetchall()

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_nodes: set[str] = set()

    for row in kn_rows:
        r = dict(row)
        if r["kind"] == "entity" and r["text"]:
            key = r["text"].lower()
            if key not in seen_nodes:
                seen_nodes.add(key)
                nodes.append({"id": r["id"], "label": r["text"], "type": "entity"})
        elif r["kind"] == "relationship" and r["subject"] and r["object"]:
            # Create nodes for subject and object if not already present
            for label in (r["subject"], r["object"]):
                key = label.lower()
                if key not in seen_nodes:
                    seen_nodes.add(key)
                    nodes.append({"id": f"auto-{key}", "label": label, "type": "concept"})
            edges.append({
                "source": f"auto-{r['subject'].lower()}",
                "target": f"auto-{r['object'].lower()}",
                "label": r["predicate"] or "relates to",
                "confidence": r["confidence"],
            })

    return {
        "work_id": work_id,
        "nodes": nodes[:limit],
        "edges": edges[:limit],
        "node_count": len(nodes),
        "edge_count": len(edges),
    }


@router.get("/works/{work_id}/gaps")
def works_gaps(work_id: str):
    """Return research gap analysis for a Work."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.gaps import detect_gaps
    report = detect_gaps(work_id, db)
    return {
        "work_id": report.work_id,
        "coverage_pct": report.coverage_pct,
        "total_chapters": report.total_chapters,
        "suggested_queries": report.suggested_queries,
        "evaluated_at": report.evaluated_at,
        "gaps": [
            {
                "kind": g.kind,
                "title": g.title,
                "description": g.description,
                "severity": g.severity,
                "metadata": g.metadata,
            }
            for g in report.gaps
        ],
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
