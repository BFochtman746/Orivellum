"""Hybrid three-channel memory retrieval + multi-stage reranking.

Channel architecture (Phase 2a — hybrid retrieval)
--------------------
1. **Semantic** (default weight 0.5)
   Embed the query via the configured embeddings endpoint, find conversation
   chunks with similar vectors (``conv_chunk`` object type), then return
   current memory facts captured from those conversations (``source_conv_id``
   match).  Degrades to an empty list when the embedding service is down so
   the other two channels continue to serve results.

2. **Lexical** (default weight 0.3)
   BM25/FTS5 search over the ``user_memory_fts`` virtual table (keys +
   values).  Always runs — independent of the embedding service.  Falls back
   to a plain LIKE search on pre-v101 schemas.

3. **Graph** (default weight 0.2)
   Extracts candidate entity names from the query, traverses one hop along
   ``edges``, then returns current memory facts whose key or value mentions
   any of the collected entity names.  Returns [] when no entities match.

All three channels are invoked concurrently (via ``concurrent.futures``).
Results are merged with weighted Reciprocal Rank Fusion (RRF), deduplicated
by memory id, and annotated with a ``retrieval_source`` field:

    'semantic' | 'lexical' | 'graph' | 'multi'

Items appearing in two or more channels receive the ``'multi'`` tag and a
higher combined RRF score (they are boosted by accumulating weight from
multiple channels).

Reranking pipeline (Phase 2b — this module)
-------------------------------------------
After hybrid retrieval, ``rerank_memories`` applies three successive stages:

    Stage 1 — Graph-native boost
        Candidates whose key/value text mentions entities extracted from the
        query receive a multiplicative boost to their rrf_score.  Pure Python,
        zero LLM calls, always runs.

    Stage 2 — Cross-encoder (pointwise LLM)
        Up to 20 candidates are scored individually by the LLM on a 0-10
        relevance scale.  Gated by the ``ai_reranking_enabled`` DB setting;
        all calls are run concurrently with an 8-second wall-clock timeout.

    Stage 3 — Listwise LLM rerank
        The top-10 cross-encoder survivors are submitted to the LLM as a
        numbered list; the model returns the optimal order.  Fires only when
        ≥ 3 candidates remain and ``ai_reranking_enabled`` is true.  Falls
        back silently on any error.

For complex multi-hop queries ``ReActMemoryAgent`` runs an iterative
tool-use loop (max 4 iterations) to collect candidates from multiple
angles before the reranking pipeline is applied.

Configurable weights
--------------------
The three retrieval weights are exposed as keyword arguments to
``search_memories`` and can be overridden per-call::

    semantic_weight = 0.5
    lexical_weight  = 0.3
    graph_weight    = 0.2

Graceful degradation
--------------------
Every stage is wrapped in a try/except.  Failures are logged at DEBUG and
the previous stage's output is returned unchanged so a bad embedding
endpoint or LLM timeout never silently breaks the response path.
"""

from __future__ import annotations

import concurrent.futures
import json as _json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.memory")

# Reciprocal rank fusion constant — standard value used across the codebase.
_RRF_K: int = 60

# Default channel weights (configurable per-call).
_W_SEMANTIC: float = 0.5
_W_LEXICAL: float = 0.3
_W_GRAPH: float = 0.2

# Maximum conversation chunks to use when deriving semantic memory hits.
_SEM_CHUNK_LIMIT: int = 10

# ─── Reranking constants ──────────────────────────────────────────────────────

# Multiplicative boost applied to rrf_score per matched entity in stage 1.
_GRAPH_BOOST_MULT: float = 1.5

# Max candidates sent to the cross-encoder (stage 2).
_CE_MAX_CANDIDATES: int = 20

# Wall-clock budget (seconds) shared across all concurrent pointwise calls.
_CE_TOTAL_TIMEOUT: float = 8.0

# Max candidates passed to the listwise LLM reranker (stage 3).
# Must align with rerank._LLM_TOP_K for RRF fusion consistency.
_LW_TOP_K: int = 10

# Default number of top reranked facts returned from rerank_memories.
_RERANK_TOP_K: int = 8

# Complexity score threshold above which ReActMemoryAgent fires instead of
# direct hybrid retrieval.  Tune by observing real query patterns.
_COMPLEXITY_THRESHOLD: int = 2

# Stopwords filtered out during entity extraction and graph boost.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "are",
        "was",
        "were",
        "will",
        "with",
        "this",
        "that",
        "from",
        "have",
        "had",
        "has",
        "been",
        "what",
        "when",
        "where",
        "who",
        "how",
        "why",
        "can",
        "did",
        "does",
        "not",
        "but",
        "his",
        "her",
        "its",
        "they",
        "them",
        "their",
        "our",
        "your",
        "all",
        "any",
        "some",
        "you",
        "which",
        "about",
        "into",
        "out",
        "get",
        "got",
        "let",
        "use",
    }
)


# ─── Channel implementations ──────────────────────────────────────────────────


def _channel_semantic(query: str, db: OrivellumDB) -> list[dict]:
    """Semantic channel: conv_chunk vectors → source_conv_id cross-reference.

    1. Embed the query and find the most similar conversation chunks.
    2. Collect the ``conv_id`` values from those chunks.
    3. Return current memory facts whose ``source_conv_id`` appears in that set.

    Returns [] when the embedding service is unavailable so the caller
    degrades gracefully to the remaining channels.
    """
    try:
        from orivellum.capabilities.embeddings import semantic_search_conversations

        chunk_hits = semantic_search_conversations(query, db, limit=_SEM_CHUNK_LIMIT)
        if not chunk_hits:
            return []

        # Build a set of conv_ids that are semantically related to the query
        related_conv_ids: set[str] = set()
        for h in chunk_hits:
            cid = h.get("conv_id")
            if cid:
                related_conv_ids.add(cid)
        if not related_conv_ids:
            return []

        # Retrieve current memory facts from those conversations.
        # We carry a proxy 'score' derived from the best chunk score for the
        # conv — used later in the score combination step.
        conv_best_score: dict[str, float] = {}
        for h in chunk_hits:
            cid = h.get("conv_id") or ""
            s = float(h.get("score", 0.0))
            if cid and s > conv_best_score.get(cid, 0.0):
                conv_best_score[cid] = s

        with db._lock:
            try:
                placeholders = ",".join("?" * len(related_conv_ids))
                rows = db._conn.execute(
                    f"""SELECT id, key, value, memory_type,
                               valid_from, valid_to, txn_time,
                               source_conv_id, source_evidence_id, created_at
                        FROM user_memory
                        WHERE valid_to IS NULL
                          AND source_conv_id IN ({placeholders})""",
                    list(related_conv_ids),
                ).fetchall()
            except Exception as exc:
                logger.debug("semantic channel DB lookup failed: %s", exc)
                return []

        results: list[dict] = []
        for row in rows:
            fact = dict(row)
            fact["_sem_score"] = conv_best_score.get(fact.get("source_conv_id") or "", 0.0)
            results.append(fact)

        # Sort by proxy semantic score so higher-similarity convs rank first
        results.sort(key=lambda x: x["_sem_score"], reverse=True)
        return results

    except Exception as exc:
        logger.debug("semantic channel failed: %s", exc)
        return []


def _channel_lexical(query: str, db: OrivellumDB, limit: int = 20) -> list[dict]:
    """Lexical channel: BM25/FTS5 search over user_memory_fts.

    Delegates to ``db.search_memories_lexical`` which handles both the FTS5
    path and the LIKE fallback transparently.  Returns [] on any error.
    """
    try:
        return db.search_memories_lexical(query, limit=limit)
    except Exception as exc:
        logger.debug("lexical channel failed: %s", exc)
        return []


def _channel_graph(query: str, db: OrivellumDB, limit: int = 20) -> list[dict]:
    """Graph channel: entity traversal → memory fact mention match.

    Delegates to ``db.search_memories_graph`` which handles entity lookup,
    1-hop edge traversal, and fact scanning.  Returns [] on any error.
    """
    try:
        return db.search_memories_graph(query, limit=limit)
    except Exception as exc:
        logger.debug("graph channel failed: %s", exc)
        return []


# ─── Merge layer ──────────────────────────────────────────────────────────────


def _merge(
    semantic_hits: list[dict],
    lexical_hits: list[dict],
    graph_hits: list[dict],
    *,
    semantic_weight: float = _W_SEMANTIC,
    lexical_weight: float = _W_LEXICAL,
    graph_weight: float = _W_GRAPH,
    limit: int = 20,
) -> list[dict]:
    """Weighted Reciprocal Rank Fusion + dedup + retrieval_source annotation.

    Formula: ``score += channel_weight / (RRF_K + rank + 1)`` accumulated
    across all channels a fact appears in.  Items from multiple channels
    accumulate higher scores, naturally surfacing cross-channel consensus.

    ``retrieval_source`` is set to:
        'semantic' — only in semantic channel
        'lexical'  — only in lexical channel
        'graph'    — only in graph channel
        'multi'    — appeared in two or more channels (score boosted)
    """
    # {memory_id → {hit, score, channels}}
    fused: dict[str, dict] = {}

    channel_configs = [
        (semantic_hits, semantic_weight, "semantic"),
        (lexical_hits, lexical_weight, "lexical"),
        (graph_hits, graph_weight, "graph"),
    ]

    for hits, weight, channel_name in channel_configs:
        for rank, hit in enumerate(hits):
            mid = hit.get("id")
            if not mid:
                continue
            entry = fused.setdefault(
                mid,
                {
                    "hit": hit,
                    "score": 0.0,
                    "channels": set(),
                },
            )
            entry["score"] += weight / (_RRF_K + rank + 1)
            entry["channels"].add(channel_name)

    # Sort by combined score descending
    ranked = sorted(fused.values(), key=lambda e: e["score"], reverse=True)

    results: list[dict] = []
    for e in ranked[:limit]:
        hit = dict(e["hit"])
        channels = e["channels"]

        # Determine retrieval_source label
        if len(channels) >= 2:
            hit["retrieval_source"] = "multi"
        else:
            hit["retrieval_source"] = next(iter(channels))

        # Attach the combined RRF score for callers / reranking (next task)
        hit["rrf_score"] = round(e["score"], 6)

        # Clean up internal-only fields before returning
        hit.pop("_sem_score", None)
        hit.pop("_graph_matched", None)
        hit.pop("_graph_score", None)
        hit.pop("bm25_score", None)

        results.append(hit)

    return results


# ─── Public API ───────────────────────────────────────────────────────────────


def search_memories(
    query: str,
    db: OrivellumDB,
    limit: int = 20,
    *,
    semantic_weight: float = _W_SEMANTIC,
    lexical_weight: float = _W_LEXICAL,
    graph_weight: float = _W_GRAPH,
) -> list[dict]:
    """Three-channel hybrid memory retrieval.

    Runs semantic, lexical, and graph channels concurrently in a small thread
    pool, merges results with weighted RRF, and returns at most *limit*
    deduplicated memory facts annotated with ``retrieval_source`` and
    ``rrf_score``.

    Args:
        query:            Free-text query (user message or recall phrase).
        db:               Live ``OrivellumDB`` instance.
        limit:            Maximum number of facts to return.
        semantic_weight:  RRF weight for the semantic channel (default 0.5).
        lexical_weight:   RRF weight for the lexical channel (default 0.3).
        graph_weight:     RRF weight for the graph channel (default 0.2).

    Returns:
        List of memory fact dicts (same shape as ``get_current_memory_facts``)
        with two extra fields:
            retrieval_source: 'semantic' | 'lexical' | 'graph' | 'multi'
            rrf_score:        combined weighted RRF score (float)

    Never raises — all channel failures are caught internally and logged at
    DEBUG level; the function returns whatever channels succeed.
    """
    if not query or not query.strip():
        return []

    # Fetch limit headroom per channel so the merge has room to deduplicate
    channel_limit = min(max(limit * 2, 20), 50)

    sem_hits: list[dict] = []
    lex_hits: list[dict] = []
    graph_hits: list[dict] = []

    # Run all three channels concurrently — they are I/O-bound (DB + optional
    # HTTP to the embedding endpoint) so threading works well here.
    #
    # Timeout strategy: use concurrent.futures.wait(timeout=8) so the
    # retrieval path waits at most 8 seconds for all channels to complete.
    # Futures that have not finished by then contribute an empty list.
    # The executor is shut down with wait=False so slow channels (e.g. a
    # hanging embedding request) do not block the request thread further.
    _CHANNEL_TIMEOUT = 8  # seconds, wall-clock budget for all channels combined
    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="mem_retrieval")
    try:
        fs: dict[concurrent.futures.Future, str] = {
            pool.submit(_channel_semantic, query, db): "semantic",
            pool.submit(_channel_lexical, query, db, channel_limit): "lexical",
            pool.submit(_channel_graph, query, db, channel_limit): "graph",
        }
        done, _not_done = concurrent.futures.wait(fs.keys(), timeout=_CHANNEL_TIMEOUT)
        for future in done:
            channel = fs[future]
            try:
                result = future.result()
            except Exception as exc:
                logger.debug("search_memories channel=%s error: %s", channel, exc)
                result = []
            if channel == "semantic":
                sem_hits = result
            elif channel == "lexical":
                lex_hits = result
            else:
                graph_hits = result
        if _not_done:
            logger.debug(
                "search_memories: %d channel(s) timed out after %ds",
                len(_not_done),
                _CHANNEL_TIMEOUT,
            )
    finally:
        # Do not block on slow channels — abandon running threads so the
        # caller receives a response within the timeout budget.
        pool.shutdown(wait=False, cancel_futures=True)

    logger.debug(
        "search_memories q=%r sem=%d lex=%d graph=%d",
        query[:60],
        len(sem_hits),
        len(lex_hits),
        len(graph_hits),
    )

    return _merge(
        sem_hits,
        lex_hits,
        graph_hits,
        semantic_weight=semantic_weight,
        lexical_weight=lexical_weight,
        graph_weight=graph_weight,
        limit=limit,
    )


# ─── Memory text helper ───────────────────────────────────────────────────────


def _memory_text(fact: dict) -> str:
    """Combine key + value into a single scorable text string.

    Used by all reranking stages that need a single text representation of a
    memory fact dict (which stores text in two separate ``key`` and ``value``
    fields rather than the ``text`` field that knowledge/chunk candidates use).
    """
    key = str(fact.get("key", "")).strip()
    value = str(fact.get("value", "")).strip()
    return f"{key}: {value}" if key else value


# ─── Stage 1: Graph-native ranking boost ──────────────────────────────────────


def _graph_boost_scores(
    query: str,
    candidates: list[dict],
    db: OrivellumDB,
) -> list[dict]:
    """Boost candidates whose text mentions entities extracted from the query.

    Algorithm:
        1. Tokenise the query; drop stopwords and short tokens.
        2. Look up each token in the ``entities`` table via case-insensitive
           LIKE; collect matching entity names.
        3. For each candidate count how many of those entity names appear in
           its combined key+value text.
        4. Multiply ``rrf_score`` by ``_GRAPH_BOOST_MULT ** match_count`` so
           candidates with more entity overlaps surface higher.
        5. Re-sort by the boosted score.

    Returns the original list (shallow copies with updated rrf_score) when
    the entity table is absent or the query contains no eligible tokens.
    Never raises — all failures return candidates unchanged.
    """
    if not candidates:
        return candidates

    q_tokens = [
        t for t in re.findall(r"\b[a-zA-Z][a-zA-Z0-9]{2,}\b", query) if t.lower() not in _STOPWORDS
    ]
    if not q_tokens:
        return candidates

    matched_entities: set[str] = set()
    try:
        with db._lock:
            for token in q_tokens:
                # Legacy entities store + ATLAS-O typed graph (fiction
                # harvest writes graph_node, not entities).
                rows = db._conn.execute(
                    """SELECT name FROM entities WHERE LOWER(name) LIKE ?
                       UNION
                       SELECT name FROM graph_node WHERE LOWER(name) LIKE ?
                       LIMIT 10""",
                    (f"%{token.lower()}%", f"%{token.lower()}%"),
                ).fetchall()
                for r in rows:
                    matched_entities.add(r["name"].lower())
    except Exception as exc:
        logger.debug("graph_boost entity lookup failed (non-fatal): %s", exc)
        return candidates

    if not matched_entities:
        return candidates

    result: list[dict] = []
    for fact in candidates:
        c = dict(fact)
        text = _memory_text(c).lower()
        match_count = sum(1 for e in matched_entities if e in text)
        if match_count > 0:
            c["rrf_score"] = round(
                c.get("rrf_score", 0.0) * (_GRAPH_BOOST_MULT**match_count),
                8,
            )
        result.append(c)

    result.sort(key=lambda x: x.get("rrf_score", 0.0), reverse=True)
    return result


# ─── Stage 2: Cross-encoder (pointwise LLM) ──────────────────────────────────


def _cross_encoder_score_one(
    query: str,
    fact: dict,
    cfg: Any,
    db: Any,
) -> float:
    """Score a single (query, memory fact) pair on a 0-10 relevance scale.

    Returns -1.0 when the LLM call fails or the response cannot be parsed,
    so the caller can distinguish scored vs. unscored candidates.
    """
    from orivellum.capabilities.llm import llm_call

    text = _memory_text(fact)
    prompt = (
        f'Query: "{query[:200]}"\n'
        f'Memory fact: "{text[:300]}"\n\n'
        "Rate how relevant this memory fact is to the query.\n"
        "Output ONLY a single integer from 0 (completely irrelevant) "
        "to 10 (perfectly relevant). Nothing else."
    )
    result = llm_call(
        [{"role": "user", "content": prompt}],
        cfg=cfg,
        db=db,
        purpose="rerank.cross_encoder",
        timeout=5,
        max_tokens=5,
        temperature=0.0,
    )
    if not result.ok or not result.text:
        return -1.0
    m = re.search(r"\b(\d+)\b", result.text.strip())
    if m:
        return min(10.0, max(0.0, float(m.group(1))))
    return -1.0


def _cross_encoder_rerank(
    query: str,
    candidates: list[dict],
    cfg: Any,
    db: Any,
) -> list[dict]:
    """Stage 2: concurrent pointwise LLM scoring for up to 20 candidates.

    Each candidate is scored in parallel.  All calls share an 8-second
    wall-clock budget (``concurrent.futures.wait``); the executor is shut
    down with ``wait=False`` so a stalled embedding or LLM request cannot
    block the response path beyond the budget.

    Candidates that receive a valid score are sorted by that score descending.
    Unscored candidates (LLM failed, timed out, or parse error) are appended
    in their original order after the scored ones.  Candidates beyond
    ``_CE_MAX_CANDIDATES`` are appended unchanged after all scored results.
    """
    if not candidates:
        return candidates

    pool_size = min(_CE_MAX_CANDIDATES, len(candidates))
    top = candidates[:pool_size]
    rest = candidates[pool_size:]
    scored = [dict(c) for c in top]

    pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ce_rerank")
    try:
        fs: dict[concurrent.futures.Future, int] = {
            pool.submit(_cross_encoder_score_one, query, c, cfg, db): i for i, c in enumerate(top)
        }
        done, _not_done = concurrent.futures.wait(fs.keys(), timeout=_CE_TOTAL_TIMEOUT)
        for future in done:
            idx = fs[future]
            try:
                score = future.result()
                if score >= 0.0:
                    scored[idx]["cross_encoder_score"] = score
            except Exception as exc:
                logger.debug("cross_encoder future %d error: %s", idx, exc)
        if _not_done:
            logger.debug(
                "cross_encoder: %d future(s) timed out after %.1fs",
                len(_not_done),
                _CE_TOTAL_TIMEOUT,
            )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    # Sort top candidates by cross_encoder_score; fall through to original
    # BM25 order for unscored ones.
    with_score = [c for c in scored if "cross_encoder_score" in c]
    without_score = [c for c in scored if "cross_encoder_score" not in c]
    with_score.sort(key=lambda x: x["cross_encoder_score"], reverse=True)

    return with_score + without_score + rest


# ─── Query complexity scorer ──────────────────────────────────────────────────


def query_complexity_score(query: str) -> int:
    """Estimate the retrieval complexity of a memory query.

    Returns an integer ≥ 0.  Scores above ``_COMPLEXITY_THRESHOLD`` trigger
    ``ReActMemoryAgent`` instead of direct one-shot hybrid retrieval.

    Heuristics applied (additive, capped where noted):
        +1..+3  for each distinct capitalised word (potential named entity /
                proper noun) — capped at 3 so a single long name doesn't
                dominate.
        +1      for multi-domain connector words (and, connects, between,
                compared, versus, relates, link, alongside, intersection).
        +1      for temporal reference words (before, after, since, during,
                when, year, month, ago, recent, last, first, early, late).
        +1      for long questions (> 12 tokens) which tend to be multi-hop.

    The heuristic is intentionally simple and fast (no LLM call, < 0.1 ms).
    """
    score = 0

    # Named-entity signal: distinct capitalised words (≥ 3 chars)
    cap_words = set(re.findall(r"\b[A-Z][a-z]{2,}\b", query))
    score += min(len(cap_words), 3)

    lower = query.lower()
    words = set(lower.split())

    # Multi-domain connectors
    connectors = {
        "and",
        "connects",
        "between",
        "compared",
        "versus",
        "vs",
        "relates",
        "link",
        "alongside",
        "intersection",
        "relationship",
    }
    if words & connectors:
        score += 1

    # Temporal references
    temporal = {
        "before",
        "after",
        "since",
        "during",
        "when",
        "year",
        "month",
        "ago",
        "recent",
        "recently",
        "last",
        "first",
        "early",
        "late",
        "history",
        "previously",
        "then",
        "now",
    }
    if words & temporal:
        score += 1

    # Long query → likely multi-hop
    if len(query.split()) > 12:
        score += 1

    return score


# ─── ReAct agentic retrieval loop ────────────────────────────────────────────


class ReActMemoryAgent:
    """Iterative tool-use loop for complex multi-hop memory queries.

    Implements a lightweight ReAct (Reasoning + Acting) pattern: at each
    iteration the LLM chooses one of three retrieval tools and a sub-query
    string; results are merged into a growing candidate set.  The loop stops
    when the model sets ``done: true``, when no new facts are found, or when
    ``MAX_ITER`` iterations are exhausted.

    Tools available:
        ``semantic_search``  — embedding-based search over conversation chunks.
        ``lexical_search``   — BM25/FTS5 search over memory key+value text.
        ``graph_traverse``   — entity-aware graph traversal over memory facts.

    Each iteration costs exactly one LLM call (small prompt, max_tokens=120).
    The total LLM budget is therefore bounded to ``MAX_ITER`` calls.

    Usage::

        agent = ReActMemoryAgent(db, cfg)
        candidates = agent.run("what did I learn about X that connects to Y?")
    """

    MAX_ITER: int = 4

    def __init__(self, db: OrivellumDB, cfg: Any) -> None:
        self.db = db
        self.cfg = cfg

    def run(self, query: str) -> list[dict]:
        """Run the ReAct loop; return deduplicated memory candidates found."""
        from orivellum.capabilities.llm import llm_call

        collected: list[dict] = []
        seen_ids: set[str] = set()
        context_log: list[str] = []

        for iteration in range(self.MAX_ITER):
            # Summarise collected facts for the decision prompt
            collected_keys = ", ".join(f.get("key", "") for f in collected[:5]) or "none"
            context_str = "\n".join(context_log[-3:]) if context_log else "No tools called yet."

            decision_prompt = (
                f"You are a memory retrieval agent. "
                f'Original query: "{query[:250]}"\n\n'
                f"Facts collected so far ({len(collected)}): {collected_keys}\n"
                f"Tool history:\n{context_str}\n\n"
                "Choose the NEXT retrieval action. Output ONLY a JSON object with:\n"
                '  {"tool": "<tool_name>", "query": "<sub-query>", "done": <true|false>}\n'
                "tool must be one of: semantic_search, lexical_search, graph_traverse\n"
                "Set done=true if you have enough context to answer the original query.\n"
                "Output ONLY the JSON object."
            )

            result = llm_call(
                [{"role": "user", "content": decision_prompt}],
                cfg=self.cfg,
                db=self.db,
                purpose="react.memory.decision",
                timeout=10,
                max_tokens=120,
                temperature=0.0,
            )
            if not result.ok or not result.text:
                break

            try:
                m = re.search(r"\{[^}]+\}", result.text, re.DOTALL)
                if not m:
                    break
                action = _json.loads(m.group(0))
            except Exception:
                break

            if action.get("done"):
                break

            tool = str(action.get("tool", "lexical_search")).strip()
            sub_query = str(action.get("query", query)).strip() or query

            hits = self._call_tool(tool, sub_query)
            new_count = 0
            for h in hits:
                mid = h.get("id")
                if mid and mid not in seen_ids:
                    seen_ids.add(mid)
                    collected.append(h)
                    new_count += 1

            context_log.append(
                f"iter={iteration + 1} tool={tool!r} q={sub_query[:60]!r} new={new_count}"
            )
            logger.debug(
                "ReAct iter=%d tool=%s new_hits=%d total=%d",
                iteration + 1,
                tool,
                new_count,
                len(collected),
            )

            if new_count == 0:
                # No new results — avoid spinning on the same tool
                break

        return collected

    def _call_tool(self, tool: str, sub_query: str) -> list[dict]:
        """Dispatch one retrieval tool call; return [] on any error."""
        try:
            if tool == "semantic_search":
                return _channel_semantic(sub_query, self.db)
            if tool == "lexical_search":
                return _channel_lexical(sub_query, self.db, limit=10)
            if tool == "graph_traverse":
                return _channel_graph(sub_query, self.db, limit=10)
            logger.debug("ReAct unknown tool %r, defaulting to lexical", tool)
            return _channel_lexical(sub_query, self.db, limit=10)
        except Exception as exc:
            logger.debug("ReAct tool=%s error: %s", tool, exc)
            return []


# ─── Three-stage reranking pipeline ──────────────────────────────────────────


def rerank_memories(
    query: str,
    candidates: list[dict],
    db: OrivellumDB,
    *,
    top_k: int = _RERANK_TOP_K,
) -> tuple[list[dict], dict]:
    """Apply the three-stage reranking pipeline to hybrid retrieval results.

    Stages
    ------
    1. **Graph-native boost** — always runs; entity overlap multiplies rrf_score.
    2. **BM25 rerank** — always runs; pure-Python BM25 over key+value text.
    3. **Cross-encoder** — pointwise LLM 0-10; gated by ``ai_reranking_enabled``.
    4. **Listwise LLM** — fires when ≥ 3 candidates and LLM is enabled; fused
       with BM25 via RRF inside ``rerank._llm_rerank``.

    Returns
    -------
    (reranked_list, stages_meta)
        ``reranked_list``  — up to *top_k* deduplicated memory fact dicts.
        ``stages_meta``    — dict with a ``stages`` list for observability.
    """
    from orivellum.capabilities.rerank import _llm_rerank, bm25_rerank

    if not candidates or not query.strip():
        return candidates[:top_k], {"stages": []}

    # Add combined text field for BM25/listwise — stripped before returning.
    def _prep(cands: list[dict]) -> list[dict]:
        return [dict(c, _mem_text=_memory_text(c)) for c in cands]

    stages: list[dict] = []

    # ── Stage 1: Graph-native boost ────────────────────────────────────────────
    try:
        after_graph = _graph_boost_scores(query, candidates, db)
    except Exception as exc:
        logger.debug("rerank stage 1 graph_boost error: %s", exc)
        after_graph = candidates
    stages.append({"name": "graph_boost", "count": len(after_graph)})

    # ── Stage 2: BM25 rerank ───────────────────────────────────────────────────
    try:
        after_bm25 = bm25_rerank(query, _prep(after_graph), text_field="_mem_text")
    except Exception as exc:
        logger.debug("rerank stage 2 bm25 error: %s", exc)
        after_bm25 = _prep(after_graph)
    stages.append({"name": "bm25", "count": len(after_bm25)})

    # Preserve BM25 rank by stable candidate id BEFORE cross-encoder can
    # reorder the list.  Stage 4 (listwise) uses this map so the RRF fusion
    # is always BM25-rank + LLM-rank, never cross-encoder-rank + LLM-rank.
    _bm25_rank_by_id: dict[str, int] = {
        c.get("id", ""): rank for rank, c in enumerate(after_bm25) if c.get("id")
    }
    _bm25_total = len(after_bm25)

    # ── Stage 3: Cross-encoder (pointwise LLM, feature-flagged) ───────────────
    after_ce = after_bm25
    ce_ran = False
    ai_on = False
    try:
        if db is not None:
            ai_on = db.get_setting("ai_reranking_enabled", "false") == "true"
        if ai_on:
            from orivellum.configuration.config import load_config as _lc

            cfg = _lc()
            after_ce = _cross_encoder_rerank(query, after_bm25, cfg, db)
            ce_ran = True
    except Exception as exc:
        logger.debug("rerank stage 3 cross_encoder error: %s", exc)
    stages.append({"name": "cross_encoder", "count": len(after_ce), "ran": ce_ran})

    # ── Stage 4: Listwise LLM rerank (fires when ≥ 3 and LLM enabled) ─────────
    # Fusion: true BM25 rank (from _bm25_rank_by_id, set before CE reordering)
    # fused with LLM rank via RRF.  Cross-encoder is a separate signal that
    # controls which candidates reach this stage, not a substitute for BM25.
    # Candidates outside the top-_LW_TOP_K window receive a large LLM-rank
    # penalty (_outside) so they naturally follow top-slice results.
    final = after_ce
    lw_ran = False
    if ai_on and len(after_ce) >= 3:
        try:
            from orivellum.configuration.config import load_config as _lc

            cfg = _lc()
            top_slice = after_ce[:_LW_TOP_K]
            lw_indices = _llm_rerank(query, top_slice, db, cfg, text_field="_mem_text")
            if lw_indices is not None:
                # _llm_rerank returns a permutation of 0-based indices into
                # top_slice (most-relevant-first).  Convert to a rank map
                # (slice_pos → lw_rank), then fuse with the saved BM25 rank
                # (looked up by candidate id, NOT by CE position).
                _outside = _bm25_total  # penalty for missing / out-of-window
                lw_rank_of: dict[int, int] = {}
                for llm_pos, slice_pos in enumerate(lw_indices):
                    if 0 <= slice_pos < len(top_slice):
                        lw_rank_of[slice_pos] = llm_pos
                for i in range(len(top_slice)):
                    lw_rank_of.setdefault(i, _outside)

                def _slice_rrf(slice_pos: int) -> float:
                    cand = top_slice[slice_pos]
                    # BM25 rank is stable, independent of CE reordering
                    bm25_r = _bm25_rank_by_id.get(cand.get("id", ""), _outside)
                    lw_r = lw_rank_of[slice_pos]
                    return 1.0 / (_RRF_K + bm25_r + 1) + 1.0 / (_RRF_K + lw_r + 1)

                reordered = sorted(range(len(top_slice)), key=_slice_rrf, reverse=True)
                final = [top_slice[i] for i in reordered] + after_ce[_LW_TOP_K:]
                lw_ran = True
        except Exception as exc:
            logger.debug("rerank stage 4 listwise error: %s", exc)
    stages.append({"name": "listwise", "count": len(final), "ran": lw_ran})

    # Strip internal helper fields before returning to callers.
    result: list[dict] = []
    for c in final[:top_k]:
        c = dict(c)
        c.pop("_mem_text", None)
        c.pop("_rerank_idx", None)
        result.append(c)

    logger.debug(
        "rerank_memories q=%r candidates=%d → top_k=%d stages=%s",
        query[:60],
        len(candidates),
        len(result),
        [s["name"] for s in stages],
    )
    return result, {"stages": stages}


# ─── Full pipeline: retrieval + reranking + optional ReAct ───────────────────


def search_and_rerank_memories(
    query: str,
    db: OrivellumDB,
    *,
    limit: int = _RERANK_TOP_K,
    semantic_weight: float = _W_SEMANTIC,
    lexical_weight: float = _W_LEXICAL,
    graph_weight: float = _W_GRAPH,
) -> tuple[list[dict], dict]:
    """Hybrid retrieval + three-stage reranking + optional ReAct agent.

    Selects the retrieval strategy based on query complexity:

    * **Simple query** (complexity ≤ ``_COMPLEXITY_THRESHOLD``): one-shot
      three-channel hybrid retrieval via ``search_memories``, then reranked.

    * **Complex query** (complexity > threshold): ``ReActMemoryAgent`` runs an
      iterative tool-use loop to collect candidates from multiple angles, then
      the same reranking pipeline is applied.

    Returns
    -------
    (reranked_facts, meta)
        ``reranked_facts``  — up to *limit* memory fact dicts.
        ``meta``            — dict with ``retrieval_stages``, ``complexity_score``,
                              and ``react_used`` fields for observability / API.

    Never raises — all internal failures are caught and the best available
    result set is returned.
    """
    if not query or not query.strip():
        return [], {"retrieval_stages": [], "complexity_score": 0, "react_used": False}

    complexity = query_complexity_score(query)
    react_used = complexity > _COMPLEXITY_THRESHOLD

    # ── Retrieval phase ────────────────────────────────────────────────────────
    def _hybrid_candidates() -> list[dict]:
        return search_memories(
            query,
            db,
            limit=limit * 3,
            semantic_weight=semantic_weight,
            lexical_weight=lexical_weight,
            graph_weight=graph_weight,
        )

    if react_used:
        try:
            from orivellum.configuration.config import load_config as _lc

            cfg = _lc()
            agent = ReActMemoryAgent(db, cfg)
            candidates = agent.run(query)
            # ReAct can legitimately return [] without throwing — for example
            # when the LLM says done=true before calling any tool, when all
            # tools return no results, or when the JSON parse fails.  In every
            # such case fall back to one-shot hybrid retrieval so callers
            # receive the same results they would have gotten on a simple query.
            if not candidates:
                logger.debug("ReAct returned no candidates; falling back to hybrid retrieval")
                candidates = _hybrid_candidates()
                # react_used stays True — the agent *was* invoked; the fallback
                # is a safety net, not a strategy change.
        except Exception as exc:
            logger.debug("ReAct agent failed, falling back to hybrid: %s", exc)
            react_used = False
            candidates = _hybrid_candidates()
    else:
        candidates = _hybrid_candidates()

    # ── Reranking phase ────────────────────────────────────────────────────────
    reranked, stages_meta = rerank_memories(query, candidates, db, top_k=limit)

    meta: dict = {
        "retrieval_stages": stages_meta.get("stages", []),
        "complexity_score": complexity,
        "react_used": react_used,
    }
    return reranked, meta
