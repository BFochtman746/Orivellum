"""Cross-encoder re-ranking for RAG retrieval.

Staged pipeline applied after the initial hybrid (BM25 + semantic) retrieval:

  1. **BM25 re-ranker** (always active, pure Python, zero new deps):
     Scores each candidate against the user query using BM25 over the local
     candidate pool (Robertson / Sparck-Jones formula, k1=1.5, b=0.75).
     Adds ``rerank_score`` to each hit dict and returns candidates
     sorted descending.  Typical latency: < 1 ms for 20 candidates.

  2a. **Cross-encoder re-ranker** (preferred, when ``serving.reranker_model``
     is configured, e.g. ``bge-reranker-v2-m3-GGUF``):
     Sends the top-30 BM25 candidates to the Lemonade ``/rerank`` endpoint,
     which scores each (query, passage) pair jointly.  Fused with BM25 via
     RRF.  Protected by a circuit breaker (mirroring the embeddings client):
     a failed call opens a cooldown during which no network attempt is made,
     so search stays fast when the model isn't pulled or the server is down.
     Gated by the ``cross_reranker_enabled`` DB setting (default on).

  2b. **LLM listwise re-ranker** (fallback, gated by ``ai_reranking_enabled``):
     Only used when the cross-encoder produced nothing.  Sends the top-10
     BM25 candidates to ``llm_call()`` with a listwise ranking prompt.
     Bounded by an 8-second timeout; any failure falls back to BM25.

All stages are pure re-orderers — they never drop candidates — so the
caller's token-budget logic still controls the final count injected into
the prompt.  Original dicts are never mutated; fresh copies are returned.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import urllib.request
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

# ── Cross-encoder (Lemonade /rerank) settings ────────────────────────────────
# Number of top BM25 candidates sent to the cross-encoder.  Cross-encoders are
# cheap per pair (~10 ms each on Strix Halo for bge-reranker-v2-m3), so the
# window can be larger than the LLM listwise one.
_CE_TOP_K: int = 30
# Per-document character cap — keeps request size bounded; bge-reranker-v2-m3
# has an 8K-token window, so 1 500 chars per passage is comfortably inside it.
_CE_MAX_DOC_LEN: int = 1500
_CE_TIMEOUT: float = 6.0
# Failed calls open a cooldown during which no network attempt is made
# (same pattern as capabilities/embeddings.py).
_CE_FAIL_COOLDOWN: float = 300.0
_ce_unavailable_until: float = 0.0
# Concurrency guard: until the endpoint has proven healthy at least once,
# only a single "prober" request may attempt the network call at a time —
# concurrent requests fall back to BM25 immediately.  This prevents a
# thundering herd of 6-second blocking calls saturating the FastAPI
# threadpool when the model isn't pulled.  After a success, concurrent
# calls are allowed; any failure flips back to probe mode.
_ce_lock = threading.Lock()
_ce_healthy: bool = False
_ce_inflight: bool = False


def cross_reranker_enabled(db: Any) -> bool:
    """Normalized read of the ``cross_reranker_enabled`` DB setting.

    ``db`` may be ``None`` (test contexts) — treated as enabled since the
    config-level ``reranker_model`` gate still applies.
    """
    if db is None:
        return True
    try:
        return db.get_setting("cross_reranker_enabled", "true").strip().lower() == "true"
    except Exception:
        return True


def _serving_reranker() -> tuple[str, str]:
    """Return (base_url, reranker_model) from live config."""
    from orivellum.api._deps import get_config
    cfg = get_config()
    return (cfg.serving.base_url.rstrip("/"),
            (getattr(cfg.serving, "reranker_model", "") or "").strip())


def cross_encoder_status() -> dict:
    """Breaker + config state for the reranker (no network call).

    Mirrors the embeddings status endpoint shape so the System page can show
    both cards consistently.
    """
    _, model = _serving_reranker()
    now = time.monotonic()
    open_ = now < _ce_unavailable_until
    return {
        "model": model,
        "configured": bool(model),
        "circuit_open": open_,
        "retry_in_sec": max(0, round(_ce_unavailable_until - now)) if open_ else 0,
    }


def reset_cross_encoder_breaker() -> None:
    """Close the circuit breaker (used by the probe endpoint)."""
    global _ce_unavailable_until
    _ce_unavailable_until = 0.0


def cross_encoder_scores(
    query: str,
    texts: list[str],
    *,
    timeout: float = _CE_TIMEOUT,
) -> list[float] | None:
    """Score each text against *query* via the Lemonade ``/rerank`` endpoint.

    Returns a list of relevance scores aligned with *texts* (higher = more
    relevant), or ``None`` when the reranker is unconfigured, cooling down,
    or the call fails.  A failed call opens the cooldown; success closes it.
    """
    global _ce_unavailable_until, _ce_healthy, _ce_inflight
    if not texts or not query.strip():
        return None
    base_url, model = _serving_reranker()
    if not model:
        return None

    # Admission control (atomic): honour the cooldown, and while the endpoint
    # is unproven allow only one in-flight prober — concurrent callers fall
    # back to BM25 immediately instead of stacking blocking network calls.
    with _ce_lock:
        if time.monotonic() < _ce_unavailable_until:
            return None
        if not _ce_healthy and _ce_inflight:
            return None
        _ce_inflight = True

    payload = json.dumps({
        "model": model,
        "query": query[:2000],
        "documents": [t[:_CE_MAX_DOC_LEN] for t in texts],
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/rerank", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        results = data.get("results") or []
        # Strict validation: exactly one finite numeric score per document,
        # no duplicate or out-of-range indices.  Anything else is treated as
        # a failure and opens the cooldown — a malformed endpoint is as
        # unusable as a down one.
        scores: list[float] = [0.0] * len(texts)
        seen_idx: set[int] = set()
        valid = True
        for r in results:
            idx = r.get("index")
            score = r.get("relevance_score")
            if (not isinstance(idx, int) or idx in seen_idx
                    or not (0 <= idx < len(texts))
                    or not isinstance(score, (int, float))
                    or not math.isfinite(float(score))):
                valid = False
                break
            scores[idx] = float(score)
            seen_idx.add(idx)
        if not valid or len(seen_idx) != len(texts):
            logger.warning("Reranker response malformed (%d/%d valid scores)",
                           len(seen_idx), len(texts))
            with _ce_lock:
                _ce_unavailable_until = time.monotonic() + _CE_FAIL_COOLDOWN
                _ce_healthy = False
            return None
        with _ce_lock:
            _ce_unavailable_until = 0.0
            _ce_healthy = True
        return scores
    except Exception as exc:
        with _ce_lock:
            _ce_unavailable_until = time.monotonic() + _CE_FAIL_COOLDOWN
            _ce_healthy = False
        logger.debug("Reranker unavailable (cooldown %.0fs): %s",
                     _CE_FAIL_COOLDOWN, exc)
        return None
    finally:
        with _ce_lock:
            _ce_inflight = False


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

        # ── Stage 2: model-based re-rank ──────────────────────────────────
        # ``model_indices`` is an ordering (most-relevant first) of positions
        # into the top-``window`` BM25 candidates, produced by either the
        # cross-encoder (2a, preferred) or the LLM listwise ranker (2b).
        model_indices: list[int] | None = None
        window = 0

        # 2a. Cross-encoder via Lemonade /rerank.  Gated by the
        #     cross_reranker_enabled setting (default on) and a configured
        #     serving.reranker_model; the circuit breaker inside
        #     cross_encoder_scores keeps failures cheap.
        try:
            if cross_reranker_enabled(db):
                _win = min(len(bm25_ranked), _CE_TOP_K)
                _texts = [str(c.get(text_field) or "")[:_CE_MAX_DOC_LEN]
                          for c in bm25_ranked[:_win]]
                _scores = cross_encoder_scores(query, _texts)
                if _scores is not None:
                    model_indices = sorted(range(_win),
                                           key=lambda i: _scores[i], reverse=True)
                    window = _win
        except Exception as _ce_exc:
            logger.debug("Cross-encoder re-rank skipped (non-fatal): %s", _ce_exc)

        # 2b. LLM listwise fallback (feature-flagged) — only when the
        #     cross-encoder produced nothing.
        if model_indices is None and db is not None:
            try:
                if db.get_setting("ai_reranking_enabled", "false") == "true":
                    from orivellum.configuration.config import load_config as _lc
                    cfg = _lc()
                    # Only pass the top-_LLM_TOP_K BM25 candidates to the LLM
                    # to keep the prompt compact and latency bounded.
                    llm = _llm_rerank(
                        query,
                        bm25_ranked[:_LLM_TOP_K],
                        db,
                        cfg,
                        text_field=text_field,
                    )
                    if llm is not None:
                        model_indices = llm
                        window = min(len(bm25_ranked), _LLM_TOP_K)
            except Exception as _llm_exc:
                logger.debug("LLM re-rank skipped (non-fatal): %s", _llm_exc)

        if model_indices is not None:
            # Build lookup: original_idx → BM25 rank position
            bm25_rank_by_orig: dict[int, int] = {
                c["_rerank_idx"]: rank for rank, c in enumerate(bm25_ranked)
            }

            # Build lookup: original_idx → model rank position.
            # Candidates outside the top-``window`` get a large rank equal to
            # len(bm25_ranked) so they are naturally penalised.
            _outside_rank = len(bm25_ranked)
            top_orig_indices = [c["_rerank_idx"] for c in bm25_ranked[:window]]
            model_rank_by_orig: dict[int, int] = {}
            for model_pos, list_pos in enumerate(model_indices):
                if list_pos < len(top_orig_indices):
                    model_rank_by_orig[top_orig_indices[list_pos]] = model_pos
            # Fill in candidates not covered by the model window
            for c in bm25_ranked:
                orig = c["_rerank_idx"]
                if orig not in model_rank_by_orig:
                    model_rank_by_orig[orig] = _outside_rank

            def _rrf_score(orig_idx: int) -> float:
                b = bm25_rank_by_orig.get(orig_idx, _outside_rank)
                mv = model_rank_by_orig.get(orig_idx, _outside_rank)
                return 1.0 / (_RRF_K + b + 1) + 1.0 / (_RRF_K + mv + 1)

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
