"""
Acceptance tests for the voice recommender endpoint with sparse Works.

Covers:
  1. Work with 0 documents + 0 knowledge items → keyword fallback (no LLM call),
     no_content=True, valid schema — so the mobile client never crashes on parse.
  2. Work with a description but no documents → LLM path attempted (no early exit);
     endpoint still returns valid schema even when LLM is stubbed to fail.
  3. Fallback JSON schema is always complete — required keys always present.
  4. Non-existent work_id → 404.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_client(tmp_path):
    """Return a TestClient + OrivellumDB backed by a temp directory."""
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=str(tmp_path))
    db = OrivellumDB(str(tmp_path / "test.db"))
    _deps.init(db=db, cfg=cfg)
    client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
    return client, db


# ── Expected schema keys ─────────────────────────────────────────────────────

_REQUIRED_TOP_KEYS = {
    "work_id",
    "work_title",
    "genre_analysis",
    "narrator_profile",
    "recommendations",
    "no_content",
}
_REQUIRED_REC_KEYS = {
    "voice_id",
    "score",
    "headline",
    "rationale",
    "dimension_match",
    "voice",
}


def _assert_schema(data: dict) -> None:
    """Assert the response dict has the complete expected schema."""
    missing = _REQUIRED_TOP_KEYS - data.keys()
    assert not missing, f"Response missing top-level keys: {missing}"
    assert isinstance(data["recommendations"], list), "recommendations must be a list"
    for rec in data["recommendations"]:
        missing_rec = _REQUIRED_REC_KEYS - rec.keys()
        assert not missing_rec, f"Recommendation missing keys: {missing_rec}"
        assert isinstance(rec["voice_id"], str) and rec["voice_id"], (
            "voice_id must be a non-empty string"
        )
        assert isinstance(rec["score"], (int, float)), "score must be numeric"
        assert isinstance(rec["voice"], dict), "voice must be a dict"


# ── Test 1: Work with 0 documents + 0 knowledge items ────────────────────────


def test_sparse_work_returns_fallback_without_llm_call(tmp_path):
    """A brand-new Work with no documents and no knowledge items must return the
    keyword fallback immediately — no LLM call, no error, and no_content=True.
    """
    client, db = _make_client(tmp_path)

    # Create a Work with no description (completely empty); create_work returns the dict
    work_id = db.create_work(title="Empty New Novel", work_type="writing")["id"]

    # Patch llm_call to a sentinel that raises if called — it must NOT be called.
    sentinel = MagicMock(side_effect=AssertionError("llm_call must not be called for sparse Work"))

    with patch("orivellum.capabilities.llm.llm_call", sentinel):
        resp = client.post(
            "/api/studio/voices/recommend",
            json={"work_id": work_id, "top_n": 3},
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    # Schema must always be complete so the mobile client can parse it
    _assert_schema(data)

    # Must signal sparse content
    assert data["no_content"] is True, "Expected no_content=True for a Work with no documents"

    # Must return recommendations (fallback defaults), not an empty list
    assert len(data["recommendations"]) == 3, (
        f"Expected 3 fallback recommendations, got {len(data['recommendations'])}"
    )

    # Every recommendation must reference a real voice from the catalog
    from orivellum.api.routes.studio import _VOICE_BY_ID

    for rec in data["recommendations"]:
        assert rec["voice_id"] in _VOICE_BY_ID, (
            f"Fallback voice_id {rec['voice_id']!r} not found in catalog"
        )

    # The genre_analysis must mention lack of content — not a hallucinated genre
    assert (
        "content" in data["genre_analysis"].lower() or "document" in data["genre_analysis"].lower()
    ), "genre_analysis should explain lack of content for a sparse Work"

    # llm_call must NOT have been called
    sentinel.assert_not_called()


# ── Test 2: Work with description only → LLM attempted; fallback on failure ──


def test_work_with_description_attempts_llm_then_falls_back(tmp_path):
    """A Work with only a description (no documents) should attempt the LLM call.
    When the LLM fails, the endpoint must still return a valid schema (no 500).
    """
    client, db = _make_client(tmp_path)

    work_id = db.create_work(
        title="Described But Undocumented Work",
        work_type="writing",
        description="A dark psychological thriller set in 1920s Vienna.",
    )["id"]

    # Stub llm_call to simulate a failed response (ok=False) and track that it
    # was actually called — a description-only work must NOT take the early exit.
    failed_result = MagicMock()
    failed_result.ok = False
    failed_result.text = ""
    mock_llm = MagicMock(return_value=failed_result)

    with patch("orivellum.capabilities.llm.llm_call", mock_llm):
        resp = client.post(
            "/api/studio/voices/recommend",
            json={"work_id": work_id, "top_n": 5},
        )

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()

    _assert_schema(data)

    # The LLM must have been invoked — a description is enough content to attempt analysis
    mock_llm.assert_called_once(), "llm_call must be invoked for a Work that has a description"

    # With description, no_content must be False (content exists via description)
    assert data["no_content"] is False, (
        "Work with a description should NOT be marked no_content=True"
    )

    # Must return fallback recommendations (LLM failed, so keyword defaults apply)
    assert len(data["recommendations"]) == 5


# ── Test 3: Complete schema even when LLM returns malformed JSON ──────────────


def test_malformed_llm_json_returns_valid_fallback_schema(tmp_path):
    """If the LLM returns non-JSON garbage, the endpoint must still return a
    valid schema — no 500, and the mobile client can always parse the response.
    """
    client, db = _make_client(tmp_path)

    work_id = db.create_work(
        title="Malformed LLM Test Work",
        description="A sprawling epic fantasy with dragons and intrigue.",
    )["id"]

    # Stub llm_call to return syntactically broken JSON
    bad_result = MagicMock()
    bad_result.ok = True
    bad_result.text = "Sure! Here are my picks: {not: valid json at all..."

    with patch("orivellum.capabilities.llm.llm_call", return_value=bad_result):
        resp = client.post(
            "/api/studio/voices/recommend",
            json={"work_id": work_id, "top_n": 5},
        )

    assert resp.status_code == 200
    data = resp.json()
    _assert_schema(data)
    assert isinstance(data["recommendations"], list)
    assert len(data["recommendations"]) > 0, "Must have at least one fallback recommendation"


# ── Test 4: Non-existent work_id → 404 ───────────────────────────────────────


def test_nonexistent_work_returns_404(tmp_path):
    """A work_id that does not exist in the database must return 404."""
    client, _ = _make_client(tmp_path)

    resp = client.post(
        "/api/studio/voices/recommend",
        json={"work_id": "does-not-exist-00000000", "top_n": 3},
    )
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"
