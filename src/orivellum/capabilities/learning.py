"""Adaptive learning engine for Orivellum Works.

Architecture (mirroring Monarch's study system):
- Concept nodes are auto-seeded from a Work's knowledge subjects on first visit.
- Each study turn: select the weakest ungraduated concept → generate a Socratic
  question grounded in a real knowledge passage → score the user's answer with a
  locked-JSON Assessment Critic → update consecutive-pass streak → route next.
- Graduation: 3 consecutive scores ≥ 0.75 → concept marked graduated.
- Routing: STEP_FORWARD (new concept), STEP_BACKWARD (revisit prereq), STAY_HERE
  (more practice needed).
- Offline fallback: generic question, score 0.5, STAY_HERE when AI unreachable.

All public functions are synchronous and safe to call via asyncio.to_thread().
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("orivellum.learning")

_GRAD_THRESHOLD   = 0.75   # score at or above this counts as a pass
_PASSES_TO_GRAD   = 3      # consecutive passes needed to graduate
_MAX_SEED_SUBJ    = 20     # max subjects to seed from knowledge
_MAX_KN_CONTEXT   = 5      # knowledge items to include in question/assess prompts


# ─── Internal helpers ──────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _call(messages: list[dict], base_url: str, model: str, timeout: int = 30) -> str | None:
    """Call the local LLM synchronously. Returns None on any failure."""
    try:
        import httpx
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                json={"model": model, "messages": messages, "stream": False},
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("Learning LLM call failed: %s", exc)
        return None


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        inner = []
        for i, line in enumerate(lines):
            if i == 0 or (i == len(lines) - 1 and line.startswith("```")):
                continue
            inner.append(line)
        text = "\n".join(inner).strip()
    return text


def reset_mastery(db: Any, work_id: str, concept_id: str | None = None) -> int:
    """Delete mastery records so concepts can be re-studied.

    If *concept_id* is given, resets only that concept.
    If omitted, resets ALL concepts for the work.
    Returns the number of rows deleted.
    """
    with db._lock:
        if concept_id:
            cur = db._conn.execute(
                "DELETE FROM work_mastery WHERE concept_id=? AND concept_id IN "
                "(SELECT id FROM work_concepts WHERE work_id=?)",
                (concept_id, work_id),
            )
        else:
            cur = db._conn.execute(
                "DELETE FROM work_mastery WHERE concept_id IN "
                "(SELECT id FROM work_concepts WHERE work_id=?)",
                (work_id,),
            )
        db._conn.commit()
    return cur.rowcount


def _get_concept(db: Any, concept_id: str) -> dict | None:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM work_concepts WHERE id=?", (concept_id,)
        ).fetchone()
    return dict(row) if row else None


def _get_mastery(db: Any, concept_id: str) -> dict:
    """Latest mastery record for the concept, or defaults."""
    with db._lock:
        row = db._conn.execute(
            """SELECT score, consecutive_passes FROM work_mastery
               WHERE concept_id=? ORDER BY created_at DESC LIMIT 1""",
            (concept_id,),
        ).fetchone()
    return dict(row) if row else {"score": 0.0, "consecutive_passes": 0}


def _is_graduated(db: Any, concept_id: str) -> bool:
    m = _get_mastery(db, concept_id)
    return m["consecutive_passes"] >= _PASSES_TO_GRAD


def _knowledge_for_concept(db: Any, work_id: str, subject: str) -> list[dict]:
    """Pull knowledge items most relevant to the subject (FTS + subject match)."""
    try:
        items = db.search_knowledge(subject, work_id=work_id, limit=_MAX_KN_CONTEXT)
    except Exception:
        items = []
    if not items:
        items = db.list_knowledge(work_id=work_id, limit=_MAX_KN_CONTEXT)
    return items


# ─── Public API ────────────────────────────────────────────────────────────────

def seed_concepts(db: Any, work_id: str, base_url: str, model: str) -> list[dict]:
    """Auto-seed learning concepts from this Work's knowledge subjects.

    Idempotent: existing concepts are NOT duplicated (subject uniqueness per work).
    Returns the full list of concepts for the work after seeding.
    """
    # Check existing concepts
    with db._lock:
        existing = db._conn.execute(
            "SELECT subject FROM work_concepts WHERE work_id=?", (work_id,)
        ).fetchall()
    existing_subjects = {r["subject"].lower() for r in existing}

    # Pull knowledge items to extract subjects
    items = db.list_knowledge(work_id=work_id, limit=_MAX_SEED_SUBJ)
    if not items:
        return list_concepts(db, work_id)

    # Build a distinct subject list
    seen_subjects: list[str] = []
    for item in items:
        subj = (item.get("subject") or item.get("kind") or "").strip()
        if not subj:
            # Fall back to first sentence of knowledge text
            text = (item.get("text") or "")[:80].split(".")[0].strip()
            subj = text or "General concepts"
        if subj.lower() not in existing_subjects and subj.lower() not in {s.lower() for s in seen_subjects}:
            seen_subjects.append(subj)

    # Ask AI to create a short description + ordering for new subjects
    if seen_subjects and base_url:
        subj_list = "\n".join(f"- {s}" for s in seen_subjects[:_MAX_SEED_SUBJ])
        prompt = (
            f"You are building a learning curriculum for the topic: {_get_work_title(db, work_id)}.\n"
            f"Order these subjects from most foundational to most advanced and give each a 1-sentence description.\n\n"
            f"Subjects:\n{subj_list}\n\n"
            "Respond ONLY with valid JSON, no markdown fences:\n"
            '[{"subject":"...","description":"...","prereq":"<subject or null>"}]'
        )
        raw = _call([{"role": "user", "content": prompt}], base_url, model, timeout=25)
        if raw:
            try:
                ordered = json.loads(_strip_fences(raw))
                seen_subjects_ordered = [o["subject"] for o in ordered if isinstance(o, dict)]
                descriptions = {o["subject"]: o.get("description", "") for o in ordered if isinstance(o, dict)}
                prereqs      = {o["subject"]: o.get("prereq")             for o in ordered if isinstance(o, dict)}
            except Exception:
                ordered = None
                descriptions = {}
                prereqs      = {}
                seen_subjects_ordered = seen_subjects
        else:
            seen_subjects_ordered = seen_subjects
            descriptions = {}
            prereqs      = {}
    else:
        seen_subjects_ordered = seen_subjects
        descriptions = {}
        prereqs      = {}

    # Insert new concepts
    subject_to_id: dict[str, str] = {}
    # Fetch existing id map
    with db._lock:
        for row in db._conn.execute(
            "SELECT id, subject FROM work_concepts WHERE work_id=?", (work_id,)
        ).fetchall():
            subject_to_id[row["subject"].lower()] = row["id"]

    now = _now()
    for subj in seen_subjects_ordered:
        if subj.lower() in existing_subjects:
            continue
        cid = _uuid()
        desc = descriptions.get(subj, "")
        prereq_subj = prereqs.get(subj)
        prereq_id = subject_to_id.get(prereq_subj.lower()) if prereq_subj else None
        with db._lock:
            db._conn.execute(
                "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at) VALUES(?,?,?,?,?,?)",
                (cid, work_id, subj, desc, prereq_id, now),
            )
            db._conn.commit()
        subject_to_id[subj.lower()] = cid

    return list_concepts(db, work_id)


def list_concepts(db: Any, work_id: str) -> list[dict]:
    """Return all concepts for the work, each annotated with mastery state."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM work_concepts WHERE work_id=? ORDER BY created_at ASC",
            (work_id,),
        ).fetchall()
    result = []
    for row in rows:
        c = dict(row)
        m = _get_mastery(db, c["id"])
        c["score"]              = m["score"]
        c["consecutive_passes"] = m["consecutive_passes"]
        c["graduated"]          = m["consecutive_passes"] >= _PASSES_TO_GRAD
        result.append(c)
    return result


def next_concept_id(db: Any, work_id: str) -> str | None:
    """Pick the next concept to study.

    Priority: ungraduated concepts with the fewest passes; prefer those whose
    prereq is already graduated (or has no prereq).
    """
    concepts = list_concepts(db, work_id)
    if not concepts:
        return None
    ungrad = [c for c in concepts if not c["graduated"]]
    if not ungrad:
        return None

    # Prefer concepts whose prereq is graduated (or none)
    graduated_ids = {c["id"] for c in concepts if c["graduated"]}
    ready = [c for c in ungrad if c.get("prereq_id") in graduated_ids or c.get("prereq_id") is None]
    pool = ready if ready else ungrad
    # Fewest consecutive passes first (weakest concept)
    pool.sort(key=lambda c: (c["consecutive_passes"], c["created_at"]))
    return pool[0]["id"]


def get_question(db: Any, concept_id: str, base_url: str, model: str) -> dict:
    """Generate a Socratic question for the concept, grounded in knowledge.

    Returns {"question": "...", "context_snippet": "..."}
    Falls back to a generic question when AI is unavailable.
    """
    concept = _get_concept(db, concept_id)
    if not concept:
        return {"question": f"What do you understand about this concept so far?", "context_snippet": ""}

    subject = concept["subject"]
    work_id = concept["work_id"]
    items   = _knowledge_for_concept(db, work_id, subject)
    ctx     = "\n".join(f"- {it.get('text','')[:200]}" for it in items[:_MAX_KN_CONTEXT])

    if not base_url or not ctx:
        return {
            "question": f"In your own words, explain the key idea behind '{subject}' and give a concrete example.",
            "context_snippet": ctx,
        }

    prompt = (
        f"You are a Socratic tutor. The student is studying '{subject}'.\n\n"
        f"Relevant knowledge from their notes:\n{ctx}\n\n"
        "Generate ONE clear, open-ended Socratic question that:\n"
        "- Tests genuine understanding (not surface recall)\n"
        "- Is grounded in the notes above\n"
        "- Is answerable in 2–4 sentences\n\n"
        "Respond ONLY with valid JSON, no fences:\n"
        '{"question":"...","context_snippet":"<1-sentence excerpt from notes that inspired the question>"}'
    )
    raw = _call([{"role": "user", "content": prompt}], base_url, model, timeout=20)
    if raw:
        try:
            parsed = json.loads(_strip_fences(raw))
            return {
                "question": parsed.get("question", ""),
                "context_snippet": parsed.get("context_snippet", ""),
            }
        except Exception:
            pass

    # Offline fallback
    return {
        "question": f"In your own words, explain the key idea behind '{subject}' and give a concrete example.",
        "context_snippet": ctx[:120] if ctx else "",
    }


def assess_answer(
    db: Any,
    concept_id: str,
    question: str,
    answer: str,
    base_url: str,
    model: str,
) -> dict:
    """Score the user's answer with an Assessment Critic.

    Returns {"score": float 0–1, "feedback": str, "route": str, "graduated": bool}
    Falls back to score=0.5, route=STAY_HERE when AI unavailable.
    """
    concept = _get_concept(db, concept_id)
    if not concept:
        return {"score": 0.5, "feedback": "Could not assess — concept not found.", "route": "STAY_HERE", "graduated": False}

    subject = concept["subject"]
    work_id = concept["work_id"]
    items   = _knowledge_for_concept(db, work_id, subject)
    ctx     = "\n".join(f"- {it.get('text','')[:200]}" for it in items[:_MAX_KN_CONTEXT])

    offline_result = {"score": 0.5, "feedback": "AI unavailable — keeping score neutral.", "route": "STAY_HERE", "graduated": False}

    if not base_url:
        _record_mastery(db, concept_id, 0.5, "STAY_HERE", "AI unavailable")
        return offline_result

    critic_prompt = (
        f"You are an Assessment Critic for the topic '{subject}'.\n\n"
        f"Knowledge context:\n{ctx}\n\n"
        f"Socratic question: {question}\n"
        f"Student answer: {answer}\n\n"
        "Evaluate strictly. Detect: circular definitions, prerequisite gaps, superficial fluency.\n"
        "Score 0.0–1.0 where:\n"
        "  ≥0.75 = genuine understanding with accurate detail\n"
        "  0.5–0.74 = partially correct; misses key nuance\n"
        "  <0.5 = incorrect, circular, or too vague\n\n"
        "Respond ONLY with valid JSON, no markdown fences:\n"
        '{"score":0.0,"feedback":"brief constructive feedback in 1-2 sentences"}'
    )
    raw = _call([{"role": "user", "content": critic_prompt}], base_url, model, timeout=25)
    if not raw:
        _record_mastery(db, concept_id, 0.5, "STAY_HERE", "AI unavailable")
        return offline_result

    try:
        parsed = json.loads(_strip_fences(raw))
        score    = float(parsed.get("score", 0.5))
        score    = max(0.0, min(1.0, score))
        feedback = parsed.get("feedback", "")
    except Exception:
        _record_mastery(db, concept_id, 0.5, "STAY_HERE", "Could not parse assessment")
        return offline_result

    route    = _compute_route(db, concept_id, score)
    _record_mastery(db, concept_id, score, route, feedback)

    graduated = _is_graduated(db, concept_id)
    return {"score": score, "feedback": feedback, "route": route, "graduated": graduated}


def get_mastery_summary(db: Any, work_id: str) -> dict:
    """Return aggregate mastery stats for the work."""
    concepts = list_concepts(db, work_id)
    total     = len(concepts)
    graduated = sum(1 for c in concepts if c["graduated"])
    in_prog   = sum(1 for c in concepts if not c["graduated"] and c["consecutive_passes"] > 0)
    not_start = total - graduated - in_prog
    pct       = round(graduated / total * 100) if total else 0
    return {
        "total":        total,
        "graduated":    graduated,
        "in_progress":  in_prog,
        "not_started":  not_start,
        "mastery_pct":  pct,
        "concepts":     concepts,
    }


# ─── Private helpers ──────────────────────────────────────────────────────────

def _compute_route(db: Any, concept_id: str, score: float) -> str:
    """Determine routing: STEP_FORWARD / STEP_BACKWARD / STAY_HERE."""
    if score < _GRAD_THRESHOLD:
        # Check if concept has a prereq that may need revisiting
        concept = _get_concept(db, concept_id)
        if concept and concept.get("prereq_id"):
            prereq_m = _get_mastery(db, concept["prereq_id"])
            if prereq_m["consecutive_passes"] < _PASSES_TO_GRAD:
                return "STEP_BACKWARD"
        return "STAY_HERE"

    # Score is a pass — are we now graduated?
    mastery = _get_mastery(db, concept_id)
    # We haven't written the new record yet; check current streak + 1
    if mastery["consecutive_passes"] + 1 >= _PASSES_TO_GRAD:
        return "STEP_FORWARD"
    return "STAY_HERE"


def _record_mastery(db: Any, concept_id: str, score: float, route: str, feedback: str) -> None:
    """Insert a mastery record and update the consecutive-pass streak."""
    now = _now()
    mid = _uuid()

    # Compute updated consecutive_passes
    prev = _get_mastery(db, concept_id)
    if score >= _GRAD_THRESHOLD:
        cons = prev["consecutive_passes"] + 1
    else:
        cons = 0   # reset streak on failure

    with db._lock:
        db._conn.execute(
            """INSERT INTO work_mastery(id,concept_id,score,consecutive_passes,brief_feedback,routed_to,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (mid, concept_id, score, cons, feedback, route, now),
        )
        db._conn.commit()


def _get_work_title(db: Any, work_id: str) -> str:
    try:
        work = db.get_work(work_id)
        return (work or {}).get("title") or "this topic"
    except Exception:
        return "this topic"
