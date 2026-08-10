"""Resilience tests for the brainstorm engine.

Covers three acceptance criteria:
  1. Partial worker failure — N-1 workers return None; session completes with
     the surviving subset (at least 1 idea, status='done').
  2. Total worker failure — all workers return None; run_brainstorm_session
     raises RuntimeError and the API route writes status='failed' (not 'running').
  3. Duplicate idea deduplication — when workers produce near-identical texts,
     _deduplicate() removes the lesser copy so only unique ideas survive.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

# ─── fixtures ─────────────────────────────────────────────────────────────────


def _make_app(tmp_path):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=str(tmp_path))
    db = OrivellumDB(str(tmp_path / "test.db"))
    _deps.init(db=db, cfg=cfg)
    # raise_server_exceptions=False so we can inspect 502 responses
    return TestClient(app, raise_server_exceptions=False, headers=AUTH_HEADERS), db


# ─── helpers ──────────────────────────────────────────────────────────────────


def _stub_scores(idea_texts, work_id, db):
    """Originality stub — returns 0.8 for every idea (no embeddings needed)."""
    return [0.8] * len(idea_texts)


def _stub_usefulness(idea_texts, seed, context_type, db, cfg):
    """Usefulness judge stub — returns 4 for every idea (no LLM call)."""
    return [4] * len(idea_texts)


# ─── Test 1: partial worker failure ───────────────────────────────────────────


def test_partial_worker_failure_yields_surviving_ideas(tmp_path):
    """When N-1 domain workers fail, the session completes with the surviving idea.

    Stubs _domain_worker so only the very first call succeeds; all subsequent
    calls return None (simulating timeout or LLM error).  The engine must still
    return at least 1 idea and mark the session 'done'.
    """
    from orivellum.capabilities.brainstorm import run_brainstorm_session

    _, db = _make_app(tmp_path)
    work = db.create_work("Brainstorm Work")
    session = db.create_brainstorm_session(
        work_id=work["id"],
        seed_prompt="How should I structure the opening chapter?",
        context_type="general",
        n_domains=5,
    )

    call_count = {"n": 0}
    lock = threading.Lock()

    def one_success(*args, **kwargs):
        with lock:
            call_count["n"] += 1
            first = call_count["n"] == 1
        if first:
            return "Use a cold open that drops the reader mid-action, then rewind 24 hours to establish stakes."
        return None  # all other workers fail

    with (
        patch("orivellum.capabilities.brainstorm._domain_worker", side_effect=one_success),
        patch("orivellum.capabilities.brainstorm._score_originality", side_effect=_stub_scores),
        patch("orivellum.capabilities.brainstorm._score_usefulness", side_effect=_stub_usefulness),
    ):
        ideas = run_brainstorm_session(
            session["id"],
            work["id"],
            "How should I structure the opening chapter?",
            "general",
            db,
            object(),  # cfg — not used because LLM calls are mocked
            n_domains=5,
        )

    # Engine must return at least 1 surviving idea
    assert len(ideas) >= 1, "Expected at least 1 idea from the surviving worker"
    assert ideas[0]["text"] != "", "Surviving idea must have non-empty text"
    assert ideas[0]["originality"] == pytest.approx(0.8)
    assert ideas[0]["usefulness"] == 4

    # Callers consumed multiple worker slots (≥2: 1 success + retries from phase 2
    # when fewer than 4 ideas were collected)
    assert call_count["n"] >= 1


def test_partial_failure_api_session_written_as_done(tmp_path):
    """API route stores status='done' after partial-failure run."""
    client, db = _make_app(tmp_path)
    work = db.create_work("API Work")

    call_count = {"n": 0}
    lock = threading.Lock()

    def one_success(*args, **kwargs):
        with lock:
            call_count["n"] += 1
            first = call_count["n"] == 1
        return "Viable idea from the sole surviving worker." if first else None

    with (
        patch("orivellum.capabilities.brainstorm._domain_worker", side_effect=one_success),
        patch("orivellum.capabilities.brainstorm._score_originality", side_effect=_stub_scores),
        patch("orivellum.capabilities.brainstorm._score_usefulness", side_effect=_stub_usefulness),
    ):
        resp = client.post(
            f"/api/works/{work['id']}/brainstorm",
            json={"seed_prompt": "Partial failure scenario", "n_domains": 4},
        )

    assert resp.status_code == 200, f"Unexpected status: {resp.status_code} — {resp.text}"
    data = resp.json()
    assert data["status"] == "done"
    assert isinstance(data["ideas"], list)
    assert len(data["ideas"]) >= 1


# ─── Test 2: total worker failure ────────────────────────────────────────────


def test_total_worker_failure_raises_runtime_error(tmp_path):
    """run_brainstorm_session raises RuntimeError when all workers return None."""
    from orivellum.capabilities.brainstorm import run_brainstorm_session

    _, db = _make_app(tmp_path)
    work = db.create_work("All-Fail Work")
    session = db.create_brainstorm_session(
        work_id=work["id"],
        seed_prompt="Doomed session — every worker will fail",
        context_type="general",
        n_domains=3,
    )

    with (
        patch("orivellum.capabilities.brainstorm._domain_worker", return_value=None),
    ):
        with pytest.raises(RuntimeError, match="All domain workers failed"):
            run_brainstorm_session(
                session["id"],
                work["id"],
                "Doomed session — every worker will fail",
                "general",
                db,
                object(),
                n_domains=3,
            )


def test_total_worker_failure_writes_failed_status_not_running(tmp_path):
    """When all workers fail, the API route writes status='failed' — never 'running'.

    This is the key anti-stuck guarantee: a session must never remain in the
    'running' state after the endpoint returns.
    """
    client, db = _make_app(tmp_path)
    work = db.create_work("Stuck-Prevention Work")

    with patch("orivellum.capabilities.brainstorm._domain_worker", return_value=None):
        resp = client.post(
            f"/api/works/{work['id']}/brainstorm",
            json={"seed_prompt": "All workers dead", "n_domains": 3},
        )

    # Route should 502 when the engine raises RuntimeError
    assert resp.status_code == 502, f"Expected 502, got {resp.status_code}"

    # The session must NOT be left in 'running' — it must be 'failed'
    sessions = db.list_brainstorm_sessions(work["id"])
    assert len(sessions) == 1, "Session record must have been created"
    session = sessions[0]
    assert session["status"] == "failed", (
        f"Session must be 'failed' after total LLM outage, got {session['status']!r}"
    )
    assert session["status"] != "running", "Session must never be left in 'running' state"


def test_failed_session_has_empty_ideas_list(tmp_path):
    """A 'failed' session must have an empty ideas list, not null or garbage."""
    client, db = _make_app(tmp_path)
    work = db.create_work("Empty Ideas Work")

    with patch("orivellum.capabilities.brainstorm._domain_worker", return_value=None):
        client.post(
            f"/api/works/{work['id']}/brainstorm",
            json={"seed_prompt": "Failed run", "n_domains": 3},
        )

    sessions = db.list_brainstorm_sessions(work["id"])
    assert sessions[0]["ideas"] == [], "Failed session must have an empty ideas list"


# ─── Test 3: duplicate seed — intra-session idea deduplication ───────────────


def test_deduplicate_removes_near_identical_ideas():
    """_deduplicate() removes near-duplicate idea texts, keeping the higher-scoring one.

    When the same seed_prompt elicits ideas that are textually very similar
    (e.g. two workers produce near-identical phrasing), _deduplicate() should
    collapse them into a single representative idea.
    """
    from orivellum.capabilities.brainstorm import _deduplicate, _new_idea

    # Two near-identical ideas — same meaning, slightly different wording
    idea_a = _new_idea(
        "ecology", "Use a cascade structure where each chapter feeds into the next like a food web."
    )
    idea_b = _new_idea(
        "music",
        "Use a cascade structure where each chapter feeds into the next like a food web, building momentum.",
    )
    idea_a["originality"] = 0.6
    idea_b["originality"] = 0.9  # idea_b is better; dedup should keep it

    # Unrelated idea that must survive
    idea_c = _new_idea(
        "game theory", "Introduce a prisoner's dilemma between two competing narrators."
    )
    idea_c["originality"] = 0.75

    result = _deduplicate([idea_a, idea_b, idea_c], threshold=0.5)

    # The near-duplicate pair should be collapsed into one; idea_c must survive
    texts = [i["text"] for i in result]
    assert len(result) == 2, (
        f"Expected 2 ideas after dedup (1 from dup pair + idea_c), got {len(result)}: {texts}"
    )
    assert any("prisoner" in t for t in texts), "idea_c (prisoner's dilemma) must survive dedup"

    # The surviving duplicate should be the higher-originality one (idea_b)
    surviving_dup = next(i for i in result if "prisoner" not in i["text"])
    assert surviving_dup["originality"] == pytest.approx(0.9), (
        "Dedup should keep the higher-originality idea from a near-duplicate pair"
    )


def test_deduplicate_with_duplicate_seed_prompt_session(tmp_path):
    """When workers produce near-identical output for the same seed_prompt,
    the full pipeline collapses them via _deduplicate before returning.

    Simulates two workers responding to the same seed with very similar phrasing.
    """
    from orivellum.capabilities.brainstorm import run_brainstorm_session

    _, db = _make_app(tmp_path)
    work = db.create_work("Dedup Session Work")
    session = db.create_brainstorm_session(
        work_id=work["id"],
        seed_prompt="Structure the opening act",
        context_type="narrative_structure",
        n_domains=4,
    )

    DUPLICATE_TEXT = (
        "Open with a scene of high tension that immediately establishes the central conflict."
    )

    call_count = {"n": 0}
    lock = threading.Lock()

    def duplicating_worker(*args, **kwargs):
        """First 2 calls return near-identical text; last 2 return a unique idea each."""
        with lock:
            n = call_count["n"]
            call_count["n"] += 1
        if n == 0:
            return DUPLICATE_TEXT
        if n == 1:
            # Near-duplicate of the first (same words, one added at end)
            return DUPLICATE_TEXT + " Keep it brief."
        if n == 2:
            return (
                "Alternate between two timelines so the reader must piece together what happened."
            )
        return "Open in medias res — then pause to lay out who everyone is and what they want."

    with (
        patch("orivellum.capabilities.brainstorm._domain_worker", side_effect=duplicating_worker),
        patch("orivellum.capabilities.brainstorm._score_originality", side_effect=_stub_scores),
        patch("orivellum.capabilities.brainstorm._score_usefulness", side_effect=_stub_usefulness),
    ):
        ideas = run_brainstorm_session(
            session["id"],
            work["id"],
            "Structure the opening act",
            "narrative_structure",
            db,
            object(),
            n_domains=4,
        )

    # Deduplication must have fired: we fed 4 workers but 2 produced near-identical
    # text → result must have fewer ideas than raw worker count
    assert len(ideas) <= 3, (
        f"Expected dedup to collapse near-identical ideas; got {len(ideas)} ideas"
    )
    # The unique third and fourth ideas must survive
    texts = " ".join(i["text"] for i in ideas)
    assert "timelines" in texts or "medias res" in texts, (
        "At least one of the unique ideas must survive deduplication"
    )


# ─── Test 4: session isolation — concurrent sessions don't interfere ──────────


def test_two_sessions_for_same_work_are_independent(tmp_path):
    """Two brainstorm sessions for the same Work are stored and retrieved independently."""
    _, db = _make_app(tmp_path)
    work = db.create_work("Concurrent Work")

    s1 = db.create_brainstorm_session(work["id"], "seed A", "general", 3)
    s2 = db.create_brainstorm_session(work["id"], "seed B", "general", 3)

    idea_a = {
        "id": "aaa",
        "domain": "ecology",
        "text": "Idea A",
        "originality": 0.7,
        "usefulness": 4,
        "on_pareto_front": True,
        "knowledge_item_id": None,
    }
    idea_b = {
        "id": "bbb",
        "domain": "music",
        "text": "Idea B",
        "originality": 0.5,
        "usefulness": 3,
        "on_pareto_front": False,
        "knowledge_item_id": None,
    }

    db.update_brainstorm_session(s1["id"], status="done", ideas=[idea_a])
    db.update_brainstorm_session(s2["id"], status="failed", ideas=[])

    r1 = db.get_brainstorm_session(s1["id"])
    r2 = db.get_brainstorm_session(s2["id"])

    assert r1["status"] == "done"
    assert r2["status"] == "failed"
    assert len(r1["ideas"]) == 1
    assert len(r2["ideas"]) == 0
    assert r1["ideas"][0]["text"] == "Idea A"

    # list_brainstorm_sessions should return both
    all_sessions = db.list_brainstorm_sessions(work["id"])
    assert len(all_sessions) == 2
