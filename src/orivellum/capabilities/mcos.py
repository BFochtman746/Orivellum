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
import math
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
    """Insert a static suite once (INSERT OR IGNORE style). Returns cases added.

    Repair path: when the benchmark row exists but has ZERO cases (e.g. the
    benchmark_cases table was lost and recreated by the schema self-heal),
    the cases are re-inserted instead of being skipped forever.
    """
    now = _now()
    added = 0
    with db._lock:
        existing = db._conn.execute(
            "SELECT id FROM benchmarks WHERE id=?", (bid,)
        ).fetchone()
        if existing:
            n_cases = db._conn.execute(
                "SELECT COUNT(*) FROM benchmark_cases WHERE benchmark_id=?",
                (bid,),
            ).fetchone()[0]
            if n_cases > 0:
                return 0
            # fall through: benchmark exists but cases are gone — re-insert them
        else:
            db._conn.execute(
                "INSERT INTO benchmarks(id,name,description,category,kind,version,"
                "enabled,created_at) VALUES(?,?,?,?,?,1,1,?)",
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

    seed_default_prompts(db)

    with db._lock:
        n_bench = db._conn.execute("SELECT COUNT(*) FROM benchmarks").fetchone()[0]
        n_cases = db._conn.execute("SELECT COUNT(*) FROM benchmark_cases").fetchone()[0]
    return {"benchmarks": int(n_bench), "cases": int(n_cases)}


# Slot registry: label + whether it can be benchmarked-as-system-preamble.
# Only the chat persona is benchmarkable (that's the only slot where "run the
# suites with this prompt as a system message" is meaningful).
PROMPT_SLOTS: dict[str, dict] = {
    "chat.base": {"label": "Chat persona", "benchmarkable": True},
    "harvest.extract": {"label": "Knowledge extraction", "benchmarkable": False},
    "mcos.judge": {"label": "Evaluation judge", "benchmarkable": False},
    "write.draft": {
        "label": "Prose drafter",
        "benchmarkable": False,
        "description": (
            "System prompt used when the AI drafts creative or narrative content. "
            "Encodes voice fidelity, beat-level discipline, and stylistic constraints "
            "derived from the user's existing writing."
        ),
    },
    "write.critic": {
        "label": "Adversarial editor",
        "benchmarkable": False,
        "description": (
            "System prompt for the adversarial critique pass. The editor reviews prose "
            "for voice consistency, pacing, precision, and filler before the user sees it."
        ),
    },
}


def _seed_prompt_slot(db: Any, slot: str, name: str, content: str,
                      notes: str) -> None:
    """Idempotently seed one slot with a v1 active prompt (skips if any rows)."""
    with db._lock:
        existing = db._conn.execute(
            "SELECT 1 FROM prompts WHERE slot=? LIMIT 1", (slot,)
        ).fetchone()
        if existing:
            return
        db._conn.execute(
            "INSERT INTO prompts(id,slot,name,content,version,active,notes,"
            "created_at) VALUES(?,?,?,?,1,1,?,?)",
            (_uuid(), slot, name, content, notes, _now()),
        )
        db._conn.commit()


def seed_default_prompts(db: Any) -> None:
    """Seed slots 'chat.base', 'harvest.extract' and 'mcos.judge' — each v1
    active — from the exact hardcoded constants.

    Idempotent per slot: a slot that already has any rows is left untouched.
    Every content string mirrors its source constant so the registry starts as
    a no-op override of the hardcoded defaults.  Seeding must never break.
    """
    try:
        # chat.base — imported lazily to avoid a hard route-module dependency.
        try:
            from orivellum.api.routes.conversations import _CHAT_BASE_PROMPT
            _seed_prompt_slot(
                db, "chat.base", "Default chat persona", _CHAT_BASE_PROMPT,
                "Seeded from the hardcoded chat base persona.")
        except Exception as exc:  # route module may be unavailable in some ctx
            logger.warning("seed chat.base failed: %s", exc)

        # harvest.extract — the LLM knowledge-extraction template.
        try:
            from orivellum.capabilities.knowledge_harvest import _EXTRACT_PROMPT
            _seed_prompt_slot(
                db, "harvest.extract", "Knowledge extraction prompt", _EXTRACT_PROMPT,
                "Must keep the {title} and {chunk} placeholders and its literal "
                "JSON braces doubled as {{ }} — it is filled via str.format().")
        except Exception as exc:
            logger.warning("seed harvest.extract failed: %s", exc)

        # mcos.judge — the judge rubric (used verbatim, no placeholders).
        try:
            _seed_prompt_slot(
                db, "mcos.judge", "Evaluation judge rubric", _JUDGE_RUBRIC,
                "Used verbatim as the judge system prompt; no placeholders.")
        except Exception as exc:
            logger.warning("seed mcos.judge failed: %s", exc)

        # write.draft — prose drafting persona (creative / narrative output).
        try:
            _seed_prompt_slot(
                db, "write.draft", "Prose drafter",
                _WRITE_DRAFT_PROMPT,
                "Voice-fidelity prompt for narrative/chapter drafting. "
                "Supports {beat_objective}, {word_target}, {theological_anchor}, "
                "{previous_beat_text}, and {voice_sample} placeholders.")
        except Exception as exc:
            logger.warning("seed write.draft failed: %s", exc)

        # write.critic — adversarial editing pass.
        try:
            _seed_prompt_slot(
                db, "write.critic", "Adversarial editor",
                _WRITE_CRITIC_PROMPT,
                "Critic pass for prose review: checks voice, pacing, filler, "
                "and factual precision. Used after drafting before delivery.")
        except Exception as exc:
            logger.warning("seed write.critic failed: %s", exc)

    except Exception as exc:  # pragma: no cover — seeding must never break
        logger.warning("seed_default_prompts failed: %s", exc)


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


# ── Judge consensus (Phase 2) ────────────────────────────────────────────────
#
# For llm-kind cases we combine up to three judges into a weighted consensus:
#   * rule      — the deterministic score_response rule engine (weight 0.5)
#   * llm       — an LLM grader over a strict JSON rubric      (weight 0.3)
#   * grounding — deterministic context-overlap heuristic      (weight 0.2)
# Absent judges (e.g. no context → no grounding judge, or AI unreachable → no
# llm judge) are dropped and the remaining weights renormalized.

_JUDGE_WEIGHTS = {"rule": 0.5, "llm": 0.3, "grounding": 0.2}


def _meaningful_words(text: str) -> set[str]:
    """Lowercased tokens with len>4 that are not stopwords."""
    out: set[str] = set()
    for tok in re.findall(r"[A-Za-z]+", text or ""):
        w = tok.lower()
        if len(w) > 4 and w not in _STOPWORDS:
            out.add(w)
    return out


def _grounding_judge(case: dict, response: str) -> float | None:
    """Deterministic grounding score for cases that carry context.

    Splits the response into sentences and returns the fraction of sentences
    that share >= 2 meaningful words with the context.  Returns ``None`` when
    the case has no context (judge absent).
    """
    context = (case.get("context") or "").strip()
    if not context:
        return None
    ctx_words = _meaningful_words(context)
    if not ctx_words:
        return 0.0
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", response or "") if s.strip()]
    if not sentences:
        return 0.0
    grounded = 0
    for sent in sentences:
        overlap = _meaningful_words(sent) & ctx_words
        if len(overlap) >= 2:
            grounded += 1
    return grounded / len(sentences)


# Judge rubric (system prompt).  No format placeholders — it is used verbatim,
# so a DB-sourced override needs no substitution.  Exact current text.
_JUDGE_RUBRIC = (
    "You are a strict evaluation judge. Given a question, the expected "
    "answer criteria, and a candidate response, rate how well the response "
    "satisfies the criteria.\n"
    "Reply with ONLY a JSON object of the form "
    '{\"score\": <number 0.0-1.0>, \"reason\": \"<one sentence>\"}. '
    "Do not include any other text."
)



# ── Writing-specific prompt constants ─────────────────────────────────────────
# Both constants are used by seed_default_prompts to seed the write.draft and
# write.critic slots.  They may be overridden per-project via the MCOS prompt
# registry without a code change.

_WRITE_DRAFT_PROMPT = """You are a prose drafter working on a long-form narrative. Your only job is to write the next passage — not to explain, summarise, or plan.

CRAFT STANDARDS — apply without exception:
• Lead with action or sensation, not setup. The scene begins mid-breath, not mid-explanation.
• Be specific. A bone-handled knife that has cut five hundred cords is more alive than "an old knife." Specificity is not decoration — it is the thing itself.
• Earn every sentence. If a sentence could be cut without losing meaning, cut it.
• Vary length deliberately. Short sentences land. They close a thought the way a door closes — final, without echo. Save them for the point that must not be missed.
• No filler phrases. Never write "suddenly", "in that moment", "he felt himself", "she couldn't help but", "it was as if", or similar clichés. Name the thing directly.
• Active voice, strong verbs. Characters do things. Things happen to characters only when the grammar of helplessness is the point.
• Subtext, not explication. Do not explain what the reader has already felt. Trust them.
• Match the voice of any provided voice sample exactly — rhythm, register, and weight.

STRUCTURAL DISCIPLINE:
• Write only the assigned beat. Do not foreshadow future beats or summarise past ones.
• Hit the word target within 10 percent. Pacing is craft, not accident.
• End the beat at a point of small tension or earned stillness — never mid-thought.

Placeholders available: {beat_objective}, {word_target}, {theological_anchor}, {previous_beat_text}, {voice_sample}."""

_WRITE_CRITIC_PROMPT = """You are an adversarial editor. Your job is to find every flaw in the draft before the author sees it — then report precisely and fix nothing yourself.

REVIEW CHECKLIST — evaluate in this order:
1. VOICE: Does the draft match the provided voice sample in rhythm, register, and density? Flag any sentence that sounds like a different author.
2. FILLER: List every phrase that adds no meaning. Examples: "suddenly", "in that moment", "it was as if", "he felt himself", "needless to say", "certainly", hedges like "sort of" or "kind of" used without irony.
3. PASSIVE CONSTRUCTIONS: Flag every passive-voice sentence where active voice would be stronger. Quote the sentence, propose the fix.
4. VAGUENESS: Flag any noun or verb that could be made more specific. "Moved quickly" — how? "Said angrily" — what did her voice do?
5. PACING: Does sentence-length variation serve the emotional arc? Note any stretches where length is uniform and the prose goes flat.
6. INTERNAL CONSISTENCY: Flag any continuity errors against prior context.
7. STRUCTURAL INTEGRITY: Does the beat end at a point of earned tension or stillness? Does it avoid mid-thought cuts?

OUTPUT FORMAT:
Return a numbered list of findings. Each finding: the flaw category, the quoted text, and a one-sentence instruction for the revision. Do not rewrite the prose. Do not praise what works. If the draft is clean, say: 'No findings.'
"""


def _llm_judge(case: dict, response: str, cfg: Any, db: Any) -> tuple[float | None, str | None]:
    """LLM grader over a strict JSON rubric.

    Returns ``(score, reason)``.  On any failure — call error, unparseable
    JSON, or a missing score — returns ``(None, None)`` so the judge is simply
    absent and never fails the case.
    """
    expected_concepts = case.get("expected_concepts")
    if isinstance(expected_concepts, str):
        expected_concepts = _jload(expected_concepts, [])
    expected_output = case.get("expected_output") or ""
    expectation_lines = []
    if expected_concepts:
        expectation_lines.append(
            "Expected concepts: " + ", ".join(str(c) for c in expected_concepts))
    if expected_output:
        expectation_lines.append("Expected output: " + str(expected_output))
    expectation = "\n".join(expectation_lines) or "(no explicit expectation)"

    # Judge rubric is the system prompt; prefer the active registry template
    # (slot 'mcos.judge') with the hardcoded constant as a never-break fallback.
    rubric = _JUDGE_RUBRIC
    try:
        active = db.get_active_prompt("mcos.judge") if db is not None else None
        if active:
            rubric = active
    except Exception:
        rubric = _JUDGE_RUBRIC
    user = (
        f"Question:\n{case.get('question', '')}\n\n"
        f"{expectation}\n\n"
        f"Candidate response:\n{response}"
    )
    try:
        result = llm_call(
            messages=[{"role": "system", "content": rubric},
                      {"role": "user", "content": user}],
            cfg=cfg, db=db, purpose="mcos.judge", timeout=45,
        )
    except Exception as exc:
        logger.debug("llm judge call raised: %s", exc)
        return None, None
    if not result.ok or not result.text:
        return None, None
    parsed = _extract_json(result.text)
    if not isinstance(parsed, dict) or "score" not in parsed:
        return None, None
    try:
        score = float(parsed["score"])
    except (TypeError, ValueError):
        return None, None
    # Reject NaN / +-inf BEFORE clamping — min/max let NaN through, which would
    # then poison consensus, avg_score, delta and JSON serialization.
    if not math.isfinite(score):
        return None, None
    score = max(0.0, min(1.0, score))
    reason = parsed.get("reason")
    reason = str(reason)[:500] if reason is not None else None
    return score, reason


def _consensus(judges: dict[str, float]) -> float:
    """Weighted average over present judges, renormalized to their weights.

    Defensively skips any non-finite judge value (NaN/inf) so a single bad
    judge can never poison the consensus, avg_score or JSON serialization.
    """
    clean = {name: val for name, val in judges.items()
             if isinstance(val, (int, float)) and math.isfinite(val)}
    if not clean:
        return 0.0
    total_w = sum(_JUDGE_WEIGHTS.get(name, 0.0) for name in clean)
    if total_w <= 0:
        # Fallback: plain mean if none of the names carry a weight.
        return sum(clean.values()) / len(clean)
    return sum(_JUDGE_WEIGHTS.get(name, 0.0) * val
               for name, val in clean.items()) / total_w


def _judge_case(case: dict, response: str, cfg: Any, db: Any,
                *, ai_reachable: bool) -> dict:
    """Run all applicable judges and build the judge_scores blob.

    Always includes ``rule``; adds ``grounding`` when the case has context and
    ``llm`` when AI is reachable and the grader returns a usable score.  Sets
    ``consensus`` (the eval_results.score) and optional ``llm_reason``.
    """
    judges: dict[str, float] = {}
    blob: dict[str, Any] = {}

    rule_score = score_response(case, response)
    judges["rule"] = rule_score
    blob["rule"] = rule_score

    grounding = _grounding_judge(case, response)
    if grounding is not None:
        judges["grounding"] = grounding
        blob["grounding"] = grounding

    if ai_reachable:
        llm_score, llm_reason = _llm_judge(case, response, cfg, db)
        if llm_score is not None:
            judges["llm"] = llm_score
            blob["llm"] = llm_score
            if llm_reason:
                blob["llm_reason"] = llm_reason

    consensus = _consensus(judges)
    blob["consensus"] = consensus
    return blob


# ── Run execution ────────────────────────────────────────────────────────────

def _prev_finished_avg(db: Any, benchmark_id: str, exclude_run_id: str) -> float | None:
    """Return the avg_score of the most recent previously-finished NORMAL run.

    Prompt A/B runs (meta carries a ``prompt_id``) are excluded so they neither
    become a regression baseline nor are compared against normal runs.
    """
    with db._lock:
        row = db._conn.execute(
            "SELECT avg_score FROM eval_runs WHERE benchmark_id=? AND status='done' "
            "AND id != ? AND avg_score IS NOT NULL "
            "AND (NOT json_valid(meta) OR json_extract(meta, '$.prompt_id') IS NULL) "
            "ORDER BY finished_at DESC, started_at DESC LIMIT 1",
            (benchmark_id, exclude_run_id),
        ).fetchone()
    return float(row["avg_score"]) if row and row["avg_score"] is not None else None


def _create_run_row(db: Any, cfg: Any, benchmark_id: str,
                    *, initial_meta: dict | None = None) -> str:
    """Insert a fresh ``eval_runs`` row (status='running') and return its id.

    ``initial_meta`` seeds the meta blob (e.g. prompt attribution) so a run is
    attributable even while still running / if it later fails.
    """
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
            "meta) VALUES(?,?,?,?,'running',?,?)",
            (run_id, benchmark_id, _now(), model, int(n_cases),
             _jdump(initial_meta or {})),
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


def _enabled_llm_benchmarks(db: Any) -> list[dict]:
    """Return enabled kind='llm' benchmarks (prompt benchmarks skip retrieval)."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT id, name FROM benchmarks WHERE enabled=1 AND kind='llm' "
            "ORDER BY category, name"
        ).fetchall()
    return [dict(r) for r in rows]


def run_prompt_benchmark(db: Any, cfg: Any, prompt_id: str) -> dict:
    """Run every enabled llm suite twice: once with the candidate prompt as the
    system preamble, once with the currently active prompt for the same slot.

    Runs sequentially in the calling (background) worker to avoid hammering the
    local model.  Each eval_runs row is tagged in meta with prompt_id /
    prompt_version / prompt_role so the paired runs are attributable.  Returns
    ``{"candidate_runs":[...],"active_runs":[...]}``.
    """
    with db._lock:
        cand = db._conn.execute(
            "SELECT id, slot, content, version FROM prompts WHERE id=?", (prompt_id,)
        ).fetchone()
    if cand is None:
        raise ValueError(f"unknown prompt: {prompt_id}")
    cand = dict(cand)
    slot = cand["slot"]
    with db._lock:
        active = db._conn.execute(
            "SELECT id, content, version FROM prompts WHERE slot=? AND active=1 LIMIT 1",
            (slot,),
        ).fetchone()
    active = dict(active) if active else None

    suites = _enabled_llm_benchmarks(db)
    candidate_runs: list[str] = []
    active_runs: list[str] = []

    for suite in suites:
        bid = suite["id"]
        # Candidate run.
        c_meta = {"prompt_id": prompt_id, "prompt_version": cand["version"],
                  "prompt_role": "candidate", "prompt_slot": slot}
        c_run = _create_run_row(db, cfg, bid, initial_meta=c_meta)
        candidate_runs.append(c_run)
        _execute_run(db, cfg, bid, c_run,
                     system_prompt=cand["content"], run_meta=c_meta)

        # Active run (only if there is an active prompt to compare against).
        if active:
            a_meta = {"prompt_id": prompt_id, "prompt_version": active["version"],
                      "prompt_role": "active", "prompt_slot": slot,
                      "active_prompt_id": active["id"]}
            a_run = _create_run_row(db, cfg, bid, initial_meta=a_meta)
            active_runs.append(a_run)
            _execute_run(db, cfg, bid, a_run,
                         system_prompt=active["content"], run_meta=a_meta)

    return {"candidate_runs": candidate_runs, "active_runs": active_runs}


def _prev_prompt_health_aggregate(db: Any, slot: str,
                                  before_session: str) -> float | None:
    """Mean avg_score of the most recent PRIOR prompt-health session for a slot.

    Sessions are identified by ``meta.prompt_health_session`` (an ISO timestamp
    stamped on every run of a set).  Returns the mean over that session's done
    runs, or None when there is no prior session.
    """
    with db._lock:
        prev_session = db._conn.execute(
            "SELECT json_extract(meta,'$.prompt_health_session') AS s "
            "FROM eval_runs WHERE status='done' AND json_valid(meta) "
            "AND json_extract(meta,'$.prompt_health')=1 "
            "AND json_extract(meta,'$.prompt_slot')=? "
            "AND json_extract(meta,'$.prompt_health_session') < ? "
            "ORDER BY s DESC LIMIT 1",
            (slot, before_session),
        ).fetchone()
        if not prev_session or prev_session["s"] is None:
            return None
        rows = db._conn.execute(
            "SELECT avg_score FROM eval_runs WHERE status='done' "
            "AND json_valid(meta) AND json_extract(meta,'$.prompt_health')=1 "
            "AND json_extract(meta,'$.prompt_slot')=? "
            "AND json_extract(meta,'$.prompt_health_session')=? "
            "AND avg_score IS NOT NULL",
            (slot, prev_session["s"]),
        ).fetchall()
    vals = [float(r["avg_score"]) for r in rows if r["avg_score"] is not None]
    return (sum(vals) / len(vals)) if vals else None


def _validate_nonbenchmarkable_slot(slot: str, content: str) -> tuple[bool, str]:
    """Structural validation for slots that cannot be benchmarked with suites.

    Returns ``(ok, reason)``.  A False result means the prompt content is
    broken in a detectable way (empty, or missing required placeholders).
    This is the only quality signal available for these slots at nightly time.

    Checks per slot:
      * harvest.extract — must contain ``{title}`` and ``{chunk}`` and be
        syntactically valid as a ``str.format()`` template.
      * mcos.judge     — must be non-empty (used verbatim; no placeholders).
      * Any unknown slot — non-empty check only.
    """
    if not content or not content.strip():
        return False, "empty prompt"
    if slot == "harvest.extract":
        if "{title}" not in content:
            return False, "missing {title} placeholder"
        if "{chunk}" not in content:
            return False, "missing {chunk} placeholder"
        try:
            content.format(title="t", chunk="c")
        except (KeyError, ValueError, IndexError) as exc:
            return False, f"template format error: {exc}"
    return True, "content valid"


def _run_prompt_health_for_slot(db: Any, cfg: Any, slot: str) -> dict:
    """Run the prompt health check for a single named ``slot``.

    For **benchmarkable** slots (``chat.base``): runs every enabled llm suite
    with the active prompt as the system preamble, compares the session's
    aggregate avg_score against the prior nightly session for the same slot,
    and flags ``prompt_health_regressed=true`` (with a ``prompt_regression``
    audit) when the drop exceeds 0.15.

    For **non-benchmarkable** slots (``harvest.extract``, ``mcos.judge``):
    performs structural validation only — no benchmark runs are created.  The
    returned dict carries ``skipped=True`` and a ``reason`` of ``"content
    valid"`` (or an error description); regressions are never flagged for these
    slots because suite-score comparison is not meaningful.

    Returns a per-slot summary dict that always contains at minimum:
        ok, slot, slot_label, runs (list), prompt_name, prompt_version
    Benchmarkable slots also carry: current_agg, prev_agg, delta, regressed,
        flagged_run_id.
    Non-benchmarkable slots also carry: skipped=True, reason.
    """
    slot_info = PROMPT_SLOTS.get(slot, {"label": slot, "benchmarkable": False})
    slot_label = slot_info["label"]

    with db._lock:
        active = db._conn.execute(
            "SELECT id, name, content, version FROM prompts "
            "WHERE slot=? AND active=1 LIMIT 1", (slot,),
        ).fetchone()
    if active is None:
        return {"ok": False, "slot": slot, "slot_label": slot_label,
                "reason": f"no active {slot} prompt", "runs": []}
    active = dict(active)

    # ── Non-benchmarkable: structural validation only ─────────────────────────
    if not slot_info.get("benchmarkable"):
        ok, reason = _validate_nonbenchmarkable_slot(slot, active["content"])
        return {
            "ok": ok, "slot": slot, "slot_label": slot_label,
            "prompt_name": active["name"], "prompt_version": active["version"],
            "skipped": True,
            "reason": reason,
            "runs": [],
        }

    # ── Benchmarkable: run suites ────────────────────────────────────────────
    suites = _enabled_llm_benchmarks(db)
    if not suites:
        return {"ok": False, "slot": slot, "slot_label": slot_label,
                "reason": "no enabled llm suites", "runs": []}

    session = _now()
    run_ids: list[str] = []
    scores: list[float] = []
    for suite in suites:
        bid = suite["id"]
        meta = {"prompt_health": True, "prompt_id": active["id"],
                "prompt_version": active["version"], "prompt_slot": slot,
                "prompt_health_session": session}
        rid = _create_run_row(db, cfg, bid, initial_meta=meta)
        run_ids.append(rid)
        _execute_run(db, cfg, bid, rid,
                     system_prompt=active["content"], run_meta=meta)
        with db._lock:
            row = db._conn.execute(
                "SELECT avg_score FROM eval_runs WHERE id=?", (rid,)
            ).fetchone()
        if row and row["avg_score"] is not None:
            scores.append(float(row["avg_score"]))

    current_agg = (sum(scores) / len(scores)) if scores else None
    prev_agg = _prev_prompt_health_aggregate(db, slot, session)
    regressed = False
    delta = None
    if current_agg is not None and prev_agg is not None:
        delta = round(current_agg - prev_agg, 6)
        regressed = delta < -0.15

    flagged_id: str | None = None
    if regressed and run_ids:
        # Flag the most recent run of this session whose status is 'done' — NOT
        # simply run_ids[-1], which may be a failed row.  /regressions filters
        # status='done', so flagging a failed run would make the regression
        # invisible and un-ackable.  If no run finished, we skip the flag but
        # still audit (noting there is no ackable run) so governance sees it.
        with db._lock:
            done_row = db._conn.execute(
                "SELECT id FROM eval_runs WHERE status='done' AND json_valid(meta) "
                "AND json_extract(meta,'$.prompt_health')=1 "
                "AND json_extract(meta,'$.prompt_health_session')=? "
                "ORDER BY finished_at DESC, started_at DESC LIMIT 1",
                (session,),
            ).fetchone()
            if done_row is not None:
                flagged_id = done_row["id"]
                db._conn.execute(
                    "UPDATE eval_runs SET meta=json_set("
                    "CASE WHEN json_valid(meta) THEN meta ELSE '{}' END, "
                    "'$.prompt_health_regressed', json('true')) WHERE id=?",
                    (flagged_id,),
                )
                db._conn.commit()
        try:
            note = "" if flagged_id else " (no finished run — not ackable)"
            db.audit(
                "prompt_regression",
                object_id=flagged_id or run_ids[-1],
                object_type="eval_run",
                actor="mcos", result="warn",
                detail=(f"slot={slot} prompt='{active['name']}' v{active['version']} "
                        f"nightly avg {current_agg:.4f} vs prev {prev_agg:.4f} "
                        f"(delta={delta}, dropped > 0.15){note}"),
            )
        except Exception as exc:  # never let governance logging strand the pass
            logger.warning("prompt_regression audit failed: %s", exc)

    return {
        "ok": True, "slot": slot, "slot_label": slot_label,
        "runs": run_ids, "current_agg": current_agg,
        "prev_agg": prev_agg, "delta": delta, "regressed": regressed,
        "flagged_run_id": flagged_id,
        "prompt_name": active["name"], "prompt_version": active["version"],
    }


def run_prompt_health(db: Any, cfg: Any,
                      slot: str | None = None) -> dict | list[dict]:
    """Nightly health check for active prompts.

    When ``slot`` is given, runs the health check for that single slot and
    returns a single result dict (backward-compatible with the original API).

    When ``slot`` is ``None`` (the default, used by nightshift), iterates
    every registered slot in ``PROMPT_SLOTS`` that has an active prompt and
    returns a **list** of per-slot result dicts — one entry per slot checked,
    in PROMPT_SLOTS definition order.

    Each result dict always contains: ok, slot, slot_label, runs (list),
    prompt_name (when an active prompt exists), prompt_version.
    Benchmarkable slots additionally carry: current_agg, prev_agg, delta,
    regressed, flagged_run_id.
    Non-benchmarkable slots additionally carry: skipped=True, reason.
    """
    if slot is not None:
        return _run_prompt_health_for_slot(db, cfg, slot)

    # Iterate ALL registered slots; each runs independently.
    results: list[dict] = []
    for s in PROMPT_SLOTS:
        try:
            results.append(_run_prompt_health_for_slot(db, cfg, s))
        except Exception as exc:
            logger.warning("prompt health check failed for slot %s: %s", s, exc)
            results.append({
                "ok": False, "slot": s,
                "slot_label": PROMPT_SLOTS[s]["label"],
                "reason": f"error: {exc}", "runs": [],
            })
    return results


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


def _execute_run(db: Any, cfg: Any, benchmark_id: str, run_id: str,
                 *, system_prompt: str | None = None,
                 run_meta: dict | None = None) -> str:
    """Run every case for an already-created ``eval_runs`` row and finalize it.

    The ENTIRE worker body — benchmark lookup, case loading and the case loop —
    is wrapped so that any exception (including pre-loop setup failures) marks
    the reserved row ``failed`` with a finished_at + error, rather than leaving
    it stuck at ``running`` (which would otherwise trip the 409 guard forever).

    ``system_prompt`` (Phase 4): when set, it is prepended as a single system
    message before every case; if a case also carries context, the two are
    merged into ONE system message.  ``run_meta`` carries extra attribution
    (e.g. prompt_id/prompt_version/prompt_role) that is merged into the final
    meta.  Runs carrying a ``prompt_id`` are excluded from regression baselines
    and never emit a regression audit.
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

        # AI-reachability is learned from the eval calls themselves — no
        # per-case probe.  Once an eval call succeeds we know the LLM judge is
        # worth attempting; if the first eval call fails we treat AI as
        # unreachable for judging.
        ai_reachable = False

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
                    ctx_block = ("Use the following context to answer.\n\n" + ctx) if ctx else ""
                    # Merge the injected system preamble and the per-case
                    # context into a SINGLE system message.
                    sys_parts = [p for p in (system_prompt, ctx_block) if p]
                    if sys_parts:
                        messages.append({
                            "role": "system",
                            "content": "\n\n".join(sys_parts),
                        })
                    messages.append({"role": "user", "content": case.get("question", "")})
                    result = llm_call(
                        messages=messages, cfg=cfg, db=db,
                        purpose="mcos.eval", timeout=60,
                    )
                    latency_ms = result.latency_ms
                    if not result.ok:
                        err = result.error or "llm call failed"
                        # No response to grade; rule-only zero.
                        judge_scores = {"rule": 0.0, "consensus": 0.0}
                        score = 0.0
                    else:
                        ai_reachable = True
                        response = result.text or ""
                        judge_scores = _judge_case(
                            case, response, cfg, db, ai_reachable=ai_reachable)
                        score = judge_scores["consensus"]
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
    is_prompt_run = bool(run_meta and run_meta.get("prompt_id"))
    delta = None
    regressed = False
    # Prompt A/B runs are attribution-only: never compute a regression against
    # normal-run baselines and never flag them as regressions.
    if not is_prompt_run:
        try:
            prev_avg = _prev_finished_avg(db, benchmark_id, run_id)
            if avg_score is not None and prev_avg is not None:
                delta = round(avg_score - prev_avg, 6)
                regressed = delta < -0.15
        except Exception as exc:  # never let delta computation strand the row
            logger.warning("delta computation for run %s failed: %s", run_id[:8], exc)
    meta: dict = {"delta": delta, "regressed": regressed}
    if run_meta:
        meta.update(run_meta)
    if run_error:
        meta["error"] = run_error

    _finalize_run(db, run_id, status=run_status, avg_score=avg_score, meta=meta)

    # Phase 3 — surface regressions to governance via the audit log.
    if regressed and not is_prompt_run:
        try:
            db.audit(
                "benchmark_regression",
                object_id=run_id,
                object_type="eval_run",
                actor="mcos",
                result="warn",
                detail=(f"benchmark={benchmark_id} avg={avg_score} delta={delta} "
                        f"(dropped > 0.15 vs previous run)"),
            )
        except Exception as exc:  # never let governance logging strand the run
            logger.warning("regression audit for run %s failed: %s", run_id[:8], exc)

    logger.info("benchmark %s run %s: status=%s avg=%s delta=%s",
                benchmark_id, run_id[:8], run_status, avg_score, delta)
    return run_id


# ── RAG calibration sweep (Phase 5) ──────────────────────────────────────────

# Grid of (target_words, overlap_words) combos; overlap must be < target/2.
_SWEEP_TARGETS = [300, 500, 800]
_SWEEP_OVERLAPS = [30, 50, 80]
_SWEEP_MAX_DOCS = 8
_SWEEP_QUERIES_PER_DOC = 3


def _sweep_grid() -> list[tuple[int, int]]:
    return [(t, o) for t in _SWEEP_TARGETS for o in _SWEEP_OVERLAPS if o < t / 2]


def _sample_sweep_docs(db: Any, limit: int = _SWEEP_MAX_DOCS) -> list[dict]:
    """Reconstruct up to ``limit`` docs' text from their chunks.

    Text is rebuilt by concatenating a doc's chunks ordered by (page, id).
    NOTE: because stored chunks overlap, the reconstruction contains duplicated
    overlap regions — acceptable for calibration (we only need representative
    text to re-chunk in memory, not a faithful original).
    """
    with db._lock:
        doc_rows = db._conn.execute(
            "SELECT doc_id FROM chunks GROUP BY doc_id "
            "HAVING COUNT(*) > 0 ORDER BY doc_id LIMIT ?",
            (limit,),
        ).fetchall()
    docs: list[dict] = []
    for dr in doc_rows:
        doc_id = dr["doc_id"]
        with db._lock:
            crows = db._conn.execute(
                "SELECT text FROM chunks WHERE doc_id=? ORDER BY page, id",
                (doc_id,),
            ).fetchall()
        text = " ".join((c["text"] or "").strip() for c in crows if c["text"])
        if text.strip():
            docs.append({"doc_id": doc_id, "text": text})
    return docs


def _sweep_queries(docs: list[dict]) -> list[dict]:
    """Up to N distinctive sentences (>=25 chars) per doc, labeled by doc_id."""
    queries: list[dict] = []
    for d in docs:
        sentences = re.split(r"(?<=[.!?])\s+", d["text"])
        taken = 0
        for s in sentences:
            s = s.strip()
            if len(s) >= 25 and len(_meaningful_words(s)) >= 2:
                queries.append({"doc_id": d["doc_id"], "query": s[:300]})
                taken += 1
                if taken >= _SWEEP_QUERIES_PER_DOC:
                    break
    return queries


def _score_combo(docs: list[dict], queries: list[dict],
                 target: int, overlap: int) -> tuple[float, int]:
    """Re-chunk every doc in memory, build a chunk→doc index, rank chunks per
    query by shared meaningful-word count, and score retrieval.

    Score per query: 1.0 if the correct doc appears among the top-3 chunks'
    docs, 0.5 if among the top-10, else 0.  Combo score = mean over queries.
    Returns ``(mean_score, total_chunk_count)``.  No DB writes, no LLM calls.
    """
    from orivellum.capabilities.chunking import _sliding_chunks

    index: list[tuple[str, set[str]]] = []  # (doc_id, meaningful words)
    for d in docs:
        for ch in _sliding_chunks(d["text"], target, overlap):
            ch = ch.strip()
            if len(ch) < 20:
                continue
            index.append((d["doc_id"], _meaningful_words(ch)))
    chunk_count = len(index)
    if not index or not queries:
        return 0.0, chunk_count

    scores: list[float] = []
    for q in queries:
        q_words = _meaningful_words(q["query"])
        if not q_words:
            scores.append(0.0)
            continue
        ranked = sorted(
            index,
            key=lambda entry: len(entry[1] & q_words),
            reverse=True,
        )
        top_docs = [doc_id for doc_id, _ in ranked]
        expected = q["doc_id"]
        if expected in top_docs[:3]:
            scores.append(1.0)
        elif expected in top_docs[:10]:
            scores.append(0.5)
        else:
            scores.append(0.0)
    mean = sum(scores) / len(scores) if scores else 0.0
    return mean, chunk_count


def _finalize_sweep(db: Any, sweep_id: str, *, status: str,
                    results: list[dict], docs_sampled: int, meta: dict) -> None:
    """Best-effort, retried final write for a sweep (never leaves it running)."""
    for attempt in range(3):
        try:
            with db._lock:
                db._conn.execute(
                    "UPDATE rag_sweeps SET status=?, finished_at=?, results=?, "
                    "docs_sampled=?, meta=? WHERE id=?",
                    (status, _now(), _jdump(results), int(docs_sampled),
                     _jdump(meta), sweep_id),
                )
                db._conn.commit()
            return
        except Exception as exc:  # pragma: no cover — retry path
            logger.warning("finalize sweep %s attempt %d failed: %s",
                           sweep_id[:8], attempt + 1, exc)
    logger.error("finalize sweep %s permanently failed — row may be stuck",
                 sweep_id[:8])


def rag_sweep(db: Any, sweep_id: str) -> str:
    """Execute a chunking grid-search for an already-created ``rag_sweeps`` row.

    Samples docs, builds queries, scores every (target, overlap) combo purely
    in memory (no chunk-table writes, no LLM), and finalizes the row.  The whole
    body is guarded so the row is never left stuck at ``running``.
    """
    status = "done"
    results: list[dict] = []
    docs_sampled = 0
    meta: dict = {}
    try:
        docs = _sample_sweep_docs(db)
        docs_sampled = len(docs)
        queries = _sweep_queries(docs)
        best: dict | None = None
        for target, overlap in _sweep_grid():
            score, chunk_count = _score_combo(docs, queries, target, overlap)
            row = {"target_words": target, "overlap_words": overlap,
                   "score": round(score, 6), "chunk_count": chunk_count}
            results.append(row)
            if best is None or score > best["score"]:
                best = {"target_words": target, "overlap_words": overlap,
                        "score": round(score, 6)}
        meta = {"best": best, "queries": len(queries)}
    except Exception as exc:
        status = "failed"
        meta = {"error": f"{type(exc).__name__}: {exc}"[:500]}
        logger.error("rag sweep %s crashed: %s", sweep_id[:8], exc, exc_info=True)

    _finalize_sweep(db, sweep_id, status=status, results=results,
                    docs_sampled=docs_sampled, meta=meta)
    logger.info("rag sweep %s: status=%s docs=%d combos=%d",
                sweep_id[:8], status, docs_sampled, len(results))
    return sweep_id


def create_sweep_row(db: Any) -> str:
    """Insert a running rag_sweeps row and return its id."""
    sweep_id = _uuid()
    with db._lock:
        db._conn.execute(
            "INSERT INTO rag_sweeps(id,started_at,status,docs_sampled,results,meta)"
            " VALUES(?,?,'running',0,'[]','{}')",
            (sweep_id, _now()),
        )
        db._conn.commit()
    return sweep_id


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
