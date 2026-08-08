"""Tests for the cross-encoder reranker stage (Lemonade /rerank).

Covers: score parsing, circuit-breaker behaviour, config gating, and the
integration of cross-encoder ordering into rerank_candidates.
"""
from __future__ import annotations

import io
import json

import pytest

from orivellum.capabilities import rerank


@pytest.fixture(autouse=True)
def _reset_breaker():
    rerank.reset_cross_encoder_breaker()
    rerank._ce_healthy = False
    rerank._ce_inflight = False
    yield
    rerank.reset_cross_encoder_breaker()
    rerank._ce_healthy = False
    rerank._ce_inflight = False


def _fake_response(payload: dict):
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _Resp(json.dumps(payload).encode())


def test_scores_none_when_model_unconfigured(monkeypatch):
    monkeypatch.setattr(rerank, "_serving_reranker", lambda: ("http://x", ""))
    assert rerank.cross_encoder_scores("q", ["a", "b"]) is None


def test_scores_parsed_and_aligned(monkeypatch):
    monkeypatch.setattr(rerank, "_serving_reranker",
                        lambda: ("http://x", "bge-reranker-v2-m3-GGUF"))
    payload = {"results": [
        {"index": 1, "relevance_score": 9.5},
        {"index": 0, "relevance_score": 1.2},
    ]}
    monkeypatch.setattr(rerank.urllib.request, "urlopen",
                        lambda req, timeout: _fake_response(payload))
    scores = rerank.cross_encoder_scores("capital of France", ["berlin", "paris"])
    assert scores == [1.2, 9.5]


def test_malformed_response_returns_none_and_opens_breaker(monkeypatch):
    monkeypatch.setattr(rerank, "_serving_reranker",
                        lambda: ("http://x", "bge-reranker-v2-m3-GGUF"))
    # Only one score for two documents → malformed
    payload = {"results": [{"index": 0, "relevance_score": 3.0}]}
    monkeypatch.setattr(rerank.urllib.request, "urlopen",
                        lambda req, timeout: _fake_response(payload))
    assert rerank.cross_encoder_scores("q", ["a", "b"]) is None
    # A malformed endpoint is as unusable as a down one → cooldown opens
    assert rerank.cross_encoder_status()["circuit_open"] is True


def test_duplicate_indices_rejected(monkeypatch):
    monkeypatch.setattr(rerank, "_serving_reranker",
                        lambda: ("http://x", "bge-reranker-v2-m3-GGUF"))
    payload = {"results": [
        {"index": 0, "relevance_score": 3.0},
        {"index": 0, "relevance_score": 5.0},  # duplicate — doc 1 never scored
    ]}
    monkeypatch.setattr(rerank.urllib.request, "urlopen",
                        lambda req, timeout: _fake_response(payload))
    assert rerank.cross_encoder_scores("q", ["a", "b"]) is None
    assert rerank.cross_encoder_status()["circuit_open"] is True


def test_unproven_endpoint_single_flight(monkeypatch):
    """While the endpoint is unproven, a concurrent caller must not block."""
    monkeypatch.setattr(rerank, "_serving_reranker",
                        lambda: ("http://x", "bge-reranker-v2-m3-GGUF"))
    inner_result = {}

    def _slow_urlopen(req, timeout):
        # Simulate a second request arriving while the first is in flight:
        # it must return None immediately (no nested network attempt).
        inner_result["concurrent"] = rerank.cross_encoder_scores("q", ["x"])
        return _fake_response({"results": [{"index": 0, "relevance_score": 1.0}]})

    monkeypatch.setattr(rerank.urllib.request, "urlopen", _slow_urlopen)
    assert rerank.cross_encoder_scores("q", ["a"]) == [1.0]
    assert inner_result["concurrent"] is None


def test_setting_normalization_case_insensitive():
    class _DB:
        def get_setting(self, key, default=""):
            return " TRUE "

    assert rerank.cross_reranker_enabled(_DB()) is True
    assert rerank.cross_reranker_enabled(None) is True

    class _DBOff:
        def get_setting(self, key, default=""):
            return "False"

    assert rerank.cross_reranker_enabled(_DBOff()) is False


def test_failure_opens_breaker_and_skips_network(monkeypatch):
    monkeypatch.setattr(rerank, "_serving_reranker",
                        lambda: ("http://x", "bge-reranker-v2-m3-GGUF"))
    calls = {"n": 0}

    def _boom(req, timeout):
        calls["n"] += 1
        raise OSError("connection refused")

    monkeypatch.setattr(rerank.urllib.request, "urlopen", _boom)
    assert rerank.cross_encoder_scores("q", ["a"]) is None
    assert calls["n"] == 1
    # Second call must not hit the network while the breaker is open
    assert rerank.cross_encoder_scores("q", ["a"]) is None
    assert calls["n"] == 1
    st = rerank.cross_encoder_status()
    assert st["circuit_open"] is True
    # Probe reset closes the breaker
    rerank.reset_cross_encoder_breaker()
    assert rerank.cross_encoder_status()["circuit_open"] is False


def test_success_closes_breaker(monkeypatch):
    monkeypatch.setattr(rerank, "_serving_reranker",
                        lambda: ("http://x", "bge-reranker-v2-m3-GGUF"))
    payload = {"results": [{"index": 0, "relevance_score": 1.0}]}
    monkeypatch.setattr(rerank.urllib.request, "urlopen",
                        lambda req, timeout: _fake_response(payload))
    rerank._ce_unavailable_until = 0.0  # ensure closed before the call
    assert rerank.cross_encoder_scores("q", ["a"]) == [1.0]
    assert rerank.cross_encoder_status()["circuit_open"] is False


class _StubDB:
    """Minimal DB stub exposing get_setting only."""

    def __init__(self, settings: dict[str, str]):
        self._settings = settings

    def get_setting(self, key: str, default: str = "") -> str:
        return self._settings.get(key, default)


def test_rerank_candidates_uses_cross_encoder_ordering(monkeypatch):
    candidates = [
        {"id": 1, "text": "unrelated filler text about weather"},
        {"id": 2, "text": "the exact answer to the query"},
        {"id": 3, "text": "another unrelated passage"},
    ]

    def _fake_scores(query, texts, **kw):
        # Highest score for the candidate containing "exact answer"
        return [10.0 if "exact answer" in t else 0.5 for t in texts]

    monkeypatch.setattr(rerank, "cross_encoder_scores", _fake_scores)
    db = _StubDB({"cross_reranker_enabled": "true", "ai_reranking_enabled": "false"})
    out = rerank.rerank_candidates("what is the exact answer", candidates, db)
    assert out[0]["id"] == 2
    assert all("rerank_score" in c for c in out)
    assert len(out) == 3  # pure re-orderer — never drops candidates


def test_rerank_candidates_setting_off_skips_cross_encoder(monkeypatch):
    called = {"ce": False}

    def _fake_scores(query, texts, **kw):
        called["ce"] = True
        return [1.0] * len(texts)

    monkeypatch.setattr(rerank, "cross_encoder_scores", _fake_scores)
    db = _StubDB({"cross_reranker_enabled": "false", "ai_reranking_enabled": "false"})
    candidates = [{"id": 1, "text": "alpha"}, {"id": 2, "text": "beta"}]
    out = rerank.rerank_candidates("alpha", candidates, db)
    assert called["ce"] is False
    assert len(out) == 2


def test_rerank_candidates_ce_failure_falls_back_to_bm25(monkeypatch):
    monkeypatch.setattr(rerank, "cross_encoder_scores", lambda q, t, **kw: None)
    db = _StubDB({"cross_reranker_enabled": "true", "ai_reranking_enabled": "false"})
    candidates = [
        {"id": 1, "text": "nothing relevant here"},
        {"id": 2, "text": "orivellum knowledge system design"},
    ]
    out = rerank.rerank_candidates("orivellum knowledge", candidates, db)
    # BM25 should still bubble the relevant candidate up
    assert out[0]["id"] == 2
