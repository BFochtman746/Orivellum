"""tests/test_rerank.py — unit tests for the BM25 + LLM re-ranking pipeline.

Coverage:
  1. BM25 re-rank promotes the most query-relevant candidate to position 0.
  2. Zero-overlap edge case: when no query token appears in any candidate,
     all BM25 scores are 0 and the original order is preserved.
  3. Empty inputs and blank queries return the original list unchanged.
  4. The ``rerank_score`` field is always present on returned dicts.
  5. The internal ``_rerank_idx`` tracking field is never exposed to callers.
  6. Malformed LLM output (letters, out-of-range numbers, duplicates) is
     handled gracefully — falls back to BM25 order.
  7. When the LLM re-ranker returns a valid ranking it is fused with BM25
     via RRF and changes the final ordering.
  8. When ``ai_reranking_enabled`` is False (default), ``llm_call`` is
     never invoked.
  9. When ``ai_reranking_enabled`` is True but the LLM call fails, the
     result degrades silently to BM25 order.
 10. ``rerank_candidates()`` wraps any unhandled exception and returns the
     original candidates list unchanged.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from orivellum.capabilities.rerank import (
    _bm25_doc_score,
    _llm_rerank,
    _tokenize,
    bm25_rerank,
    rerank_candidates,
)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _cands(*texts: str) -> list[dict]:
    """Create minimal candidate dicts from positional text strings."""
    return [{"id": str(i), "text": t, "review_status": "auto"} for i, t in enumerate(texts)]


def _fake_db(ai_reranking_enabled: str = "false") -> MagicMock:
    db = MagicMock()
    db.get_setting.return_value = ai_reranking_enabled
    return db


# ──────────────────────────────────────────────────────────────────────────────
# Tokeniser
# ──────────────────────────────────────────────────────────────────────────────


def test_tokenize_basic():
    assert _tokenize("Hello, World! 42") == ["hello", "world", "42"]


def test_tokenize_empty():
    assert _tokenize("") == []
    assert _tokenize("   ") == []


# ──────────────────────────────────────────────────────────────────────────────
# BM25 doc scorer
# ──────────────────────────────────────────────────────────────────────────────


def test_bm25_doc_score_basic():
    """Term that appears once in a two-word doc gives a positive score."""
    query_terms = ["apple"]
    doc_terms = ["apple", "fruit"]
    idf = {"apple": 1.0}
    score = _bm25_doc_score(query_terms, doc_terms, avg_dl=2.0, idf=idf)
    assert score > 0.0


def test_bm25_doc_score_zero_overlap():
    """No shared terms → score is exactly 0."""
    score = _bm25_doc_score(["banana"], ["apple", "fruit"], 2.0, {"banana": 1.0})
    assert score == 0.0


# ──────────────────────────────────────────────────────────────────────────────
# BM25 re-ranker
# ──────────────────────────────────────────────────────────────────────────────


def test_bm25_rerank_promotes_relevant():
    """The candidate whose text contains the query term should rank first."""
    cands = _cands(
        "machine learning algorithms and neural networks",  # index 0 — relevant
        "the quick brown fox jumps over the lazy dog",  # index 1 — irrelevant
        "python programming language features",  # index 2 — irrelevant
    )
    ranked = bm25_rerank("machine learning", cands)
    assert ranked[0]["id"] == "0", f"Expected id='0' at rank 0, got id='{ranked[0]['id']}'"


def test_bm25_rerank_adds_rerank_score():
    """Every returned dict must contain a non-negative rerank_score."""
    ranked = bm25_rerank("machine", _cands("machine", "apple"))
    for hit in ranked:
        assert "rerank_score" in hit
        assert hit["rerank_score"] >= 0.0


def test_bm25_rerank_zero_overlap_preserves_order():
    """When no query term appears in any candidate, original order is kept.

    With no overlap all BM25 scores are 0.0, so Python's stable sort
    leaves the original insertion order intact.
    """
    cands = _cands("cat sat mat", "dog log fog", "bat hat rat")
    ranked = bm25_rerank("zzz", cands)
    assert [r["id"] for r in ranked] == ["0", "1", "2"]


def test_bm25_rerank_empty_candidates():
    """Empty input returns an empty list without raising."""
    assert bm25_rerank("query", []) == []


def test_bm25_rerank_blank_query():
    """Blank query returns the original list reference unchanged."""
    cands = _cands("some text")
    result = bm25_rerank("   ", cands)
    assert result is cands


def test_bm25_rerank_no_internal_field_leak():
    """The _rerank_idx field must not appear in bm25_rerank output."""
    for hit in bm25_rerank("test", _cands("test text", "other text")):
        assert "_rerank_idx" not in hit


def test_bm25_rerank_does_not_mutate_original():
    """bm25_rerank returns new dicts; the originals are unchanged."""
    orig = _cands("test text")
    bm25_rerank("test", orig)
    assert "rerank_score" not in orig[0]


# ──────────────────────────────────────────────────────────────────────────────
# LLM re-ranker internals
# ──────────────────────────────────────────────────────────────────────────────


def _make_llm_result(text: str, ok: bool = True) -> Any:
    return SimpleNamespace(ok=ok, text=text)


# llm_call is imported lazily inside _llm_rerank(); patch it at its source.
_LLM_PATCH = "orivellum.capabilities.llm.llm_call"


def test_llm_rerank_valid_output():
    """A well-formed ranking reverses the candidate order."""
    cands = [{"text": "aaa"}, {"text": "bbb"}, {"text": "ccc"}]
    with patch(_LLM_PATCH, return_value=_make_llm_result("3, 2, 1")):
        indices = _llm_rerank("query", cands, db=None, cfg=None)
    assert indices == [2, 1, 0]


def test_llm_rerank_partial_output_appends_missing():
    """Candidates omitted by the LLM are appended in their original order."""
    cands = [{"text": "a"}, {"text": "b"}, {"text": "c"}]
    with patch(_LLM_PATCH, return_value=_make_llm_result("2")):
        indices = _llm_rerank("query", cands, db=None, cfg=None)
    # "2" → index 1 (0-based); 0 and 2 appended in order
    assert indices[0] == 1
    assert set(indices) == {0, 1, 2}


def test_llm_rerank_malformed_output_preserves_order():
    """Non-numeric garbage from the LLM produces no explicit ranking.

    When re.findall finds no digits the function falls back to appending all
    candidates in their original order.  It does NOT return None — it returns
    a complete (though unordered) index list so the caller can still fuse
    with BM25 via RRF.  Returning None is reserved for ok=False or empty text.
    """
    cands = [{"text": "a"}, {"text": "b"}]
    with patch(_LLM_PATCH, return_value=_make_llm_result("sure! great question")):
        result = _llm_rerank("query", cands, db=None, cfg=None)
    # All indices present, original order preserved (no explicit ranking signal)
    assert result is not None
    assert set(result) == {0, 1}
    assert result == [0, 1]


def test_llm_rerank_out_of_range_numbers_ignored():
    """Out-of-range passage numbers are silently discarded."""
    cands = [{"text": "a"}, {"text": "b"}]
    with patch(_LLM_PATCH, return_value=_make_llm_result("99, 1, 2")):
        indices = _llm_rerank("query", cands, db=None, cfg=None)
    # 99 is out of range; 1 → idx 0, 2 → idx 1
    assert indices is not None
    assert 98 not in indices  # 99-1=98 must not appear


def test_llm_rerank_failed_call_returns_none():
    """A failed llm_call (ok=False) returns None."""
    cands = [{"text": "a"}]
    with patch(_LLM_PATCH, return_value=_make_llm_result("", ok=False)):
        result = _llm_rerank("query", cands, db=None, cfg=None)
    assert result is None


def test_llm_rerank_empty_candidates():
    """Empty candidate list returns None without invoking llm_call."""
    with patch(_LLM_PATCH) as mock_llm:
        result = _llm_rerank("query", [], db=None, cfg=None)
    mock_llm.assert_not_called()
    assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# rerank_candidates — integration
# ──────────────────────────────────────────────────────────────────────────────


def test_rerank_candidates_bm25_only_when_flag_off():
    """When ai_reranking_enabled is false, llm_call is never invoked."""
    cands = _cands(
        "unrelated content about cats and dogs",
        "machine learning neural networks deep learning",
    )
    db = _fake_db("false")
    # Patch at the source so we can track calls (lazy import inside _llm_rerank)
    with patch(_LLM_PATCH) as mock_llm:
        result = rerank_candidates("machine learning", cands, db)
    mock_llm.assert_not_called()
    # BM25 should still promote the relevant candidate
    assert result[0]["id"] == "1"


def test_rerank_candidates_no_internal_field_in_output():
    """_rerank_idx must not appear in rerank_candidates() output."""
    cands = _cands("machine learning", "cat dog")
    db = _fake_db("false")
    for hit in rerank_candidates("machine", cands, db):
        assert "_rerank_idx" not in hit


def test_rerank_candidates_rerank_score_always_present():
    """Every returned dict has a rerank_score field."""
    cands = _cands("alpha", "beta", "gamma")
    db = _fake_db("false")
    for hit in rerank_candidates("alpha", cands, db):
        assert "rerank_score" in hit


def test_rerank_candidates_top_k():
    """top_k limits the returned list length."""
    cands = _cands("a", "b", "c", "d", "e")
    db = _fake_db("false")
    result = rerank_candidates("a", cands, db, top_k=2)
    assert len(result) == 2


def test_rerank_candidates_llm_changes_order_when_enabled():
    """When ai_reranking_enabled is true and the LLM returns a ranking,
    RRF fusion produces a different order than pure BM25.

    Setup:
      - Candidate 0 "machine learning" → high BM25 score (BM25 rank 0)
      - Candidate 1 "deep neural networks" → medium BM25 score (BM25 rank 1)
      - Candidate 2 "cat sat mat" → zero BM25 score (BM25 rank 2)
      - LLM says: [2, 0, 1] → candidate 2 is most relevant, then 0, then 1

    Pure BM25 order: 0, 1, 2
    With RRF fusion (llm_indices=[2, 0, 1]):
      RRF(id=0): 1/(61+0) + 1/(61+1) = 1/61 + 1/62 ≈ 0.03252  (rank 0)
      RRF(id=2): 1/(61+2) + 1/(61+0) = 1/63 + 1/61 ≈ 0.03226  (rank 1)
      RRF(id=1): 1/(61+1) + 1/(61+2) = 1/62 + 1/63 ≈ 0.03200  (rank 2)

    Fused order: 0, 2, 1  — candidate 2 moves up, 1 moves down.
    """
    cands = [
        {"id": "0", "text": "machine learning"},
        {"id": "1", "text": "deep neural networks"},
        {"id": "2", "text": "cat sat mat"},
    ]
    db = _fake_db("true")

    # Patch both _llm_rerank (to avoid a real LLM call) and load_config.
    with (
        patch("orivellum.capabilities.rerank._llm_rerank", return_value=[2, 0, 1]),
        patch("orivellum.configuration.config.load_config", return_value=MagicMock()),
    ):
        result = rerank_candidates("machine learning", cands, db)

    ids = [r["id"] for r in result]
    # Pure BM25 would give [0, 1, 2].  RRF pushes 2 above 1.
    assert ids == ["0", "2", "1"], f"Expected ['0','2','1'], got {ids}"
    assert len(result) == 3


def test_rerank_candidates_llm_exception_falls_back_to_bm25():
    """When LLM re-ranker raises an exception, result is still BM25-ordered."""
    cands = _cands(
        "irrelevant content about weather",
        "neural networks machine learning deep learning",
    )
    db = _fake_db("true")

    with (
        patch(
            "orivellum.capabilities.rerank._llm_rerank", side_effect=RuntimeError("endpoint down")
        ),
        patch.object(db, "get_setting", return_value="true"),
    ):
        result = rerank_candidates("machine learning", cands, db)

    # BM25 should still work: candidate 1 is more relevant
    assert result[0]["id"] == "1"
    assert all("rerank_score" in h for h in result)


def test_rerank_candidates_none_db():
    """When db=None, stage 2 is skipped and BM25 result is returned."""
    cands = _cands("machine learning", "cat hat mat")
    result = rerank_candidates("machine learning", cands, db=None)
    assert result[0]["id"] == "0"
    assert all("_rerank_idx" not in h for h in result)


def test_rerank_candidates_empty():
    """Empty candidates return an empty list."""
    assert rerank_candidates("query", [], db=None) == []


def test_rerank_candidates_blank_query():
    """Blank query returns the original list without re-ranking."""
    cands = _cands("a", "b")
    result = rerank_candidates("  ", cands, db=None)
    assert result is cands


def test_rerank_candidates_survives_corrupt_candidate():
    """A candidate with None text does not crash rerank_candidates."""
    cands = [{"id": "0", "text": None}, {"id": "1", "text": "machine learning"}]
    result = rerank_candidates("machine learning", cands, db=None)
    # Should return both candidates without raising
    assert len(result) == 2
    assert all("rerank_score" in h for h in result)
