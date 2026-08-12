"""
Tests: depth ladder, teach-back, and the reverse research loop (task T-M4/5/6).

Required behaviors under test:
  1. Graduation is blocked on a recall-only streak.
  2. No hint / remediation / source material is available before the learner's
     attempt is recorded.
  3. Teach-back grading quotes each satisfied criterion extractively, plays the
     naive student, and can fail a graduated concept.
  4. Repeated failure with a thin corpus schedules a research request; the
     other two diagnoses (never_learned / learned_and_decayed) do not.

All tests use the real OrivellumDB with in-memory SQLite.  The LLM gateway is
patched at the module boundary so tests never make real network calls.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import patch

import pytest

from tests.conftest import AUTH_HEADERS

DUMMY_URL = "http://localhost:11434/v1"  # truthy; _call is always patched


def _make_db():
    from orivellum.database import OrivellumDB

    return OrivellumDB(":memory:")


def _seed(db, *, knowledge_texts: list[str] | None = None, subjects: list[str] | None = None):
    """Create a Work with knowledge items and concepts; return (work_id, [concept_ids])."""
    work = db.create_work("Ladder Work", work_type="learning")
    work_id = work["id"]
    for text in (
        knowledge_texts
        if knowledge_texts is not None
        else [
            "Rayleigh scattering: shorter wavelengths scatter far more strongly.",
            "Rayleigh scattering intensity varies with the inverse fourth power of wavelength.",
            "Rayleigh scattering fades for particles comparable to the wavelength (Mie regime).",
        ]
    ):
        db.create_knowledge_item(work_id=work_id, kind="fact", text=text, confidence=1.0)
    now = "2024-01-01T00:00:00+00:00"
    cids = []
    for i, subject in enumerate(subjects or ["Rayleigh scattering"]):
        cid = str(uuid.uuid4())
        cids.append(cid)
        with db._lock:
            db._conn.execute(
                "INSERT INTO work_concepts(id,work_id,subject,description,prereq_id,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (cid, work_id, subject, f"desc {i}", None, now),
            )
            db._conn.commit()
    return work_id, cids


def _make_test_client(db):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from orivellum.api import _deps
    from orivellum.api.routes import learning
    from orivellum.configuration.config import OrivellumConfig, ServingConfig

    cfg = OrivellumConfig()
    cfg.serving = ServingConfig(base_url=DUMMY_URL, workhorse_model="test-model")
    _deps.init(db=db, cfg=cfg)
    app = FastAPI()
    app.include_router(learning.router)
    return TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)


# ── 1. Graduation blocked on recall-only ──────────────────────────────────────


class TestGraduationRequiresLadder:
    def test_recall_only_streak_is_blocked(self):
        from orivellum.capabilities.learning import _is_graduated, _record_mastery

        db = _make_db()
        _, (cid,) = _seed(db)
        for _ in range(5):
            _record_mastery(db, cid, 0.95, "STAY_HERE", "", question_type="recall")
        assert not _is_graduated(db, cid), "recall-only streak must never graduate (T-M4)"

    def test_full_ladder_graduates_two_concept_work(self):
        """With a neighbour present, contrast is part of the required ladder."""
        from orivellum.capabilities.learning import _is_graduated, _record_mastery

        db = _make_db()
        _, (c1, _c2) = _seed(db, subjects=["Rayleigh scattering", "Mie scattering"])
        for qt in ("recall", "self_explanation", "transfer"):
            _record_mastery(db, c1, 0.9, "STAY_HERE", "", question_type=qt)
        assert not _is_graduated(db, c1), "contrast is required when a neighbour exists"
        _record_mastery(db, c1, 0.9, "STAY_HERE", "", question_type="contrast")
        assert _is_graduated(db, c1)

    def test_auto_ladder_resolution_order(self):
        from orivellum.capabilities.learning import _record_mastery, _resolve_question_type

        db = _make_db()
        _, (c1, _c2) = _seed(db, subjects=["Rayleigh scattering", "Mie scattering"])
        assert _resolve_question_type(db, c1, "auto") == "recall"
        _record_mastery(db, c1, 0.9, "STAY_HERE", "", question_type="recall")
        assert _resolve_question_type(db, c1, "auto") == "self_explanation"
        _record_mastery(db, c1, 0.9, "STAY_HERE", "", question_type="self_explanation")
        assert _resolve_question_type(db, c1, "auto") == "contrast"
        _record_mastery(db, c1, 0.9, "STAY_HERE", "", question_type="contrast")
        assert _resolve_question_type(db, c1, "auto") == "transfer"

    def test_contrast_neighbour_from_prereq_graph(self):
        """Siblings sharing a prerequisite are preferred as the contrast neighbour."""
        from orivellum.capabilities.learning import _contrast_neighbour

        db = _make_db()
        _, (root, a, b) = _seed(db, subjects=["Optics", "Rayleigh", "Mie"])
        with db._lock:
            for child in (a, b):
                db._conn.execute(
                    "INSERT INTO work_concept_prereqs(concept_id, prereq_id) VALUES(?,?)",
                    (child, root),
                )
            db._conn.commit()
        n = _contrast_neighbour(db, a)
        assert n is not None and n["id"] == b, "sibling sharing a prereq must be preferred"


# ── 2. No hint before the attempt ─────────────────────────────────────────────


class TestNoHintBeforeAttempt:
    _HINT_KEYS = ("remediation_hint", "socratic_followup", "feedback", "rubric")

    def test_question_response_contains_no_hint_material(self):
        from orivellum.capabilities.learning import get_question

        db = _make_db()
        _, (c1, _c2) = _seed(db, subjects=["Rayleigh scattering", "Mie scattering"])
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"question":"Q?","context_snippet":"ctx"}',
        ):
            for level in ("recall", "self_explanation", "contrast", "transfer"):
                q = get_question(db, c1, base_url=DUMMY_URL, model="t", question_type=level)
                for key in self._HINT_KEYS:
                    assert key not in q, f"{level} question leaked pre-attempt hint {key!r}"

    def test_self_explanation_withholds_source_snippet(self):
        """The source excerpt is withheld so the explanation is self-generated."""
        from orivellum.capabilities.learning import get_question

        db = _make_db()
        _, (cid,) = _seed(db)
        q = get_question(db, cid, base_url=DUMMY_URL, model="t", question_type="self_explanation")
        assert q["question_type"] == "self_explanation"
        assert q["context_snippet"] == "", "self_explanation must not show source material"

    def test_attempt_recorded_before_remediation_returned(self):
        """The mastery row exists even when assess returns remediation with it."""
        from orivellum.capabilities.learning import assess_answer

        db = _make_db()
        _, (cid,) = _seed(db)
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"score":0.2,"feedback":"Wrong.","error_type":"knowledge_gap",'
            '"remediation_hint":"Review the notes."}',
        ):
            result = assess_answer(db, cid, "Q?", "wrong answer", base_url=DUMMY_URL, model="t")
        assert result["remediation_hint"], "remediation should be returned after the attempt"
        with db._lock:
            count = db._conn.execute(
                "SELECT COUNT(*) FROM work_mastery WHERE concept_id=?", (cid,)
            ).fetchone()[0]
        assert count == 1, "the attempt must be recorded before remediation is surfaced"


# ── Rubric enforcement ────────────────────────────────────────────────────────


class TestRubricEnforcement:
    def test_unverifiable_quote_demotes_criterion(self):
        """met=true without a real extractive quote must not count — score computed in code."""
        from orivellum.capabilities.learning import assess_answer

        answer = "Shorter wavelengths scatter more strongly."
        critic = json.dumps(
            {
                "criteria": [
                    {
                        "criterion": "states wavelength dependence",
                        "met": True,
                        "quote": "Shorter wavelengths scatter more strongly",
                    },
                    {
                        "criterion": "mentions inverse fourth power",
                        "met": True,
                        "quote": "inverse fourth power",  # NOT in the answer
                    },
                    {
                        "criterion": "names a real-world consequence",
                        "met": False,
                        "quote": "",
                    },
                ],
                "feedback": "ok",
                "error_type": "null",
                "remediation_hint": "",
            }
        )
        db = _make_db()
        _, (cid,) = _seed(db)
        with patch("orivellum.capabilities.learning._call", return_value=critic):
            result = assess_answer(db, cid, "Q?", answer, base_url=DUMMY_URL, model="t")
        assert result["score"] == pytest.approx(1 / 3), "score must be met/total computed in code"
        met = [c for c in result["rubric"] if c["met"]]
        assert len(met) == 1
        assert met[0]["quote"].lower() in answer.lower(), "met criterion must carry a real quote"

    def test_rubric_stored_on_mastery_row(self):
        from orivellum.capabilities.learning import assess_answer

        critic = json.dumps(
            {
                "criteria": [
                    {"criterion": "c1", "met": True, "quote": "my answer"},
                    {"criterion": "c2", "met": False, "quote": ""},
                    {"criterion": "c3", "met": False, "quote": ""},
                ],
                "feedback": "ok",
            }
        )
        db = _make_db()
        _, (cid,) = _seed(db)
        with patch("orivellum.capabilities.learning._call", return_value=critic):
            assess_answer(db, cid, "Q?", "my answer", base_url=DUMMY_URL, model="t")
        with db._lock:
            row = db._conn.execute(
                "SELECT rubric_json FROM work_mastery WHERE concept_id=?", (cid,)
            ).fetchone()
        stored = json.loads(row["rubric_json"])
        assert stored[0]["criterion"] == "c1" and stored[0]["met"] is True


# ── 3. Teach-back ─────────────────────────────────────────────────────────────


class TestTeachBack:
    def _graduate(self, db, cid):
        from orivellum.capabilities.learning import _is_graduated, _record_mastery

        for qt in ("recall", "self_explanation", "transfer"):
            _record_mastery(db, cid, 0.9, "STAY_HERE", "", question_type=qt)
        assert _is_graduated(db, cid)

    def test_prompt_contains_no_source_material(self):
        from orivellum.capabilities.learning import get_teach_back

        db = _make_db()
        _, (cid,) = _seed(db)
        tb = get_teach_back(db, cid)
        assert tb["level"] == "teach_back"
        assert "Rayleigh scattering" in tb["prompt"]  # the subject itself
        assert "inverse fourth power" not in tb["prompt"], "prompt must not leak source facts"

    def test_pass_quotes_every_satisfied_criterion(self):
        from orivellum.capabilities.learning import assess_teach_back

        explanation = (
            "Rayleigh scattering means tiny particles scatter short wavelengths the most. "
            "That is why the sky looks blue. For example sunsets look red because blue "
            "light was scattered away along the long path."
        )
        critic = json.dumps(
            {
                "criteria": [
                    {
                        "criterion": "explains wavelength dependence",
                        "met": True,
                        "quote": "scatter short wavelengths the most",
                    },
                    {
                        "criterion": "gives a concrete example",
                        "met": True,
                        "quote": "sunsets look red",
                    },
                    {
                        "criterion": "states the everyday consequence",
                        "met": True,
                        "quote": "the sky looks blue",
                    },
                ],
                "student_followup": "But why do SHORT wavelengths scatter more?",
                "feedback": "Clear teaching.",
            }
        )
        db = _make_db()
        _, (cid,) = _seed(db)
        with patch("orivellum.capabilities.learning._call", return_value=critic):
            result = assess_teach_back(db, cid, explanation, DUMMY_URL, "t")
        assert result["passed"] is True
        assert result["student_followup"], "system must play the naive student"
        for c in result["rubric"]:
            if c["met"]:
                assert c["quote"] and c["quote"].lower() in explanation.lower(), (
                    "every satisfied criterion must carry an extractive quote"
                )

    def test_failed_teach_back_ungraduates_concept(self):
        from orivellum.capabilities.learning import _is_graduated, assess_teach_back

        db = _make_db()
        _, (cid,) = _seed(db)
        self._graduate(db, cid)
        critic = json.dumps(
            {
                "criteria": [
                    {"criterion": "explains the mechanism", "met": False, "quote": ""},
                    {"criterion": "gives an example", "met": False, "quote": ""},
                    {"criterion": "says why it matters", "met": False, "quote": ""},
                ],
                "student_followup": "So... what actually happens to the light?",
                "feedback": "Too vague to teach from.",
            }
        )
        with patch("orivellum.capabilities.learning._call", return_value=critic):
            result = assess_teach_back(db, cid, "It is about light stuff.", DUMMY_URL, "t")
        assert result["passed"] is False
        assert result["graduated"] is False, "a graduated concept CAN fail a teach-back (T-M5)"
        assert not _is_graduated(db, cid)

    def test_http_endpoints(self):
        db = _make_db()
        work_id, (cid,) = _seed(db)
        self._graduate(db, cid)  # teach-back is gated to graduated concepts
        client = _make_test_client(db)
        r = client.get(f"/api/works/{work_id}/learning/teach-back", params={"concept_id": cid})
        assert r.status_code == 200 and r.json()["level"] == "teach_back"
        critic = json.dumps(
            {
                "criteria": [
                    {"criterion": "c1", "met": True, "quote": "light scatters"},
                    {"criterion": "c2", "met": True, "quote": "a lot"},
                    {"criterion": "c3", "met": True, "quote": "light"},
                ],
                "student_followup": "why?",
                "feedback": "ok",
            }
        )
        with patch("orivellum.capabilities.learning._call", return_value=critic):
            r2 = client.post(
                f"/api/works/{work_id}/learning/teach-back/assess",
                json={"concept_id": cid, "explanation": "light scatters a lot"},
            )
        assert r2.status_code == 200
        body = r2.json()
        assert body["passed"] is True and "summary" in body

    def test_ungraduated_concept_cannot_teach_back(self):
        """Teach-back is the retention check for graduated concepts only."""
        db = _make_db()
        work_id, (cid,) = _seed(db)
        client = _make_test_client(db)
        client.get(f"/api/works/{work_id}/learning/teach-back", params={"concept_id": cid})
        r = client.post(
            f"/api/works/{work_id}/learning/teach-back/assess",
            json={"concept_id": cid, "explanation": "light scatters a lot"},
        )
        assert r.status_code == 409

    def test_teach_back_assess_requires_issued_prompt(self):
        db = _make_db()
        work_id, (cid,) = _seed(db)
        self._graduate(db, cid)
        client = _make_test_client(db)
        # No GET first → no issued prompt → rejected
        r = client.post(
            f"/api/works/{work_id}/learning/teach-back/assess",
            json={"concept_id": cid, "explanation": "light scatters a lot"},
        )
        assert r.status_code == 409


# ── 3b. Issued-question binding & fail-closed rubric ─────────────────────────


class TestIssuedQuestionBinding:
    def test_client_authored_question_rejected(self):
        """POST /assess without a server-issued question must be refused."""
        db = _make_db()
        work_id, (cid,) = _seed(db)
        client = _make_test_client(db)
        r = client.post(
            f"/api/works/{work_id}/learning/assess",
            json={"concept_id": cid, "question": "What is 1+1?", "answer": "2"},
        )
        assert r.status_code == 409

    def test_mismatched_question_rejected(self):
        """A submitted question that differs from the issued one is refused."""
        db = _make_db()
        work_id, (cid,) = _seed(db)
        client = _make_test_client(db)
        qr = client.get(f"/api/works/{work_id}/learning/question", params={"concept_id": cid})
        assert qr.status_code == 200
        r = client.post(
            f"/api/works/{work_id}/learning/assess",
            json={"concept_id": cid, "question": "my own trivial question", "answer": "easy"},
        )
        assert r.status_code == 409

    def test_issued_question_is_single_use(self):
        """Replaying the same issued question must be refused the second time."""
        db = _make_db()
        work_id, (cid,) = _seed(db)
        client = _make_test_client(db)
        qr = client.get(f"/api/works/{work_id}/learning/question", params={"concept_id": cid})
        question = qr.json()["question"]
        payload = {"concept_id": cid, "question": question, "answer": "an answer"}
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"score":0.9,"feedback":"ok"}',
        ):
            r1 = client.post(f"/api/works/{work_id}/learning/assess", json=payload)
            r2 = client.post(f"/api/works/{work_id}/learning/assess", json=payload)
        assert r1.status_code == 200
        assert r2.status_code == 409, "issued questions are single-use"

    def test_nonrecall_level_fails_closed_without_rubric(self):
        """Above recall, a bare model float must never grant ladder credit."""
        from orivellum.capabilities.learning import _get_mastery, assess_answer

        db = _make_db()
        _, (cid,) = _seed(db)
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"score":0.95,"feedback":"great"}',  # no criteria list
        ):
            result = assess_answer(
                db, cid, "Q?", "A", base_url=DUMMY_URL, model="t", question_type="transfer"
            )
        assert result["score"] <= 0.5, "unverified float must be capped at neutral"
        assert _get_mastery(db, cid)["consecutive_passes"] == 0, "no streak credit"


# ── 4. Reverse research loop ──────────────────────────────────────────────────


class TestReverseLoop:
    def _fail_n(self, db, cid, n):
        from orivellum.capabilities.learning import _record_mastery

        for _ in range(n):
            _record_mastery(db, cid, 0.2, "STAY_HERE", "wrong", question_type="recall")

    def test_repeated_failure_thin_corpus_schedules_research(self):
        from orivellum.capabilities.learning import list_research_requests, triage_failure

        db = _make_db()
        work_id, (cid,) = _seed(db, knowledge_texts=[])  # thin corpus
        self._fail_n(db, cid, 3)
        result = triage_failure(db, cid)
        assert result["diagnosis"] == "corpus_insufficient"
        assert result["request_id"], "corpus_insufficient must emit a research request"
        reqs = list_research_requests(db, work_id)
        assert len(reqs) == 1 and reqs[0]["status"] == "open"
        assert "Rayleigh scattering" in reqs[0]["need"], "request must name what is needed"

    def test_only_one_open_request_per_concept(self):
        from orivellum.capabilities.learning import list_research_requests, triage_failure

        db = _make_db()
        work_id, (cid,) = _seed(db, knowledge_texts=[])
        self._fail_n(db, cid, 4)
        first = triage_failure(db, cid)
        second = triage_failure(db, cid)
        assert first["request_id"] == second["request_id"]
        assert len(list_research_requests(db, work_id)) == 1

    def test_rich_corpus_diagnoses_never_learned_no_request(self):
        from orivellum.capabilities.learning import list_research_requests, triage_failure

        db = _make_db()
        work_id, (cid,) = _seed(db)  # default = 3 knowledge items
        self._fail_n(db, cid, 3)
        result = triage_failure(db, cid)
        assert result["diagnosis"] == "never_learned"
        assert result["request_id"] is None, "only corpus_insufficient emits research"
        assert list_research_requests(db, work_id) == []

    def test_graduated_cold_failure_diagnoses_decayed(self):
        from orivellum.capabilities.learning import _record_mastery, triage_failure

        db = _make_db()
        _, (cid,) = _seed(db)
        for qt in ("recall", "self_explanation", "transfer"):
            _record_mastery(db, cid, 0.9, "STAY_HERE", "", question_type=qt)
        _record_mastery(db, cid, 0.2, "STAY_HERE", "cold fail", question_type="transfer")
        result = triage_failure(db, cid, cold_check=True)
        assert result["diagnosis"] == "learned_and_decayed"
        assert result["request_id"] is None

    def test_below_threshold_failures_do_not_trigger(self):
        from orivellum.capabilities.learning import triage_failure

        db = _make_db()
        _, (cid,) = _seed(db, knowledge_texts=[])
        self._fail_n(db, cid, 2)  # below _RESEARCH_FAIL_THRESHOLD
        result = triage_failure(db, cid)
        assert result["diagnosis"] is None and result["request_id"] is None

    def test_assess_failure_surfaces_diagnosis_and_request(self):
        """The assess path itself emits the research request on the triggering failure."""
        from orivellum.capabilities.learning import assess_answer

        db = _make_db()
        work_id, (cid,) = _seed(db, knowledge_texts=[])
        self._fail_n(db, cid, 2)
        with patch(
            "orivellum.capabilities.learning._call",
            return_value='{"score":0.1,"feedback":"Wrong.","error_type":"knowledge_gap",'
            '"remediation_hint":"..."}',
        ):
            result = assess_answer(db, cid, "Q?", "no idea", base_url=DUMMY_URL, model="t")
        assert result["diagnosis"] == "corpus_insufficient"
        assert result["research_request_id"], "third failure must schedule research"
        client = _make_test_client(db)
        r = client.get(f"/api/works/{work_id}/learning/research-requests")
        assert r.status_code == 200
        assert len(r.json()["requests"]) == 1

    def test_research_import_resolves_open_request(self):
        """The next research run's writeback closes the request it answered."""
        from orivellum.capabilities.learning import list_research_requests, triage_failure
        from orivellum.capabilities.research_import import import_research_digests

        db = _make_db()
        work_id, (cid,) = _seed(db, knowledge_texts=[])
        self._fail_n(db, cid, 3)
        rid = triage_failure(db, cid)["request_id"]
        digests = {
            "topic": "Rayleigh scattering",
            "digests": [
                {
                    "query": "Rayleigh scattering fundamentals",
                    "origin": "research_request",
                    "request_id": rid,
                    "sources": [
                        {
                            "id": "s1",
                            "url": "https://example.org/rayleigh",
                            "title": "Rayleigh",
                            "retrieved": "2026-08-12",
                        }
                    ],
                    "claims": [
                        {
                            "claim": "Scattering intensity scales inversely with wavelength^4.",
                            "sources": ["s1"],
                            "quote": "intensity scales inversely with the fourth power",
                            "confidence": "high",
                        }
                    ],
                }
            ],
        }
        result = import_research_digests(db, work_id, digests)
        assert result["proposals_created"] == 1
        assert result["research_requests_resolved"] == 1
        reqs = list_research_requests(db, work_id, status=None)  # all statuses
        assert reqs[0]["status"] == "resolved" and reqs[0]["resolved_at"]

    def test_research_import_without_claims_leaves_request_open(self):
        from orivellum.capabilities.learning import list_research_requests, triage_failure
        from orivellum.capabilities.research_import import import_research_digests

        db = _make_db()
        work_id, (cid,) = _seed(db, knowledge_texts=[])
        self._fail_n(db, cid, 3)
        rid = triage_failure(db, cid)["request_id"]
        digests = {
            "topic": "t",
            "digests": [
                {"query": "q", "request_id": rid, "sources": [], "claims": []},
            ],
        }
        result = import_research_digests(db, work_id, digests)
        assert result["research_requests_resolved"] == 0
        reqs = list_research_requests(db, work_id)
        assert reqs and reqs[0]["status"] == "open", "an unanswered request must stay open"

    def test_cross_work_writeback_cannot_resolve_request(self):
        """A digest imported into Work B must never resolve Work A's request."""
        from orivellum.capabilities.learning import list_research_requests, triage_failure
        from orivellum.capabilities.research_import import import_research_digests

        db = _make_db()
        work_a, (cid,) = _seed(db, knowledge_texts=[])
        work_b = db.create_work("Other Work", work_type="learning")["id"]
        self._fail_n(db, cid, 3)
        rid = triage_failure(db, cid)["request_id"]
        digests = {
            "topic": "Rayleigh scattering",
            "digests": [
                {
                    "query": "q",
                    "request_id": rid,  # belongs to work_a
                    "sources": [
                        {"id": "s1", "url": "https://example.org/x", "title": "X"},
                    ],
                    "claims": [
                        {"claim": "Some sourced claim.", "sources": ["s1"], "quote": "q"},
                    ],
                }
            ],
        }
        result = import_research_digests(db, work_b, digests)
        assert result["research_requests_resolved"] == 0
        reqs = list_research_requests(db, work_a)
        assert reqs and reqs[0]["status"] == "open", "cross-Work writeback must not resolve"

    def test_resolve_endpoint(self):
        from orivellum.capabilities.learning import triage_failure

        db = _make_db()
        work_id, (cid,) = _seed(db, knowledge_texts=[])
        self._fail_n(db, cid, 3)
        rid = triage_failure(db, cid)["request_id"]
        client = _make_test_client(db)
        r = client.post(f"/api/works/{work_id}/learning/research-requests/{rid}/resolve")
        assert r.status_code == 200 and r.json()["resolved"] is True
        # second resolve → 409 (not open any more)
        r2 = client.post(f"/api/works/{work_id}/learning/research-requests/{rid}/resolve")
        assert r2.status_code == 409
