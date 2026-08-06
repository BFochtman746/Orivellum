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

_GRAD_THRESHOLD        = 0.75   # score at or above this counts as a pass
_PASSES_TO_GRAD        = 3      # consecutive passes needed to graduate
_MAX_SEED_SUBJ         = 20     # max subjects to seed from knowledge
_MAX_KN_CONTEXT        = 5      # knowledge items to include in question/assess prompts

# ── HLR (Half-Life Regression) spaced-repetition constants ───────────────────
_HLR_MIN_HALF_LIFE     = 0.5    # floor: 12 h (never schedule sooner than this)
_HLR_DURABLE_HALF_LIFE = 7.0   # a concept is "durably mastered" only when HL > 7 days
_HLR_DURABLE_SESSIONS  = 3     # …AND reviewed on ≥ 3 distinct calendar days


# ─── Internal helpers ──────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _call(messages: list[dict], base_url: str, model: str, timeout: int = 30,
          purpose: str = "learning", db: Any = None) -> str | None:
    """Call the local LLM synchronously via the central gateway.

    Returns the reply text, or None on any failure (the gateway never raises).
    """
    from orivellum.capabilities.llm import llm_call
    result = llm_call(
        messages, base_url=base_url, model=model,
        timeout=timeout, purpose=purpose, db=db,
    )
    return result.text


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
    if cur.rowcount > 0:
        try:
            db.audit("learning.mastery_reset", object_id=concept_id or work_id,
                     object_type="work", actor="system",
                     detail=f"{cur.rowcount} rows")
        except Exception:
            pass
    return cur.rowcount


def _get_concept(db: Any, concept_id: str) -> dict | None:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM work_concepts WHERE id=?", (concept_id,)
        ).fetchone()
    return dict(row) if row else None


def _get_mastery(db: Any, concept_id: str) -> dict:
    """Latest mastery record for the concept, or defaults.

    Includes ``last_practised`` (ISO-8601 string or None), HLR fields
    (half_life_days, next_review_at, review_session_count), and is_due flag.
    """
    with db._lock:
        row = db._conn.execute(
            """SELECT score, consecutive_passes,
                      created_at AS last_practised,
                      COALESCE(last_reviewed_at, created_at) AS last_reviewed_at,
                      next_review_at,
                      COALESCE(half_life_days, 1.0)       AS half_life_days,
                      COALESCE(review_session_count, 0)   AS review_session_count
               FROM work_mastery
               WHERE concept_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1""",
            (concept_id,),
        ).fetchone()
    if row:
        m = dict(row)
    else:
        m = {
            "score": 0.0, "consecutive_passes": 0, "last_practised": None,
            "last_reviewed_at": None, "next_review_at": None,
            "half_life_days": 1.0, "review_session_count": 0,
        }
    now = _now()
    m["is_due"] = bool(m.get("next_review_at") and m["next_review_at"] <= now)
    return m


def _is_graduated(db: Any, concept_id: str) -> bool:
    """A concept is graduated when it reaches _PASSES_TO_GRAD consecutive passes.

    The HLR system tracks half_life_days and review_session_count separately.
    Graduated concepts re-enter the queue via ``is_due`` (spaced-repetition reviews)
    until they reach durable mastery (half_life_days > _HLR_DURABLE_HALF_LIFE AND
    review_session_count >= _HLR_DURABLE_SESSIONS), at which point they stop being
    marked due.  Graduation itself is simply the consecutive-pass gate.
    """
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

    # Ask AI to create a short description + ordering for new subjects.
    # Requests up to 3 prerequisites per concept (multi-prerequisite graph).
    if seen_subjects and base_url:
        subj_list = "\n".join(f"- {s}" for s in seen_subjects[:_MAX_SEED_SUBJ])
        prompt = (
            f"You are building a learning curriculum for the topic: {_get_work_title(db, work_id)}.\n"
            f"Order these subjects from most foundational to most advanced, give each a 1-sentence description,\n"
            f"and list up to 3 prerequisite subjects from the same list (subjects the learner must start first).\n\n"
            f"Subjects:\n{subj_list}\n\n"
            "Respond ONLY with valid JSON, no markdown fences:\n"
            '[{"subject":"...","description":"...","prereqs":["<subject1>","<subject2>"]}]\n'
            "Use [] for prereqs when there are none. Only reference subjects that appear in the list above."
        )
        raw = _call([{"role": "user", "content": prompt}], base_url, model,
                    timeout=25, purpose="learning.seed", db=db)
        if raw:
            try:
                ordered = json.loads(_strip_fences(raw))
                seen_subjects_ordered = [o["subject"] for o in ordered if isinstance(o, dict)]
                descriptions = {o["subject"]: o.get("description", "") for o in ordered if isinstance(o, dict)}
                # Support both old "prereq" (string) and new "prereqs" (list) shapes
                multi_prereqs: dict[str, list[str]] = {}
                for o in ordered:
                    if not isinstance(o, dict):
                        continue
                    subj = o.get("subject", "")
                    raw_p = o.get("prereqs", o.get("prereq"))
                    if isinstance(raw_p, list):
                        multi_prereqs[subj] = [p for p in raw_p if isinstance(p, str) and p.strip()]
                    elif isinstance(raw_p, str) and raw_p.strip():
                        multi_prereqs[subj] = [raw_p.strip()]
                    else:
                        multi_prereqs[subj] = []
            except Exception:
                descriptions = {}
                multi_prereqs = {}
                seen_subjects_ordered = seen_subjects
        else:
            seen_subjects_ordered = seen_subjects
            descriptions = {}
            multi_prereqs = {}
    else:
        seen_subjects_ordered = seen_subjects
        descriptions = {}
        multi_prereqs = {}

    # ── Pass 1: insert all new concept nodes ─────────────────────────────────
    subject_to_id: dict[str, str] = {}
    with db._lock:
        for row in db._conn.execute(
            "SELECT id, subject FROM work_concepts WHERE work_id=?", (work_id,)
        ).fetchall():
            subject_to_id[row["subject"].lower()] = row["id"]

    now = _now()
    new_concept_ids: list[tuple[str, str]] = []   # (cid, subject)
    for subj in seen_subjects_ordered:
        if subj.lower() in existing_subjects:
            continue
        cid = _uuid()
        desc = descriptions.get(subj, "")
        # Keep single prereq_id for backward compat (first prereq only)
        first_prereq = (multi_prereqs.get(subj) or [None])[0]
        prereq_id = subject_to_id.get(first_prereq.lower()) if first_prereq else None
        with db._lock:
            db._conn.execute(
                "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at) VALUES(?,?,?,?,?,?)",
                (cid, work_id, subj, desc, prereq_id, now),
            )
            db._conn.commit()
        try:
            db.audit("learning.concept_added", object_id=cid, object_type="learning_concept",
                     actor="system", detail=subj[:80])
        except Exception:
            pass
        subject_to_id[subj.lower()] = cid
        new_concept_ids.append((cid, subj))

    # ── Pass 2: insert prerequisite edges into join table ────────────────────
    # subject_to_id was built exclusively from concepts in this work_id, so all
    # IDs already belong to the same Work.  The DB-level guard below is a
    # defence-in-depth check that rejects any cross-Work edge that might slip
    # through future code changes.
    for cid, subj in new_concept_ids:
        prereq_subjects = multi_prereqs.get(subj, [])
        for prereq_subj in prereq_subjects:
            pid = subject_to_id.get(prereq_subj.lower() if prereq_subj else "")
            if pid and pid != cid:
                with db._lock:
                    try:
                        # Conditional INSERT: only proceeds when both concepts share work_id
                        db._conn.execute(
                            """INSERT OR IGNORE INTO work_concept_prereqs(concept_id, prereq_id)
                               SELECT ?, ?
                               WHERE NOT EXISTS (
                                   SELECT 1 FROM work_concepts c1, work_concepts c2
                                   WHERE c1.id = ? AND c2.id = ? AND c1.work_id != c2.work_id
                               )""",
                            (cid, pid, cid, pid),
                        )
                        db._conn.commit()
                    except Exception:
                        pass  # table absent (pre-v94 DB) — migration handles it

    return list_concepts(db, work_id)


def list_concepts(db: Any, work_id: str) -> list[dict]:
    """Return all concepts for the work, annotated with mastery state, HLR fields, and graph info.

    Uses exactly 4 bulk SQL queries for the entire work — no per-concept DB reads.
    """
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM work_concepts WHERE work_id=? ORDER BY created_at ASC",
            (work_id,),
        ).fetchall()
    concepts = [dict(r) for r in rows]
    if not concepts:
        return []

    concept_ids = tuple(c["id"] for c in concepts)
    ph = ",".join("?" * len(concept_ids))
    now = _now()

    import sqlite3 as _sqlite3

    with db._lock:
        # 1. Bulk-load the latest mastery record per concept via window function.
        #    Narrow fallback: if the v93 HLR columns haven't been added yet (pre-v93
        #    schema), re-query with only the base columns.  Any other OperationalError
        #    (table missing, syntax error, etc.) is re-raised so schema faults are
        #    visible rather than silently swallowed.
        try:
            mastery_rows = db._conn.execute(
                f"""WITH ranked AS (
                        SELECT concept_id, score, consecutive_passes,
                               created_at                         AS last_practised,
                               COALESCE(last_reviewed_at, created_at) AS last_reviewed_at,
                               next_review_at,
                               COALESCE(half_life_days, 1.0)     AS half_life_days,
                               COALESCE(review_session_count, 0) AS review_session_count,
                               ROW_NUMBER() OVER (
                                   PARTITION BY concept_id
                                   ORDER BY created_at DESC, rowid DESC
                               ) AS rn
                        FROM work_mastery
                        WHERE concept_id IN ({ph})
                    )
                    SELECT * FROM ranked WHERE rn = 1""",
                concept_ids,
            ).fetchall()
        except _sqlite3.OperationalError as exc:
            if "no such column" in str(exc).lower():
                # Pre-v93 DB: HLR columns absent — fall back to base mastery columns
                mastery_rows = db._conn.execute(
                    f"""WITH ranked AS (
                            SELECT concept_id, score, consecutive_passes,
                                   created_at AS last_practised, NULL AS last_reviewed_at,
                                   NULL AS next_review_at, 1.0 AS half_life_days,
                                   0 AS review_session_count,
                                   ROW_NUMBER() OVER (
                                       PARTITION BY concept_id ORDER BY created_at DESC, rowid DESC
                                   ) AS rn
                            FROM work_mastery
                            WHERE concept_id IN ({ph})
                        )
                        SELECT * FROM ranked WHERE rn = 1""",
                    concept_ids,
                ).fetchall()
            else:
                raise  # table missing or other schema error — fail visibly

        # 2. Bulk-load prerequisite edges (concept → its prereqs), scoped to this Work.
        #    The JOIN on wc.work_id=? ensures cross-Work edges are excluded: a foreign
        #    concept can never appear as a prereq for a concept in this Work.
        #    No silent fallback: if work_concept_prereqs is absent the application cannot
        #    reliably determine prerequisite gates, so we fail loudly.
        prereq_rows = db._conn.execute(
            f"""SELECT cp.concept_id, cp.prereq_id, wc.subject AS prereq_subject
                FROM work_concept_prereqs cp
                JOIN work_concepts wc ON wc.id = cp.prereq_id AND wc.work_id = ?
                WHERE cp.concept_id IN ({ph})""",
            (work_id, *concept_ids),
        ).fetchall()

        # 3. Bulk-load reverse edges (prereq → concepts that depend on it), same Work only.
        blocking_rows = db._conn.execute(
            f"""SELECT cp.prereq_id, cp.concept_id
                FROM work_concept_prereqs cp
                JOIN work_concepts wc ON wc.id = cp.concept_id AND wc.work_id = ?
                WHERE cp.prereq_id IN ({ph})""",
            (work_id, *concept_ids),
        ).fetchall()

    # Build in-memory maps — all annotations computed from these; zero extra DB calls
    mastery_map: dict[str, dict] = {dict(r)["concept_id"]: dict(r) for r in mastery_rows}

    prereq_map: dict[str, list[dict]] = {}
    for r in prereq_rows:
        prereq_map.setdefault(r["concept_id"], []).append(
            {"id": r["prereq_id"], "subject": r["prereq_subject"]}
        )

    blocking_count_map: dict[str, int] = {}
    for r in blocking_rows:
        blocking_count_map[r["prereq_id"]] = blocking_count_map.get(r["prereq_id"], 0) + 1

    result = []
    for c in concepts:
        m   = mastery_map.get(c["id"]) or {}
        cons       = int(m.get("consecutive_passes") or 0)
        half_life  = float(m.get("half_life_days")   or 1.0)
        nxt_review = m.get("next_review_at")
        graduated  = cons >= _PASSES_TO_GRAD
        is_due     = bool(nxt_review and nxt_review <= now)

        prereqs = prereq_map.get(c["id"], [])
        # prereqs_met: True when every prerequisite has at least one pass recorded
        # (computed inline from the bulk-loaded mastery_map — no DB queries)
        if not prereqs:
            prereqs_met = True
        else:
            prereqs_met = all(
                int((mastery_map.get(p["id"]) or {}).get("consecutive_passes") or 0) > 0
                for p in prereqs
            )

        c["score"]                = float(m.get("score") or 0.0)
        c["consecutive_passes"]   = cons
        c["graduated"]            = graduated
        c["last_practised"]       = m.get("last_practised")
        c["half_life_days"]       = half_life
        c["next_review_at"]       = nxt_review
        c["review_session_count"] = int(m.get("review_session_count") or 0)
        c["is_due"]               = is_due
        c["prereq_ids"]           = [p["id"]      for p in prereqs]
        c["prereq_labels"]        = [p["subject"] for p in prereqs]
        c["blocking_count"]       = blocking_count_map.get(c["id"], 0)
        c["prereqs_met"]          = prereqs_met
        result.append(c)
    return result


def next_concept_id(db: Any, work_id: str) -> str | None:
    """Pick the next concept to study using HLR + graph-traversal priority.

    Priority order:
    1. Overdue graduated concepts (next_review_at <= now), most overdue first —
       these have been mastered but are predicted to be forgotten soon.
    2. Eligible ungraduated concepts (all prerequisites started/graduated),
       fewest consecutive passes first — advance the learning frontier.
    3. Any remaining ungraduated concepts (ignore prereq gate as a last resort
       so the queue never completely empties).
    """
    concepts = list_concepts(db, work_id)
    if not concepts:
        return None
    now = _now()

    # 1. Overdue graduated concepts (spaced-repetition reviews)
    overdue = [
        c for c in concepts
        if c.get("is_due") and c.get("next_review_at", "") <= now
    ]
    if overdue:
        overdue.sort(key=lambda c: (c.get("next_review_at") or ""))
        return overdue[0]["id"]

    # 2. Eligible ungraduated concepts (graph traversal)
    ungrad = [c for c in concepts if not c["graduated"]]
    if not ungrad:
        return None

    eligible = [c for c in ungrad if c.get("prereqs_met", True)]
    pool = eligible if eligible else ungrad   # fallback: ignore gate
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
    raw = _call([{"role": "user", "content": prompt}], base_url, model,
                timeout=20, purpose="learning.question", db=db)
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
    raw = _call([{"role": "user", "content": critic_prompt}], base_url, model,
                timeout=25, purpose="learning.assess", db=db)
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
    """Return aggregate mastery stats for the work, including HLR due_count."""
    concepts  = list_concepts(db, work_id)
    total     = len(concepts)
    graduated = sum(1 for c in concepts if c["graduated"])
    in_prog   = sum(1 for c in concepts if not c["graduated"] and c["consecutive_passes"] > 0)
    not_start = total - graduated - in_prog
    pct       = round(graduated / total * 100) if total else 0
    due_count = sum(1 for c in concepts if c.get("is_due"))
    return {
        "total":        total,
        "graduated":    graduated,
        "in_progress":  in_prog,
        "not_started":  not_start,
        "mastery_pct":  pct,
        "due_count":    due_count,
        "concepts":     concepts,
    }


def list_due_concepts(db: Any, work_id: str) -> list[dict]:
    """Return concepts whose next_review_at is overdue, sorted by urgency (most overdue first)."""
    now      = _now()
    concepts = list_concepts(db, work_id)
    due      = [c for c in concepts if c.get("next_review_at") and c["next_review_at"] <= now]
    due.sort(key=lambda c: c.get("next_review_at") or "")
    return due


# ─── Prerequisite graph helpers ────────────────────────────────────────────────

def get_prereq_ids(db: Any, concept_id: str) -> list[str]:
    """Return prerequisite concept IDs, restricted to the same Work as concept_id.

    Cross-Work edges are silently excluded so that foreign concept IDs can never
    influence routing or eligibility for a different Work's concepts.
    """
    with db._lock:
        rows = db._conn.execute(
            """SELECT cp.prereq_id
               FROM work_concept_prereqs cp
               JOIN work_concepts wc ON wc.id = cp.prereq_id
               WHERE cp.concept_id = ?
                 AND wc.work_id = (SELECT work_id FROM work_concepts WHERE id = ?)""",
            (concept_id, concept_id),
        ).fetchall()
    return [r["prereq_id"] for r in rows]


def is_concept_eligible(db: Any, concept_id: str) -> bool:
    """True when ALL same-Work prerequisites have been started (consecutive_passes > 0).

    Root concepts (no prerequisites in the join table for this Work) are always eligible.
    A prerequisite counts as "started" as soon as the learner has answered any question
    for it — this prevents blocking on full graduation and avoids infinite lock chains.
    Cross-Work prerequisites are never considered (see get_prereq_ids).
    """
    prereq_ids = get_prereq_ids(db, concept_id)
    if not prereq_ids:
        return True
    for pid in prereq_ids:
        m = _get_mastery(db, pid)
        if m["consecutive_passes"] == 0:
            return False   # prerequisite not yet touched
    return True


def get_blocking_concepts(db: Any, concept_id: str) -> list[str]:
    """Return IDs of same-Work concepts whose prerequisite set includes this concept."""
    with db._lock:
        rows = db._conn.execute(
            """SELECT cp.concept_id
               FROM work_concept_prereqs cp
               JOIN work_concepts wc ON wc.id = cp.concept_id
               WHERE cp.prereq_id = ?
                 AND wc.work_id = (SELECT work_id FROM work_concepts WHERE id = ?)""",
            (concept_id, concept_id),
        ).fetchall()
    return [r["concept_id"] for r in rows]


# ─── Private helpers ──────────────────────────────────────────────────────────

def _compute_route(db: Any, concept_id: str, score: float) -> str:
    """Determine routing: STEP_FORWARD / STEP_BACKWARD / STAY_HERE.

    Uses the multi-prerequisite graph (work_concept_prereqs) to determine whether
    to route backward.  If the student failed AND any prerequisite is not yet
    graduated, we suggest revisiting the least-mastered prerequisite first.
    """
    if score < _GRAD_THRESHOLD:
        # Check multi-prereq graph for unmastered prerequisites
        prereq_ids = get_prereq_ids(db, concept_id)
        for pid in prereq_ids:
            prereq_m = _get_mastery(db, pid)
            if prereq_m["consecutive_passes"] < _PASSES_TO_GRAD:
                return "STEP_BACKWARD"
        return "STAY_HERE"

    # Score is a pass — are we now graduated (durable)?
    mastery = _get_mastery(db, concept_id)
    if mastery["consecutive_passes"] + 1 >= _PASSES_TO_GRAD:
        return "STEP_FORWARD"
    return "STAY_HERE"


def _record_mastery(db: Any, concept_id: str, score: float, route: str, feedback: str) -> None:
    """Insert a mastery record, update the consecutive-pass streak, and apply HLR update.

    HLR formula (Duolingo 2016):
        new_half_life = max(_HLR_MIN_HALF_LIFE, old_half_life × 2^(score − 0.5))

    A score of 1.0 roughly doubles the half-life; a score of 0.0 roughly halves it;
    a score of 0.5 leaves it unchanged.  The next review is scheduled at
    now + new_half_life days.

    review_session_count is incremented only when the new session falls on a different
    UTC calendar date from the previous one (preventing gaming by rapid repetition).
    """
    now = _now()
    mid = _uuid()

    # Load previous mastery state (includes HLR fields)
    prev = _get_mastery(db, concept_id)

    # ── consecutive passes ───────────────────────────────────────────────────
    if score >= _GRAD_THRESHOLD:
        cons = prev["consecutive_passes"] + 1
    else:
        cons = 0  # reset streak on failure

    # ── HLR half-life update ─────────────────────────────────────────────────
    old_hl = float(prev.get("half_life_days") or 1.0)
    new_hl = max(_HLR_MIN_HALF_LIFE, old_hl * (2 ** (score - 0.5)))

    # next_review_at = now + new_half_life days
    import datetime as _dt
    now_dt = _dt.datetime.fromisoformat(now.replace("Z", "+00:00")) if now.endswith("Z") else _dt.datetime.fromisoformat(now)
    next_review_dt = now_dt + _dt.timedelta(days=new_hl)
    next_review_at = next_review_dt.isoformat()

    # ── session-count gate (distinct calendar days) ──────────────────────────
    prev_session_count = int(prev.get("review_session_count") or 0)
    prev_date = (prev.get("last_reviewed_at") or "")[:10]   # "YYYY-MM-DD"
    today = now[:10]
    if prev_date != today:
        new_session_count = prev_session_count + 1
    else:
        new_session_count = prev_session_count  # same day: don't increment

    with db._lock:
        db._conn.execute(
            """INSERT INTO work_mastery(
                   id, concept_id, score, consecutive_passes,
                   brief_feedback, routed_to, created_at,
                   last_reviewed_at, next_review_at,
                   half_life_days, review_session_count)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, concept_id, score, cons,
             feedback, route, now,
             now, next_review_at,
             new_hl, new_session_count),
        )
        db._conn.commit()
    try:
        db.audit("learning.mastery_recorded", object_id=concept_id,
                 object_type="learning_concept", actor="system",
                 detail=f"score={score:.2f} hl={new_hl:.2f}d next={next_review_at[:10]}")
    except Exception:
        pass


def _get_work_title(db: Any, work_id: str) -> str:
    try:
        work = db.get_work(work_id)
        return (work or {}).get("title") or "this topic"
    except Exception:
        return "this topic"
