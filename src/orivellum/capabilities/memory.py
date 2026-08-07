"""Hybrid three-channel memory retrieval.

Channel architecture
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

Configurable weights
--------------------
The three weights are exposed as keyword arguments to ``search_memories``
and can be overridden per-call.  The starting defaults recommended by the
blueprint are::

    semantic_weight = 0.5
    lexical_weight  = 0.3
    graph_weight    = 0.2

These need not sum to 1.0; they are relative importance multipliers fed
into the RRF formula: ``channel_weight / (RRF_K + rank)``.

Graceful degradation
--------------------
Each channel is wrapped in a try/except inside its ``concurrent.futures``
task.  If a channel raises (embedding timeout, missing FTS table, missing
entity tables, etc.) its contribution is an empty list.  The merge layer
handles any subset of results — even one channel returning results is
sufficient for a valid response.
"""
from __future__ import annotations

import concurrent.futures
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.memory")

# Reciprocal rank fusion constant — standard value used across the codebase.
_RRF_K: int = 60

# Default channel weights (configurable per-call).
_W_SEMANTIC: float = 0.5
_W_LEXICAL:  float = 0.3
_W_GRAPH:    float = 0.2

# Maximum conversation chunks to use when deriving semantic memory hits.
_SEM_CHUNK_LIMIT: int = 10


# ─── Channel implementations ──────────────────────────────────────────────────


def _channel_semantic(query: str, db: "OrivellumDB") -> list[dict]:
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
            s   = float(h.get("score", 0.0))
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


def _channel_lexical(query: str, db: "OrivellumDB", limit: int = 20) -> list[dict]:
    """Lexical channel: BM25/FTS5 search over user_memory_fts.

    Delegates to ``db.search_memories_lexical`` which handles both the FTS5
    path and the LIKE fallback transparently.  Returns [] on any error.
    """
    try:
        return db.search_memories_lexical(query, limit=limit)
    except Exception as exc:
        logger.debug("lexical channel failed: %s", exc)
        return []


def _channel_graph(query: str, db: "OrivellumDB", limit: int = 20) -> list[dict]:
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
    lexical_hits:  list[dict],
    graph_hits:    list[dict],
    *,
    semantic_weight: float = _W_SEMANTIC,
    lexical_weight:  float = _W_LEXICAL,
    graph_weight:    float = _W_GRAPH,
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
        (lexical_hits,  lexical_weight,  "lexical"),
        (graph_hits,    graph_weight,    "graph"),
    ]

    for hits, weight, channel_name in channel_configs:
        for rank, hit in enumerate(hits):
            mid = hit.get("id")
            if not mid:
                continue
            entry = fused.setdefault(mid, {
                "hit": hit,
                "score": 0.0,
                "channels": set(),
            })
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
        hit.pop("_sem_score",      None)
        hit.pop("_graph_matched",  None)
        hit.pop("_graph_score",    None)
        hit.pop("bm25_score",      None)

        results.append(hit)

    return results


# ─── Public API ───────────────────────────────────────────────────────────────


def search_memories(
    query: str,
    db: "OrivellumDB",
    limit: int = 20,
    *,
    semantic_weight: float = _W_SEMANTIC,
    lexical_weight:  float = _W_LEXICAL,
    graph_weight:    float = _W_GRAPH,
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

    sem_hits:  list[dict] = []
    lex_hits:  list[dict] = []
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
            pool.submit(_channel_semantic, query, db):               "semantic",
            pool.submit(_channel_lexical,  query, db, channel_limit): "lexical",
            pool.submit(_channel_graph,    query, db, channel_limit): "graph",
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
                sem_hits   = result
            elif channel == "lexical":
                lex_hits   = result
            else:
                graph_hits = result
        if _not_done:
            logger.debug(
                "search_memories: %d channel(s) timed out after %ds",
                len(_not_done), _CHANNEL_TIMEOUT,
            )
    finally:
        # Do not block on slow channels — abandon running threads so the
        # caller receives a response within the timeout budget.
        pool.shutdown(wait=False, cancel_futures=True)

    logger.debug(
        "search_memories q=%r sem=%d lex=%d graph=%d",
        query[:60], len(sem_hits), len(lex_hits), len(graph_hits),
    )

    return _merge(
        sem_hits, lex_hits, graph_hits,
        semantic_weight=semantic_weight,
        lexical_weight=lexical_weight,
        graph_weight=graph_weight,
        limit=limit,
    )
