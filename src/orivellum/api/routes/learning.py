"""Adaptive learning API — /api/works/{work_id}/learning/*"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_config, get_db, require_auth

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
def _cfg():
    c = get_config()
    return c.serving.base_url, c.serving.workhorse_model


# ─── Pydantic models ───────────────────────────────────────────────────────────

class AssessBody(BaseModel):
    concept_id: str
    question: str
    answer: str
    question_type: str = "recall"   # "recall" | "transfer" — echoed from the question endpoint
    session_mode: str = "blocked"   # "blocked" | "interleaved" — echoed from the session


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
async def learning_question(
    work_id: str,
    concept_id: str | None = None,
    type: str = "auto",
    mode: str = "blocked",
):
    """Generate a Socratic question for the given concept (or the next unmastered one).

    ?type=recall|transfer|auto  (default: auto)
      - recall   — Socratic question grounded in source material (classic mode)
      - transfer — Application question using a novel scenario not in the notes
      - auto     — recall until streak ≥ 2 consecutive passes, then transfer

    ?mode=blocked|interleaved  (default: blocked)
      - blocked     — standard mode; studies one concept at a time
      - interleaved — picks from 2-4 in-progress concepts randomly (weighted by urgency
                      and mastery weakness); requires ≥ 3 in-progress concepts
    """
    import asyncio
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.learning import (
        _INTERLEAVED_MIN_CONCEPTS,
        _VALID_QUESTION_TYPES,
        _VALID_SESSION_MODES,
        get_question,
        next_concept_id,
        select_interleaved_concept,
    )
    if type not in _VALID_QUESTION_TYPES:
        raise HTTPException(422, f"Invalid type {type!r}. Must be one of: recall, transfer, auto")
    if mode not in _VALID_SESSION_MODES:
        raise HTTPException(422, f"Invalid mode {mode!r}. Must be one of: blocked, interleaved")

    if mode == "interleaved":
        # Ignore caller-supplied concept_id; let weighted selection choose
        concept_id = select_interleaved_concept(db, work_id)
        if concept_id is None:
            raise HTTPException(
                422,
                f"Interleaved mode requires at least {_INTERLEAVED_MIN_CONCEPTS} in-progress "
                "concepts. Keep studying in blocked mode until more concepts are underway.",
            )
    else:
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
    result = await asyncio.to_thread(get_question, db, concept_id, base_url, model, type)
    result["concept_id"]   = concept_id
    result["concept_name"] = row["subject"]     # always present so UI can reveal post-answer
    result["subject"]      = row["subject"]
    result["description"]  = row["description"] or ""
    result["session_mode"] = mode
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


@router.get("/works/{work_id}/learning/graph")
def learning_graph(work_id: str):
    """Return the prerequisite dependency graph as {nodes, edges} for UI rendering.

    Nodes contain mastery state, graph metadata (prereqs_met, blocking_count, prereq_ids).
    Edges encode prerequisite relationships: source requires target to be started first.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.learning import list_concepts
    concepts = list_concepts(db, work_id)

    nodes = [
        {
            "id":                 c["id"],
            "subject":            c["subject"],
            "description":        c.get("description") or "",
            "graduated":          c["graduated"],
            "consecutive_passes": c["consecutive_passes"],
            "prereqs_met":        c.get("prereqs_met", True),
            "prereq_ids":         c.get("prereq_ids", []),
            "prereq_labels":      c.get("prereq_labels", []),
            "blocking_count":     c.get("blocking_count", 0),
            "is_due":             c.get("is_due", False),
            "half_life_days":     c.get("half_life_days", 1.0),
        }
        for c in concepts
    ]
    edges = [
        {"source": c["id"], "target": pid, "type": "requires"}
        for c in concepts
        for pid in c.get("prereq_ids", [])
    ]
    return {"nodes": nodes, "edges": edges,
            "node_count": len(nodes), "edge_count": len(edges)}


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


@router.get("/works/{work_id}/learning/analytics")
def learning_analytics(work_id: str):
    """Return learning analytics for the Analytics panel.

    Computed from existing work_mastery + work_concepts tables.
    Response shape:
      velocity           — [{ week, graduated }]  4-week sparkline
      stuck              — [{ concept_id, subject, fail_count, error_types }]
      retention_forecast — [{ concept_id, subject, next_review_at, days_overdue, half_life_days }]
      session_history    — [{ concept_id, subject, score, question_type, error_type, date }]
      distribution       — { not_started, in_progress, graduated, due_for_review, total }
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.learning import get_learning_analytics
    return get_learning_analytics(db, work_id)


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
    from orivellum.capabilities.learning import (
        _VALID_SESSION_MODES,
        _resolve_question_type,
        assess_answer,
        get_mastery_summary,
        next_concept_id,
    )
    # Re-derive question_type server-side from the concept's current streak using
    # the same "auto" logic as get_question.  This prevents clients from forging a
    # transfer question_type to obtain +2 mastery bonus on a recall question.
    # A concept at streak ≥ _TRANSFER_STREAK_THRESHOLD gets "transfer"; below → "recall".
    qt = _resolve_question_type(db, body.concept_id, "auto")
    smode = body.session_mode if body.session_mode in _VALID_SESSION_MODES else "blocked"
    result = await asyncio.to_thread(
        assess_answer, db, body.concept_id, body.question, body.answer,
        base_url, model, qt, smode,
    )
    # Attach summary + next concept hint
    result["summary"]         = get_mastery_summary(db, work_id)
    if result["route"] == "STEP_FORWARD":
        result["next_concept_id"]        = next_concept_id(db, work_id)
        result["suggested_prereq_id"]    = None
        result["suggested_prereq_subject"] = None
    elif result["route"] == "STEP_BACKWARD":
        # Pick the weakest-mastery prerequisite using the latest mastery row per prereq.
        # Window-function ranking by created_at DESC, rowid DESC is deterministic even
        # when two records share the same timestamp (rowid is monotonically increasing).
        from orivellum.capabilities.learning import get_prereq_ids
        prereq_ids = get_prereq_ids(db, body.concept_id)
        if prereq_ids:
            _ph = ",".join("?" * len(prereq_ids))
            with db._lock:
                _rows = db._conn.execute(
                    f"""WITH ranked AS (
                            SELECT concept_id,
                                   consecutive_passes,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY concept_id
                                       ORDER BY created_at DESC, rowid DESC
                                   ) AS rn
                            FROM work_mastery
                            WHERE concept_id IN ({_ph})
                        )
                        SELECT concept_id, consecutive_passes
                        FROM ranked WHERE rn = 1""",
                    tuple(prereq_ids),
                ).fetchall()
            _passes = {r["concept_id"]: r["consecutive_passes"] for r in _rows}
            # Concepts absent from work_mastery have 0 passes — pick the weakest
            weakest_prereq_id = min(prereq_ids, key=lambda pid: _passes.get(pid, 0))
            result["next_concept_id"] = weakest_prereq_id
        else:
            weakest_prereq_id = concept_row["prereq_id"] or body.concept_id
            result["next_concept_id"] = weakest_prereq_id

        # Resolve subject name for the suggested prereq so the UI can name it
        result["suggested_prereq_id"] = weakest_prereq_id
        with db._lock:
            _prereq_row = db._conn.execute(
                "SELECT subject FROM work_concepts WHERE id=?", (weakest_prereq_id,)
            ).fetchone()
        result["suggested_prereq_subject"] = (
            _prereq_row["subject"] if _prereq_row else None
        )
    else:
        result["next_concept_id"]        = body.concept_id
        result["suggested_prereq_id"]    = None
        result["suggested_prereq_subject"] = None
    return result
