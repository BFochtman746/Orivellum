"""MCOS Phase 1 — benchmark repository + evaluation engine.

Seeds a small set of golden benchmark suites (static hand-written ones plus
dynamic ones sampled from the live knowledge / chunk corpus), runs them
through the central LLM gateway (``capabilities/llm.py``) or a retrieval-only
scorer, and records every case score into ``eval_runs`` / ``eval_results`` so
regressions between runs are detectable.

Public API:
  * seed_default_benchmarks(db)          — idempotent suite seeding
  * run_benchmark(db, cfg, benchmark_id) — execute one suite, return run_id
  * score_response(case, text)           — the scoring rule engine
  * is_ai_reachable(cfg)                 — tiny LLM reachability probe
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from orivellum.capabilities.llm import llm_call

logger = logging.getLogger("orivellum.mcos")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _jdump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _jload(s: Any, default: Any = None) -> Any:
    if s is None:
        return default
    if not isinstance(s, str):
        return s
    try:
        return json.loads(s)
    except Exception:
        return default


# Small stopword set for distinctive-word extraction from dynamic content.
_STOPWORDS = frozenset({
    "about", "above", "after", "again", "against", "along", "among", "around",
    "because", "before", "being", "below", "between", "beyond", "could",
    "doing", "during", "every", "found", "from", "further", "having", "however",
    "into", "itself", "might", "more", "most", "much", "other", "over", "should",
    "since", "some", "such", "than", "that", "their", "them", "then", "there",
    "these", "they", "thing", "things", "this", "those", "through", "under",
    "until", "very", "were", "what", "when", "where", "which", "while", "with",
    "would", "your", "yours", "also", "been", "does", "each", "here", "just",
    "like", "many", "only", "same", "will", "well", "them", "used", "using",
    "within", "without", "based", "known", "given", "shall", "must",
})


# ── Static suite definitions ─────────────────────────────────────────────────

_REASONING_CASES: list[dict] = [
    {
        "question": "A basket holds 3 red apples and 4 green apples. If you add 2 more red "
                    "apples and then remove 1 green apple, how many apples are in the "
                    "basket? Answer with the number only.",
        "scoring": {"type": "regex", "pattern": r"\b8\b"},
        "difficulty": "easy",
    },
    {
        "question": "What is 6 multiplied by 7? Give just the number.",
        "scoring": {"type": "regex", "pattern": r"\b42\b"},
        "difficulty": "easy",
    },
    {
        "question": "Tom is twice as old as Sarah. Sarah is 9 years old. How old is Tom? "
                    "Answer with the number only.",
        "scoring": {"type": "regex", "pattern": r"\b18\b"},
        "difficulty": "easy",
    },
    {
        "question": "If today is Wednesday, what day of the week will it be in 3 days? "
                    "Answer with the day name.",
        "scoring": {"type": "regex", "pattern": r"(?i)\bsaturday\b"},
        "difficulty": "easy",
    },
    {
        "question": "All roses are flowers. Some flowers fade quickly. A red rose is a rose. "
                    "Is the red rose a flower? Answer yes or no.",
        "scoring": {"type": "regex", "pattern": r"(?i)\byes\b"},
        "difficulty": "medium",
    },
    {
        "question": "A train leaves at 2:15 PM and arrives 90 minutes later. What time does "
                    "it arrive? Answer in H:MM AM/PM format.",
        "scoring": {"type": "regex", "pattern": r"(?i)3:45\s*pm"},
        "difficulty": "medium",
    },
    {
        "question": "You have 12 cookies to share equally among 4 children. How many cookies "
                    "does each child get? Answer with the number only.",
        "scoring": {"type": "regex", "pattern": r"\b3\b"},
        "difficulty": "easy",
    },
    {
        "question": "January 1st, 2024 was a Monday. What day of the week was January 8th, "
                    "2024? Answer with the day name.",
        "scoring": {"type": "regex", "pattern": r"(?i)\bmonday\b"},
        "difficulty": "medium",
    },
]

_INSTRUCTION_CASES: list[dict] = [
    {
        "question": "List exactly three colors of the rainbow. Answer with exactly three "
                    "bullet points, each starting with '- '.",
        "scoring": {"type": "regex", "pattern": r"(?s)^(?:.*\n)?-\s.+\n-\s.+\n-\s.+\s*$"},
        "difficulty": "medium",
    },
    {
        "question": "Reply with valid JSON containing exactly the keys \"a\" and \"b\", where "
                    "a is 1 and b is 2. Output only the JSON.",
        "scoring": {"type": "json_keys", "keys": ["a", "b"]},
        "difficulty": "medium",
    },
    {
        "question": "Respond with the single word DONE in uppercase and nothing else.",
        "scoring": {"type": "exact", "expected": "DONE"},
        "difficulty": "easy",
    },
    {
        "question": "Reply with a JSON object that has the keys \"name\" and \"age\". Output "
                    "only JSON.",
        "scoring": {"type": "json_keys", "keys": ["name", "age"]},
        "difficulty": "medium",
    },
    {
        "question": "Write the word 'hello' exactly five times, separated by single spaces, "
                    "on one line.",
        "scoring": {"type": "regex", "pattern": r"(?i)\bhello\b(?:\s+hello\b){4}"},
        "difficulty": "medium",
    },
    {
        "question": "Answer using exactly two numbered list items: 1. and 2. — describe two "
                    "primary colors.",
        "scoring": {"type": "regex", "pattern": r"(?s)1\.\s.+2\.\s.+"},
        "difficulty": "medium",
    },
]


# ── Seeding ──────────────────────────────────────────────────────────────────

def _get_benchmark(db: Any, benchmark_id: str) -> dict | None:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM benchmarks WHERE id=?", (benchmark_id,)
        ).fetchone()
    return dict(row) if row else None


def _insert_static_benchmark(db: Any, bid: str, name: str, description: str,
                             category: str, kind: str, cases: list[dict]) -> int:
    """Insert a static suite once (INSERT OR IGNORE style). Returns cases added."""
    now = _now()
    added = 0
    with db._lock:
        existing = db._conn.execute(
            "SELECT id FROM benchmarks WHERE id=?", (bid,)
        ).fetchone()
        if existing:
            return 0
        db._conn.execute(
            "INSERT INTO benchmarks(id,name,description,category,kind,version,enabled,"
            "created_at) VALUES(?,?,?,?,?,1,1,?)",
            (bid, name, description, category, kind, now),
        )
        for case in cases:
            db._conn.execute(
                "INSERT INTO benchmark_cases(id,benchmark_id,question,context,"
                "expected_output,expected_concepts,scoring,difficulty,tags,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (_uuid(), bid, case["question"], case.get("context", ""),
                 case.get("expected_output", ""),
                 _jdump(case.get("expected_concepts", [])),
                 _jdump(case.get("scoring", {})),
                 case.get("difficulty", "medium"),
                 _jdump(case.get("tags", [])), now),
            )
            added += 1
        db._conn.commit()
    return added


def _upsert_dynamic_benchmark(db: Any, bid: str, name: str, description: str,
                              category: str, kind: str, cases: list[dict]) -> int:
    """Create/refresh a dynamic suite's cases.

    Deletes existing cases and regenerates them; bumps ``version`` only when the
    resulting case set actually changed (compared by a stable signature).
    Returns the number of cases now present.
    """
    now = _now()
    with db._lock:
        row = db._conn.execute(
            "SELECT id, version FROM benchmarks WHERE id=?", (bid,)
        ).fetchone()
        if row is None:
            db._conn.execute(
                "INSERT INTO benchmarks(id,name,description,category,kind,version,"
                "enabled,created_at) VALUES(?,?,?,?,?,1,1,?)",
                (bid, name, description, category, kind, now),
            )
            old_version = 1
            old_sig: list = []
        else:
            old_version = int(row["version"])
            old_rows = db._conn.execute(
                "SELECT question, expected_output, expected_concepts, scoring "
                "FROM benchmark_cases WHERE benchmark_id=? ORDER BY question",
                (bid,),
            ).fetchall()
            old_sig = [
                (r["question"], r["expected_output"], r["expected_concepts"], r["scoring"])
                for r in old_rows
            ]

        new_sig = sorted(
            (c["question"], c.get("expected_output", ""),
             _jdump(c.get("expected_concepts", [])), _jdump(c.get("scoring", {})))
            for c in cases
        )
        changed = sorted(old_sig) != new_sig

        # Refresh cases (delete + regenerate).
        db._conn.execute("DELETE FROM benchmark_cases WHERE benchmark_id=?", (bid,))
        for case in cases:
            db._conn.execute(
                "INSERT INTO benchmark_cases(id,benchmark_id,question,context,"
                "expected_output,expected_concepts,scoring,difficulty,tags,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (_uuid(), bid, case["question"], case.get("context", ""),
                 case.get("expected_output", ""),
                 _jdump(case.get("expected_concepts", [])),
                 _jdump(case.get("scoring", {})),
                 case.get("difficulty", "medium"),
                 _jdump(case.get("tags", [])), now),
            )
        if changed and old_version:
            db._conn.execute(
                "UPDATE benchmarks SET version=? WHERE id=?",
                (old_version + 1, bid),
            )
        db._conn.commit()
    return len(cases)


def _distinctive_words(text: str, want: int = 5) -> list[str]:
    """Extract up to ``want`` distinctive lowercased words (len>4, not stopwords)."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for tok in re.findall(r"[A-Za-z]+", text or ""):
        w = tok.lower()
        if len(w) <= 4 or w in _STOPWORDS or w in seen_set:
            continue
        seen_set.add(w)
        seen.append(w)
        if len(seen) >= max(want, 6):
            break
    return seen


def _build_knowledge_cases(db: Any, limit: int = 10) -> list[dict]:
    """Sample high-confidence knowledge items into knowledge_qa cases."""
    cases: list[dict] = []
    try:
        with db._lock:
            rows = db._conn.execute(
                "SELECT text, subject FROM knowledge "
                "WHERE review_status != 'rejected' AND length(text) > 30 "
                "ORDER BY confidence DESC, created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except Exception as exc:
        logger.warning("knowledge_qa sampling failed: %s", exc)
        return cases

    for r in rows:
        content = (r["text"] or "").strip()
        title = (r["subject"] or content[:60]).strip()
        if not content:
            continue
        concepts = _distinctive_words(content, want=5)
        if len(concepts) < 3:
            continue
        cases.append({
            "question": f"Based on your knowledge, what do you know about: {title}?",
            "context": content,
            "expected_concepts": concepts[:6],
            "scoring": {"type": "concepts"},
            "difficulty": "medium",
        })
    return cases


def _build_retrieval_cases(db: Any, limit: int = 10) -> list[dict]:
    """Sample chunks into rag_retrieval cases keyed by doc_id."""
    cases: list[dict] = []
    try:
        with db._lock:
            rows = db._conn.execute(
                "SELECT doc_id, text FROM chunks WHERE length(text) > 40 "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    except Exception as exc:
        logger.warning("rag_retrieval sampling failed: %s", exc)
        return cases

    for r in rows:
        text = (r["text"] or "").strip()
        doc_id = r["doc_id"]
        if not text or not doc_id:
            continue
        # Pick a distinctive sentence/phrase from the chunk as the query.
        sentences = re.split(r"(?<=[.!?])\s+", text)
        query = ""
        for s in sentences:
            if len(s.strip()) >= 25:
                query = s.strip()
                break
        if not query:
            query = text[:120].strip()
        query = query[:200]
        cases.append({
            "question": query,
            "context": "",
            "expected_output": doc_id,
            "scoring": {"type": "retrieval"},
            "difficulty": "medium",
        })
    return cases


def seed_default_benchmarks(db: Any) -> dict:
    """Idempotently seed the four default suites.

    Static suites (reasoning, instruction_following) are inserted once.
    Dynamic suites (knowledge_qa, rag_retrieval) are refreshed each call from
    the live corpus; their ``version`` bumps only when cases actually change.

    Returns ``{"benchmarks": N, "cases": M}`` reflecting the total suites/cases
    present after seeding.
    """
    _insert_static_benchmark(
        db, "reasoning", "Reasoning",
        "Arithmetic word problems, logic puzzles and date math with "
        "deterministic answers.",
        "reasoning", "llm", _REASONING_CASES,
    )
    _insert_static_benchmark(
        db, "instruction_following", "Instruction Following",
        "Format-compliance checks: bullet counts, JSON key presence, exact "
        "output.",
        "instruction", "llm", _INSTRUCTION_CASES,
    )

    knowledge_cases = _build_knowledge_cases(db, limit=10)
    _upsert_dynamic_benchmark(
        db, "knowledge_qa", "Knowledge QA",
        "Recall of distinctive concepts from high-confidence knowledge items.",
        "knowledge", "llm", knowledge_cases,
    )

    retrieval_cases = _build_retrieval_cases(db, limit=10)
    _upsert_dynamic_benchmark(
        db, "rag_retrieval", "RAG Retrieval",
        "Chunk search recall: does the source document rank in the top results "
        "for a distinctive phrase from it.",
        "retrieval", "retrieval", retrieval_cases,
    )

    with db._lock:
        n_bench = db._conn.execute("SELECT COUNT(*) FROM benchmarks").fetchone()[0]
        n_cases = db._conn.execute("SELECT COUNT(*) FROM benchmark_cases").fetchone()[0]
    return {"benchmarks": int(n_bench), "cases": int(n_cases)}


# ── Scoring rule engine ──────────────────────────────────────────────────────

def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _extract_json(text: str) -> Any:
    """Find and parse the first JSON object/array anywhere in ``text``."""
    if not text:
        return None
    # Try a straight parse first.
    try:
        return json.loads(text)
    except Exception:
        pass
    # Scan for a balanced {...} or [...] region.
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        while start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == open_ch:
                    depth += 1
                elif text[i] == close_ch:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            return json.loads(candidate)
                        except Exception:
                            break
            start = text.find(open_ch, start + 1)
    return None


def score_response(case: dict, text: str) -> float:
    """Score an LLM ``text`` response for ``case`` per its scoring rule.

    Supported scoring types:
      * "concepts"  — fraction of expected_concepts present (case-insensitive)
      * "regex"     — 1.0 if pattern matches, else 0.0
      * "exact"     — 1.0 if normalized text equals expected, else 0.0
      * "json_keys" — parse JSON anywhere; fraction of required keys present
    Unknown / missing types score 0.0.
    """
    scoring = case.get("scoring") or {}
    if isinstance(scoring, str):
        scoring = _jload(scoring, {})
    stype = (scoring.get("type") or "").lower()
    text = text or ""

    if stype == "concepts":
        concepts = case.get("expected_concepts") or []
        if isinstance(concepts, str):
            concepts = _jload(concepts, [])
        if not concepts:
            return 0.0
        hay = text.lower()
        hits = sum(1 for c in concepts if str(c).lower() in hay)
        return hits / len(concepts)

    if stype == "regex":
        pattern = scoring.get("pattern") or ""
        if not pattern:
            return 0.0
        try:
            return 1.0 if re.search(pattern, text) else 0.0
        except re.error:
            return 0.0

    if stype == "exact":
        expected = scoring.get("expected", case.get("expected_output", ""))
        return 1.0 if _normalize(text) == _normalize(str(expected)) else 0.0

    if stype == "json_keys":
        keys = scoring.get("keys") or []
        if not keys:
            return 0.0
        parsed = _extract_json(text)
        if not isinstance(parsed, dict):
            return 0.0
        hits = sum(1 for k in keys if k in parsed)
        return hits / len(keys)

    return 0.0


def _score_retrieval(db: Any, case: dict) -> float:
    """Retrieval scoring: run chunk search, score by rank of expected doc.

    1.0 if the expected doc is in the top-3 results, 0.5 if in the top-10,
    else 0.0.  No LLM is involved.
    """
    expected_doc = case.get("expected_output") or ""
    query = case.get("question") or ""
    if not expected_doc or not query:
        return 0.0
    # FTS5 MATCH treats punctuation as query syntax; reduce to bare terms so a
    # verbatim sentence (with periods/commas) doesn't raise a syntax error.
    terms = re.findall(r"[A-Za-z0-9]+", query)
    fts_query = " ".join(terms)
    if not fts_query:
        return 0.0
    try:
        results = db.search_chunks(fts_query, limit=10)
    except Exception as exc:
        logger.warning("retrieval search failed: %s", exc)
        return 0.0
    doc_ids = [r.get("doc_id") for r in results]
    if expected_doc in doc_ids[:3]:
        return 1.0
    if expected_doc in doc_ids[:10]:
        return 0.5
    return 0.0


# ── Run execution ────────────────────────────────────────────────────────────

def _prev_finished_avg(db: Any, benchmark_id: str, exclude_run_id: str) -> float | None:
    """Return the avg_score of the most recent previously-finished run."""
    with db._lock:
        row = db._conn.execute(
            "SELECT avg_score FROM eval_runs WHERE benchmark_id=? AND status='done' "
            "AND id != ? AND avg_score IS NOT NULL "
            "ORDER BY finished_at DESC, started_at DESC LIMIT 1",
            (benchmark_id, exclude_run_id),
        ).fetchone()
    return float(row["avg_score"]) if row and row["avg_score"] is not None else None


def _create_run_row(db: Any, cfg: Any, benchmark_id: str) -> str:
    """Insert a fresh ``eval_runs`` row (status='running') and return its id."""
    model = ""
    if cfg is not None:
        try:
            model = cfg.serving.workhorse_model or ""
        except Exception:
            model = ""
    with db._lock:
        n_cases = db._conn.execute(
            "SELECT COUNT(*) FROM benchmark_cases WHERE benchmark_id=?", (benchmark_id,)
        ).fetchone()[0]
        run_id = _uuid()
        db._conn.execute(
            "INSERT INTO eval_runs(id,benchmark_id,started_at,model,status,total_cases,"
            "meta) VALUES(?,?,?,?,'running',?,'{}')",
            (run_id, benchmark_id, _now(), model, int(n_cases)),
        )
        db._conn.commit()
    return run_id


def run_benchmark(db: Any, cfg: Any, benchmark_id: str) -> str:
    """Execute one benchmark suite and return the new ``eval_runs`` id.

    Creates a running eval_runs row, iterates cases (LLM or retrieval), stores
    a per-case eval_results row, then finalizes the run with status/avg_score/
    finished_at and a meta blob carrying the delta vs the previous finished run
    and a ``regressed`` flag.
    """
    if _get_benchmark(db, benchmark_id) is None:
        raise ValueError(f"unknown benchmark: {benchmark_id}")
    run_id = _create_run_row(db, cfg, benchmark_id)
    return _execute_run(db, cfg, benchmark_id, run_id)


def _finalize_run(db: Any, run_id: str, *, status: str, avg_score: float | None,
                  meta: dict) -> None:
    """Best-effort, retried final status write for a run.

    A background worker must never leave a row stuck at ``running``; if the
    UPDATE fails transiently we retry a few times and log, but never raise.
    """
    for attempt in range(3):
        try:
            with db._lock:
                db._conn.execute(
                    "UPDATE eval_runs SET status=?, avg_score=?, finished_at=?, "
                    "meta=? WHERE id=?",
                    (status, avg_score, _now(), _jdump(meta), run_id),
                )
                db._conn.commit()
            return
        except Exception as exc:  # pragma: no cover — retry path
            logger.warning("finalize run %s attempt %d failed: %s",
                           run_id[:8], attempt + 1, exc)
    logger.error("finalize run %s permanently failed — row may be stuck", run_id[:8])


def _execute_run(db: Any, cfg: Any, benchmark_id: str, run_id: str) -> str:
    """Run every case for an already-created ``eval_runs`` row and finalize it.

    The ENTIRE worker body — benchmark lookup, case loading and the case loop —
    is wrapped so that any exception (including pre-loop setup failures) marks
    the reserved row ``failed`` with a finished_at + error, rather than leaving
    it stuck at ``running`` (which would otherwise trip the 409 guard forever).
    """
    scores: list[float] = []
    run_status = "done"
    run_error: str | None = None
    try:
        bench = _get_benchmark(db, benchmark_id)
        if bench is None:
            raise ValueError(f"unknown benchmark: {benchmark_id}")
        kind = bench.get("kind", "llm")

        with db._lock:
            cases = [dict(r) for r in db._conn.execute(
                "SELECT * FROM benchmark_cases WHERE benchmark_id=? "
                "ORDER BY created_at, id",
                (benchmark_id,),
            ).fetchall()]

        for case in cases:
            case_id = case["id"]
            score: float | None = None
            judge_scores: dict = {}
            response = ""
            latency_ms: int | None = None
            err: str | None = None
            try:
                if kind == "retrieval":
                    score = _score_retrieval(db, case)
                    judge_scores = {"retrieval": score}
                else:
                    messages = []
                    ctx = (case.get("context") or "").strip()
                    if ctx:
                        messages.append({
                            "role": "system",
                            "content": "Use the following context to answer.\n\n" + ctx,
                        })
                    messages.append({"role": "user", "content": case.get("question", "")})
                    result = llm_call(
                        messages=messages, cfg=cfg, db=db,
                        purpose="mcos.eval", timeout=60,
                    )
                    latency_ms = result.latency_ms
                    if not result.ok:
                        err = result.error or "llm call failed"
                        score = 0.0
                        judge_scores = {"rule": 0.0}
                    else:
                        response = result.text or ""
                        score = score_response(case, response)
                        judge_scores = {"rule": score}
            except Exception as exc:  # per-case guard
                err = f"{type(exc).__name__}: {exc}"[:500]
                score = 0.0
                judge_scores = {"error": 0.0}
                logger.warning("case %s failed: %s", case_id[:8], err)

            if score is not None:
                scores.append(score)
            with db._lock:
                db._conn.execute(
                    "INSERT INTO eval_results(run_id,case_id,score,judge_scores,response,"
                    "latency_ms,error) VALUES(?,?,?,?,?,?,?)",
                    (run_id, case_id, score, _jdump(judge_scores),
                     response[:8000], latency_ms, err),
                )
                db._conn.commit()
    except Exception as exc:  # run-level guard (setup or loop failure)
        run_status = "failed"
        run_error = f"{type(exc).__name__}: {exc}"[:500]
        logger.error("benchmark run %s crashed: %s", run_id[:8], exc, exc_info=True)

    avg_score = (sum(scores) / len(scores)) if scores else None
    delta = None
    regressed = False
    try:
        prev_avg = _prev_finished_avg(db, benchmark_id, run_id)
        if avg_score is not None and prev_avg is not None:
            delta = round(avg_score - prev_avg, 6)
            regressed = delta < -0.15
    except Exception as exc:  # never let delta computation strand the row
        logger.warning("delta computation for run %s failed: %s", run_id[:8], exc)
    meta: dict = {"delta": delta, "regressed": regressed}
    if run_error:
        meta["error"] = run_error

    _finalize_run(db, run_id, status=run_status, avg_score=avg_score, meta=meta)
    logger.info("benchmark %s run %s: status=%s avg=%s delta=%s",
                benchmark_id, run_id[:8], run_status, avg_score, delta)
    return run_id


def is_ai_reachable(cfg: Any) -> bool:
    """Tiny probe: ask the model to reply OK; True iff the call succeeds."""
    try:
        result = llm_call(
            messages=[{"role": "user", "content": "Reply with OK"}],
            cfg=cfg, purpose="mcos.probe", timeout=10, max_tokens=5,
        )
        return bool(result.ok)
    except Exception:
        return False
