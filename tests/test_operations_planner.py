"""Natural-language job planner — validation, repair-retry, and clarification.

Proves the plan-once-then-execute reliability contract with the LLM mocked:

- a valid plan passes validation and resolves the Work to a real id
- a hallucinated action is fed back for ONE repair retry; a corrected second
  response succeeds
- when the repair also fails, the caller gets a clear error — never a silent
  guess (and exactly two LLM calls were made)
- unknown / mistyped parameters are rejected
- ambiguity (which Work?) surfaces as a clarifying question, both when the
  LLM asks and when work-title resolution is ambiguous
- human voice names resolve to catalog ids
- custom playbooks only save validated steps and round-trip through lookup
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orivellum.capabilities.operations import hooks, planner
from orivellum.database.db import OrivellumDB


@pytest.fixture()
def db(tmp_path):
    d = OrivellumDB(str(Path(tmp_path) / "test.db"))
    yield d
    d.close()


@pytest.fixture()
def llm(monkeypatch):
    """Queue canned LLM responses; records every call's messages."""

    class FakeLLM:
        def __init__(self):
            self.responses: list[str | None] = []
            self.calls: list[list[dict]] = []

        def __call__(self, messages, db, cfg, model):
            self.calls.append(messages)
            return self.responses.pop(0) if self.responses else None

    fake = FakeLLM()
    monkeypatch.setattr(planner, "_llm_text", fake)
    return fake


def _plan_json(steps, work=None, title="Test op", clarification=None) -> str:
    return json.dumps(
        {"title": title, "work": work, "clarification": clarification, "steps": steps}
    )


NOTIFY_STEP = {"action_id": "notify", "label": "Tell me", "params": {"title": "Done"}}


def test_valid_plan_resolves_work(db, llm):
    work = db.create_work("Sci-Fi Novel")
    llm.responses = [
        _plan_json(
            [
                {"action_id": "wait_for_extraction", "label": "Wait for processing", "params": {}},
                NOTIFY_STEP,
            ],
            work="Sci-Fi Novel",
        )
    ]
    result = planner.plan_job(db, None, "wait for my sci-fi novel then tell me")
    assert result["status"] == "ok"
    plan = result["plan"]
    assert plan["work_id"] == work["id"]
    assert plan["work_title"] == "Sci-Fi Novel"
    assert [s["action_id"] for s in plan["steps"]] == ["wait_for_extraction", "notify"]
    assert len(llm.calls) == 1


def test_hallucinated_action_gets_one_repair_retry(db, llm):
    llm.responses = [
        _plan_json([{"action_id": "make_magic", "label": "Magic", "params": {}}]),
        _plan_json([NOTIFY_STEP]),
    ]
    result = planner.plan_job(db, None, "do the thing")
    assert result["status"] == "ok"
    assert len(llm.calls) == 2
    # The repair prompt names the concrete problem, not a vague "try again".
    repair_text = llm.calls[1][-1]["content"]
    assert "make_magic" in repair_text


def test_repair_failure_is_a_clear_error_never_a_guess(db, llm):
    llm.responses = [
        _plan_json([{"action_id": "make_magic", "label": "Magic", "params": {}}]),
        _plan_json([{"action_id": "still_fake", "label": "Nope", "params": {}}]),
    ]
    result = planner.plan_job(db, None, "do the thing")
    assert result["status"] == "error"
    assert any("still_fake" in p for p in result["problems"])
    assert len(llm.calls) == 2  # exactly one repair retry, never more


def test_unknown_and_mistyped_params_rejected(db, llm):
    bad = {"action_id": "notify", "label": "Tell me", "params": {"volume": 11}}
    llm.responses = [_plan_json([bad]), _plan_json([bad])]
    result = planner.plan_job(db, None, "notify me loudly")
    assert result["status"] == "error"
    assert any("volume" in p for p in result["problems"])

    # work_id smuggled into step params is also rejected.
    errs = planner.validate_steps(
        [{"action_id": "notify", "label": "x", "params": {"work_id": "abc"}}],
        __import__(
            "orivellum.capabilities.operations.registry", fromlist=["get_op_registry"]
        ).get_op_registry(),
    )
    assert any("work_id" in e for e in errs)


def test_llm_clarification_passes_through(db, llm):
    llm.responses = [_plan_json([], clarification="Which Work should I use?")]
    result = planner.plan_job(db, None, "make an audiobook")
    assert result["status"] == "clarify"
    assert "Which Work" in result["question"]


def test_ambiguous_work_title_asks_instead_of_guessing(db, llm):
    db.create_work("Alpha Book")
    db.create_work("Alpha Notes")
    llm.responses = [
        _plan_json(
            [{"action_id": "wait_for_extraction", "label": "Wait", "params": {}}], work="Alpha"
        )
    ]
    result = planner.plan_job(db, None, "process alpha")
    assert result["status"] == "clarify"
    assert "Alpha Book" in result["question"] and "Alpha Notes" in result["question"]


def test_work_needed_but_none_named_asks(db, llm):
    db.create_work("Only Work")
    llm.responses = [
        _plan_json([{"action_id": "render_audiobook", "label": "Render", "params": {}}], work=None)
    ]
    result = planner.plan_job(db, None, "render an audiobook")
    assert result["status"] == "clarify"
    assert "Only Work" in result["question"]


def test_voice_name_resolves_to_catalog_id(db, llm, monkeypatch):
    class FakeStudio:
        _VOICE_CATALOG = [
            {"id": "bm_george", "name": "George"},
            {"id": "af_bella", "name": "Bella"},
        ]

    saved = hooks.HOOKS.studio
    hooks.configure(studio=FakeStudio)
    try:
        db.create_work("My Book")
        llm.responses = [
            _plan_json(
                [
                    {
                        "action_id": "render_audiobook",
                        "label": "Render",
                        "params": {"voice": "George"},
                    }
                ],
                work="My Book",
            )
        ]
        result = planner.plan_job(db, None, "render my book with the george voice")
        assert result["status"] == "ok"
        assert result["plan"]["steps"][0]["params"]["voice"] == "bm_george"
    finally:
        hooks.HOOKS.studio = saved


def test_unreachable_model_is_an_explicit_error(db, llm):
    llm.responses = [None]
    result = planner.plan_job(db, None, "do anything")
    assert result["status"] == "error"
    assert "not reachable" in result["message"]


# ── Custom playbooks ─────────────────────────────────────────────────────────


def test_custom_playbook_save_validates_and_round_trips(db):
    from orivellum.capabilities.operations import playbooks

    with pytest.raises(ValueError, match="unknown action"):
        playbooks.save_custom_playbook(
            db, "Bad", [{"action_id": "fake_action", "label": "x", "params": {}}]
        )

    pb = playbooks.save_custom_playbook(db, "My nightly run", [NOTIFY_STEP])
    assert pb["custom"] is True

    found = playbooks.get_playbook(pb["id"], db)
    assert found is not None
    assert found["title"] == "My nightly run"
    assert found["steps"][0]["action_id"] == "notify"

    assert playbooks.delete_custom_playbook(db, pb["id"]) is True
    assert playbooks.get_playbook(pb["id"], db) is None
    assert playbooks.delete_custom_playbook(db, pb["id"]) is False
