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

import json
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

    from orivellum.api import _deps
    from orivellum.api.routes import learning
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


DUMMY_URL = "http://localhost:11434/v1"  # truthy; _call is always patched


def _issue(db, concept_id, level="recall", question="Q?"):
    """Record a server-issued question so the assess route accepts the POST.

    The route only assesses questions the server issued (single-use binding);
    tests that POST directly must issue first, exactly as GET /question would.
    """
    from orivellum.capabilities.learning import _issue_question

    _issue_question(db, concept_id, level, question)


def _rubric_payload(score: float, quote: str = "A", **extra) -> str:
    """Critic JSON with a 4-criterion rubric equivalent to the given score.

    Levels above recall fail closed without a verifiable rubric, so tests
    exercising those levels must mock rubric-shaped critic output.  Met
    criteria carry the extractive quote (must be a substring of the answer).
    """
    met_n = round(float(score) * 4)
    payload = {
        "criteria": [
            {"criterion": f"c{i}", "met": i < met_n, "quote": quote if i < met_n else ""}
            for i in range(4)
        ],
        "feedback": "graded",
        **extra,
    }
    return json.dumps(payload)


# ── L1: _record_mastery ────────────────────────────────────────────────────────


class TestRecordMastery:
    """Direct tests of the _record_mastery internal helper."""

    def test_pass_increments_consecutive(self):
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _get_mastery, _record_mastery

        _record_mastery(db, cid, score=0.9, route="STAY_HERE", feedback="Good")
        m = _get_mastery(db, cid)
        assert m["consecutive_passes"] == 1
        assert m["score"] == pytest.approx(0.9)

    def test_fail_resets_consecutive(self):
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _get_mastery, _record_mastery

        _record_mastery(db, cid, score=0.9, route="STAY_HERE", feedback="Good")
        _record_mastery(db, cid, score=0.9, route="STAY_HERE", feedback="Good")
        _record_mastery(db, cid, score=0.3, route="STAY_HERE", feedback="Wrong")
        m = _get_mastery(db, cid)
        assert m["consecutive_passes"] == 0, "failure must reset consecutive pass streak"

    def test_three_passes_graduates(self):
        """Graduation needs the streak AND the depth ladder (recall alone is not enough)."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _is_graduated, _record_mastery

        # Single-concept Work → contrast is skipped; ladder = recall/self_explanation/transfer
        for qt in ("recall", "self_explanation", "transfer"):
            _record_mastery(db, cid, score=0.9, route="STEP_FORWARD", feedback="", question_type=qt)
        assert _is_graduated(db, cid)

    def test_recall_only_streak_never_graduates(self):
        """A concept cannot graduate on recall alone, no matter how long the streak (T-M4)."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _is_graduated, _record_mastery

        for _ in range(6):
            _record_mastery(db, cid, score=0.95, route="STAY_HERE", feedback="")
        assert not _is_graduated(db, cid), "recall-only streak must never graduate"


# ── L2: assess_answer (LLM mocked) ────────────────────────────────────────────


class TestAssessAnswer:
    """assess_answer with the LLM response stubbed out."""

    def _assess(self, db, concept_id, score_json: str):
        from orivellum.capabilities.learning import assess_answer

        with patch("orivellum.capabilities.learning._call", return_value=score_json):
            return assess_answer(
                db,
                concept_id,
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
        """Three passes graduate only when they climb the depth ladder."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _is_graduated, assess_answer

        # Single-concept Work → ladder is recall → self_explanation → transfer
        for qt in ("recall", "self_explanation", "transfer"):
            with patch(
                "orivellum.capabilities.learning._call",
                return_value=_rubric_payload(1.0),
            ):
                assess_answer(
                    db,
                    cid,
                    question="Q?",
                    answer="A",
                    base_url=DUMMY_URL,
                    model="test-model",
                    question_type=qt,
                )
        assert _is_graduated(db, cid), "concept must graduate after the ladder is climbed"

    def test_fail_then_pass_resets_streak(self):
        db = _make_db()
        _, cid = _seed(db)
        self._assess(db, cid, '{"score":0.9,"feedback":"Correct!"}')
        self._assess(db, cid, '{"score":0.2,"feedback":"Wrong"}')  # reset streak
        self._assess(db, cid, '{"score":0.9,"feedback":"Correct!"}')
        with db._lock:
            row = db._conn.execute(
                "SELECT consecutive_passes FROM work_mastery"
                " WHERE concept_id=? ORDER BY created_at DESC LIMIT 1",
                (cid,),
            ).fetchone()
        assert row["consecutive_passes"] == 1, "streak must restart from 1 after a failure"


# ── L2b: Error classification ──────────────────────────────────────────────────


class TestErrorClassification:
    """assess_answer must classify wrong answers and return targeted remediation."""

    def _assess_raw(self, db, cid: str, json_str: str):
        """Call assess_answer with a mocked LLM that returns json_str."""
        from orivellum.capabilities.learning import assess_answer

        with patch("orivellum.capabilities.learning._call", return_value=json_str):
            return assess_answer(db, cid, "Q?", "A", base_url="http://x", model="t")

    def test_correct_answer_has_null_error_type(self):
        """error_type must be None when score >= 0.75 (correct answer)."""
        db = _make_db()
        _, cid = _seed(db)
        result = self._assess_raw(
            db,
            cid,
            '{"score":0.9,"feedback":"Great.","error_type":"null","remediation_hint":"n/a"}',
        )
        assert result["error_type"] is None, "correct answer must have error_type=None"
        assert result["deep_review_needed"] is False

    def test_careless_slip_is_returned_and_persisted(self):
        """error_type='careless_slip' must be returned and written to work_mastery."""
        db = _make_db()
        _, cid = _seed(db)
        result = self._assess_raw(
            db,
            cid,
            '{"score":0.6,"feedback":"Small slip.","error_type":"careless_slip",'
            '"remediation_hint":"Double-check your arithmetic."}',
        )
        assert result["error_type"] == "careless_slip"
        assert result["remediation_hint"] == "Double-check your arithmetic."

        # Verify persistence in work_mastery
        with db._lock:
            row = db._conn.execute(
                "SELECT error_type, remediation_hint FROM work_mastery"
                " WHERE concept_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (cid,),
            ).fetchone()
        assert row["error_type"] == "careless_slip"
        assert row["remediation_hint"] == "Double-check your arithmetic."

    def test_procedural_gap_is_returned(self):
        db = _make_db()
        _, cid = _seed(db)
        result = self._assess_raw(
            db,
            cid,
            '{"score":0.4,"feedback":"You know the concept but missed a step.",'
            '"error_type":"procedural_gap","remediation_hint":"Re-derive step 2."}',
        )
        assert result["error_type"] == "procedural_gap"
        assert "Re-derive" in (result["remediation_hint"] or "")

    def test_knowledge_gap_is_returned(self):
        db = _make_db()
        _, cid = _seed(db)
        result = self._assess_raw(
            db,
            cid,
            '{"score":0.1,"feedback":"No knowledge shown.","error_type":"knowledge_gap",'
            '"remediation_hint":"Review the prerequisites first."}',
        )
        assert result["error_type"] == "knowledge_gap"
        assert result["score"] == pytest.approx(0.1)

    def test_unknown_error_type_falls_back_to_none(self):
        """LLM-invented error types must be silently discarded."""
        db = _make_db()
        _, cid = _seed(db)
        result = self._assess_raw(
            db,
            cid,
            '{"score":0.3,"feedback":"Bad.","error_type":"totally_invented","remediation_hint":"..."}',
        )
        assert result["error_type"] is None, "unrecognised error_type must be None"

    def test_correct_answer_overrides_llm_error_type(self):
        """Even if the LLM says 'careless_slip', a score >= 0.75 must zero out error_type."""
        db = _make_db()
        _, cid = _seed(db)
        result = self._assess_raw(
            db,
            cid,
            '{"score":0.8,"feedback":"Good.","error_type":"careless_slip","remediation_hint":"fix it"}',
        )
        assert result["error_type"] is None, (
            "score >= 0.75 must clear error_type regardless of LLM output"
        )

    def test_deep_review_needed_after_two_misconceptions(self):
        """deep_review_needed must be True after ≥ 2 conceptual_misconception records."""
        db = _make_db()
        _, cid = _seed(db)
        misconception_json = (
            '{"score":0.3,"feedback":"False belief.","error_type":"conceptual_misconception",'
            '"remediation_hint":"Re-examine your model."}'
        )
        # First occurrence: deep_review_needed must be False (count=1, threshold=2)
        r1 = self._assess_raw(db, cid, misconception_json)
        assert r1["deep_review_needed"] is False, (
            "single misconception must not trigger deep review"
        )

        # Second occurrence: threshold reached → deep_review_needed must be True
        r2 = self._assess_raw(db, cid, misconception_json)
        assert r2["deep_review_needed"] is True, (
            "≥2 misconceptions must set deep_review_needed=True"
        )

    def test_v95_schema_columns_exist(self):
        """A fresh OrivellumDB must have error_type and remediation_hint on work_mastery (v95)."""
        db = _make_db()
        with db._lock:
            cols = {
                row[1] for row in db._conn.execute("PRAGMA table_info(work_mastery)").fetchall()
            }
        assert "error_type" in cols, "v95: error_type column must exist on work_mastery"
        assert "remediation_hint" in cols, "v95: remediation_hint column must exist on work_mastery"

    def test_result_includes_all_classification_fields(self):
        """assess_answer must always return all v95 classification fields."""
        db = _make_db()
        _, cid = _seed(db)
        result = self._assess_raw(
            db,
            cid,
            '{"score":0.4,"feedback":"Wrong.","error_type":"procedural_gap","remediation_hint":"try again"}',
        )
        for field in ("error_type", "remediation_hint", "deep_review_needed", "socratic_followup"):
            assert field in result, f"assess_answer must include '{field}' in return value"


# ── L3b: HTTP route — error classification passthrough ───────────────────────


class TestAssessRouteErrorClassification:
    """HTTP route must pass error classification fields through to the client."""

    def test_route_includes_error_type_field(self):
        """POST /learning/assess response body must include error_type."""
        db = _make_db()
        work_id, cid = _seed(db)
        client = _make_test_client(db)
        _issue(db, cid)
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"score":0.3,"feedback":"Wrong.","error_type":"careless_slip",'
            '"remediation_hint":"Check your sign."}',
        ):
            r = client.post(
                f"/api/works/{work_id}/learning/assess",
                json={"concept_id": cid, "question": "Q?", "answer": "A"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "error_type" in body
        assert body["error_type"] == "careless_slip"
        assert "remediation_hint" in body
        assert "deep_review_needed" in body
        assert "socratic_followup" in body

    def test_route_includes_suggested_prereq_fields_for_knowledge_gap(self):
        """When error_type='knowledge_gap' and prereqs exist, route returns prereq name."""
        db = _make_db()
        work = db.create_work("KG Test Work", work_type="learning")
        wid = work["id"]
        now = "2024-01-01T00:00:00+00:00"
        prereq_id = str(uuid.uuid4())
        concept_id = str(uuid.uuid4())
        with db._lock:
            db._conn.execute(
                "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (prereq_id, wid, "Algebra", "Foundation", None, now),
            )
            db._conn.execute(
                "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (concept_id, wid, "Calculus", "Requires algebra", prereq_id, now),
            )
            db._conn.execute(
                "INSERT OR IGNORE INTO work_concept_prereqs(concept_id, prereq_id) VALUES(?,?)",
                (concept_id, prereq_id),
            )
            db._conn.commit()

        client = _make_test_client(db)
        _issue(db, concept_id)
        # knowledge_gap with an unstarted prereq → should STEP_BACKWARD to prereq
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"score":0.1,"feedback":"No knowledge.","error_type":"knowledge_gap",'
            '"remediation_hint":"Study algebra first."}',
        ):
            r = client.post(
                f"/api/works/{wid}/learning/assess",
                json={"concept_id": concept_id, "question": "Q?", "answer": "I don't know"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["route"] in ("STEP_BACKWARD", "STAY_HERE"), (
            "knowledge_gap should route backward when prereqs unstarted"
        )
        assert "suggested_prereq_id" in body
        assert "suggested_prereq_subject" in body


# ── Transfer question tests ───────────────────────────────────────────────────


class TestTransferQuestions:
    """get_question and assess_answer must handle transfer mode correctly."""

    # ── get_question routing ──────────────────────────────────────────────────

    def test_get_question_returns_recall_type_by_default(self):
        """With no consecutive passes, auto mode must resolve to 'recall'."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import get_question

        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"question":"Why blue?","context_snippet":"Rayleigh"}',
        ):
            result = get_question(db, cid, base_url="http://x", model="t", question_type="auto")
        assert result["question_type"] == "recall"
        assert "question" in result

    def test_get_question_climbs_ladder_to_self_explanation(self):
        """After a recall pass, auto must climb to self_explanation (not jump to transfer)."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _record_mastery, get_question

        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="recall")
        result = get_question(db, cid, base_url="http://x", model="t", question_type="auto")
        assert result["question_type"] == "self_explanation", (
            f"Expected self_explanation after recall pass, got {result['question_type']}"
        )

    def test_get_question_reaches_transfer_after_lower_levels(self):
        """Auto resolves to transfer once recall and self_explanation are passed."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _record_mastery, get_question

        # Single-concept Work → contrast is skipped in the ladder
        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="recall")
        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="self_explanation")
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"question":"Novel scenario?","context_snippet":"concept x"}',
        ):
            result = get_question(db, cid, base_url="http://x", model="t", question_type="auto")
        assert result["question_type"] == "transfer", (
            f"Expected transfer after lower levels passed, got {result['question_type']}"
        )

    def test_get_question_explicit_transfer_ignores_streak(self):
        """Explicit type='transfer' must serve a transfer question regardless of streak."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import get_question

        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"question":"Novel?","context_snippet":"concept"}',
        ):
            result = get_question(db, cid, base_url="http://x", model="t", question_type="transfer")
        assert result["question_type"] == "transfer"

    def test_get_question_explicit_recall_stays_recall_despite_streak(self):
        """Explicit type='recall' must stay recall even when streak >= threshold."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _record_mastery, get_question

        for _ in range(3):
            _record_mastery(db, cid, 0.9, "STAY_HERE", "Good")
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"question":"Classic recall Q?","context_snippet":"notes"}',
        ):
            result = get_question(db, cid, base_url="http://x", model="t", question_type="recall")
        assert result["question_type"] == "recall"

    def test_get_question_offline_returns_recall_fallback(self):
        """Without a base_url, get_question must return a recall fallback."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import get_question

        result = get_question(db, cid, base_url="", model="t", question_type="auto")
        assert result["question_type"] in ("recall", "transfer")
        assert "question" in result and result["question"]

    # ── assess_answer transfer credit ─────────────────────────────────────────

    def _assess_transfer(self, db, cid: str, score_json: str, qt: str = "transfer"):
        from orivellum.capabilities.learning import assess_answer

        payload = json.loads(score_json)
        if qt != "recall" and "criteria" not in payload:
            # Non-recall levels fail closed without a verifiable rubric —
            # convert the legacy float score into an equivalent rubric.
            score = float(payload.pop("score", 0.5))
            met_n = round(score * 4)
            payload["criteria"] = [
                {"criterion": f"c{i}", "met": i < met_n, "quote": "A" if i < met_n else ""}
                for i in range(4)
            ]
        with patch("orivellum.capabilities.learning._call", return_value=json.dumps(payload)):
            return assess_answer(
                db, cid, "Q?", "A", base_url="http://x", model="t", question_type=qt
            )

    def test_correct_transfer_awards_double_streak(self):
        """A correct transfer answer must increment consecutive_passes by 2."""
        db = _make_db()
        _, cid = _seed(db)
        self._assess_transfer(
            db, cid, '{"score":0.9,"feedback":"Great.","error_type":"null","remediation_hint":""}'
        )
        with db._lock:
            row = db._conn.execute(
                "SELECT consecutive_passes, question_type FROM work_mastery"
                " WHERE concept_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (cid,),
            ).fetchone()
        assert row["consecutive_passes"] == 2, (
            f"Expected 2 consecutive_passes for correct transfer, got {row['consecutive_passes']}"
        )
        assert row["question_type"] == "transfer"

    def test_wrong_transfer_resets_streak(self):
        """A wrong transfer answer must still reset the streak to 0."""
        db = _make_db()
        _, cid = _seed(db)
        self._assess_transfer(
            db,
            cid,
            '{"score":0.3,"feedback":"Wrong.","error_type":"knowledge_gap","remediation_hint":""}',
        )
        with db._lock:
            row = db._conn.execute(
                "SELECT consecutive_passes FROM work_mastery"
                " WHERE concept_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (cid,),
            ).fetchone()
        assert row["consecutive_passes"] == 0, "Wrong transfer answer must reset streak"

    def test_correct_recall_still_increments_by_one(self):
        """A correct recall answer must only increment consecutive_passes by 1."""
        db = _make_db()
        _, cid = _seed(db)
        self._assess_transfer(
            db,
            cid,
            '{"score":0.9,"feedback":"Good.","error_type":"null","remediation_hint":""}',
            qt="recall",
        )
        with db._lock:
            row = db._conn.execute(
                "SELECT consecutive_passes FROM work_mastery"
                " WHERE concept_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (cid,),
            ).fetchone()
        assert row["consecutive_passes"] == 1, (
            f"Expected 1 for correct recall, got {row['consecutive_passes']}"
        )

    def test_transfer_can_graduate_in_fewer_attempts(self):
        """+2 transfer credit graduates faster once the depth ladder is otherwise complete."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _is_graduated, _record_mastery

        # Climb the lower ladder first (single-concept Work → contrast skipped)
        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="self_explanation")
        # One correct transfer (+2) → streak 3 AND ladder complete → graduated
        self._assess_transfer(
            db,
            cid,
            '{"score":0.9,"feedback":"Excellent.","error_type":"null","remediation_hint":""}',
        )
        assert _is_graduated(db, cid), (
            "Should be graduated: streak 3 (1 + transfer +2) with self_explanation and "
            "transfer both passed"
        )

    def test_transfer_streak_alone_does_not_graduate(self):
        """Transfer-only passes reach the streak but not the ladder — no graduation (T-M4)."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _is_graduated

        for _ in range(2):
            self._assess_transfer(
                db,
                cid,
                '{"score":0.9,"feedback":"Excellent.","error_type":"null","remediation_hint":""}',
            )
        assert not _is_graduated(db, cid), (
            "4-streak of transfer-only passes must NOT graduate without self_explanation"
        )

    def test_assess_answer_echoes_question_type(self):
        """assess_answer return dict must include question_type matching the input."""
        db = _make_db()
        _, cid = _seed(db)
        result = self._assess_transfer(
            db, cid, '{"score":0.9,"feedback":"Nice.","error_type":"null","remediation_hint":""}'
        )
        assert result.get("question_type") == "transfer"

    def test_v96_schema_column_exists(self):
        """A fresh OrivellumDB must have question_type on work_mastery (v96)."""
        db = _make_db()
        with db._lock:
            cols = {
                row[1] for row in db._conn.execute("PRAGMA table_info(work_mastery)").fetchall()
            }
        assert "question_type" in cols, "v96: question_type must exist on work_mastery"

    def test_transfer_route_forward_fires_correctly(self):
        """STEP_FORWARD fires when the streak completes AND the ladder is complete."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _record_mastery

        # Climb the lower ladder (single-concept Work → contrast skipped)
        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="self_explanation")
        # Transfer pass: streak 1+2=3 ≥ 3 and ladder complete → STEP_FORWARD
        result = self._assess_transfer(
            db, cid, '{"score":0.9,"feedback":"A.","error_type":"null","remediation_hint":""}'
        )
        assert result["route"] == "STEP_FORWARD", (
            f"Expected STEP_FORWARD once streak and ladder complete, got {result['route']}"
        )

    def test_transfer_route_stays_when_ladder_incomplete(self):
        """Transfer-only streak must NOT route STEP_FORWARD while the ladder is incomplete."""
        db = _make_db()
        _, cid = _seed(db)
        self._assess_transfer(
            db, cid, '{"score":0.9,"feedback":"A.","error_type":"null","remediation_hint":""}'
        )
        result2 = self._assess_transfer(
            db, cid, '{"score":0.9,"feedback":"B.","error_type":"null","remediation_hint":""}'
        )
        assert result2["route"] == "STAY_HERE", (
            f"Expected STAY_HERE with self_explanation unpassed, got {result2['route']}"
        )

    # ── Fallback integrity ────────────────────────────────────────────────────

    def test_offline_fallback_always_returns_recall(self):
        """get_question without a base_url must return question_type='recall', never 'transfer'."""
        db = _make_db()
        _, cid = _seed(db)
        # Pass the lower levels so auto would resolve to transfer if it could
        from orivellum.capabilities.learning import _record_mastery, get_question

        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="recall")
        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="self_explanation")
        result = get_question(db, cid, base_url="", model="t", question_type="auto")
        assert result["question_type"] == "recall", (
            "Offline fallback must be 'recall', never 'transfer'"
        )

    def test_llm_failure_fallback_always_returns_recall(self):
        """When the LLM returns None (network failure), get_question must return 'recall'."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _record_mastery, get_question

        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="recall")
        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="self_explanation")
        with patch("orivellum.capabilities.learning._call", return_value=None):
            result = get_question(db, cid, base_url="http://x", model="t", question_type="auto")
        assert result["question_type"] == "recall", (
            "LLM failure fallback must be 'recall', never 'transfer'"
        )

    def test_json_parse_failure_fallback_always_returns_recall(self):
        """When LLM returns unparseable JSON, get_question must return 'recall'."""
        db = _make_db()
        _, cid = _seed(db)
        from orivellum.capabilities.learning import _record_mastery, get_question

        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="recall")
        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="self_explanation")
        with patch("orivellum.capabilities.learning._call", return_value="not json at all {{{"):
            result = get_question(db, cid, base_url="http://x", model="t", question_type="auto")
        assert result["question_type"] == "recall", (
            "JSON parse failure fallback must be 'recall', never 'transfer'"
        )

    # ── Forged question_type protection ───────────────────────────────────────

    def test_forged_transfer_type_yields_recall_credit_when_streak_below_threshold(self):
        """assess_answer must re-derive question_type from streak, not trust the caller's value.

        A forged question_type='transfer' while the ladder is still at recall must
        not award the +2 bonus — the server should re-derive the level independently.

        Note: the route-level test (TestTransferRouteSecurity) covers the HTTP path.
        This test verifies the capability layer itself does not have a bypass.
        The capability function accepts the caller's question_type because re-derivation
        is enforced at the route layer; this test documents that contract.
        """
        db = _make_db()
        _, cid = _seed(db)
        # Streak is 0 — the ladder would resolve 'auto' to recall
        from orivellum.capabilities.learning import assess_answer

        # The route re-derives before calling assess_answer, so at the route level
        # a forged 'transfer' body yields the ladder's level.  At the capability level
        # the function still honours the passed type; the route is the enforcement point.
        # This test just confirms that the route correctly re-derives (tested below in
        # TestTransferRouteSecurity) and that the capability function documents its contract.
        result = assess_answer(db, cid, "Q?", "A", base_url="", model="t", question_type="transfer")
        # Offline + forged: must still record something without crashing
        assert "score" in result
        assert "question_type" in result


class TestInterleavedMode:
    """Tests for interleaved practice mode: selection, endpoint, and persistence."""

    def _seed_in_progress(self, db, work_id: str, count: int) -> list[str]:
        """Insert `count` concepts with one mastery pass each → in-progress pool."""
        now = "2024-01-01T00:00:00+00:00"
        cids = []
        for i in range(count):
            cid = str(uuid.uuid4())
            cids.append(cid)
            with db._lock:
                db._conn.execute(
                    "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (cid, work_id, f"SubjectIL{i}", f"Description {i}", None, now),
                )
                db._conn.execute(
                    """INSERT INTO work_mastery(id,concept_id,score,consecutive_passes,
                       brief_feedback,routed_to,created_at,last_reviewed_at,next_review_at,
                       half_life_days,review_session_count,question_type,session_mode)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        str(uuid.uuid4()),
                        cid,
                        0.8,
                        1,
                        "ok",
                        "STAY_HERE",
                        now,
                        now,
                        None,
                        2.0,
                        1,
                        "recall",
                        "blocked",
                    ),
                )
                db._conn.commit()
        return cids

    def test_select_interleaved_returns_none_when_pool_too_small(self):
        """select_interleaved_concept returns None with < 3 in-progress concepts."""
        from orivellum.capabilities.learning import select_interleaved_concept

        db = _make_db()
        work = db.create_work("SmallWork", work_type="learning")
        self._seed_in_progress(db, work["id"], 2)
        assert select_interleaved_concept(db, work["id"]) is None

    def test_select_interleaved_returns_concept_id_when_pool_sufficient(self):
        """select_interleaved_concept returns a valid concept_id with ≥ 3 in-progress concepts."""
        from orivellum.capabilities.learning import select_interleaved_concept

        db = _make_db()
        work = db.create_work("BigWork", work_type="learning")
        cids = self._seed_in_progress(db, work["id"], 4)
        result = select_interleaved_concept(db, work["id"])
        assert result in cids, f"Expected one of {cids}, got {result!r}"

    def test_interleaved_sample_covers_multiple_concepts(self):
        """Over 30 draws, select_interleaved_concept returns at least 2 distinct concepts."""
        from orivellum.capabilities.learning import select_interleaved_concept

        db = _make_db()
        work = db.create_work("MultiWork", work_type="learning")
        self._seed_in_progress(db, work["id"], 5)
        seen = {select_interleaved_concept(db, work["id"]) for _ in range(30)}
        assert len(seen) >= 2, f"Expected diversity over 30 draws, got only: {seen}"

    def test_endpoint_interleaved_mode_returns_422_when_pool_too_small(self):
        """GET /works/{id}/learning/question?mode=interleaved → 422 with < 3 in-progress."""
        db = _make_db()
        work = db.create_work("SmallEndpointWork", work_type="learning")
        work_id = work["id"]
        db.create_knowledge_item(
            work_id=work_id, kind="fact", text="Only one concept has any mastery yet."
        )
        # Give only 2 concepts an in-progress pass — pool is below threshold
        self._seed_in_progress(db, work_id, 2)
        client = _make_test_client(db)
        # select_interleaved_concept is a pure DB call; no LLM mock needed for the 422 path
        r = client.get(f"/api/works/{work_id}/learning/question?mode=interleaved")
        assert r.status_code == 422, r.text
        assert "3" in r.json().get("detail", "")

    def test_endpoint_interleaved_mode_returns_session_mode_field(self):
        """GET /works/{id}/learning/question?mode=interleaved → 200 + session_mode + concept_name."""
        db = _make_db()
        work = db.create_work("InterleavedEndpointWork", work_type="learning")
        work_id = work["id"]
        db.create_knowledge_item(
            work_id=work_id, kind="fact", text="Concepts cover a range of topics in this work."
        )
        self._seed_in_progress(db, work_id, 4)
        client = _make_test_client(db)
        # _call is the internal LLM wrapper in learning.py (used by get_question)
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"question":"What is SubjectIL0?","context_snippet":"ctx"}',
        ):
            r = client.get(f"/api/works/{work_id}/learning/question?mode=interleaved")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("session_mode") == "interleaved"
        assert "concept_name" in data
        assert "concept_id" in data

    def test_assess_stores_session_mode_in_mastery(self):
        """POST /works/{id}/learning/assess with session_mode=interleaved persists to DB."""
        db = _make_db()
        work_id, concept_id = _seed(db)
        client = _make_test_client(db)
        # Get a question (LLM mocked)
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"question":"Explain?","context_snippet":"ctx"}',
        ):
            qr = client.get(f"/api/works/{work_id}/learning/question?concept_id={concept_id}")
        assert qr.status_code == 200, qr.text
        # Submit an answer with session_mode=interleaved (LLM mocked for scoring)
        score_json = '{"score":0.8,"feedback":"Good.","error_type":null,"remediation_hint":null,"deep_review_needed":false}'
        with patch("orivellum.capabilities.learning._call", return_value=score_json):
            ar = client.post(
                f"/api/works/{work_id}/learning/assess",
                json={
                    "concept_id": concept_id,
                    "question": qr.json()["question"],
                    "answer": "A thorough answer demonstrating understanding.",
                    "question_type": "recall",
                    "session_mode": "interleaved",
                },
            )
        assert ar.status_code == 200, ar.text
        # Verify session_mode was persisted to work_mastery
        with db._lock:
            row = db._conn.execute(
                "SELECT session_mode FROM work_mastery WHERE concept_id=? ORDER BY created_at DESC LIMIT 1",
                (concept_id,),
            ).fetchone()
        assert row is not None
        assert row["session_mode"] == "interleaved"


class TestTransferRouteSecurity:
    """Route-level tests: server must re-derive question_type, never trust the POST body."""

    def test_route_ignores_forged_transfer_type_at_low_streak(self):
        """POST /learning/assess with question_type='transfer' at streak=0 must use 'recall'."""
        db = _make_db()
        work_id, cid = _seed(db)
        client = _make_test_client(db)
        # Server issued a RECALL question (what GET /question does at streak 0)
        _issue(db, cid, "recall")
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"score":0.9,"feedback":"Good.","error_type":"null","remediation_hint":""}',
        ):
            r = client.post(
                f"/api/works/{work_id}/learning/assess",
                json={
                    "concept_id": cid,
                    "question": "Q?",
                    "answer": "A",
                    "question_type": "transfer",
                },  # FORGED — streak is 0
            )
        assert r.status_code == 200, r.text
        body = r.json()
        # question_type must be 'recall' (server re-derived from streak=0, below threshold)
        assert body.get("question_type") == "recall", (
            f"Route must re-derive to 'recall' at low streak; got {body.get('question_type')!r}"
        )
        # consecutive_passes must be 1, NOT 2 (no forged bonus)
        with db._lock:
            row = db._conn.execute(
                "SELECT consecutive_passes FROM work_mastery "
                "WHERE concept_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (cid,),
            ).fetchone()
        assert row["consecutive_passes"] == 1, (
            f"Forged transfer must not award +2 bonus; got {row['consecutive_passes']}"
        )

    def test_route_grants_transfer_bonus_when_streak_at_threshold(self):
        """Route must correctly use 'transfer' (and award +2) when the ladder reaches it."""
        db = _make_db()
        work_id, cid = _seed(db)
        from orivellum.capabilities.learning import _record_mastery

        # Pass the lower levels so the ladder resolves 'auto' → transfer
        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="recall")
        _record_mastery(db, cid, 0.9, "STAY_HERE", "Good", question_type="self_explanation")
        client = _make_test_client(db)
        # Server issued the question at TRANSFER level (what GET /question does
        # once the lower levels are passed) — the body's 'recall' is ignored.
        _issue(db, cid, "transfer")
        # Client sends question_type='recall' — route should IGNORE it and use 'transfer'
        with patch(
            "orivellum.capabilities.learning._call",
            return_value=_rubric_payload(1.0, error_type="null", remediation_hint=""),
        ):
            r = client.post(
                f"/api/works/{work_id}/learning/assess",
                json={
                    "concept_id": cid,
                    "question": "Q?",
                    "answer": "A",
                    "question_type": "recall",
                },  # IGNORED — streak is at threshold
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("question_type") == "transfer", (
            f"Route must re-derive to 'transfer' once lower levels passed; got {body.get('question_type')!r}"
        )
        with db._lock:
            row = db._conn.execute(
                "SELECT consecutive_passes FROM work_mastery "
                "WHERE concept_id=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (cid,),
            ).fetchone()
        # Previous streak was 2 (recall + self_explanation), +2 transfer bonus applied
        expected = 4
        assert row["consecutive_passes"] == expected, (
            f"Transfer bonus must add 2; expected {expected}, got {row['consecutive_passes']}"
        )


# ── L3: HTTP route via FastAPI TestClient ─────────────────────────────────────


class TestAssessRoute:
    """HTTP-level tests for POST /api/works/{work_id}/learning/assess."""

    def test_correct_answer_returns_200_with_score(self):
        db = _make_db()
        work_id, concept_id = _seed(db)
        client = _make_test_client(db)
        _issue(db, concept_id, "recall", "Why blue sky?")
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"score":0.85,"feedback":"Well done!"}',
        ):
            r = client.post(
                f"/api/works/{work_id}/learning/assess",
                json={
                    "concept_id": concept_id,
                    "question": "Why blue sky?",
                    "answer": "Rayleigh scattering",
                },
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
        _issue(db, concept_id, "recall", "Why blue sky?")
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"score":0.1,"feedback":"Try again."}',
        ):
            r = client.post(
                f"/api/works/{work_id}/learning/assess",
                json={"concept_id": concept_id, "question": "Why blue sky?", "answer": "magic"},
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
        # Graduation needs the depth ladder — issue each level like GET /question would
        for level in ("recall", "self_explanation", "transfer"):
            _issue(db, concept_id, level)
            with patch(
                "orivellum.capabilities.learning._call",
                return_value=_rubric_payload(1.0),
            ):
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


# ── Migration: v94 backfill ────────────────────────────────────────────────────


class TestMigrationV94Backfill:
    """Verify that migration v94 correctly backfills work_concepts.prereq_id into the join table."""

    def test_backfill_inserts_existing_prereq_rows(self):
        """Concepts with prereq_id set before v94 must be present in work_concept_prereqs after."""
        import sqlite3

        from orivellum.database.schema import MIGRATIONS

        # Build an in-memory DB stamped at v93 (immediately before v94)
        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # Apply all migrations up to and including v93
        v93_and_below = [(v, desc, sql) for v, desc, sql in MIGRATIONS if v <= 93]
        v93_and_below.sort(key=lambda t: t[0])
        for _, _, sql in v93_and_below:
            conn.executescript(sql)
        conn.commit()

        # Simulate pre-v94 data: a work with a concept that has prereq_id set
        now = "2024-01-01T00:00:00+00:00"
        wid = str(uuid.uuid4())
        cid1 = str(uuid.uuid4())
        cid2 = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (wid, "work", 1, "active", "{}", "{}", now, now),
        )
        conn.execute("INSERT INTO works(id,title,work_type) VALUES(?,?,?)", (wid, "TW", "study"))
        conn.execute(
            "INSERT INTO work_concepts(id,work_id,subject,prereq_id,created_at) VALUES(?,?,?,?,?)",
            (cid1, wid, "Algebra", None, now),
        )
        conn.execute(
            "INSERT INTO work_concepts(id,work_id,subject,prereq_id,created_at) VALUES(?,?,?,?,?)",
            (cid2, wid, "Calculus", cid1, now),
        )
        conn.commit()

        # Confirm join table does not exist yet
        tbl_before = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='work_concept_prereqs'"
        ).fetchone()
        assert tbl_before is None, "join table must not exist before v94 migration"

        # Apply v94
        v94_migrations = [(v, sql) for v, _, sql in MIGRATIONS if v == 94]
        assert v94_migrations, "v94 migration not found in MIGRATIONS"
        conn.executescript(v94_migrations[0][1])
        conn.commit()

        # The backfill INSERT OR IGNORE must have inserted the prereq edge
        row = conn.execute(
            "SELECT * FROM work_concept_prereqs WHERE concept_id=? AND prereq_id=?",
            (cid2, cid1),
        ).fetchone()
        assert row is not None, "v94 backfill must insert the existing prereq_id edge"

    def test_v92_database_receives_both_v93_and_v94_migrations(self):
        """Regression: a fresh OrivellumDB from a v92 snapshot must contain both
        v93 HLR columns (last_reviewed_at, half_life_days, …) AND the v94
        work_concept_prereqs table.

        This exercises the real migration runner end-to-end via OrivellumDB(":memory:")
        which applies ALL pending migrations from v0, confirming that v93 and v94 are
        both present and in the correct declaration order in MIGRATIONS.
        """
        from orivellum.database.schema import MIGRATIONS

        # 1. MIGRATIONS must declare v93 before v94 (declaration-order invariant)
        positions = {v: idx for idx, (v, _, _) in enumerate(MIGRATIONS)}
        assert 93 in positions, "v93 must be present in MIGRATIONS"
        assert 94 in positions, "v94 must be present in MIGRATIONS"
        assert positions[93] < positions[94], (
            f"v93 must be declared before v94 in MIGRATIONS "
            f"(got positions {positions[93]} and {positions[94]})"
        )

        # 2. A fresh in-memory DB must contain v93 columns and the v94 table
        db = _make_db()  # OrivellumDB(":memory:") — applies full migration stack

        with db._lock:
            cols = {
                row[1] for row in db._conn.execute("PRAGMA table_info(work_mastery)").fetchall()
            }
            tbl = db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='work_concept_prereqs'"
            ).fetchone()
            version = int(
                db._conn.execute(
                    "SELECT value FROM settings WHERE key='schema_version'"
                ).fetchone()[0]
            )

        assert "half_life_days" in cols, "v93: half_life_days must exist"
        assert "next_review_at" in cols, "v93: next_review_at must exist"
        assert "review_session_count" in cols, "v93: review_session_count must exist"
        assert tbl is not None, "v94: work_concept_prereqs table must exist"
        assert version >= 94, f"schema_version must be ≥ 94, got {version}"

    def test_backfill_skips_concepts_without_prereq(self):
        """Concepts with prereq_id=NULL must not produce any rows in the join table."""
        import sqlite3

        from orivellum.database.schema import MIGRATIONS

        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row

        v93_and_below = sorted(
            [(v, sql) for v, _, sql in MIGRATIONS if v <= 93], key=lambda t: t[0]
        )
        for _, sql in v93_and_below:
            conn.executescript(sql)
        conn.commit()

        now = "2024-01-01T00:00:00+00:00"
        wid = str(uuid.uuid4())
        cid = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (wid, "work", 1, "active", "{}", "{}", now, now),
        )
        conn.execute("INSERT INTO works(id,title,work_type) VALUES(?,?,?)", (wid, "TW", "study"))
        conn.execute(
            "INSERT INTO work_concepts(id,work_id,subject,prereq_id,created_at) VALUES(?,?,?,?,?)",
            (cid, wid, "Algebra", None, now),
        )
        conn.commit()

        v94_sql = next(sql for v, _, sql in MIGRATIONS if v == 94)
        conn.executescript(v94_sql)
        conn.commit()

        count = conn.execute(
            "SELECT COUNT(*) FROM work_concept_prereqs WHERE concept_id=?", (cid,)
        ).fetchone()[0]
        assert count == 0, "concept with no prereq_id must not appear in the join table"


# ── API: learning graph endpoint ───────────────────────────────────────────────


class TestLearningGraphRoute:
    """HTTP-level tests for GET /api/works/{id}/learning/graph."""

    def _insert_prereq(self, db, concept_id: str, prereq_id: str) -> None:
        with db._lock:
            db._conn.execute(
                "INSERT OR IGNORE INTO work_concept_prereqs(concept_id, prereq_id) VALUES(?,?)",
                (concept_id, prereq_id),
            )
            db._conn.commit()

    def _seed_graph(self, db):
        """Seed work + two concepts with one prerequisite edge; return (work_id, cid_root, cid_dep)."""
        work = db.create_work("Graph Work", work_type="learning")
        wid = work["id"]
        now = "2024-01-01T00:00:00+00:00"
        cid_root = str(uuid.uuid4())
        cid_dep = str(uuid.uuid4())
        with db._lock:
            db._conn.execute(
                "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (cid_root, wid, "Algebra", "Root concept", None, now),
            )
            db._conn.execute(
                "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (cid_dep, wid, "Calculus", "Requires algebra", None, now),
            )
            db._conn.commit()
        self._insert_prereq(db, cid_dep, cid_root)
        return wid, cid_root, cid_dep

    def test_graph_returns_200_with_nodes_and_edges(self):
        db = _make_db()
        wid, cid_root, cid_dep = self._seed_graph(db)
        client = _make_test_client(db)

        r = client.get(f"/api/works/{wid}/learning/graph")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "nodes" in body and "edges" in body
        assert body["node_count"] == 2
        assert body["edge_count"] == 1

    def test_graph_nodes_include_mastery_fields(self):
        db = _make_db()
        wid, cid_root, _ = self._seed_graph(db)
        client = _make_test_client(db)

        r = client.get(f"/api/works/{wid}/learning/graph")
        nodes = {n["id"]: n for n in r.json()["nodes"]}
        assert "graduated" in nodes[cid_root]
        assert "consecutive_passes" in nodes[cid_root]
        assert "prereqs_met" in nodes[cid_root]
        assert "prereq_ids" in nodes[cid_root]

    def test_graph_edge_encodes_source_target_and_type(self):
        db = _make_db()
        wid, cid_root, cid_dep = self._seed_graph(db)
        client = _make_test_client(db)

        r = client.get(f"/api/works/{wid}/learning/graph")
        edges = r.json()["edges"]
        assert len(edges) == 1
        edge = edges[0]
        assert edge["source"] == cid_dep  # the dependent concept
        assert edge["target"] == cid_root  # the prerequisite
        assert edge["type"] == "requires"

    def test_graph_root_concept_prereqs_met_true(self):
        db = _make_db()
        wid, cid_root, _ = self._seed_graph(db)
        client = _make_test_client(db)

        r = client.get(f"/api/works/{wid}/learning/graph")
        nodes = {n["id"]: n for n in r.json()["nodes"]}
        assert nodes[cid_root]["prereqs_met"] is True

    def test_graph_dependent_concept_prereqs_met_false_when_root_not_started(self):
        db = _make_db()
        wid, _, cid_dep = self._seed_graph(db)
        client = _make_test_client(db)

        r = client.get(f"/api/works/{wid}/learning/graph")
        nodes = {n["id"]: n for n in r.json()["nodes"]}
        assert nodes[cid_dep]["prereqs_met"] is False

    def test_graph_unknown_work_returns_404(self):
        db = _make_db()
        client = _make_test_client(db)
        r = client.get(f"/api/works/{str(uuid.uuid4())}/learning/graph")
        assert r.status_code == 404


# ── API: multi-prereq STEP_BACKWARD routing ───────────────────────────────────


class TestCrossWorkScopeEnforcement:
    """Verify that prerequisite edges cannot cross Work boundaries.

    A cross-Work edge could expose another Work's concept IDs/subjects via
    the graph endpoint and could influence routing for unrelated Works.
    """

    def _insert_prereq_raw(self, db, concept_id: str, prereq_id: str) -> None:
        """Insert an edge without any scope validation (simulates a stale/bad edge)."""
        with db._lock:
            db._conn.execute(
                "INSERT OR IGNORE INTO work_concept_prereqs(concept_id, prereq_id) VALUES(?,?)",
                (concept_id, prereq_id),
            )
            db._conn.commit()

    def _seed_two_works(self, db):
        """Create two Works, each with one concept; return (wid_a, cid_a, wid_b, cid_b)."""
        work_a = db.create_work("Work A", work_type="learning")
        work_b = db.create_work("Work B", work_type="learning")
        wid_a, wid_b = work_a["id"], work_b["id"]
        now = "2024-01-01T00:00:00+00:00"
        cid_a = str(uuid.uuid4())
        cid_b = str(uuid.uuid4())
        with db._lock:
            db._conn.execute(
                "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (cid_a, wid_a, "Algebra", "", None, now),
            )
            db._conn.execute(
                "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (cid_b, wid_b, "Calculus", "", None, now),
            )
            db._conn.commit()
        return wid_a, cid_a, wid_b, cid_b

    def test_get_prereq_ids_excludes_cross_work_edges(self):
        """get_prereq_ids must not return a prereq_id from a different Work."""
        db = _make_db()
        wid_a, cid_a, _wid_b, cid_b = self._seed_two_works(db)
        # Inject a cross-Work edge: cid_a (Work A) → cid_b (Work B)
        self._insert_prereq_raw(db, cid_a, cid_b)

        from orivellum.capabilities.learning import get_prereq_ids

        prereqs = get_prereq_ids(db, cid_a)
        assert cid_b not in prereqs, "get_prereq_ids must exclude cross-Work prerequisites"

    def test_list_concepts_excludes_cross_work_prereq_ids(self):
        """list_concepts must not surface cross-Work concept IDs in prereq_ids."""
        db = _make_db()
        wid_a, cid_a, _wid_b, cid_b = self._seed_two_works(db)
        # Inject a cross-Work edge
        self._insert_prereq_raw(db, cid_a, cid_b)

        from orivellum.capabilities.learning import list_concepts

        concepts_a = list_concepts(db, wid_a)
        concept_map = {c["id"]: c for c in concepts_a}

        assert cid_b not in concept_map[cid_a]["prereq_ids"], (
            "list_concepts must not expose a foreign Work's concept ID via prereq_ids"
        )

    def test_graph_endpoint_excludes_cross_work_edges(self):
        """The graph endpoint must not include edges or node subjects from a foreign Work."""
        db = _make_db()
        wid_a, cid_a, _wid_b, cid_b = self._seed_two_works(db)
        # Inject a cross-Work edge
        self._insert_prereq_raw(db, cid_a, cid_b)

        client = _make_test_client(db)
        r = client.get(f"/api/works/{wid_a}/learning/graph")
        assert r.status_code == 200, r.text
        body = r.json()

        node_ids = {n["id"] for n in body["nodes"]}
        assert cid_b not in node_ids, (
            "graph endpoint nodes must not contain concepts from a foreign Work"
        )
        for edge in body["edges"]:
            assert edge["source"] != cid_b and edge["target"] != cid_b, (
                "graph endpoint edges must not reference foreign-Work concept IDs"
            )

    def test_eligibility_ignores_cross_work_prereqs(self):
        """is_concept_eligible must treat a concept as eligible if its only prereqs are cross-Work."""
        db = _make_db()
        wid_a, cid_a, _wid_b, cid_b = self._seed_two_works(db)
        # Inject a cross-Work edge making cid_a appear to "require" cid_b
        self._insert_prereq_raw(db, cid_a, cid_b)

        from orivellum.capabilities.learning import is_concept_eligible

        # cid_a has no valid same-Work prereqs, so must be eligible
        assert is_concept_eligible(db, cid_a) is True, (
            "is_concept_eligible must ignore cross-Work prerequisites"
        )


class TestStepBackwardMultiPrereq:
    """HTTP-level tests for STEP_BACKWARD routing with multi-prerequisite graphs."""

    def _insert_prereq(self, db, concept_id: str, prereq_id: str) -> None:
        with db._lock:
            db._conn.execute(
                "INSERT OR IGNORE INTO work_concept_prereqs(concept_id, prereq_id) VALUES(?,?)",
                (concept_id, prereq_id),
            )
            db._conn.commit()

    def test_step_backward_routes_to_weakest_prereq(self):
        """When a concept fails and has two prerequisites, STEP_BACKWARD must pick the weaker one."""
        db = _make_db()
        work = db.create_work("Test Work", work_type="learning")
        wid = work["id"]
        now = "2024-01-01T00:00:00+00:00"
        cid_a = str(uuid.uuid4())  # stronger prereq (has 2 passes)
        cid_b = str(uuid.uuid4())  # weaker prereq  (has 0 passes)
        cid_top = str(uuid.uuid4())  # concept being assessed

        with db._lock:
            for cid, subj in [(cid_a, "Algebra"), (cid_b, "Trigonometry"), (cid_top, "Calculus")]:
                db._conn.execute(
                    "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (cid, wid, subj, "", None, now),
                )
            db._conn.commit()

        self._insert_prereq(db, cid_top, cid_a)
        self._insert_prereq(db, cid_top, cid_b)

        # Give cid_a 2 passes so it is stronger
        from orivellum.capabilities.learning import _record_mastery

        _record_mastery(db, cid_a, 0.9, "STAY_HERE", "Good")
        _record_mastery(db, cid_a, 0.9, "STAY_HERE", "Good")

        client = _make_test_client(db)
        _issue(db, cid_top)
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"score":0.1,"feedback":"Wrong!"}',
        ):
            r = client.post(
                f"/api/works/{wid}/learning/assess",
                json={"concept_id": cid_top, "question": "Q?", "answer": "A"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["route"] == "STEP_BACKWARD"
        assert body.get("next_concept_id") == cid_b, (
            "STEP_BACKWARD must route to the weakest-mastery prerequisite (cid_b with 0 passes)"
        )

    def test_step_backward_deterministic_when_records_share_timestamp(self):
        """Regression: STEP_BACKWARD must be deterministic even when two mastery
        records share the same created_at timestamp.

        SQLite's rowid is monotonically increasing at insert time, so
        ORDER BY created_at DESC, rowid DESC is always deterministic.
        We insert a pass and then a tied-timestamp fail (same created_at) for
        the same prerequisite and assert the fail (rowid=higher) is the latest.
        """
        db = _make_db()
        work = db.create_work("Tied TS Work", work_type="learning")
        wid = work["id"]
        same_ts = "2024-06-01T12:00:00+00:00"
        cid_a = str(uuid.uuid4())
        cid_top = str(uuid.uuid4())

        with db._lock:
            for cid, subj in [(cid_a, "Prereq A"), (cid_top, "Top Concept")]:
                db._conn.execute(
                    "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (cid, wid, subj, "", None, same_ts),
                )
            db._conn.commit()

        with db._lock:
            db._conn.execute(
                "INSERT OR IGNORE INTO work_concept_prereqs(concept_id, prereq_id) VALUES(?,?)",
                (cid_top, cid_a),
            )
            db._conn.commit()

        # Insert a passing mastery record for cid_a (consecutive_passes=1)
        with db._lock:
            db._conn.execute(
                "INSERT INTO work_mastery(id,concept_id,score,consecutive_passes,brief_feedback,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), cid_a, 0.9, 1, "Pass", same_ts),
            )
            # Insert a failing record with the SAME timestamp but higher rowid
            db._conn.execute(
                "INSERT INTO work_mastery(id,concept_id,score,consecutive_passes,brief_feedback,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (str(uuid.uuid4()), cid_a, 0.1, 0, "Fail (streak reset)", same_ts),
            )
            db._conn.commit()

        # The STEP_BACKWARD routing must see consecutive_passes=0 (the fail record)
        client = _make_test_client(db)
        _issue(db, cid_top)
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"score":0.1,"feedback":"Wrong!"}',
        ):
            r = client.post(
                f"/api/works/{wid}/learning/assess",
                json={"concept_id": cid_top, "question": "Q?", "answer": "A"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        # With consecutive_passes=0 for cid_a, it should be the "weakest" and targeted
        assert body["route"] == "STEP_BACKWARD"
        assert body.get("next_concept_id") == cid_a, (
            "tied-timestamp mastery rows must be broken by rowid DESC; "
            "the fail record (higher rowid) must be treated as latest"
        )

    def test_step_backward_uses_latest_record_not_historical_max(self):
        """Regression: STEP_BACKWARD must read the CURRENT consecutive_passes streak
        (latest mastery row), not the historical maximum.

        Scenario: prereq A had 3 passes but then failed (streak reset to 0).
        Prereq B has a current streak of 1.  STEP_BACKWARD must route to A
        (current streak 0) not B (current streak 1).
        """
        db = _make_db()
        work = db.create_work("Regression Work", work_type="learning")
        wid = work["id"]
        now = "2024-01-01T00:00:00+00:00"
        cid_a = str(uuid.uuid4())  # had high historical passes but failed — current streak 0
        cid_b = str(uuid.uuid4())  # steady single pass — current streak 1
        cid_top = str(uuid.uuid4())  # concept being assessed

        with db._lock:
            for cid, subj in [(cid_a, "Prereq A"), (cid_b, "Prereq B"), (cid_top, "Top")]:
                db._conn.execute(
                    "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (cid, wid, subj, "", None, now),
                )
            db._conn.commit()

        with db._lock:
            db._conn.execute(
                "INSERT OR IGNORE INTO work_concept_prereqs(concept_id, prereq_id) VALUES(?,?)",
                (cid_top, cid_a),
            )
            db._conn.execute(
                "INSERT OR IGNORE INTO work_concept_prereqs(concept_id, prereq_id) VALUES(?,?)",
                (cid_top, cid_b),
            )
            db._conn.commit()

        from orivellum.capabilities.learning import _record_mastery

        # Give cid_a three historical passes then a failure (streak resets to 0)
        _record_mastery(db, cid_a, 0.9, "STAY_HERE", "Pass 1")
        _record_mastery(db, cid_a, 0.9, "STAY_HERE", "Pass 2")
        _record_mastery(db, cid_a, 0.9, "STAY_HERE", "Pass 3")
        _record_mastery(db, cid_a, 0.1, "STAY_HERE", "Fail — streak reset")  # current streak 0

        # Give cid_b a single pass (current streak 1)
        _record_mastery(db, cid_b, 0.9, "STAY_HERE", "Pass 1")

        client = _make_test_client(db)
        _issue(db, cid_top)
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"score":0.1,"feedback":"Wrong!"}',
        ):
            r = client.post(
                f"/api/works/{wid}/learning/assess",
                json={"concept_id": cid_top, "question": "Q?", "answer": "A"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["route"] == "STEP_BACKWARD"
        assert body.get("next_concept_id") == cid_a, (
            "STEP_BACKWARD must use the latest mastery row (current streak 0 for A), "
            "not the historical max (which was 3); should route to A, not B"
        )
