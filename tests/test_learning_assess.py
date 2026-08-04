"""
Tests: quiz → mastery update pipeline (task #275)

Three coverage layers:
  L1  _record_mastery — database writes are correct for pass and fail.
  L2  assess_answer  — LLM mocked; verifies DB state and return shape.
  L3  HTTP route     — POST /api/works/{id}/learning/assess end-to-end
                       via FastAPI TestClient with the real learning router.

All tests use the real OrivellumDB with in-memory SQLite so the full schema
and migration stack is exercised without disk I/O.  The LLM gateway is
patched at the module boundary so tests never make real network calls.
"""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest

from tests.conftest import AUTH_HEADERS


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db():
    """Fresh in-memory OrivellumDB with full schema applied."""
    from orivellum.database import OrivellumDB
    return OrivellumDB(":memory:")


def _seed(db):
    """Create Work + concept + one knowledge item; return (work_id, concept_id)."""
    work = db.create_work("Test Work", work_type="learning")
    work_id = work["id"]
    db.create_knowledge_item(
        work_id=work_id,
        kind="fact",
        text="The sky appears blue because of Rayleigh scattering of sunlight.",
        confidence=1.0,
    )
    concept_id = str(uuid.uuid4())
    now = "2024-01-01T00:00:00+00:00"
    with db._lock:
        db._conn.execute(
            "INSERT INTO work_concepts"
            "(id,work_id,subject,description,prereq_id,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (concept_id, work_id, "Optics", "Study of light and colour", None, now),
        )
        db._conn.commit()
    return work_id, concept_id


def _make_test_client(db):
    """Build a FastAPI TestClient wired to the given DB (no lifespan needed)."""
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from orivellum.api.routes import learning
    from orivellum.api import _deps
    from orivellum.configuration.config import OrivellumConfig, ServingConfig

    cfg = OrivellumConfig()
    cfg.serving = ServingConfig(
        base_url="http://localhost:11434/v1",
        workhorse_model="test-model",
    )
    _deps.init(db=db, cfg=cfg)

    app = FastAPI()
    app.include_router(learning.router)
    return TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)


DUMMY_URL = "http://localhost:11434/v1"   # truthy; _call is always patched


# ── L1: _record_mastery ────────────────────────────────────────────────────────

class TestRecordMastery:
    """Direct tests of the _record_mastery internal helper."""

    def test_pass_increments_consecutive(self):
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _record_mastery, _get_mastery
        _record_mastery(db, cid, score=0.9, route="STAY_HERE", feedback="Good")
        m = _get_mastery(db, cid)
        assert m["consecutive_passes"] == 1
        assert m["score"] == pytest.approx(0.9)

    def test_fail_resets_consecutive(self):
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _record_mastery, _get_mastery
        _record_mastery(db, cid, score=0.9, route="STAY_HERE", feedback="Good")
        _record_mastery(db, cid, score=0.9, route="STAY_HERE", feedback="Good")
        _record_mastery(db, cid, score=0.3, route="STAY_HERE", feedback="Wrong")
        m = _get_mastery(db, cid)
        assert m["consecutive_passes"] == 0, "failure must reset consecutive pass streak"

    def test_three_passes_graduates(self):
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _record_mastery, _is_graduated
        for _ in range(3):
            _record_mastery(db, cid, score=0.9, route="STEP_FORWARD", feedback="")
        assert _is_graduated(db, cid)


# ── L2: assess_answer (LLM mocked) ────────────────────────────────────────────

class TestAssessAnswer:
    """assess_answer with the LLM response stubbed out."""

    def _assess(self, db, concept_id, score_json: str):
        from orivellum.capabilities.learning import assess_answer
        with patch("orivellum.capabilities.learning._call", return_value=score_json):
            return assess_answer(
                db, concept_id,
                question="Why does the sky look blue?",
                answer="Because of Rayleigh scattering of sunlight",
                base_url=DUMMY_URL,
                model="test-model",
            )

    def test_correct_answer_stores_row_with_high_score(self):
        db = _make_db()
        _, cid = _seed(db)
        self._assess(db, cid, '{"score":0.9,"feedback":"Correct!"}')
        with db._lock:
            row = db._conn.execute(
                "SELECT score, consecutive_passes FROM work_mastery"
                " WHERE concept_id=? ORDER BY created_at DESC LIMIT 1",
                (cid,),
            ).fetchone()
        assert row is not None, "work_mastery must gain a row after assess"
        assert row["score"] >= 0.6, f"expected score ≥ 0.6, got {row['score']}"
        assert row["consecutive_passes"] == 1

    def test_wrong_answer_does_not_increment_consecutive(self):
        db = _make_db()
        _, cid = _seed(db)
        self._assess(db, cid, '{"score":0.2,"feedback":"Incorrect."}')
        with db._lock:
            row = db._conn.execute(
                "SELECT consecutive_passes FROM work_mastery"
                " WHERE concept_id=? ORDER BY created_at DESC LIMIT 1",
                (cid,),
            ).fetchone()
        assert row is not None
        assert row["consecutive_passes"] == 0, "failed answer must not increment streak"

    def test_result_includes_route(self):
        db = _make_db()
        _, cid = _seed(db)
        result = self._assess(db, cid, '{"score":0.9,"feedback":"Nice"}')
        assert "route" in result
        assert result["route"] in ("STEP_FORWARD", "STAY_HERE", "STEP_BACKWARD")

    def test_result_includes_graduated_flag(self):
        db = _make_db()
        _, cid = _seed(db)
        result = self._assess(db, cid, '{"score":0.8,"feedback":"Good"}')
        assert "graduated" in result
        assert isinstance(result["graduated"], bool)

    def test_summary_reflects_mastery(self):
        db = _make_db()
        work_id, cid = _seed(db)
        self._assess(db, cid, '{"score":0.9,"feedback":"Correct!"}')
        from orivellum.capabilities.learning import get_mastery_summary
        summary = get_mastery_summary(db, work_id)
        assert summary["total"] >= 1
        assert "mastery_pct" in summary
        assert summary["mastery_pct"] >= 0

    def test_invalid_json_falls_back_gracefully(self):
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import assess_answer
        with patch("orivellum.capabilities.learning._call", return_value="not json"):
            result = assess_answer(db, cid, "Q?", "A", base_url=DUMMY_URL, model="test")
        assert result["route"] == "STAY_HERE"
        assert result["score"] == pytest.approx(0.5)

    def test_three_correct_answers_graduate_concept(self):
        db = _make_db()
        _, cid = _seed(db)
        for _ in range(3):
            self._assess(db, cid, '{"score":0.9,"feedback":"Correct!"}')
        from orivellum.capabilities.learning import _is_graduated
        assert _is_graduated(db, cid), "concept must be graduated after 3 consecutive passes"

    def test_fail_then_pass_resets_streak(self):
        db = _make_db()
        _, cid = _seed(db)
        self._assess(db, cid, '{"score":0.9,"feedback":"Correct!"}')
        self._assess(db, cid, '{"score":0.2,"feedback":"Wrong"}')   # reset streak
        self._assess(db, cid, '{"score":0.9,"feedback":"Correct!"}')
        with db._lock:
            row = db._conn.execute(
                "SELECT consecutive_passes FROM work_mastery"
                " WHERE concept_id=? ORDER BY created_at DESC LIMIT 1",
                (cid,),
            ).fetchone()
        assert row["consecutive_passes"] == 1, "streak must restart from 1 after a failure"


# ── L3: HTTP route via FastAPI TestClient ─────────────────────────────────────

class TestAssessRoute:
    """HTTP-level tests for POST /api/works/{work_id}/learning/assess."""

    def test_correct_answer_returns_200_with_score(self):
        db = _make_db()
        work_id, concept_id = _seed(db)
        client = _make_test_client(db)
        with patch("orivellum.capabilities.learning._call",
                   return_value='{"score":0.85,"feedback":"Well done!"}'):
            r = client.post(
                f"/api/works/{work_id}/learning/assess",
                json={"concept_id": concept_id, "question": "Why blue sky?",
                      "answer": "Rayleigh scattering"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["score"] == pytest.approx(0.85)
        assert "route" in body
        assert "summary" in body

    def test_wrong_answer_returns_200_and_no_streak(self):
        db = _make_db()
        work_id, concept_id = _seed(db)
        client = _make_test_client(db)
        with patch("orivellum.capabilities.learning._call",
                   return_value='{"score":0.1,"feedback":"Try again."}'):
            r = client.post(
                f"/api/works/{work_id}/learning/assess",
                json={"concept_id": concept_id, "question": "Why blue sky?",
                      "answer": "magic"},
            )
        assert r.status_code == 200, r.text
        with db._lock:
            row = db._conn.execute(
                "SELECT consecutive_passes FROM work_mastery"
                " WHERE concept_id=? ORDER BY created_at DESC LIMIT 1",
                (concept_id,),
            ).fetchone()
        assert row is not None
        assert row["consecutive_passes"] == 0

    def test_summary_shows_nonzero_mastery_after_graduation(self):
        """Summary returned in the assess response must reflect graduation."""
        db = _make_db()
        work_id, concept_id = _seed(db)
        client = _make_test_client(db)
        last_resp = None
        for _ in range(3):
            with patch("orivellum.capabilities.learning._call",
                       return_value='{"score":0.9,"feedback":"Correct!"}'):
                last_resp = client.post(
                    f"/api/works/{work_id}/learning/assess",
                    json={"concept_id": concept_id, "question": "Q?", "answer": "A"},
                )
        assert last_resp.status_code == 200, last_resp.text
        summary = last_resp.json()["summary"]
        assert summary["graduated"] >= 1, "at least one concept must be graduated"
        assert summary["mastery_pct"] > 0, "mastery_pct must be non-zero after graduation"

    def test_empty_answer_returns_422(self):
        db = _make_db()
        work_id, concept_id = _seed(db)
        client = _make_test_client(db)
        r = client.post(
            f"/api/works/{work_id}/learning/assess",
            json={"concept_id": concept_id, "question": "Q?", "answer": ""},
        )
        assert r.status_code == 422

    def test_unknown_concept_returns_404(self):
        db = _make_db()
        work_id, _ = _seed(db)
        client = _make_test_client(db)
        r = client.post(
            f"/api/works/{work_id}/learning/assess",
            json={"concept_id": str(uuid.uuid4()), "question": "Q?", "answer": "A"},
        )
        assert r.status_code == 404

    def test_unknown_work_returns_404(self):
        db = _make_db()
        _, concept_id = _seed(db)
        client = _make_test_client(db)
        r = client.post(
            f"/api/works/{str(uuid.uuid4())}/learning/assess",
            json={"concept_id": concept_id, "question": "Q?", "answer": "A"},
        )
        assert r.status_code == 404
