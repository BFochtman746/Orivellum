"""Cross-encoder re-ranking for RAG retrieval.

Two-stage pipeline applied after the initial hybrid (BM25 + semantic) retrieval:

  1. **BM25 re-ranker** (always active, pure Python, zero new deps):
     Scores each candidate against the user query using BM25 over the local
     candidate pool (Robertson / Sparck-Jones formula, k1=1.5, b=0.75).
     Adds ``rerank_score`` to each hit dict and returns candidates
     sorted descending.  Typical latency: < 1 ms for 20 candidates.

  2. **LLM listwise re-ranker** (optional, gated by ``ai_reranking_enabled``):
     Sends the top-10 BM25 candidates to ``llm_call()`` with a listwise
     ranking prompt and re-orders them by the model's output ranking.
     The final list is produced by RRF over BM25 and LLM ranks so a
     consensus ordering wins.  Bounded by an 8-second timeout; any failure
     falls back silently to BM25 ranking.

Both stages are pure re-orderers — they never drop candidates — so the
caller's token-budget logic still controls the final count injected into
the prompt.  Original dicts are never mutated; fresh copies are returned.
"""
from __future__ import annotations

import logging
import math
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# ── BM25 hyper-parameters (Robertson / Sparck-Jones defaults) ─────────────────
_BM25_K1: float = 1.5
_BM25_B: float  = 0.75

# Number of top BM25 candidates sent to the optional LLM re-ranker.
# Keeping this small (10) bounds LLM prompt size and latency.
_LLM_TOP_K: int = 10

# Standard RRF constant used when fusing BM25 and LLM rankings.
_RRF_K: int = 60


# ──────────────────────────────────────────────────────────────────────────────
# Tokeniser
# ──────────────────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    """Lower-case word tokeniser (no external deps).

    Extracts runs of alphanumeric characters so punctuation and whitespace
    are ignored.  Returns an empty list for blank input.
    """
    return re.findall(r"\b[a-z0-9]+\b", text.lower())


# ──────────────────────────────────────────────────────────────────────────────
# BM25 scorer
# ──────────────────────────────────────────────────────────────────────────────

def _bm25_doc_score(
    query_terms: list[str],
    doc_terms: list[str],
    avg_dl: float,
    idf: dict[str, float],
) -> float:
    """BM25 score for a single document against pre-tokenised query terms.

    ``idf`` should be pre-computed over the entire candidate pool so the
    score reflects term rareness within the set being re-ranked.
    """
    dl = len(doc_terms)
    tf_dict: dict[str, int] = {}
    for t in doc_terms:
        tf_dict[t] = tf_dict.get(t, 0) + 1

    score = 0.0
    for t in query_terms:
        idf_val = idf.get(t)
        if idf_val is None:
            continue
        tf = tf_dict.get(t, 0)
        # Length-normalised TF
        norm = _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / max(avg_dl, 1.0))
        tf_adj = tf * (_BM25_K1 + 1) / (tf + norm)
        score += idf_val * tf_adj
    return score


def bm25_rerank(
    query: str,
    candidates: list[dict],
    *,
    text_field: str = "text",
) -> list[dict]:
    """Sort *candidates* by BM25 score against *query* and return them.

    Each returned dict is a shallow copy of the original with a
    ``rerank_score`` field added (float, higher = more relevant).
    Returns the input list unchanged (no copy) when *query* is blank or
    *candidates* is empty.
    """
    if not candidates or not query.strip():
        return candidates

    query_terms = _tokenize(query)
    if not query_terms:
        return candidates

    # Tokenise all candidate texts once
    doc_term_lists = [_tokenize(str(c.get(text_field) or "")) for c in candidates]
    n = len(candidates)
    avg_dl = sum(len(d) for d in doc_term_lists) / max(n, 1.0)

    # Local IDF over the candidate pool (smoothed so unmatched terms → 0)
    idf: dict[str, float] = {}
    for t in set(query_terms):
        df = sum(1 for d in doc_term_lists if t in d)
        idf[t] = math.log((n - df + 0.5) / (df + 0.5) + 1.0)

    scored: list[dict] = []
    for cand, doc_terms in zip(candidates, doc_term_lists):
        s = _bm25_doc_score(query_terms, doc_terms, avg_dl, idf)
        c = dict(cand)
        c["rerank_score"] = round(s, 6)
        scored.append(c)

    scored.sort(key=lambda x: x["rerank_score"], reverse=True)
    return scored


# ──────────────────────────────────────────────────────────────────────────────
# LLM listwise re-ranker (optional)
# ──────────────────────────────────────────────────────────────────────────────

def _llm_rerank(
    query: str,
    candidates: list[dict],
    db: Any,
    cfg: Any,
    *,
    text_field: str = "text",
) -> list[int] | None:
    """Ask the LLM to produce a ranked ordering of *candidates* by relevance.

    Returns a list of 0-based indices into *candidates* ordered most-relevant
    first.  Any candidate omitted by the model is appended at the end in
    its original order.

    Returns ``None`` on any failure so the caller can fall back to BM25.
    Bounded by an 8-second timeout and a very small ``max_tokens`` budget
    so this never significantly delays interactive chat.
    """
    from orivellum.capabilities.llm import llm_call

    if not candidates:
        return None

    # Build a numbered passage list (truncated so the prompt is compact)
    lines: list[str] = [f'Query: "{query}"', ""]
    for i, c in enumerate(candidates, 1):
        text = str(c.get(text_field) or "")[:250].replace("\n", " ")
        lines.append(f"[{i}] {text}")
    lines += [
        "",
        "Rank these passages from most to least relevant to the query above.",
        "Output ONLY a comma-separated list of passage numbers in relevance order.",
        "Example for 5 passages: 3, 1, 5, 2, 4",
    ]

    result = llm_call(
        [{"role": "user", "content": "\n".join(lines)}],
        cfg=cfg,
        db=db,
        purpose="rerank.listwise",
        timeout=8,
        max_tokens=80,
        temperature=0.0,
    )

    if not result.ok or not result.text:
        return None

    try:
        num_strs = re.findall(r"\d+", result.text.strip())
        indices: list[int] = []
        seen: set[int] = set()
        for n_str in num_strs:
            idx = int(n_str) - 1  # convert 1-based to 0-based
            if 0 <= idx < len(candidates) and idx not in seen:
                indices.append(idx)
                seen.add(idx)
        # Append any indices the model omitted, preserving BM25 sub-order
        for idx in range(len(candidates)):
            if idx not in seen:
                indices.append(idx)
        return indices
    except Exception as exc:
        logger.debug("LLM re-rank parse failed: %s", exc)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────

def rerank_candidates(
    query: str,
    candidates: list[dict],
    db: "OrivellumDB | None",
    *,
    text_field: str = "text",
    top_k: int | None = None,
) -> list[dict]:
    """Re-rank *candidates* against *query* and return them sorted best-first.

    **Stage 1 — BM25** (always):
        Pure-Python BM25 over the local candidate pool.  O(n · q) where n
        is the number of candidates and q is the number of query tokens.
        Typical latency < 1 ms for 20 candidates.

    **Stage 2 — LLM listwise** (optional):
        Only runs when the DB setting ``ai_reranking_enabled`` is ``"true"``
        and the LLM endpoint is available.  Passes the top-10 BM25
        candidates to ``llm_call()`` with a listwise ranking prompt.
        The BM25 and LLM rankings are fused with RRF (k=60) so a
        consensus ordering is preferred.

    Parameters
    ----------
    query:
        The user's natural-language query string.
    candidates:
        List of hit dicts from hybrid retrieval.  Each dict must have at
        least ``text_field`` as a key.
    db:
        Live ``OrivellumDB`` instance (used to read ``ai_reranking_enabled``
        and to pass to ``llm_call`` for telemetry).  May be ``None`` in
        test contexts; stage 2 is skipped when ``db`` is ``None``.
    text_field:
        Dict key to use as the document text for scoring.  Defaults to
        ``"text"``.  Use ``"snippet"`` when FTS snippet is preferred.
    top_k:
        If set, only the top-*k* candidates are returned.

    Returns
    -------
    list[dict]
        Shallow-copied dicts with a ``rerank_score`` field added.  On any
        unhandled exception the original *candidates* are returned unchanged
        so a bug here never silently breaks the response path.
    """
    try:
        if not candidates or not query.strip():
            return candidates[:top_k] if top_k is not None else candidates

        # Tag each candidate with a stable original index so we can cross-
        # reference between BM25 and LLM rank lists (dict copies would
        # otherwise lose object identity via id()).
        tagged = [dict(c, _rerank_idx=i) for i, c in enumerate(candidates)]

        # ── Stage 1: BM25 (always) ────────────────────────────────────────
        bm25_ranked = bm25_rerank(query, tagged, text_field=text_field)

        # ── Stage 2: LLM listwise (feature-flagged) ───────────────────────
        llm_indices: list[int] | None = None
        if db is not None:
            try:
                if db.get_setting("ai_reranking_enabled", "false") == "true":
                    from orivellum.configuration.config import load_config as _lc
                    cfg = _lc()
                    # Only pass the top-_LLM_TOP_K BM25 candidates to the LLM
                    # to keep the prompt compact and latency bounded.
                    llm_indices = _llm_rerank(
                        query,
                        bm25_ranked[:_LLM_TOP_K],
                        db,
                        cfg,
                        text_field=text_field,
                    )
            except Exception as _llm_exc:
                logger.debug("LLM re-rank skipped (non-fatal): %s", _llm_exc)

        if llm_indices is not None:
            # Build lookup: original_idx → BM25 rank position
            bm25_rank_by_orig: dict[int, int] = {
                c["_rerank_idx"]: rank for rank, c in enumerate(bm25_ranked)
            }

            # Build lookup: original_idx → LLM rank position.
            # Candidates outside the top-_LLM_TOP_K window get a large LLM
            # rank equal to len(bm25_ranked) so they are naturally penalised.
            _outside_rank = len(bm25_ranked)
            top_orig_indices = [c["_rerank_idx"] for c in bm25_ranked[:_LLM_TOP_K]]
            llm_rank_by_orig: dict[int, int] = {}
            for llm_pos, list_pos in enumerate(llm_indices):
                if list_pos < len(top_orig_indices):
                    llm_rank_by_orig[top_orig_indices[list_pos]] = llm_pos
            # Fill in candidates not covered by the LLM window
            for c in bm25_ranked:
                orig = c["_rerank_idx"]
                if orig not in llm_rank_by_orig:
                    llm_rank_by_orig[orig] = _outside_rank

            def _rrf_score(orig_idx: int) -> float:
                b = bm25_rank_by_orig.get(orig_idx, _outside_rank)
                lv = llm_rank_by_orig.get(orig_idx, _outside_rank)
                return 1.0 / (_RRF_K + b + 1) + 1.0 / (_RRF_K + lv + 1)

            result = sorted(bm25_ranked, key=lambda c: _rrf_score(c["_rerank_idx"]),
                            reverse=True)
            for c in result:
                c["rerank_score"] = round(_rrf_score(c["_rerank_idx"]), 8)
        else:
            result = bm25_ranked

        # Strip the internal tracking field — callers never need to see it.
        for c in result:
            c.pop("_rerank_idx", None)

        if top_k is not None:
            return result[:top_k]
        return result

    except Exception as exc:
        logger.debug(
            "rerank_candidates: non-fatal error, returning original order: %s", exc
        )
        return candidates[:top_k] if top_k is not None else candidates
