"""Operations runner — durable multi-step runs with per-step checkpoints.

Proves the core guarantees of the operations system with fake actions
registered in-test (so the registry is exercised generically, and no LLM,
ffmpeg, or kokoro is needed):

- a run executes steps in order and checkpoints each one as done
- a failing step marks the step + operation failed and stops the run
- resume after a failure re-runs only the failed step (done steps are skipped
  and their results are still visible to later steps)
- a pause request is honoured mid-step; the interrupted step reverts to
  pending and resume redoes exactly that step
- startup reconciliation flips operations orphaned by a restart to paused
- cancel marks the run and its unfinished steps cancelled
- claim fencing: a stale runner (superseded by a newer resume) can never
  mutate the newer run's state, and only one of two racing resumes wins
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orivellum.capabilities.operations import store
from orivellum.capabilities.operations.registry import (
    OpAction,
    OperationInterrupted,
    get_op_registry,
    register,
    unregister,
)
from orivellum.capabilities.operations.runner import run_operation
from orivellum.database.db import OrivellumDB


@pytest.fixture()
def db(tmp_path):
    d = OrivellumDB(str(Path(tmp_path) / "test.db"))
    yield d
    d.close()


@pytest.fixture()
def fake_actions():
    """Register counting fake actions; clean them up afterwards."""
    calls: dict[str, list[dict]] = {"a": [], "b": [], "c": [], "boom": [], "pauser": []}

    def _mk(name):
        def _exec(ctx, params):
            calls[name].append(dict(params))
            return {"ran": name, "seen_results": {k: v.get("ran") for k, v in ctx.results.items()}}

        return _exec

    def _boom(ctx, params):
        calls["boom"].append(dict(params))
        raise RuntimeError("kaboom")

    def _pauser(ctx, params):
        # Simulates a long step interrupted mid-poll: an external pause lands,
        # the action notices via should_stop() and bails out.
        calls["pauser"].append(dict(params))
        store.request_pause(ctx.db, ctx.operation_id)
        assert ctx.should_stop()
        raise OperationInterrupted()

    for name in ("a", "b", "c"):
        register(
            OpAction(
                id=f"fake_{name}",
                label=name,
                description="",
                params_schema={},
                execute=_mk(name),
            )
        )
    register(
        OpAction(id="fake_boom", label="boom", description="", params_schema={}, execute=_boom)
    )
    register(
        OpAction(
            id="fake_pauser", label="pauser", description="", params_schema={}, execute=_pauser
        )
    )
    yield calls
    for aid in ("fake_a", "fake_b", "fake_c", "fake_boom", "fake_pauser"):
        unregister(aid)


def _start(db, steps, params=None):
    op_id = store.create_operation(db, "Test op", steps, params=params or {})
    token = store.claim_operation(db, op_id)
    assert token
    return op_id, token


def test_run_completes_and_checkpoints(db, fake_actions):
    op_id, token = _start(
        db,
        [
            {"action_id": "fake_a", "label": "Step A"},
            {"action_id": "fake_b", "label": "Step B", "params": {"x": 1}},
            {"action_id": "fake_c", "label": "Step C"},
        ],
        params={"work_id": "w1"},
    )
    run_operation(db, None, op_id, token)

    op = store.get_operation(db, op_id)
    assert op["state"] == "done"
    assert op["finished_at"]
    steps = store.list_steps(db, op_id)
    assert [s["state"] for s in steps] == ["done", "done", "done"]
    assert all(s["started_at"] and s["finished_at"] for s in steps)
    # op-level params merge under step params
    assert fake_actions["b"] == [{"work_id": "w1", "x": 1}]
    # later steps see earlier results (JSON round-trip stringifies the keys)
    assert json.loads(steps[2]["result"])["seen_results"] == {"0": "a", "1": "b"}


def test_failure_marks_step_and_operation(db, fake_actions):
    op_id, token = _start(
        db,
        [
            {"action_id": "fake_a", "label": "Step A"},
            {"action_id": "fake_boom", "label": "Step B"},
            {"action_id": "fake_c", "label": "Step C"},
        ],
    )
    run_operation(db, None, op_id, token)

    op = store.get_operation(db, op_id)
    assert op["state"] == "failed"
    assert "kaboom" in op["error"]
    states = [s["state"] for s in store.list_steps(db, op_id)]
    assert states == ["done", "failed", "pending"]
    assert fake_actions["c"] == []  # never reached


def test_resume_after_failure_skips_done_steps(db, fake_actions):
    op_id, token = _start(
        db,
        [
            {"action_id": "fake_a", "label": "Step A"},
            {"action_id": "fake_boom", "label": "Step B"},
        ],
    )
    run_operation(db, None, op_id, token)
    assert store.get_operation(db, op_id)["state"] == "failed"

    # Swap the failing action for a working one under the same id (the fix
    # "arrived"), then retry the way the resume route does: claim atomically
    # resets the failed step and issues a fresh token.
    unregister("fake_boom")
    register(
        OpAction(
            id="fake_boom",
            label="fixed",
            description="",
            params_schema={},
            execute=lambda ctx, p: {"ran": "fixed"},
        )
    )
    token2 = store.claim_operation(db, op_id)
    assert token2 and token2 != token
    run_operation(db, None, op_id, token2)

    assert store.get_operation(db, op_id)["state"] == "done"
    assert len(fake_actions["a"]) == 1  # step A was NOT re-run


def test_pause_mid_step_reverts_and_resume_redoes_it(db, fake_actions):
    op_id, token = _start(
        db,
        [
            {"action_id": "fake_a", "label": "Step A"},
            {"action_id": "fake_pauser", "label": "Step B"},
            {"action_id": "fake_c", "label": "Step C"},
        ],
    )
    run_operation(db, None, op_id, token)

    op = store.get_operation(db, op_id)
    assert op["state"] == "paused"
    states = [s["state"] for s in store.list_steps(db, op_id)]
    assert states == ["done", "pending", "pending"]  # interrupted step reverted
    assert fake_actions["c"] == []

    # Resume: replace the pauser with a normal action so the run finishes.
    unregister("fake_pauser")
    register(
        OpAction(
            id="fake_pauser",
            label="ok now",
            description="",
            params_schema={},
            execute=lambda ctx, p: {"ran": "ok"},
        )
    )
    token2 = store.claim_operation(db, op_id)
    assert token2
    run_operation(db, None, op_id, token2)
    assert store.get_operation(db, op_id)["state"] == "done"
    assert len(fake_actions["a"]) == 1


def test_reconcile_flips_orphaned_running_to_paused(db, fake_actions):
    op_id, token = _start(db, [{"action_id": "fake_a", "label": "Step A"}])
    # Fake a restart: op is 'running', its step is 'running', no thread exists.
    steps = store.list_steps(db, op_id)
    store.mark_step_running(db, steps[0]["id"], token)

    n = store.reconcile_interrupted_operations(db)
    assert n == 1
    op = store.get_operation(db, op_id)
    assert op["state"] == "paused"
    assert "restart" in (op["error"] or "").lower()
    assert store.list_steps(db, op_id)[0]["state"] == "pending"

    # And the paused run is resumable.
    token2 = store.claim_operation(db, op_id)
    assert token2
    run_operation(db, None, op_id, token2)
    assert store.get_operation(db, op_id)["state"] == "done"


def test_startup_recovery_helper_is_a_plain_function(db, fake_actions):
    """The app-startup hook must actually run (guards a decorator mishap)."""
    from orivellum.api.app import _recover_interrupted_operations

    op_id, _token = _start(db, [{"action_id": "fake_a", "label": "Step A"}])
    result = _recover_interrupted_operations(db)
    assert result is None  # plain function, not a context manager
    assert store.get_operation(db, op_id)["state"] == "paused"


def test_cancel_marks_unfinished_steps_cancelled(db, fake_actions):
    op_id = store.create_operation(
        db,
        "Cancel me",
        [{"action_id": "fake_a", "label": "A"}, {"action_id": "fake_b", "label": "B"}],
    )
    assert store.request_cancel(db, op_id)
    op = store.get_operation(db, op_id)
    assert op["state"] == "cancelled"
    assert [s["state"] for s in store.list_steps(db, op_id)] == ["cancelled", "cancelled"]
    # A cancelled op cannot be claimed again.
    assert store.claim_operation(db, op_id) is None


def test_concurrent_resume_only_one_winner(db, fake_actions):
    op_id, token = _start(
        db,
        [
            {"action_id": "fake_a", "label": "A"},
            {"action_id": "fake_boom", "label": "B"},
        ],
    )
    run_operation(db, None, op_id, token)
    assert store.get_operation(db, op_id)["state"] == "failed"

    first = store.claim_operation(db, op_id)
    second = store.claim_operation(db, op_id)  # racing retry loses cleanly
    assert first is not None
    assert second is None


def test_resume_before_paused_worker_reaches_checkpoint(db, fake_actions):
    """Resume can land while the old worker is still inside a step.

    request_pause exposes 'paused' immediately, but the worker only notices at
    its next should_stop() poll. If the user resumes in that window, the claim
    must reset the still-'running' step so the new runner can redo it — and the
    stale worker's late revert/complete must no-op against the new token.
    """
    op_id, stale_token = _start(
        db,
        [
            {"action_id": "fake_a", "label": "A"},
            {"action_id": "fake_b", "label": "B"},
        ],
    )
    steps = store.list_steps(db, op_id)
    # Old worker is mid-step A…
    assert store.mark_step_running(db, steps[0]["id"], stale_token)
    # …user pauses, then resumes before the worker reaches its checkpoint.
    store.request_pause(db, op_id)
    fresh_token = store.claim_operation(db, op_id)
    assert fresh_token and fresh_token != stale_token

    # The claim reset the stranded step so the new runner owns it again.
    assert store.list_steps(db, op_id)[0]["state"] == "pending"

    # The new runner completes the whole operation…
    run_operation(db, None, op_id, fresh_token)
    assert store.get_operation(db, op_id)["state"] == "done"

    # …and the old worker's late reactions are harmless no-ops.
    store.revert_step(db, steps[0]["id"], stale_token)
    assert not store.mark_step_done(db, steps[0]["id"], {"late": True}, stale_token)
    assert [s["state"] for s in store.list_steps(db, op_id)] == ["done", "done"]


def test_stale_runner_cannot_mutate_after_reclaim(db, fake_actions):
    op_id, stale_token = _start(
        db,
        [
            {"action_id": "fake_a", "label": "A"},
            {"action_id": "fake_b", "label": "B"},
        ],
    )
    # The original runner is paused, then a new resume rotates the token.
    store.request_pause(db, op_id)
    fresh_token = store.claim_operation(db, op_id)
    assert fresh_token and fresh_token != stale_token

    # The stale runner's transitions all no-op…
    steps = store.list_steps(db, op_id)
    assert not store.mark_step_running(db, steps[0]["id"], stale_token)
    assert not store.mark_operation_done(db, op_id, stale_token)
    run_operation(db, None, op_id, stale_token)  # refuses to run at all
    assert store.get_operation(db, op_id)["state"] == "running"
    assert all(s["state"] == "pending" for s in store.list_steps(db, op_id))
    assert fake_actions["a"] == []

    # …while the fresh claim runs to completion.
    run_operation(db, None, op_id, fresh_token)
    assert store.get_operation(db, op_id)["state"] == "done"
    assert len(fake_actions["a"]) == 1


def test_unknown_action_fails_cleanly(db):
    op_id = store.create_operation(db, "Test op", [{"action_id": "does_not_exist", "label": "??"}])
    token = store.claim_operation(db, op_id)
    run_operation(db, None, op_id, token)
    op = store.get_operation(db, op_id)
    assert op["state"] == "failed"
    assert "Unknown action" in op["error"]


def test_registry_includes_builtins_and_wrapped_actions(db):
    reg = get_op_registry()
    for expected in ("wait_for_extraction", "render_audiobook", "notify"):
        assert expected in reg
    # every one-shot action is wrapped under the action: prefix
    assert "action:study_plan" in reg
    assert "action:book_export" in reg
