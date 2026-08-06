"""Adaptive learning API — /api/works/{work_id}/learning/*"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config

router = APIRouter(prefix="/api")


def _cfg():
    c = get_config()
    return c.serving.base_url, c.serving.workhorse_model


# ─── Pydantic models ───────────────────────────────────────────────────────────

class AssessBody(BaseModel):
    concept_id: str
    question: str
    answer: str


# ─── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/works/{work_id}/learning/summary")
def learning_summary(work_id: str):
    """Return aggregate mastery stats for the work (total, graduated, %)."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.learning import get_mastery_summary
    return get_mastery_summary(db, work_id)


@router.get("/works/{work_id}/learning/concepts")
def work_concepts(work_id: str):
    """List all learning concepts for the work with per-concept mastery state."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.learning import list_concepts
    return {"concepts": list_concepts(db, work_id)}


@router.post("/works/{work_id}/learning/seed")
async def learning_seed(work_id: str):
    """Auto-seed concept nodes from the Work's knowledge subjects (idempotent)."""
    import asyncio
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    base_url, model = _cfg()
    from orivellum.capabilities.learning import seed_concepts
    concepts = await asyncio.to_thread(seed_concepts, db, work_id, base_url, model)
    return {"seeded": len(concepts), "concepts": concepts}


@router.get("/works/{work_id}/learning/question")
async def learning_question(work_id: str, concept_id: str | None = None):
    """Generate a Socratic question for the given concept (or the next unmastered one)."""
    import asyncio
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.learning import next_concept_id, get_question
    if not concept_id:
        concept_id = next_concept_id(db, work_id)
    if not concept_id:
        raise HTTPException(422, "No ungraduated concepts — all mastered or none seeded yet.")
    # Validate concept belongs to this work (prevents cross-Work access)
    with db._lock:
        row = db._conn.execute(
            "SELECT subject, description, work_id FROM work_concepts WHERE id=?", (concept_id,)
        ).fetchone()
    if not row or row["work_id"] != work_id:
        raise HTTPException(404, f"Concept {concept_id!r} not found in work {work_id!r}")
    base_url, model = _cfg()
    result = await asyncio.to_thread(get_question, db, concept_id, base_url, model)
    result["concept_id"]  = concept_id
    result["subject"]     = row["subject"]
    result["description"] = row["description"] or ""
    return result


@router.post("/works/{work_id}/learning/reset")
def learning_reset_all(work_id: str):
    """Reset ALL mastery streaks for this Work so the user can re-study from scratch."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.learning import reset_mastery
    deleted = reset_mastery(db, work_id)
    return {"reset": deleted, "scope": "all"}


@router.post("/works/{work_id}/learning/concepts/{concept_id}/reset")
def learning_reset_concept(work_id: str, concept_id: str):
    """Reset mastery streak for a single concept so it can be re-studied."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    with db._lock:
        row = db._conn.execute(
            "SELECT work_id FROM work_concepts WHERE id=?", (concept_id,)
        ).fetchone()
    if not row or row["work_id"] != work_id:
        raise HTTPException(404, f"Concept {concept_id!r} not found in work {work_id!r}")
    from orivellum.capabilities.learning import reset_mastery
    deleted = reset_mastery(db, work_id, concept_id)
    return {"reset": deleted, "concept_id": concept_id, "scope": "concept"}


@router.get("/works/{work_id}/learning/due")
def learning_due(work_id: str):
    """Return concepts with next_review_at <= now, sorted by urgency (most overdue first).

    Use this to populate a 'Due for review' section in the UI.  Each concept carries
    the standard mastery fields plus HLR fields: half_life_days, next_review_at,
    review_session_count, and is_due=True.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.learning import list_due_concepts
    due = list_due_concepts(db, work_id)
    return {"due": due, "count": len(due)}


@router.post("/works/{work_id}/learning/assess")
async def learning_assess(work_id: str, body: AssessBody):
    """Score the student's answer, update streak, and return routing decision."""
    import asyncio
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    if not body.answer.strip():
        raise HTTPException(422, "Answer cannot be empty")
    # Validate concept belongs to this work (prevents cross-Work mutation)
    with db._lock:
        concept_row = db._conn.execute(
            "SELECT prereq_id, work_id FROM work_concepts WHERE id=?", (body.concept_id,)
        ).fetchone()
    if not concept_row or concept_row["work_id"] != work_id:
        raise HTTPException(404, f"Concept {body.concept_id!r} not found in work {work_id!r}")
    base_url, model = _cfg()
    from orivellum.capabilities.learning import assess_answer, next_concept_id, get_mastery_summary
    result = await asyncio.to_thread(
        assess_answer, db, body.concept_id, body.question, body.answer, base_url, model
    )
    # Attach summary + next concept hint
    result["summary"]         = get_mastery_summary(db, work_id)
    if result["route"] == "STEP_FORWARD":
        result["next_concept_id"] = next_concept_id(db, work_id)
    elif result["route"] == "STEP_BACKWARD":
        result["next_concept_id"] = concept_row["prereq_id"] or body.concept_id
    else:
        result["next_concept_id"] = body.concept_id
    return result
