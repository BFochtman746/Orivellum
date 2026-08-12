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
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("orivellum.learning")

_GRAD_THRESHOLD = 0.75  # score at or above this counts as a pass
_PASSES_TO_GRAD = 3  # consecutive passes needed to graduate
_ISSUED_QUESTION_TTL_SECONDS = 6 * 3600  # issued questions expire; stale prompts earn no credit
_MAX_SEED_SUBJ = 500  # max distinct subjects considered per re-seed sweep
_SEED_KN_SCAN = 2000  # knowledge rows scanned per re-seed (whole-corpus, not top-20)
_SEED_LLM_BATCH = 60  # max NEW subjects sent to the LLM for ordering/prereqs per seed
_MAX_KN_CONTEXT = 5  # knowledge items to include in question/assess prompts

# Review-status allowlist for anything that grounds a question or an answer
# key.  ONLY human-approved knowledge may ground a question or an answer key
# (THE RE-PROJECTION Phase 6): 'auto'/'ai_auto' machine extractions,
# 'proposed' research claims, quarantined pre-reprojection evidence, and
# 'rejected' items are all excluded; unknown future statuses are excluded by
# default (allowlist fails closed).
_QUESTION_SAFE_REVIEW = ("approved",)

# ── HLR (Half-Life Regression) spaced-repetition constants ───────────────────
_HLR_MIN_HALF_LIFE = 0.5  # floor: 12 h (never schedule sooner than this)
_HLR_DURABLE_HALF_LIFE = 7.0  # a concept is "durably mastered" only when HL > 7 days
_HLR_DURABLE_SESSIONS = 3  # …AND reviewed on ≥ 3 distinct calendar days

# ── Depth ladder (T-M4) ───────────────────────────────────────────────────────
# Four levels, each rubric-scored separately.  "auto" climbs the ladder: the
# next question is asked at the lowest level the learner has not yet passed.
# A concept cannot graduate on recall alone — the higher levels are required.
_LEVELS = ("recall", "self_explanation", "contrast", "transfer")
_TEACH_BACK = "teach_back"  # long-form mode outside the ladder; can fail a graduate
_MAX_TRANSFER_STREAK_CREDIT = 2  # max consecutive_passes increment for a correct transfer answer
_VALID_QUESTION_TYPES = frozenset({*_LEVELS, "auto"})
_MIN_RUBRIC_CRITERIA = 3  # fewer valid criteria than this → rubric unusable (fail closed)
_MAX_RUBRIC_CRITERIA = 6  # criteria beyond this are dropped (prompt asks for 3-6)

# ── Reverse research loop (T-M6) ──────────────────────────────────────────────
_RESEARCH_FAIL_WINDOW_DAYS = 7  # window for "repeated failure"
_RESEARCH_FAIL_THRESHOLD = 3  # fails in window (no pass) that trigger triage
_CORPUS_MIN_ITEMS = 3  # fewer question-safe items than this → corpus_insufficient

# ── Interleaved practice mode ─────────────────────────────────────────────────
_INTERLEAVED_MIN_CONCEPTS = 3  # min in-progress concepts to activate interleaved mode
_INTERLEAVED_SESSION_LENGTH = 10  # questions per interleaved session
_VALID_SESSION_MODES = frozenset({"blocked", "interleaved"})

# ── Error classification ──────────────────────────────────────────────────────
_VALID_ERROR_TYPES = frozenset(
    {
        "careless_slip",  # mostly correct; minor arithmetic / wording slip
        "procedural_gap",  # knows the concept but can't execute a step
        "conceptual_misconception",  # holds a false belief about the concept
        "knowledge_gap",  # no prior knowledge of a prerequisite
    }
)
# Threshold for "deep review needed" flag (same misconception ≥ N times)
_DEEP_REVIEW_THRESHOLD = 2


# ─── Internal helpers ──────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _call(
    messages: list[dict],
    base_url: str,
    model: str,
    timeout: int = 30,
    purpose: str = "learning",
    db: Any = None,
) -> str | None:
    """Call the local LLM synchronously via the central gateway.

    Returns the reply text, or None on any failure (the gateway never raises).
    """
    from orivellum.capabilities.llm import llm_call

    result = llm_call(
        messages,
        base_url=base_url,
        model=model,
        timeout=timeout,
        purpose=purpose,
        db=db,
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
            db.audit(
                "learning.mastery_reset",
                object_id=concept_id or work_id,
                object_type="work",
                actor="system",
                detail=f"{cur.rowcount} rows",
            )
        except Exception:
            pass
    return cur.rowcount


def _get_concept(db: Any, concept_id: str) -> dict | None:
    with db._lock:
        row = db._conn.execute("SELECT * FROM work_concepts WHERE id=?", (concept_id,)).fetchone()
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
            "score": 0.0,
            "consecutive_passes": 0,
            "last_practised": None,
            "last_reviewed_at": None,
            "next_review_at": None,
            "half_life_days": 1.0,
            "review_session_count": 0,
        }
    now = _now()
    m["is_due"] = bool(m.get("next_review_at") and m["next_review_at"] <= now)
    return m


def _levels_passed(db: Any, concept_id: str) -> set[str]:
    """Distinct depth-ladder levels at which the concept has a recorded pass.

    Fails closed: on any DB error the set is empty (never graduated by accident).
    """
    try:
        with db._lock:
            rows = db._conn.execute(
                "SELECT DISTINCT question_type FROM work_mastery WHERE concept_id=? AND score>=?",
                (concept_id, _GRAD_THRESHOLD),
            ).fetchall()
        return {(r["question_type"] or "recall") for r in rows}
    except Exception:
        return set()


def _has_sibling_concepts(db: Any, concept_id: str) -> bool:
    """True when the concept's Work has at least one OTHER concept.

    Contrast questions need a neighbour to contrast against; a single-concept
    Work skips the contrast level (and it is not required for graduation).
    """
    try:
        with db._lock:
            row = db._conn.execute(
                """SELECT COUNT(*) FROM work_concepts
                   WHERE work_id=(SELECT work_id FROM work_concepts WHERE id=?) AND id != ?""",
                (concept_id, concept_id),
            ).fetchone()
        return int(row[0]) > 0
    except Exception:
        return False


def _required_levels(db: Any, concept_id: str) -> tuple[str, ...]:
    """Depth levels (beyond recall) a concept must pass before it can graduate.

    Contrast is required only when a neighbour concept exists to contrast
    against.  Recall alone is NEVER sufficient.
    """
    if _has_sibling_concepts(db, concept_id):
        return ("self_explanation", "contrast", "transfer")
    return ("self_explanation", "transfer")


def _is_graduated(db: Any, concept_id: str) -> bool:
    """Graduation = consecutive-pass streak AND the depth ladder climbed.

    A concept graduates only when consecutive_passes >= _PASSES_TO_GRAD AND it
    has a recorded pass at every required higher level (self-explanation,
    contrast when a neighbour exists, transfer).  Recall-only streaks never
    graduate (T-M4).

    The HLR system tracks half_life_days and review_session_count separately.
    Graduated concepts re-enter the queue via ``is_due`` (spaced-repetition
    reviews) until they reach durable mastery.
    """
    m = _get_mastery(db, concept_id)
    if m["consecutive_passes"] < _PASSES_TO_GRAD:
        return False
    passed = _levels_passed(db, concept_id)
    return all(lvl in passed for lvl in _required_levels(db, concept_id))


def _knowledge_for_concept(db: Any, work_id: str, subject: str) -> list[dict]:
    """Pull knowledge items most relevant to the subject (FTS + subject match).

    Review gate: only _QUESTION_SAFE_REVIEW items may ground a question or an
    answer key.  Unratified research proposals must never become exam
    material, so the filter is applied both in SQL and again here (defence in
    depth for DB fakes/older signatures)."""
    try:
        items = db.search_knowledge(
            subject,
            work_id=work_id,
            limit=_MAX_KN_CONTEXT,
            review_status_in=_QUESTION_SAFE_REVIEW,
        )
    except Exception:
        items = []
    if not items:
        try:
            items = db.list_knowledge(
                work_id=work_id, limit=_MAX_KN_CONTEXT, review_status_in=_QUESTION_SAFE_REVIEW
            )
        except TypeError:  # older DB fakes without the review filter
            items = db.list_knowledge(work_id=work_id, limit=_MAX_KN_CONTEXT)
    return [i for i in items if i.get("review_status", "auto") in _QUESTION_SAFE_REVIEW]


# ─── Public API ────────────────────────────────────────────────────────────────


def seed_concepts(db: Any, work_id: str, base_url: str, model: str) -> list[dict]:
    """Auto-seed learning concepts from this Work's knowledge subjects.

    Incremental and idempotent over the whole corpus: existing concepts are
    never duplicated (subject uniqueness per work), the scan covers up to
    _SEED_KN_SCAN knowledge rows rather than a top-20 snapshot, and re-running
    after new material lands adds only the new subjects.  Only question-safe
    knowledge seeds concepts — unratified research proposals do not.
    Every call ends with a prerequisite-graph cycle check.
    Returns the full list of concepts for the work after seeding.
    """
    # Check existing concepts
    with db._lock:
        existing = db._conn.execute(
            "SELECT subject FROM work_concepts WHERE work_id=?", (work_id,)
        ).fetchall()
    existing_subjects = {r["subject"].lower() for r in existing}

    # Distinct subjects across the WHOLE corpus (question-safe statuses only,
    # oldest first so early material seeds before late material).  This query
    # is unbounded on rows scanned — coverage never depends on a top-N
    # snapshot for subject-bearing knowledge.
    corpus_subjects: list[str] = []
    try:
        with db._lock:
            _subj_rows = db._conn.execute(
                """SELECT subject FROM knowledge
                   WHERE work_id=? AND subject IS NOT NULL AND TRIM(subject) != ''
                     AND review_status IN ('approved')
                   GROUP BY lower(subject) ORDER BY MIN(created_at) ASC""",
                (work_id,),
            ).fetchall()
        corpus_subjects = [r["subject"].strip() for r in _subj_rows]
    except Exception:
        corpus_subjects = []  # DB fakes without a knowledge table

    # Recent rows still get scanned for subject-less items (text-derived).
    try:
        items = db.list_knowledge(
            work_id=work_id, limit=_SEED_KN_SCAN, review_status_in=_QUESTION_SAFE_REVIEW
        )
    except TypeError:  # older DB fakes without the review filter
        items = db.list_knowledge(work_id=work_id, limit=_SEED_KN_SCAN)
        items = [i for i in items if i.get("review_status", "auto") in _QUESTION_SAFE_REVIEW]
    if not items and not corpus_subjects:
        return list_concepts(db, work_id)

    # Build a distinct subject list: corpus-wide subjects first, then
    # text-derived subjects from the recent scan.
    seen_subjects: list[str] = []
    for subj in corpus_subjects:
        if subj.lower() not in existing_subjects and subj.lower() not in {
            s.lower() for s in seen_subjects
        }:
            seen_subjects.append(subj)
        if len(seen_subjects) >= _MAX_SEED_SUBJ:
            break
    for item in items:
        subj = (item.get("subject") or item.get("kind") or "").strip()
        if not subj:
            # Fall back to first sentence of knowledge text
            text = (item.get("text") or "")[:80].split(".")[0].strip()
            subj = text or "General concepts"
        if subj.lower() not in existing_subjects and subj.lower() not in {
            s.lower() for s in seen_subjects
        }:
            seen_subjects.append(subj)
        if len(seen_subjects) >= _MAX_SEED_SUBJ:
            break

    # Ask AI to create a short description + ordering for new subjects.
    # Requests up to 3 prerequisites per concept (multi-prerequisite graph).
    # Only the first _SEED_LLM_BATCH new subjects go to the model; the rest
    # are still inserted (plain) so the curriculum is never capped by the
    # prompt size.
    if seen_subjects and base_url:
        subj_list = "\n".join(f"- {s}" for s in seen_subjects[:_SEED_LLM_BATCH])
        prompt = (
            f"You are building a learning curriculum for the topic: {_get_work_title(db, work_id)}.\n"
            f"Order these subjects from most foundational to most advanced, give each a 1-sentence description,\n"
            f"and list up to 3 prerequisite subjects from the same list (subjects the learner must start first).\n\n"
            f"Subjects:\n{subj_list}\n\n"
            "Respond ONLY with valid JSON, no markdown fences:\n"
            '[{"subject":"...","description":"...","prereqs":["<subject1>","<subject2>"]}]\n'
            "Use [] for prereqs when there are none. Only reference subjects that appear in the list above."
        )
        raw = _call(
            [{"role": "user", "content": prompt}],
            base_url,
            model,
            timeout=25,
            purpose="learning.seed",
            db=db,
        )
        if raw:
            try:
                ordered = json.loads(_strip_fences(raw))
                seen_subjects_ordered = [o["subject"] for o in ordered if isinstance(o, dict)]
                # Anything the model omitted — or beyond the LLM batch — is
                # still seeded, unordered and without description/prereqs.
                _ordered_lower = {s.lower() for s in seen_subjects_ordered}
                seen_subjects_ordered += [
                    s for s in seen_subjects if s.lower() not in _ordered_lower
                ]
                descriptions = {
                    o["subject"]: o.get("description", "") for o in ordered if isinstance(o, dict)
                }
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
    new_concept_ids: list[tuple[str, str]] = []  # (cid, subject)
    for subj in seen_subjects_ordered:
        if subj.lower() in existing_subjects:
            continue
        cid = _uuid()
        desc = descriptions.get(subj, "")
        # Keep single prereq_id for backward compat (first prereq only)
        first_prereq = (multi_prereqs.get(subj) or [None])[0]
        prereq_id = subject_to_id.get(first_prereq.lower()) if first_prereq else None
        # Guarded insert: the LLM call above leaves a window where a concurrent
        # import/reseed may have inserted the same subject — the WHERE NOT
        # EXISTS makes the check+insert atomic under the shared write lock.
        with db._lock:
            cur = db._conn.execute(
                """INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at)
                   SELECT ?,?,?,?,?,?
                   WHERE NOT EXISTS (
                       SELECT 1 FROM work_concepts
                       WHERE work_id=? AND lower(subject)=lower(?))""",
                (cid, work_id, subj, desc, prereq_id, now, work_id, subj),
            )
            db._conn.commit()
        if cur.rowcount == 0:
            with db._lock:
                row = db._conn.execute(
                    "SELECT id FROM work_concepts WHERE work_id=? AND lower(subject)=lower(?)",
                    (work_id, subj),
                ).fetchone()
            if row:
                subject_to_id[subj.lower()] = row["id"]
            continue
        try:
            db.audit(
                "learning.concept_added",
                object_id=cid,
                object_type="learning_concept",
                actor="system",
                detail=subj[:80],
            )
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

    # Every re-seed ends with a graph validation: deterministic cycle guard.
    validate_prereq_graph(db, work_id)
    return list_concepts(db, work_id)


def validate_prereq_graph(db: Any, work_id: str) -> list[tuple[str, str]]:
    """Cycle-check the prerequisite graph for a Work and break any cycles.

    Deterministic: concepts are visited in (created_at, id) order and edges in
    sorted order, so the same graph always drops the same back-edges.  A
    back-edge (one that closes a cycle) is DELETEd from work_concept_prereqs
    and audited.  Returns the list of removed (concept_id, prereq_id) edges.

    A cycle would deadlock eligibility (each concept waiting on the other),
    so removal — not refusal — is the right remedy here: the concepts stay,
    only the impossible ordering constraint goes.
    """
    with db._lock:
        rows = db._conn.execute(
            "SELECT id FROM work_concepts WHERE work_id=? ORDER BY created_at ASC, id ASC",
            (work_id,),
        ).fetchall()
        node_order = [r["id"] for r in rows]
        if not node_order:
            return []
        ph = ",".join("?" * len(node_order))
        try:
            edge_rows = db._conn.execute(
                f"SELECT concept_id, prereq_id FROM work_concept_prereqs "
                f"WHERE concept_id IN ({ph})",
                node_order,
            ).fetchall()
        except Exception:
            return []  # pre-v94 DB: no join table, nothing to validate
    edges: dict[str, list[str]] = {}
    for r in edge_rows:
        edges.setdefault(r["concept_id"], []).append(r["prereq_id"])
    for k in edges:
        edges[k].sort()

    removed: list[tuple[str, str]] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color = dict.fromkeys(node_order, WHITE)

    def _visit(node: str) -> None:
        # Iterative DFS; a GRAY target means the edge closes a cycle.
        stack: list[tuple[str, int]] = [(node, 0)]
        color[node] = GRAY
        while stack:
            cur, idx = stack[-1]
            targets = edges.get(cur, [])
            if idx >= len(targets):
                color[cur] = BLACK
                stack.pop()
                continue
            stack[-1] = (cur, idx + 1)
            nxt = targets[idx]
            if color.get(nxt, BLACK) == GRAY:
                removed.append((cur, nxt))
            elif color.get(nxt) == WHITE:
                color[nxt] = GRAY
                stack.append((nxt, 0))

    for n in node_order:
        if color[n] == WHITE:
            _visit(n)

    for cid, pid in removed:
        with db._lock:
            db._conn.execute(
                "DELETE FROM work_concept_prereqs WHERE concept_id=? AND prereq_id=?",
                (cid, pid),
            )
            db._conn.commit()
        try:
            db.audit(
                "learning.prereq_cycle_removed",
                object_id=cid,
                object_type="learning_concept",
                actor="system",
                detail=f"dropped back-edge {cid[:8]}→{pid[:8]}",
            )
        except Exception:
            pass
    return removed


def import_training_plan(db: Any, work_id: str, plan_items: list[dict]) -> dict:
    """Import training-plan/curriculum items into work_concepts (T-M3).

    Preserves the six-field item shape (topic/why/evidence/read/check/
    question) plus prereq + schedule.  Idempotent: an existing concept with
    the same subject is reused; the verification question becomes the
    concept's first stored item in work_concept_items (UNIQUE(concept_id,
    question) makes re-import a no-op).  Prerequisite edges are created by
    topic name within the imported set + existing concepts; the graph is
    cycle-checked at the end.
    """
    now = _now()
    created, reused, items_stored, edges_added = 0, 0, 0, 0

    with db._lock:
        rows = db._conn.execute(
            "SELECT id, subject FROM work_concepts WHERE work_id=?", (work_id,)
        ).fetchall()
    subject_to_id = {r["subject"].lower(): r["id"] for r in rows}

    valid_items = [
        it
        for it in plan_items
        if isinstance(it, dict) and (it.get("topic") or "").strip() and it.get("question")
    ]

    # Pass 1: concepts.  Lookup + insert happen under ONE lock hold so a
    # concurrent reseed/import cannot slip a duplicate subject in between.
    for it in valid_items:
        topic = it["topic"].strip()[:200]
        cid = subject_to_id.get(topic.lower())
        if cid:
            reused += 1
        else:
            new_id = _uuid()
            with db._lock:
                row = db._conn.execute(
                    "SELECT id FROM work_concepts WHERE work_id=? AND lower(subject)=lower(?)",
                    (work_id, topic),
                ).fetchone()
                if row:
                    cid = row["id"]
                else:
                    db._conn.execute(
                        "INSERT INTO work_concepts(id,work_id,subject,description,created_at) "
                        "VALUES(?,?,?,?,?)",
                        (new_id, work_id, topic, (it.get("why") or "")[:500], now),
                    )
                    db._conn.commit()
                    cid = new_id
            subject_to_id[topic.lower()] = cid
            if cid == new_id:
                created += 1
            else:
                reused += 1
            try:
                db.audit(
                    "learning.concept_imported",
                    object_id=cid,
                    object_type="learning_concept",
                    actor="system",
                    detail=topic[:80],
                )
            except Exception:
                pass
        # The verification question becomes the concept's first stored item.
        with db._lock:
            cur = db._conn.execute(
                """INSERT OR IGNORE INTO work_concept_items
                   (id,concept_id,question,why,read_text,check_text,
                    evidence_json,schedule_json,source,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    _uuid(),
                    cid,
                    str(it["question"])[:1000],
                    (it.get("why") or "")[:1000],
                    (it.get("read") or "")[:1000],
                    (it.get("check") or "")[:1000],
                    json.dumps(list(it.get("evidence") or [])[:10]),
                    json.dumps(it.get("schedule") or {}),
                    "training_plan",
                    now,
                ),
            )
            db._conn.commit()
        items_stored += cur.rowcount

    # Pass 2: prerequisite edges by topic name (within this Work only)
    for it in valid_items:
        cid = subject_to_id[it["topic"].strip()[:200].lower()]
        for prereq_topic in it.get("prereq") or []:
            pid = subject_to_id.get(str(prereq_topic).strip()[:200].lower())
            if pid and pid != cid:
                with db._lock:
                    cur = db._conn.execute(
                        "INSERT OR IGNORE INTO work_concept_prereqs(concept_id,prereq_id) "
                        "VALUES(?,?)",
                        (cid, pid),
                    )
                    db._conn.commit()
                edges_added += cur.rowcount

    removed = validate_prereq_graph(db, work_id)
    return {
        "concepts_created": created,
        "concepts_reused": reused,
        "items_stored": items_stored,
        "prereq_edges_added": edges_added,
        "cycle_edges_removed": len(removed),
        "skipped": len(plan_items) - len(valid_items),
    }


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

        # 4. Bulk-load depth-ladder levels passed per concept (T-M4): graduation
        #    requires passes at the higher levels, not just a recall streak.
        level_rows = db._conn.execute(
            f"""SELECT DISTINCT concept_id, question_type FROM work_mastery
                WHERE concept_id IN ({ph}) AND score >= ?""",
            (*concept_ids, _GRAD_THRESHOLD),
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

    levels_map: dict[str, set[str]] = {}
    for r in level_rows:
        levels_map.setdefault(r["concept_id"], set()).add(r["question_type"] or "recall")

    # Contrast is required for graduation only when a neighbour concept exists.
    _req_levels = (
        ("self_explanation", "contrast", "transfer")
        if len(concepts) >= 2
        else ("self_explanation", "transfer")
    )

    result = []
    for c in concepts:
        m = mastery_map.get(c["id"]) or {}
        cons = int(m.get("consecutive_passes") or 0)
        half_life = float(m.get("half_life_days") or 1.0)
        nxt_review = m.get("next_review_at")
        _passed = levels_map.get(c["id"], set())
        graduated = cons >= _PASSES_TO_GRAD and all(lvl in _passed for lvl in _req_levels)
        is_due = bool(nxt_review and nxt_review <= now)

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

        c["score"] = float(m.get("score") or 0.0)
        c["consecutive_passes"] = cons
        c["graduated"] = graduated
        c["levels_passed"] = sorted(_passed & set(_LEVELS))
        c["last_practised"] = m.get("last_practised")
        c["half_life_days"] = half_life
        c["next_review_at"] = nxt_review
        c["review_session_count"] = int(m.get("review_session_count") or 0)
        c["is_due"] = is_due
        c["prereq_ids"] = [p["id"] for p in prereqs]
        c["prereq_labels"] = [p["subject"] for p in prereqs]
        c["blocking_count"] = blocking_count_map.get(c["id"], 0)
        c["prereqs_met"] = prereqs_met
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
    overdue = [c for c in concepts if c.get("is_due") and c.get("next_review_at", "") <= now]
    if overdue:
        overdue.sort(key=lambda c: c.get("next_review_at") or "")
        return overdue[0]["id"]

    # 2. Eligible ungraduated concepts (graph traversal)
    ungrad = [c for c in concepts if not c["graduated"]]
    if not ungrad:
        return None

    eligible = [c for c in ungrad if c.get("prereqs_met", True)]
    pool = eligible if eligible else ungrad  # fallback: ignore gate
    pool.sort(key=lambda c: (c["consecutive_passes"], c["created_at"]))
    return pool[0]["id"]


def select_interleaved_concept(db: Any, work_id: str) -> str | None:
    """Pick one concept for an interleaved practice turn via weighted random selection.

    Eligibility pool:
      • In-progress concepts: consecutive_passes > 0 and not yet graduated.
      • Graduated concepts currently due for spaced-repetition review (is_due).

    Weight = urgency_factor × (1 − mastery_fraction):
      • urgency_factor: 1 + days_overdue for overdue concepts; 0.5–1.0 for others.
      • mastery_fraction: consecutive_passes / _PASSES_TO_GRAD (capped at 1.0).

    Returns None when the eligible pool has fewer than _INTERLEAVED_MIN_CONCEPTS
    entries; the caller should raise HTTP 422 with a helpful error message.
    """
    import random

    concepts = list_concepts(db, work_id)

    in_progress = [c for c in concepts if c["consecutive_passes"] > 0 and not c["graduated"]]
    due_graduated = [c for c in concepts if c["graduated"] and c.get("is_due")]
    pool = in_progress + due_graduated

    if len(pool) < _INTERLEAVED_MIN_CONCEPTS:
        return None

    now_dt = datetime.now(UTC)
    weights: list[float] = []
    for c in pool:
        mastery_fraction = min(1.0, c["consecutive_passes"] / max(1, _PASSES_TO_GRAD))
        if c.get("is_due") and c.get("next_review_at"):
            try:
                nra_str = c["next_review_at"].replace("Z", "+00:00")
                nra = datetime.fromisoformat(nra_str)
                days_overdue = max(0.0, (now_dt - nra).total_seconds() / 86_400.0)
                urgency = 1.0 + days_overdue
            except Exception:
                urgency = 1.0
        else:
            urgency = 0.5 + 0.5 * (1.0 - mastery_fraction)  # 0.5 – 1.0 range
        weights.append(max(0.01, urgency * (1.0 - mastery_fraction)))

    selected = random.choices(pool, weights=weights, k=1)[0]
    return selected["id"]


def _resolve_question_type(db: Any, concept_id: str, question_type: str) -> str:
    """Resolve 'auto' to the next depth-ladder level the concept has not passed.

    Ladder order: recall → self_explanation → contrast → transfer.  Contrast is
    skipped when the Work has no other concept to contrast against.  Once every
    level is passed, reviews are asked at transfer (the hardest level).
    Explicit levels are returned unchanged.
    """
    if question_type != "auto":
        return (
            question_type
            if question_type in _VALID_QUESTION_TYPES and question_type != "auto"
            else "recall"
        )
    passed = _levels_passed(db, concept_id)
    has_neighbour = _has_sibling_concepts(db, concept_id)
    for lvl in _LEVELS:
        if lvl == "contrast" and not has_neighbour:
            continue
        if lvl not in passed:
            return lvl
    return "transfer"


def _contrast_neighbour(db: Any, concept_id: str) -> dict | None:
    """Pick the nearest-confusable neighbour concept for a contrast question.

    Deterministic preference order, all within the same Work:
      1. Siblings — concepts sharing at least one prerequisite (most confusable).
      2. Direct prerequisites.
      3. Dependents — concepts that list this one as a prerequisite.
      4. Highest subject-token overlap; tie-break oldest-first.
    Returns {"id","subject","description"} or None when the Work has no other
    concept.
    """
    concept = _get_concept(db, concept_id)
    if not concept:
        return None
    work_id = concept["work_id"]
    try:
        with db._lock:
            # 1. Siblings sharing a prerequisite
            row = db._conn.execute(
                """SELECT c.id, c.subject, c.description
                   FROM work_concept_prereqs cp
                   JOIN work_concepts c ON c.id = cp.concept_id
                   WHERE cp.prereq_id IN (
                         SELECT prereq_id FROM work_concept_prereqs WHERE concept_id=?)
                     AND cp.concept_id != ? AND c.work_id = ?
                   ORDER BY c.created_at ASC, c.id ASC LIMIT 1""",
                (concept_id, concept_id, work_id),
            ).fetchone()
            # 2. Direct prerequisites
            if not row:
                row = db._conn.execute(
                    """SELECT c.id, c.subject, c.description
                       FROM work_concept_prereqs cp
                       JOIN work_concepts c ON c.id = cp.prereq_id
                       WHERE cp.concept_id=? AND c.work_id=?
                       ORDER BY c.created_at ASC, c.id ASC LIMIT 1""",
                    (concept_id, work_id),
                ).fetchone()
            # 3. Dependents
            if not row:
                row = db._conn.execute(
                    """SELECT c.id, c.subject, c.description
                       FROM work_concept_prereqs cp
                       JOIN work_concepts c ON c.id = cp.concept_id
                       WHERE cp.prereq_id=? AND c.work_id=?
                       ORDER BY c.created_at ASC, c.id ASC LIMIT 1""",
                    (concept_id, work_id),
                ).fetchone()
            if row:
                return dict(row)
            # 4. Token-overlap fallback across the Work
            others = db._conn.execute(
                "SELECT id, subject, description, created_at FROM work_concepts "
                "WHERE work_id=? AND id != ? ORDER BY created_at ASC, id ASC",
                (work_id, concept_id),
            ).fetchall()
    except Exception:
        return None
    if not others:
        return None
    my_tokens = {t for t in concept["subject"].lower().split() if len(t) > 2}
    best, best_overlap = others[0], -1
    for o in others:
        overlap = len(my_tokens & {t for t in o["subject"].lower().split() if len(t) > 2})
        if overlap > best_overlap:
            best, best_overlap = o, overlap
    return {"id": best["id"], "subject": best["subject"], "description": best["description"]}


def get_question(
    db: Any,
    concept_id: str,
    base_url: str,
    model: str,
    question_type: str = "auto",
) -> dict:
    """Generate a Socratic question for the concept, grounded in knowledge.

    question_type: one of the depth-ladder levels, or 'auto'.
      - 'recall'           — state it, grounded in the source material.
      - 'self_explanation' — the learner explains in their OWN words and makes their
        own connection to prior knowledge.  Deterministic prompt; the system never
        supplies the connection, and no source excerpt is shown before the attempt.
      - 'contrast'         — distinguish the concept from its nearest-confusable
        neighbour (picked automatically from the prerequisite graph).
      - 'transfer'         — apply it to a novel scenario not in the notes.
      - 'auto'             — climbs the ladder: the lowest level not yet passed.

    Hint-withholding rule (T-M4): this function NEVER returns hints, remediation,
    or answer material.  For self_explanation the source excerpt itself is
    withheld so the learner's explanation is self-generated, not paraphrased.

    Returns {"question", "context_snippet", "question_type", "level", ...}.
    Falls back to a generic recall question when AI is unavailable.
    """
    concept = _get_concept(db, concept_id)
    if not concept:
        return {
            "question": "What do you understand about this concept so far?",
            "context_snippet": "",
            "question_type": "recall",
            "level": "recall",
        }

    subject = concept["subject"]
    work_id = concept["work_id"]
    items = _knowledge_for_concept(db, work_id, subject)
    ctx = "\n".join(f"- {it.get('text', '')[:200]}" for it in items[:_MAX_KN_CONTEXT])

    resolved_type = _resolve_question_type(db, concept_id, question_type)

    # ── Self-explanation: deterministic, works offline, source withheld ──────
    if resolved_type == "self_explanation":
        q = (
            f"In your own words: what does '{subject}' mean, why is it true or why does "
            "it work the way it does, and how does it connect to something YOU already "
            "know or have experienced? Make the connection yourself — it will not be "
            "given to you."
        )
        _issue_question(db, concept_id, "self_explanation", q)
        return {
            "question": q,
            "context_snippet": "",  # withheld: the explanation must be self-generated
            "question_type": "self_explanation",
            "level": "self_explanation",
        }

    # ── Contrast: distinguish from the nearest-confusable neighbour ──────────
    if resolved_type == "contrast":
        neighbour = _contrast_neighbour(db, concept_id)
        if neighbour is None:
            # Single-concept Work — ladder should have skipped contrast; recall fallback.
            resolved_type = "recall"
        else:
            fallback_q = (
                f"How is '{subject}' different from '{neighbour['subject']}'? Name one "
                "situation where confusing the two would lead you to the wrong conclusion."
            )
            question = fallback_q
            if base_url and ctx:
                prompt = (
                    f"You are a discrimination-testing tutor. The student is studying "
                    f"'{subject}' and often confuses it with '{neighbour['subject']}'.\n\n"
                    f"Notes on '{subject}':\n{ctx}\n\n"
                    "Generate ONE question that forces the student to DISTINGUISH the two "
                    "concepts: where they differ, when each applies, or what goes wrong if "
                    "they are swapped. Do NOT explain the difference yourself — the question "
                    "must demand that the student articulate it. Answerable in 2-4 sentences.\n\n"
                    "Respond ONLY with valid JSON, no fences:\n"
                    '{"question":"..."}'
                )
                raw = _call(
                    [{"role": "user", "content": prompt}],
                    base_url,
                    model,
                    timeout=20,
                    purpose="learning.contrast_question",
                    db=db,
                )
                if raw:
                    try:
                        parsed = json.loads(_strip_fences(raw))
                        question = str(parsed.get("question") or "").strip() or fallback_q
                    except Exception:
                        question = fallback_q
            _issue_question(db, concept_id, "contrast", question)
            return {
                "question": question,
                # Only the neighbour's NAME is shown — never source excerpts or the
                # difference itself (that would be the answer).
                "context_snippet": f"Contrast with: {neighbour['subject']}",
                "question_type": "contrast",
                "level": "contrast",
                "contrast_concept_id": neighbour["id"],
                "contrast_subject": neighbour["subject"],
            }

    if not base_url or not ctx:
        # No LLM or no source material — always recall; never label a generic
        # "explain in your own words" question as a transfer application question.
        q = f"In your own words, explain the key idea behind '{subject}' and give a concrete example."
        _issue_question(db, concept_id, "recall", q)
        return {
            "question": q,
            "context_snippet": ctx,
            "question_type": "recall",
            "level": "recall",
        }

    if resolved_type == "transfer":
        prompt = (
            f"You are a transfer-testing tutor. The student is studying '{subject}'.\n\n"
            f"Background knowledge from their notes (DO NOT quote or paraphrase these in your question):\n{ctx}\n\n"
            "Generate ONE application question that:\n"
            "- Presents a NOVEL scenario the notes do NOT describe\n"
            "- Requires applying the concept to reason through the scenario (not recalling a fact)\n"
            "- Could be an analogy, a 'what-if', a real-world situation, or a problem to diagnose\n"
            "- Is answerable in 2–4 sentences by someone who truly understands the concept\n"
            "- Does NOT quote, paraphrase, or hint at the source material above\n\n"
            "Respond ONLY with valid JSON, no fences:\n"
            '{"question":"...","context_snippet":"<concept being tested, in ≤10 words>"}'
        )
        purpose = "learning.transfer_question"
    else:
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
        purpose = "learning.question"

    raw = _call(
        [{"role": "user", "content": prompt}], base_url, model, timeout=20, purpose=purpose, db=db
    )
    if raw:
        try:
            parsed = json.loads(_strip_fences(raw))
            q = str(parsed.get("question", ""))
            _issue_question(db, concept_id, resolved_type, q)
            return {
                "question": q,
                "context_snippet": parsed.get("context_snippet", ""),
                "question_type": resolved_type,
                "level": resolved_type,
            }
        except Exception:
            pass

    # LLM call failed / JSON unparseable — fall back to a generic recall question.
    # NEVER label this fallback as "transfer": it isn't a novel application scenario,
    # so it must not display the ⚡ badge or grant the +2 mastery bonus.
    q = f"In your own words, explain the key idea behind '{subject}' and give a concrete example."
    _issue_question(db, concept_id, "recall", q)
    return {
        "question": q,
        "context_snippet": ctx[:120] if ctx else "",
        "question_type": "recall",
        "level": "recall",
    }


def _issue_question(db: Any, concept_id: str, level: str, question: str) -> None:
    """Record the question the server issued for this concept (one row per concept).

    Assessments are only accepted against an issued question (see
    consume_issued_question) so ladder credit is always bound to an exercise
    the SERVER generated at a server-derived level — never client-authored.
    """
    try:
        with db._lock:
            db._conn.execute(
                "INSERT INTO learning_issued_questions(concept_id, level, question, created_at) "
                "VALUES(?,?,?,?) ON CONFLICT(concept_id) DO UPDATE SET "
                "level=excluded.level, question=excluded.question, created_at=excluded.created_at",
                (concept_id, level, question, _now()),
            )
            db._conn.commit()
    except Exception:
        logger.warning("could not record issued question for %s", concept_id, exc_info=True)


def consume_issued_question(
    db: Any, concept_id: str, question: str | None = None, level: str | None = None
) -> str | None:
    """Atomically claim (delete) the issued question for a concept.

    Returns the level it was issued at, or None when there is no issued
    question, the submitted question text does not match, the issuance has
    expired (older than _ISSUED_QUESTION_TTL_SECONDS), or (when ``level`` is
    given, e.g. teach_back) the issued level differs.  Single-use: a
    successful claim deletes the row, so replays and concurrent double-submits
    of the same question are rejected.
    """
    try:
        with db._lock:
            row = db._conn.execute(
                "SELECT level, question, created_at FROM learning_issued_questions "
                "WHERE concept_id=?",
                (concept_id,),
            ).fetchone()
            if not row:
                return None
            try:
                issued_at = datetime.fromisoformat(row["created_at"])
                expired = (
                    datetime.now(UTC) - issued_at
                ).total_seconds() > _ISSUED_QUESTION_TTL_SECONDS
            except Exception:
                expired = True  # unparseable timestamp → fail closed
            if expired:
                # Stale prompts must never receive current ladder credit.
                db._conn.execute(
                    "DELETE FROM learning_issued_questions WHERE concept_id=?", (concept_id,)
                )
                db._conn.commit()
                return None
            if question is not None and row["question"] != question:
                return None
            if level is not None and row["level"] != level:
                return None
            cur = db._conn.execute(
                "DELETE FROM learning_issued_questions WHERE concept_id=? AND question=?",
                (concept_id, row["question"]),
            )
            db._conn.commit()
            return row["level"] if cur.rowcount else None
    except Exception:
        logger.warning("could not consume issued question for %s", concept_id, exc_info=True)
        return None


def _generate_socratic_followup(
    db: Any,
    concept_id: str,
    question: str,
    answer: str,
    misconception_hint: str,
    base_url: str,
    model: str,
) -> str | None:
    """Generate a Socratic follow-up question that surfaces and challenges a misconception.

    Does NOT tell the student they are wrong; instead surfaces the false belief by asking
    them to apply or extend it.  Returns the follow-up question text, or None on failure.
    """
    concept = _get_concept(db, concept_id)
    if not concept or not base_url:
        return None
    subject = concept["subject"]
    prompt = (
        f"A student is studying '{subject}'.\n"
        f"They were asked: {question}\n"
        f"Their answer: {answer}\n"
        f"The identified issue: {misconception_hint}\n\n"
        "Write ONE Socratic follow-up question that:\n"
        "- Does NOT say the student is wrong or give the correct answer\n"
        "- Surfaces the false belief by asking them to apply or extend their reasoning\n"
        "- Is concise (one sentence) and answerable in 2–3 sentences\n\n"
        "Respond ONLY with the question text — no JSON, no preamble."
    )
    raw = _call(
        [{"role": "user", "content": prompt}],
        base_url,
        model,
        timeout=15,
        purpose="learning.socratic_followup",
        db=db,
    )
    if raw:
        return raw.strip().strip('"').strip("'")
    return None


def _enforce_rubric(criteria: Any, answer: str) -> list[dict] | None:
    """Validate and enforce a rubric returned by the critic model.

    Returns a cleaned list of {criterion, met, quote} or None when no usable
    criteria list was supplied (legacy / malformed output).

    Extractive-quote enforcement (in CODE, never trusted from the model): a
    criterion may only count as met when its quote is a real substring of the
    student's answer (case/whitespace-normalised).  Unverifiable quotes demote
    the criterion to unmet.
    """
    if not isinstance(criteria, list):
        return None

    def _norm(s: str) -> str:
        return " ".join(s.lower().split())

    answer_norm = _norm(answer)
    cleaned: list[dict] = []
    for entry in criteria[:_MAX_RUBRIC_CRITERIA]:
        if not isinstance(entry, dict):
            continue
        criterion = str(entry.get("criterion") or "").strip()
        if not criterion:
            continue
        met = bool(entry.get("met"))
        quote = str(entry.get("quote") or "").strip()
        if met and (not quote or _norm(quote) not in answer_norm):
            met = False  # no verifiable extractive evidence → not met
            quote = ""
        cleaned.append({"criterion": criterion, "met": met, "quote": quote if met else quote})
    if len(cleaned) < _MIN_RUBRIC_CRITERIA:
        return None
    return cleaned


def assess_answer(
    db: Any,
    concept_id: str,
    question: str,
    answer: str,
    base_url: str,
    model: str,
    question_type: str = "recall",
    session_mode: str = "blocked",
) -> dict:
    """Score the user's answer, classify the error type, and return targeted remediation.

    question_type: the depth-ladder level the question was generated at
      (recall / self_explanation / contrast / transfer).  When 'transfer' and
      score ≥ _GRAD_THRESHOLD, the consecutive_passes streak is incremented by 2
      (capped at _MAX_TRANSFER_STREAK_CREDIT).

    Rubric grading (T-M4): every answer is graded against 3-6 atomic binary
    criteria.  A criterion only counts as met when the critic supplies an EXACT
    extractive quote from the student's answer — enforced in code, not trusted
    from the model.  The score is the fraction of criteria met, computed here.

    Attempt-before-hint invariant: the mastery row (the attempt) is ALWAYS
    recorded before any remediation/follow-up is generated or returned.

    Returns:
        score             — float 0–1 (fraction of rubric criteria met)
        rubric            — list of {criterion, met, quote} or None (legacy/offline)
        feedback          — 1–2 sentence constructive feedback
        route             — STEP_FORWARD / STEP_BACKWARD / STAY_HERE
        graduated         — True when streak AND depth ladder are both complete
        error_type        — None (correct) or one of _VALID_ERROR_TYPES
        remediation_hint  — 1-sentence targeted suggestion, or None
        deep_review_needed— True when same misconception appears ≥ _DEEP_REVIEW_THRESHOLD
        socratic_followup — Socratic follow-up question for conceptual_misconception, else None
        question_type     — echoes the level that was assessed
        diagnosis         — never_learned / learned_and_decayed / corpus_insufficient / None
        research_request_id — id of the emitted research request, or None

    Falls back to score=0.5, route=STAY_HERE, error_type=None when AI unavailable.
    """
    resolved_qt = question_type if question_type in _LEVELS else "recall"

    _empty = {
        "score": 0.5,
        "rubric": None,
        "feedback": "Could not assess.",
        "route": "STAY_HERE",
        "graduated": False,
        "error_type": None,
        "remediation_hint": None,
        "deep_review_needed": False,
        "socratic_followup": None,
        "question_type": resolved_qt,
        "diagnosis": None,
        "research_request_id": None,
    }

    concept = _get_concept(db, concept_id)
    if not concept:
        return {**_empty, "feedback": "Could not assess — concept not found."}

    subject = concept["subject"]
    work_id = concept["work_id"]
    items = _knowledge_for_concept(db, work_id, subject)
    ctx = "\n".join(f"- {it.get('text', '')[:200]}" for it in items[:_MAX_KN_CONTEXT])

    offline_result = {**_empty, "feedback": "AI unavailable — keeping score neutral."}

    # Streak BEFORE this attempt — needed to detect a "cold check" failure
    # (a graduated concept failing), which feeds the reverse research loop.
    prev_cons = _get_mastery(db, concept_id)["consecutive_passes"]
    was_graduated = _is_graduated(db, concept_id)

    if not base_url:
        _record_mastery(
            db,
            concept_id,
            0.5,
            "STAY_HERE",
            "AI unavailable",
            question_type=resolved_qt,
            session_mode=session_mode,
        )
        return offline_result

    # Level-specific critic preamble so the rubric criteria match the depth
    # being tested — each level is rubric-scored separately (T-M4).
    if resolved_qt == "transfer":
        critic_preamble = (
            f"You are an Assessment Critic for an APPLICATION question on '{subject}'.\n"
            "The student was asked to apply the concept to a NOVEL scenario — not to recall the source material.\n"
            "Criteria must test whether the underlying principle was correctly applied to the new situation.\n"
        )
    elif resolved_qt == "self_explanation":
        critic_preamble = (
            f"You are an Assessment Critic for a SELF-EXPLANATION on '{subject}'.\n"
            "The student was asked to explain the concept in their OWN words, say WHY it is "
            "true or works, and connect it to something THEY already know.\n"
            "Criteria must include: (a) accurate own-words explanation (not verbatim source), "
            "(b) a correct mechanism/why, (c) a self-generated connection to prior knowledge.\n"
        )
    elif resolved_qt == "contrast":
        critic_preamble = (
            f"You are an Assessment Critic for a CONTRAST question on '{subject}'.\n"
            "The student was asked to distinguish this concept from a confusable neighbour.\n"
            "Criteria must test whether the student articulated a REAL difference and when "
            "each concept applies — not merely defined one of them.\n"
        )
    else:
        critic_preamble = f"You are an Assessment Critic for the topic '{subject}'.\n"

    critic_prompt = (
        critic_preamble + "\n"
        f"Knowledge context:\n{ctx}\n\n"
        f"Question: {question}\n"
        f"Student answer: {answer}\n\n"
        "Grade with an atomic rubric: derive 3-6 BINARY criteria a correct answer at this "
        "depth must satisfy. For EACH criterion report:\n"
        '  "criterion" — one atomic requirement\n'
        '  "met"       — true only when the student answer clearly satisfies it\n'
        '  "quote"     — an EXACT substring copied verbatim from the student answer that '
        "proves it (empty string when unmet). Never paraphrase the quote.\n\n"
        "Identify WHY the answer fell short (error_type):\n"
        '  "null"                     — all or nearly all criteria met\n'
        '  "careless_slip"            — mostly correct but a minor slip\n'
        '  "procedural_gap"           — understands the concept but cannot execute a step\n'
        '  "conceptual_misconception" — holds a false belief about the underlying concept\n'
        '  "knowledge_gap"            — shows no prior knowledge or cannot apply it at all\n\n'
        "Respond ONLY with valid JSON, no markdown fences:\n"
        '{"criteria":[{"criterion":"...","met":true,"quote":"..."}],'
        '"feedback":"1-2 sentence constructive feedback",'
        '"error_type":"null","remediation_hint":"1 sentence on what to review or do next"}'
    )
    raw = _call(
        [{"role": "user", "content": critic_prompt}],
        base_url,
        model,
        timeout=25,
        purpose="learning.assess",
        db=db,
    )
    if not raw:
        _record_mastery(
            db,
            concept_id,
            0.5,
            "STAY_HERE",
            "AI unavailable",
            question_type=resolved_qt,
            session_mode=session_mode,
        )
        return offline_result

    try:
        parsed = json.loads(_strip_fences(raw))
        rubric = _enforce_rubric(parsed.get("criteria"), answer)
        if rubric is not None:
            # Score is computed HERE from the fraction of criteria met — never
            # trusted as a bare float from the model.
            score = sum(1 for c in rubric if c["met"]) / len(rubric)
        elif resolved_qt == "recall":
            # Legacy shape (no criteria list) — accept a clamped float score.
            # Only recall may use it: recall alone can never graduate, so an
            # unverified float cannot advance the depth ladder.
            score = max(0.0, min(1.0, float(parsed.get("score", 0.5))))
        else:
            # Ladder levels above recall FAIL CLOSED without a verifiable
            # rubric: cap at neutral so no unverified model float ever grants
            # level credit or streak progress toward graduation.
            score = min(0.5, max(0.0, min(1.0, float(parsed.get("score", 0.5)))))
        feedback = str(parsed.get("feedback", ""))
        raw_et = parsed.get("error_type") or "null"
        error_type: str | None = raw_et if raw_et in _VALID_ERROR_TYPES else None
        # Correct answers must have no error_type regardless of what the LLM said
        if score >= _GRAD_THRESHOLD:
            error_type = None
        remediation_hint: str | None = str(parsed.get("remediation_hint", "")).strip() or None
    except Exception:
        _record_mastery(
            db,
            concept_id,
            0.5,
            "STAY_HERE",
            "Could not parse assessment",
            question_type=resolved_qt,
            session_mode=session_mode,
        )
        return offline_result

    # Compute streak increment: transfer + correct → +2, everything else → +1
    _streak_inc = (
        _MAX_TRANSFER_STREAK_CREDIT
        if (resolved_qt == "transfer" and score >= _GRAD_THRESHOLD)
        else 1
    )
    route = _compute_route(db, concept_id, score, streak_increment=_streak_inc, level=resolved_qt)

    # Knowledge-gap consistency guard: if the critic identified a knowledge gap
    # but _compute_route chose STAY_HERE, check if there are unstarted prereqs
    # and promote to STEP_BACKWARD so routing is consistent.
    if error_type == "knowledge_gap" and route == "STAY_HERE":
        prereq_ids = get_prereq_ids(db, concept_id)
        if any(_get_mastery(db, pid)["consecutive_passes"] == 0 for pid in prereq_ids):
            route = "STEP_BACKWARD"

    # ── Attempt recorded FIRST ────────────────────────────────────────────────
    # The mastery row (the attempt) is stored before any remediation or
    # follow-up is generated — hints are never available pre-attempt.
    _record_mastery(
        db,
        concept_id,
        score,
        route,
        feedback,
        error_type=error_type,
        remediation_hint=remediation_hint,
        question_type=resolved_qt,
        session_mode=session_mode,
        rubric_json=json.dumps(rubric) if rubric is not None else None,
    )

    graduated = _is_graduated(db, concept_id)

    # ── Reverse research loop (T-M6) ─────────────────────────────────────────
    # On failure, triage: repeated failure or a graduated concept failing a
    # cold check gets one of three diagnoses; only corpus_insufficient emits a
    # research request.
    diagnosis: str | None = None
    research_request_id: str | None = None
    if score < _GRAD_THRESHOLD:
        triage = triage_failure(
            db,
            concept_id,
            cold_check=was_graduated and prev_cons >= _PASSES_TO_GRAD,
        )
        diagnosis = triage.get("diagnosis")
        research_request_id = triage.get("request_id")

    # ── Deep review flag ─────────────────────────────────────────────────────
    deep_review_needed = False
    if error_type == "conceptual_misconception":
        with db._lock:
            cnt = db._conn.execute(
                "SELECT COUNT(*) FROM work_mastery "
                "WHERE concept_id=? AND error_type='conceptual_misconception'",
                (concept_id,),
            ).fetchone()[0]
        deep_review_needed = int(cnt) >= _DEEP_REVIEW_THRESHOLD

    # ── Socratic follow-up (second LLM call, only for misconceptions) ────────
    socratic_followup: str | None = None
    if error_type == "conceptual_misconception":
        socratic_followup = _generate_socratic_followup(
            db,
            concept_id,
            question,
            answer,
            remediation_hint or feedback,
            base_url,
            model,
        )

    return {
        "score": score,
        "rubric": rubric,
        "feedback": feedback,
        "route": route,
        "graduated": graduated,
        "error_type": error_type,
        "remediation_hint": remediation_hint,
        "deep_review_needed": deep_review_needed,
        "socratic_followup": socratic_followup,
        "question_type": resolved_qt,
        "diagnosis": diagnosis,
        "research_request_id": research_request_id,
    }


# ─── Teach-back (T-M5) ────────────────────────────────────────────────────────


def get_teach_back(db: Any, concept_id: str) -> dict | None:
    """Return the teach-back prompt for a concept.

    Deterministic and hint-free: no source excerpts, no criteria preview — the
    learner must produce the explanation from their own understanding before
    any grading material exists.
    """
    concept = _get_concept(db, concept_id)
    if not concept:
        return None
    subject = concept["subject"]
    prompt = (
        f"Teach '{subject}' to someone who has never heard of it. In your own words: "
        "what is it, why does it work or matter, and what is one concrete example? "
        "Write it the way you would actually say it to a curious beginner."
    )
    _issue_question(db, concept_id, _TEACH_BACK, prompt)
    return {
        "concept_id": concept_id,
        "subject": subject,
        "level": _TEACH_BACK,
        "prompt": prompt,
    }


def assess_teach_back(
    db: Any,
    concept_id: str,
    explanation: str,
    base_url: str,
    model: str,
) -> dict:
    """Grade a teach-back explanation criterion-by-criterion with extractive quotes.

    The system plays the naive student: alongside the rubric it returns ONE
    follow-up question a beginner would ask about the weakest part.

    A failed teach-back resets the streak via _record_mastery — a graduated
    concept CAN fail a teach-back and lose graduation (T-M5).  Failures feed
    the reverse research loop like any other failure.
    """
    _empty = {
        "score": 0.5,
        "passed": False,
        "rubric": None,
        "student_followup": None,
        "feedback": "Could not assess.",
        "route": "STAY_HERE",
        "graduated": False,
        "question_type": _TEACH_BACK,
        "diagnosis": None,
        "research_request_id": None,
    }
    concept = _get_concept(db, concept_id)
    if not concept:
        return {**_empty, "feedback": "Could not assess — concept not found."}

    subject = concept["subject"]
    items = _knowledge_for_concept(db, concept["work_id"], subject)
    ctx = "\n".join(f"- {it.get('text', '')[:200]}" for it in items[:_MAX_KN_CONTEXT])

    prev_cons = _get_mastery(db, concept_id)["consecutive_passes"]
    was_graduated = _is_graduated(db, concept_id)

    offline = {**_empty, "feedback": "AI unavailable — keeping score neutral."}
    if not base_url:
        _record_mastery(
            db,
            concept_id,
            0.5,
            "STAY_HERE",
            "AI unavailable",
            question_type=_TEACH_BACK,
        )
        return offline

    prompt = (
        f"You are grading a TEACH-BACK: a student explained '{subject}' as if teaching a "
        "complete beginner.\n\n"
        f"Source material:\n{ctx}\n\n"
        f"Student's teaching explanation:\n{explanation}\n\n"
        "1. Derive 3-6 atomic BINARY criteria a correct teaching explanation of this "
        "concept must contain, based ONLY on the source material.\n"
        "2. For EACH criterion report:\n"
        '   "criterion" — the atomic requirement\n'
        '   "met"       — true only when the explanation clearly satisfies it\n'
        '   "quote"     — an EXACT substring copied verbatim from the student\'s '
        "explanation that proves it (empty string when unmet). Never paraphrase.\n"
        "3. Play the naive student: write ONE short follow-up question a beginner would "
        "ask, aimed at the weakest or vaguest part of the explanation.\n\n"
        "Respond ONLY with valid JSON, no markdown fences:\n"
        '{"criteria":[{"criterion":"...","met":true,"quote":"..."}],'
        '"student_followup":"...","feedback":"1-2 sentences"}'
    )
    raw = _call(
        [{"role": "user", "content": prompt}],
        base_url,
        model,
        timeout=35,
        purpose="learning.teach_back",
        db=db,
    )
    if not raw:
        _record_mastery(
            db, concept_id, 0.5, "STAY_HERE", "AI unavailable", question_type=_TEACH_BACK
        )
        return offline

    try:
        parsed = json.loads(_strip_fences(raw))
    except Exception:
        _record_mastery(
            db,
            concept_id,
            0.5,
            "STAY_HERE",
            "Could not parse teach-back assessment",
            question_type=_TEACH_BACK,
        )
        return offline

    rubric = _enforce_rubric(parsed.get("criteria"), explanation)
    if rubric is None:
        _record_mastery(
            db,
            concept_id,
            0.5,
            "STAY_HERE",
            "Teach-back critic returned no usable rubric",
            question_type=_TEACH_BACK,
        )
        return offline

    score = sum(1 for c in rubric if c["met"]) / len(rubric)
    feedback = str(parsed.get("feedback", ""))
    student_followup = str(parsed.get("student_followup", "")).strip() or None

    route = _compute_route(db, concept_id, score, level=_TEACH_BACK)

    # Attempt recorded before any follow-up material is returned.
    _record_mastery(
        db,
        concept_id,
        score,
        route,
        feedback,
        question_type=_TEACH_BACK,
        rubric_json=json.dumps(rubric),
    )

    diagnosis: str | None = None
    research_request_id: str | None = None
    if score < _GRAD_THRESHOLD:
        triage = triage_failure(
            db,
            concept_id,
            cold_check=was_graduated and prev_cons >= _PASSES_TO_GRAD,
        )
        diagnosis = triage.get("diagnosis")
        research_request_id = triage.get("request_id")

    return {
        "score": score,
        "passed": score >= _GRAD_THRESHOLD,
        "rubric": rubric,
        "student_followup": student_followup,
        "feedback": feedback,
        "route": route,
        "graduated": _is_graduated(db, concept_id),
        "question_type": _TEACH_BACK,
        "diagnosis": diagnosis,
        "research_request_id": research_request_id,
    }


# ─── Reverse research loop (T-M6) ─────────────────────────────────────────────


def diagnose_concept(db: Any, concept_id: str) -> str | None:
    """Three-way diagnosis of a struggling concept.

    - corpus_insufficient — the question-safe corpus is too thin to learn from;
      the ONLY diagnosis that emits a research request.
    - learned_and_decayed — the concept was EVER graduated (the streak reached
      _PASSES_TO_GRAD at some point AND every required ladder level has a
      passing record) but is failing now.
    - never_learned      — corpus is adequate but the learner never got there.

    "Ever graduated" must use the same two-axis definition as _is_graduated —
    streak alone is not learning: three recall-only passes never graduated the
    concept, so their later failure is never_learned, not decay.
    """
    concept = _get_concept(db, concept_id)
    if not concept:
        return None
    items = _knowledge_for_concept(db, concept["work_id"], concept["subject"])
    if len(items) < _CORPUS_MIN_ITEMS:
        return "corpus_insufficient"
    try:
        with db._lock:
            row = db._conn.execute(
                "SELECT MAX(consecutive_passes) FROM work_mastery WHERE concept_id=?",
                (concept_id,),
            ).fetchone()
        ever_max = int(row[0] or 0)
    except Exception:
        ever_max = 0
    # Level passes are historical (a later failure never erases them), so a
    # historical streak + all required levels passed ⇔ graduated at some point.
    ever_graduated = ever_max >= _PASSES_TO_GRAD and set(_required_levels(db, concept_id)) <= (
        _levels_passed(db, concept_id)
    )
    return "learned_and_decayed" if ever_graduated else "never_learned"


def triage_failure(db: Any, concept_id: str, *, cold_check: bool = False) -> dict:
    """Decide whether a recorded failure warrants a diagnosis and research request.

    Trigger gate: ≥ _RESEARCH_FAIL_THRESHOLD failures in the last
    _RESEARCH_FAIL_WINDOW_DAYS with no pass (the existing "stuck" definition),
    OR cold_check=True (a graduated concept just failed a review/teach-back).

    Only a corpus_insufficient diagnosis emits a research request; the other
    two diagnoses are remediation problems, not research problems.
    Returns {"diagnosis": str|None, "request_id": str|None}.
    """
    none = {"diagnosis": None, "request_id": None}
    concept = _get_concept(db, concept_id)
    if not concept:
        return none
    try:
        with db._lock:
            row = db._conn.execute(
                f"""SELECT
                        SUM(CASE WHEN score <  ? THEN 1 ELSE 0 END) AS fails,
                        SUM(CASE WHEN score >= ? THEN 1 ELSE 0 END) AS passes
                    FROM work_mastery
                    WHERE concept_id=?
                      AND created_at >= datetime('now','-{_RESEARCH_FAIL_WINDOW_DAYS} days')""",
                (_GRAD_THRESHOLD, _GRAD_THRESHOLD, concept_id),
            ).fetchone()
        fails = int(row["fails"] or 0)
        passes = int(row["passes"] or 0)
    except Exception:
        fails, passes = 0, 0

    repeated = fails >= _RESEARCH_FAIL_THRESHOLD and passes == 0
    if not (repeated or cold_check):
        return none

    diagnosis = diagnose_concept(db, concept_id)
    if diagnosis != "corpus_insufficient":
        return {"diagnosis": diagnosis, "request_id": None}

    request_id = _emit_research_request(
        db,
        concept,
        diagnosis,
        evidence={
            "recent_fails": fails,
            "recent_passes": passes,
            "cold_check": cold_check,
            "corpus_items": len(_knowledge_for_concept(db, concept["work_id"], concept["subject"])),
        },
    )
    return {"diagnosis": diagnosis, "request_id": request_id}


def _emit_research_request(db: Any, concept: dict, diagnosis: str, evidence: dict) -> str | None:
    """Insert an open research request for the concept (at most one open per concept).

    The request names what the corpus needs; the next research run consumes
    open requests as units.  Returns the request id (existing open one when
    already present), or None on failure.
    """
    subject = concept["subject"]
    need = (
        f"The corpus lacks enough material to learn '{subject}'. Gather sources that "
        f"explain what it is, why it works, how it differs from related ideas, and at "
        f"least one worked example."
    )
    try:
        with db._lock:
            existing = db._conn.execute(
                "SELECT id FROM research_requests WHERE concept_id=? AND status='open'",
                (concept["id"],),
            ).fetchone()
            if existing:
                return existing["id"]
            rid = _uuid()
            db._conn.execute(
                """INSERT INTO research_requests
                       (id, work_id, concept_id, need, diagnosis, evidence_json,
                        status, created_at)
                   VALUES(?,?,?,?,?,?,'open',?)""",
                (
                    rid,
                    concept["work_id"],
                    concept["id"],
                    need,
                    diagnosis,
                    json.dumps(evidence),
                    _now(),
                ),
            )
            db._conn.commit()
    except Exception:
        logger.warning("research request emission failed for concept %s", concept["id"])
        return None
    try:
        db.audit(
            "learning.research_requested",
            object_id=concept["id"],
            object_type="learning_concept",
            actor="system",
            detail=f"diagnosis={diagnosis} subject={subject[:80]}",
        )
    except Exception:
        pass
    return rid


def list_research_requests(db: Any, work_id: str, status: str | None = "open") -> list[dict]:
    """Open (or all) research requests for a Work, oldest first."""
    q = "SELECT * FROM research_requests WHERE work_id=?"
    params: list[Any] = [work_id]
    if status:
        q += " AND status=?"
        params.append(status)
    q += " ORDER BY created_at ASC"
    try:
        with db._lock:
            rows = db._conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def resolve_research_request(db: Any, request_id: str) -> bool:
    """Atomically mark an open research request resolved. False when not open."""
    try:
        with db._lock:
            cur = db._conn.execute(
                "UPDATE research_requests SET status='resolved', resolved_at=? "
                "WHERE id=? AND status='open'",
                (_now(), request_id),
            )
            db._conn.commit()
        return cur.rowcount > 0
    except Exception:
        return False


def get_learning_analytics(db: Any, work_id: str) -> dict:
    """Return structured analytics for the Learning Analytics Panel.

    Computes entirely from existing work_mastery / work_concepts tables — no new storage.

    Returns
    -------
    velocity           list[{week, graduated}] — 4 weekly buckets (3w ago → this week)
    stuck              list[{concept_id, subject, fail_count, error_types}]
    retention_forecast list[{concept_id, subject, next_review_at, days_overdue, half_life_days}]
    session_history    list[{concept_id, subject, score, question_type, error_type, date}]
    distribution       {not_started, in_progress, graduated, due_for_review, total}
    """
    from datetime import timedelta

    now_dt = datetime.now(UTC)
    now_str = now_dt.isoformat()
    seven_days_ago = (now_dt - timedelta(days=7)).isoformat()
    twentyeight_days_ago = (now_dt - timedelta(days=28)).isoformat()

    with db._lock:
        concept_ids = [
            r["id"]
            for r in db._conn.execute(
                "SELECT id FROM work_concepts WHERE work_id=?", (work_id,)
            ).fetchall()
        ]

    if not concept_ids:
        buckets = [
            {"week": "3w ago", "graduated": 0},
            {"week": "2w ago", "graduated": 0},
            {"week": "Last week", "graduated": 0},
            {"week": "This week", "graduated": 0},
        ]
        return {
            "velocity": buckets,
            "stuck": [],
            "retention_forecast": [],
            "session_history": [],
            "distribution": {
                "not_started": 0,
                "in_progress": 0,
                "graduated": 0,
                "due_for_review": 0,
                "total": 0,
            },
        }

    ph = ",".join("?" * len(concept_ids))

    with db._lock:
        # ── 1. Velocity: first graduation per concept in last 28 days ───────────
        grad_events = db._conn.execute(
            f"""WITH first_grad AS (
                    SELECT concept_id, MIN(created_at) AS graduated_at
                    FROM work_mastery
                    WHERE concept_id IN ({ph})
                      AND consecutive_passes >= ?
                    GROUP BY concept_id
                )
                SELECT graduated_at
                FROM first_grad
                WHERE graduated_at >= ?""",
            (*concept_ids, _PASSES_TO_GRAD, twentyeight_days_ago),
        ).fetchall()

        # ── 2. Stuck concepts: ≥3 failures in last 7 days, no pass in same window ─
        stuck_rows = db._conn.execute(
            f"""WITH recent AS (
                    SELECT concept_id,
                           SUM(CASE WHEN score < ? THEN 1 ELSE 0 END) AS fail_count,
                           MAX(CASE WHEN score >= ? THEN 1 ELSE 0 END) AS had_pass
                    FROM work_mastery
                    WHERE concept_id IN ({ph})
                      AND created_at >= ?
                    GROUP BY concept_id
                )
                SELECT wc.id, wc.subject, r.fail_count
                FROM recent r
                JOIN work_concepts wc ON wc.id = r.concept_id
                WHERE r.fail_count >= 3 AND r.had_pass = 0
                ORDER BY r.fail_count DESC""",
            (_GRAD_THRESHOLD, _GRAD_THRESHOLD, *concept_ids, seven_days_ago),
        ).fetchall()

        # Error-type breakdown for stuck concepts only
        stuck_ids = [r["id"] for r in stuck_rows]
        error_rows: list = []
        if stuck_ids:
            ph2 = ",".join("?" * len(stuck_ids))
            error_rows = db._conn.execute(
                f"""SELECT concept_id, error_type, COUNT(*) AS cnt
                    FROM work_mastery
                    WHERE concept_id IN ({ph2})
                      AND created_at >= ?
                      AND score < ?
                      AND error_type IS NOT NULL
                    GROUP BY concept_id, error_type""",
                (*stuck_ids, seven_days_ago, _GRAD_THRESHOLD),
            ).fetchall()

        # ── 3. Retention forecast: overdue GRADUATED concepts only ────────────
        # "graduated" means the latest mastery row has consecutive_passes >= _PASSES_TO_GRAD.
        # Unstarted / in-progress concepts also receive next_review_at after assessments,
        # so we must filter on the graduation predicate to avoid surfacing unmastered material
        # as "retention work."
        forecast_rows = db._conn.execute(
            f"""WITH latest AS (
                    SELECT concept_id,
                           consecutive_passes,
                           next_review_at,
                           COALESCE(half_life_days, 1.0) AS half_life_days,
                           ROW_NUMBER() OVER (
                               PARTITION BY concept_id
                               ORDER BY created_at DESC, rowid DESC
                           ) AS rn
                    FROM work_mastery
                    WHERE concept_id IN ({ph})
                )
                SELECT l.concept_id, wc.subject,
                       l.next_review_at, l.half_life_days
                FROM latest l
                JOIN work_concepts wc ON wc.id = l.concept_id
                WHERE l.rn = 1
                  AND l.consecutive_passes >= ?
                  AND l.next_review_at IS NOT NULL
                  AND l.next_review_at <= ?
                ORDER BY l.next_review_at ASC
                LIMIT 5""",
            (*concept_ids, _PASSES_TO_GRAD, now_str),
        ).fetchall()

        # ── 4. Session history: last 10 assessments with concept name ───────────
        history_rows = db._conn.execute(
            f"""SELECT wm.concept_id, wc.subject,
                       wm.score, wm.question_type, wm.error_type, wm.created_at
                FROM work_mastery wm
                JOIN work_concepts wc ON wc.id = wm.concept_id
                WHERE wm.concept_id IN ({ph})
                ORDER BY wm.created_at DESC, wm.rowid DESC
                LIMIT 10""",
            concept_ids,
        ).fetchall()

    # ── 5. Mastery distribution (reuse get_mastery_summary) ────────────────────
    summary = get_mastery_summary(db, work_id)

    # ── Process velocity: bucket into 4 weekly time slots ──────────────────────
    bucket_keys = ["3w ago", "2w ago", "Last week", "This week"]
    buckets: dict[str, int] = {k: 0 for k in bucket_keys}
    for row in grad_events:
        try:
            ev_dt = datetime.fromisoformat(row["graduated_at"].replace("Z", "+00:00"))
            days_ago = (now_dt - ev_dt).days
            if days_ago < 7:
                buckets["This week"] += 1
            elif days_ago < 14:
                buckets["Last week"] += 1
            elif days_ago < 21:
                buckets["2w ago"] += 1
            else:
                buckets["3w ago"] += 1
        except Exception:
            pass

    velocity = [{"week": k, "graduated": buckets[k]} for k in bucket_keys]

    # ── Build stuck with error breakdown ────────────────────────────────────────
    error_by_concept: dict[str, list[dict]] = {}
    for r in error_rows:
        error_by_concept.setdefault(r["concept_id"], []).append(
            {"error_type": r["error_type"], "count": r["cnt"]}
        )

    stuck = [
        {
            "concept_id": r["id"],
            "subject": r["subject"],
            "fail_count": r["fail_count"],
            "error_types": error_by_concept.get(r["id"], []),
        }
        for r in stuck_rows
    ]

    # ── Retention forecast ───────────────────────────────────────────────────────
    retention_forecast = []
    for r in forecast_rows:
        try:
            nra_dt = datetime.fromisoformat(r["next_review_at"].replace("Z", "+00:00"))
            days_overdue = max(0.0, (now_dt - nra_dt).total_seconds() / 86400)
        except Exception:
            days_overdue = 0.0
        retention_forecast.append(
            {
                "concept_id": r["concept_id"],
                "subject": r["subject"],
                "next_review_at": r["next_review_at"],
                "days_overdue": round(days_overdue, 1),
                "half_life_days": round(float(r["half_life_days"]), 1),
            }
        )

    return {
        "velocity": velocity,
        "stuck": stuck,
        "retention_forecast": retention_forecast,
        "session_history": [
            {
                "concept_id": r["concept_id"],
                "subject": r["subject"],
                "score": float(r["score"]),
                "question_type": r["question_type"] or "recall",
                "error_type": r["error_type"],
                "date": r["created_at"],
            }
            for r in history_rows
        ],
        "distribution": {
            "not_started": summary["not_started"],
            "in_progress": summary["in_progress"],
            "graduated": summary["graduated"],
            "due_for_review": summary["due_count"],
            "total": summary["total"],
        },
    }


def get_learn_health(db: Any) -> dict:
    """Aggregate learning health metrics across ALL Works — used by the mobile learn tab.

    Returns
    -------
    total_due            int  — concepts whose latest next_review_at has passed
    stuck_count          int  — concepts with ≥3 failures and no pass in last 7 days
    graduating_this_week int  — concepts that first graduated within the last 7 days
    """
    from datetime import timedelta

    now_dt = datetime.now(UTC)
    now_str = now_dt.isoformat()
    seven_days_ago = (now_dt - timedelta(days=7)).isoformat()

    with db._lock:
        # Total overdue: latest mastery row per concept where next_review_at <= now
        total_due = db._conn.execute(
            """WITH latest AS (
                   SELECT concept_id, next_review_at,
                          ROW_NUMBER() OVER (
                              PARTITION BY concept_id ORDER BY created_at DESC, rowid DESC
                          ) AS rn
                   FROM work_mastery
               )
               SELECT COUNT(DISTINCT concept_id)
               FROM latest
               WHERE rn = 1
                 AND next_review_at IS NOT NULL
                 AND next_review_at <= ?""",
            (now_str,),
        ).fetchone()[0]

        # Stuck across all works: ≥3 failures in last 7 days, no pass in same window
        stuck_count = db._conn.execute(
            """SELECT COUNT(*) FROM (
                   SELECT concept_id,
                          SUM(CASE WHEN score < ? THEN 1 ELSE 0 END) AS fail_count,
                          MAX(CASE WHEN score >= ? THEN 1 ELSE 0 END) AS had_pass
                   FROM work_mastery
                   WHERE created_at >= ?
                   GROUP BY concept_id
                   HAVING fail_count >= 3 AND had_pass = 0
               )""",
            (_GRAD_THRESHOLD, _GRAD_THRESHOLD, seven_days_ago),
        ).fetchone()[0]

        # Graduating this week: first graduation event for each concept within last 7 days
        graduating_this_week = db._conn.execute(
            """SELECT COUNT(*) FROM (
                   SELECT concept_id, MIN(created_at) AS first_grad
                   FROM work_mastery
                   WHERE consecutive_passes >= ?
                   GROUP BY concept_id
                   HAVING first_grad >= ?
               )""",
            (_PASSES_TO_GRAD, seven_days_ago),
        ).fetchone()[0]

    return {
        "total_due": int(total_due or 0),
        "stuck_count": int(stuck_count or 0),
        "graduating_this_week": int(graduating_this_week or 0),
    }


def get_mastery_summary(db: Any, work_id: str) -> dict:
    """Return aggregate mastery stats for the work, including HLR due_count."""
    concepts = list_concepts(db, work_id)
    total = len(concepts)
    graduated = sum(1 for c in concepts if c["graduated"])
    in_prog = sum(1 for c in concepts if not c["graduated"] and c["consecutive_passes"] > 0)
    not_start = total - graduated - in_prog
    pct = round(graduated / total * 100) if total else 0
    due_count = sum(1 for c in concepts if c.get("is_due"))
    return {
        "total": total,
        "graduated": graduated,
        "in_progress": in_prog,
        "not_started": not_start,
        "mastery_pct": pct,
        "due_count": due_count,
        "concepts": concepts,
    }


def list_due_concepts(db: Any, work_id: str) -> list[dict]:
    """Return concepts whose next_review_at is overdue, sorted by urgency (most overdue first)."""
    now = _now()
    concepts = list_concepts(db, work_id)
    due = [c for c in concepts if c.get("next_review_at") and c["next_review_at"] <= now]
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
            return False  # prerequisite not yet touched
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


def _compute_route(
    db: Any,
    concept_id: str,
    score: float,
    streak_increment: int = 1,
    level: str | None = None,
) -> str:
    """Determine routing: STEP_FORWARD / STEP_BACKWARD / STAY_HERE.

    streak_increment: how many consecutive passes this assessment will award (normally 1;
    2 for a correctly-answered transfer question).  This lets the route correctly reflect
    graduation when the +2 bonus would push the learner past _PASSES_TO_GRAD even though
    the current record shows one fewer pass.

    level: the depth-ladder level of the attempt being routed (counted as passed
    when the score passes).  STEP_FORWARD requires the FULL depth ladder, not
    just the streak.

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

    # Score is a pass — would we be graduated after this attempt?  The streak
    # alone is no longer enough: the depth ladder must also be complete, so a
    # recall-only streak keeps the learner climbing (STAY_HERE) rather than
    # stepping forward prematurely (T-M4).
    mastery = _get_mastery(db, concept_id)
    if mastery["consecutive_passes"] + streak_increment >= _PASSES_TO_GRAD:
        passed = _levels_passed(db, concept_id)
        if level and level in _LEVELS:
            passed = passed | {level}
        if all(lvl in passed for lvl in _required_levels(db, concept_id)):
            return "STEP_FORWARD"
    return "STAY_HERE"


def _record_mastery(
    db: Any,
    concept_id: str,
    score: float,
    route: str,
    feedback: str,
    *,
    error_type: str | None = None,
    remediation_hint: str | None = None,
    question_type: str = "recall",
    session_mode: str = "blocked",
    rubric_json: str | None = None,
) -> None:
    """Insert a mastery record, update the consecutive-pass streak, and apply HLR update.

    HLR formula (Duolingo 2016):
        new_half_life = max(_HLR_MIN_HALF_LIFE, old_half_life × 2^(score − 0.5))

    A score of 1.0 roughly doubles the half-life; a score of 0.0 roughly halves it;
    a score of 0.5 leaves it unchanged.  The next review is scheduled at
    now + new_half_life days.

    review_session_count is incremented only when the new session falls on a different
    UTC calendar date from the previous one (preventing gaming by rapid repetition).

    error_type: one of _VALID_ERROR_TYPES or None (correct / AI unavailable).
    remediation_hint: 1-sentence targeted hint from the LLM critic, or None.
    """
    now = _now()
    mid = _uuid()

    # Load previous mastery state (includes HLR fields)
    prev = _get_mastery(db, concept_id)

    # ── consecutive passes ───────────────────────────────────────────────────
    # Transfer questions answered correctly award +2 (capped by _MAX_TRANSFER_STREAK_CREDIT)
    # to reward genuine deep understanding on a harder, novel-scenario question.
    if score >= _GRAD_THRESHOLD:
        increment = _MAX_TRANSFER_STREAK_CREDIT if question_type == "transfer" else 1
        cons = prev["consecutive_passes"] + increment
    else:
        cons = 0  # reset streak on failure

    # ── HLR half-life update ─────────────────────────────────────────────────
    old_hl = float(prev.get("half_life_days") or 1.0)
    new_hl = max(_HLR_MIN_HALF_LIFE, old_hl * (2 ** (score - 0.5)))

    # next_review_at = now + new_half_life days
    import datetime as _dt

    now_dt = (
        _dt.datetime.fromisoformat(now.replace("Z", "+00:00"))
        if now.endswith("Z")
        else _dt.datetime.fromisoformat(now)
    )
    next_review_dt = now_dt + _dt.timedelta(days=new_hl)
    next_review_at = next_review_dt.isoformat()

    # ── session-count gate (distinct calendar days) ──────────────────────────
    prev_session_count = int(prev.get("review_session_count") or 0)
    prev_date = (prev.get("last_reviewed_at") or "")[:10]  # "YYYY-MM-DD"
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
                   half_life_days, review_session_count,
                   error_type, remediation_hint,
                   question_type, session_mode, rubric_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                mid,
                concept_id,
                score,
                cons,
                feedback,
                route,
                now,
                now,
                next_review_at,
                new_hl,
                new_session_count,
                error_type,
                remediation_hint,
                question_type if question_type in (*_LEVELS, _TEACH_BACK) else "recall",
                session_mode if session_mode in _VALID_SESSION_MODES else "blocked",
                rubric_json,
            ),
        )
        db._conn.commit()
    try:
        db.audit(
            "learning.mastery_recorded",
            object_id=concept_id,
            object_type="learning_concept",
            actor="system",
            detail=f"score={score:.2f} hl={new_hl:.2f}d next={next_review_at[:10]}"
            + (f" err={error_type}" if error_type else "")
            + (f" qtype={question_type}" if question_type == "transfer" else ""),
        )
    except Exception:
        pass


def _get_work_title(db: Any, work_id: str) -> str:
    try:
        work = db.get_work(work_id)
        return (work or {}).get("title") or "this topic"
    except Exception:
        return "this topic"
