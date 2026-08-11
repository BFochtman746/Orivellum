"""The flagship work_audiobook playbook, end-to-end against a stubbed studio.

tests/test_operations_runner.py proves the runner's checkpoint/fencing logic
with fake actions; this file exercises the REAL playbook steps
(wait_for_extraction → render_audiobook → notify) with real Work documents in
the DB and a stubbed studio render registry, covering the behaviours that were
previously only reasoned about:

- pausing mid-render detaches WITHOUT cancelling the render job; resume
  re-attaches to the same live job via the start route's 409 path (no second
  render is ever started while the server stays up)
- a server restart mid-render leaves the operation paused after
  reconciliation; resume starts a fresh render (the in-memory job registry is
  gone) and the run completes
- a render job that vanished from the registry fails the step with the
  "render job disappeared (server restarted?)" message, and resume retries
  with a fresh render

No TTS engine, ffmpeg, or kokoro is involved — the studio hook is replaced by
a fake whose job registry the tests mutate at exactly the render step's poll
points (via a dict subclass with a .get() callback).
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path

import pytest

from orivellum.capabilities.operations import hooks, store
from orivellum.capabilities.operations.playbooks import PLAYBOOKS
from orivellum.capabilities.operations.runner import run_operation
from orivellum.database.db import OrivellumDB


@pytest.fixture()
def db(tmp_path):
    d = OrivellumDB(str(Path(tmp_path) / "test.db"))
    yield d
    d.close()


class _HookedJobs(dict):
    """A job registry whose .get() fires a callback first.

    _render_audiobook polls ``studio._work_tts_jobs.get(job_id)`` in its wait
    loop — hooking .get() lets a test inject a pause request or a job-state
    change at exactly that poll point, with zero timing sensitivity.
    """

    on_get = None  # Callable[[dict, str], None] | None

    def get(self, key, default=None):
        cb = self.on_get
        if cb is not None:
            cb(self, key)
        return super().get(key, default)


def _make_fake_studio():
    class FakeRequest:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    # The terminal-state set comes from the REAL studio module so the fake
    # can never drift from production (e.g. 'failed' vs 'error').
    from orivellum.api.routes import studio as real_studio

    class FakeStudio:
        WorkAudiobookStartRequest = FakeRequest
        _work_tts_jobs_lock = threading.Lock()
        _WORK_TTS_TERMINAL = real_studio._WORK_TTS_TERMINAL
        _work_tts_jobs = _HookedJobs()
        starts: list[str] = []  # job ids actually STARTED (not 409-attached)
        attaches: list[str] = []  # job ids handed back via the 409 path

        @classmethod
        def start_work_audiobook_async(cls, body):
            from fastapi import HTTPException

            # Mirror the real route's duplicate-render guard exactly: only a
            # live (non-terminal) job for THIS work answers 409 with its id.
            live = next(
                (
                    jid
                    for jid, j in cls._work_tts_jobs.items()
                    if j.get("work_id") == body.work_id
                    and j.get("state") not in cls._WORK_TTS_TERMINAL
                ),
                None,
            )
            if live is not None:
                cls.attaches.append(live)
                raise HTTPException(409, detail={"job_id": live})
            jid = f"job-{len(cls.starts) + 1}"
            cls.starts.append(jid)
            cls._work_tts_jobs[jid] = {"state": "running", "work_id": body.work_id}
            return {"job_id": jid}

    return FakeStudio


@pytest.fixture()
def playbook_env(db):
    """Fake studio + notify recorder wired into the operations hooks."""
    studio = _make_fake_studio()
    notifications: list[tuple] = []

    saved_studio = hooks.HOOKS.studio
    saved_notify = hooks.HOOKS.notify
    hooks.configure(studio=studio, notify=lambda *a, **k: notifications.append((a, k)))
    try:
        yield studio, notifications
    finally:
        hooks.HOOKS.studio = saved_studio
        hooks.HOOKS.notify = saved_notify


def _seed_work(db):
    work = db.create_work("Audiobook Work")
    doc = db.create_document(title="Chapter 1", work_id=work["id"], kind="text")
    db.update_document_extracted(doc["id"], "Some narratable text. " * 30, 120, "ready")
    return work


def _playbook_steps():
    pb = next(p for p in PLAYBOOKS if p["id"] == "work_audiobook")
    return copy.deepcopy(pb["steps"])


def _create_op(db, work):
    return store.create_operation(
        db,
        "Turn a Work into an audiobook",
        _playbook_steps(),
        work_id=work["id"],
        playbook_id="work_audiobook",
        params={"work_id": work["id"], "poll_s": 0.01},
    )


def test_pause_detaches_and_resume_reattaches_same_render(db, playbook_env):
    studio, notifications = playbook_env
    work = _seed_work(db)
    op_id = _create_op(db, work)

    # A live render for a DIFFERENT work must not confuse the duplicate
    # guard — the step starts its own render instead of attaching to it.
    studio._work_tts_jobs["other-job"] = {"state": "running", "work_id": "other-work"}

    # A pause lands at the render step's FIRST poll of the job registry.
    def _pause_once(jobs, _key):
        jobs.on_get = None
        store.request_pause(db, op_id)

    studio._work_tts_jobs.on_get = _pause_once

    token = store.claim_operation(db, op_id)
    run_operation(db, None, op_id, token)

    op = store.get_operation(db, op_id)
    assert op["state"] == "paused"
    assert [s["state"] for s in store.list_steps(db, op_id)] == ["done", "pending", "pending"]
    # Detached WITHOUT cancelling: the render job is still live.
    assert studio._work_tts_jobs["job-1"]["state"] == "running"
    assert notifications == []

    # Resume: the render finishes at the next poll.
    def _finish(jobs, key):
        jobs.on_get = None
        jobs[key].update(state="done", output_path="/out/audiobook.m4b")

    studio._work_tts_jobs.on_get = _finish
    token2 = store.claim_operation(db, op_id)
    assert token2 and token2 != token
    run_operation(db, None, op_id, token2)

    op = store.get_operation(db, op_id)
    assert op["state"] == "done"
    assert [s["state"] for s in store.list_steps(db, op_id)] == ["done", "done", "done"]
    # Exactly ONE render was ever started; resume re-attached via the 409 path.
    assert studio.starts == ["job-1"]
    assert studio.attaches == ["job-1"]
    # The playbook's notify step fired exactly once (the runner adds its own
    # operation_done notification on top).
    assert [n for n in notifications if n[0][1] == "Audiobook ready"] == [
        (
            ("operation", "Audiobook ready"),
            {"body": "Your audiobook has finished rendering.", "url": "/operations"},
        )
    ]


def test_restart_mid_render_reconciles_to_paused_and_resume_completes(db, playbook_env):
    studio, notifications = playbook_env
    work = _seed_work(db)
    op_id = _create_op(db, work)

    # Run the REAL playbook and kill the process at the render step's first
    # poll of its live job. KeyboardInterrupt is a BaseException, so it blows
    # straight through the runner's `except Exception` handling — exactly like
    # a process death, the DB is left with op 'running' / render step
    # 'running' and no worker thread.
    class _ProcessDied(KeyboardInterrupt):
        pass

    def _die(jobs, key):
        jobs.on_get = None
        assert jobs[key]["state"] == "running"  # a real render WAS in flight
        raise _ProcessDied()

    studio._work_tts_jobs.on_get = _die
    token = store.claim_operation(db, op_id)
    with pytest.raises(_ProcessDied):
        run_operation(db, None, op_id, token)

    op = store.get_operation(db, op_id)
    assert op["state"] == "running"  # orphaned crash state, pre-reconcile
    assert [s["state"] for s in store.list_steps(db, op_id)] == ["done", "running", "pending"]
    assert studio.starts == ["job-1"]

    # The server comes back: the in-memory job registry is empty.
    studio._work_tts_jobs.clear()

    # Startup reconciliation flips the orphaned run to paused.
    assert store.reconcile_interrupted_operations(db) == 1
    op = store.get_operation(db, op_id)
    assert op["state"] == "paused"
    assert "restart" in (op["error"] or "").lower()
    assert [s["state"] for s in store.list_steps(db, op_id)] == ["done", "pending", "pending"]

    # Resume: no live job to attach to (registry is empty), so a FRESH render
    # starts — the persisted step params reconstruct the original request and
    # the segment cache (not modelled here) makes the redo cheap.
    def _finish(jobs, key):
        jobs.on_get = None
        jobs[key].update(state="done", output_path="/out/audiobook.m4b")

    studio._work_tts_jobs.on_get = _finish
    token2 = store.claim_operation(db, op_id)
    assert token2
    run_operation(db, None, op_id, token2)

    assert store.get_operation(db, op_id)["state"] == "done"
    assert studio.starts == ["job-1", "job-2"]  # fresh render started on resume
    assert studio.attaches == []  # nothing to re-attach to after a restart
    # The done wait step was NOT redone (its checkpointed result survived).
    final_steps = store.list_steps(db, op_id)
    assert [s["state"] for s in final_steps] == ["done", "done", "done"]
    assert len([n for n in notifications if n[0][1] == "Audiobook ready"]) == 1


def test_disappeared_render_job_fails_with_clear_message_and_resume_retries(db, playbook_env):
    studio, _notifications = playbook_env
    work = _seed_work(db)
    op_id = _create_op(db, work)

    # The start answered with a job id, but the registry entry is gone by the
    # first poll (in-memory registry wiped under the runner's feet).
    def _vanish(jobs, key):
        jobs.on_get = None
        jobs.pop(key, None)

    studio._work_tts_jobs.on_get = _vanish
    token = store.claim_operation(db, op_id)
    run_operation(db, None, op_id, token)

    op = store.get_operation(db, op_id)
    assert op["state"] == "failed"
    assert "The render job disappeared (server restarted?) — resume to retry" in (op["error"] or "")
    assert [s["state"] for s in store.list_steps(db, op_id)] == ["done", "failed", "pending"]

    # Resume retries exactly as the error message promises: the claim resets
    # the failed step and a fresh render runs to completion.
    def _finish(jobs, key):
        jobs.on_get = None
        jobs[key].update(state="done", output_path="/out/audiobook.m4b")

    studio._work_tts_jobs.on_get = _finish
    token2 = store.claim_operation(db, op_id)
    assert token2
    run_operation(db, None, op_id, token2)

    assert store.get_operation(db, op_id)["state"] == "done"
    assert studio.starts == ["job-1", "job-2"]
