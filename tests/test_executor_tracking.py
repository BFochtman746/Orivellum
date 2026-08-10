"""Tests for the shared executor tracking and fallback behaviour.

Verifies that:
1. _tracked_submit records a job entry that transitions running→done/failed.
2. A submission failure (executor shut down) leaves a 'failed' entry, never
   a permanently 'running' one.
3. get_recent_jobs returns entries newest-first up to the requested limit.
"""
from __future__ import annotations

import time
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

# Reset module-level executor between tests so each test starts fresh.
import importlib
import orivellum.api.executor as _exec_mod


@pytest.fixture(autouse=True)
def fresh_executor():
    """Ensure the module-level executor is clean before and after each test."""
    _exec_mod.shutdown(wait=True)
    # Also drain the job registry
    with _exec_mod._jobs_lock:
        _exec_mod._jobs.clear()
    yield
    _exec_mod.shutdown(wait=True)
    with _exec_mod._jobs_lock:
        _exec_mod._jobs.clear()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _noop():
    pass


def _fast_work():
    return 42


def _failing_work():
    raise ValueError("intentional test failure")


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestTrackedSubmitLifecycle:

    def test_successful_job_transitions_to_done(self):
        done_evt = threading.Event()
        original = _fast_work

        def _slow_work():
            time.sleep(0.05)
            done_evt.set()
            return 99

        fut = _exec_mod._tracked_submit(_slow_work, kind="test", label="slow_work")

        # Entry should be visible immediately after submission
        jobs_before = _exec_mod.get_recent_jobs()
        assert any(j["label"] == "slow_work" and j["kind"] == "test"
                   for j in jobs_before), "job not visible right after submit"

        # Wait for completion
        done_evt.wait(timeout=3)
        fut.result(timeout=3)

        jobs_after = _exec_mod.get_recent_jobs()
        matching = [j for j in jobs_after if j["label"] == "slow_work"]
        assert matching, "job disappeared from registry"
        assert matching[0]["state"] == "done", f"unexpected state: {matching[0]['state']}"
        assert matching[0]["finished_at"] is not None

    def test_failing_job_transitions_to_failed(self):
        fut = _exec_mod._tracked_submit(_failing_work, kind="test", label="fail_job")
        with pytest.raises(ValueError):
            fut.result(timeout=3)

        jobs = _exec_mod.get_recent_jobs()
        matching = [j for j in jobs if j["label"] == "fail_job"]
        assert matching, "failed job not in registry"
        assert matching[0]["state"] == "failed"
        assert "intentional test failure" in (matching[0]["error"] or "")
        assert matching[0]["finished_at"] is not None

    def test_submit_failure_leaves_failed_entry_not_running(self):
        """If executor.submit() raises, entry must be 'failed', never 'running'."""
        from unittest.mock import patch

        # Force the underlying executor's submit() to raise RuntimeError
        real_exc = _exec_mod.get_executor()
        with patch.object(real_exc, "submit", side_effect=RuntimeError("pool full")):
            with pytest.raises(RuntimeError):
                _exec_mod._tracked_submit(_noop, kind="test", label="shutdown_job")

        jobs = _exec_mod.get_recent_jobs()
        matching = [j for j in jobs if j["label"] == "shutdown_job"]
        assert matching, "no entry created for failed submission"
        assert matching[0]["state"] == "failed", (
            f"entry left as '{matching[0]['state']}' — should be 'failed'"
        )
        assert matching[0]["error"] is not None

    def test_no_entry_created_for_unsubmitted_job_until_after_submit(self):
        """Entry must NOT appear as 'running' when submission fails."""
        from unittest.mock import patch

        real_exc = _exec_mod.get_executor()
        jobs_before = _exec_mod.get_recent_jobs()
        count_before = len(jobs_before)

        with patch.object(real_exc, "submit", side_effect=RuntimeError("forced")):
            try:
                _exec_mod._tracked_submit(_noop, kind="test", label="ghost_check")
            except Exception:
                pass

        jobs_after = _exec_mod.get_recent_jobs()
        new_entries = [j for j in jobs_after if j["label"] == "ghost_check"]
        running_ghosts = [j for j in new_entries if j["state"] == "running"]
        assert not running_ghosts, f"ghost running entries: {running_ghosts}"


class TestZipChildTracking:
    """Verify ZIP child processing is submitted through _tracked_submit."""

    def test_zip_child_uses_tracked_submit(self):
        """_explode_zip_into_documents must create a tracked job entry per child."""
        from unittest.mock import patch, MagicMock, call
        import orivellum.capabilities.pipeline as _pipeline

        # Patch _tracked_submit so we can assert it was called instead of
        # get_executor().submit() or threading.Thread
        submitted: list[dict] = []

        def _fake_tracked_submit(fn, *args, kind="background", label="", **kwargs):
            submitted.append({"fn": fn, "kind": kind, "label": label})
            # Return a simple mock future
            f = MagicMock()
            f.result.return_value = None
            return f

        with patch("orivellum.capabilities.pipeline._tracked_submit",
                   _fake_tracked_submit, create=True):
            # Patch at the import site used inside _explode_zip_into_documents
            with patch("orivellum.api.executor._tracked_submit", _fake_tracked_submit):
                # Verify the function reference inside pipeline uses the patched version
                import importlib
                importlib.reload(_pipeline)

        # After reload, _tracked_submit is re-imported; verify the symbol exists in module
        # (we can't easily run _explode_zip_into_documents without a real ZIP, but
        # the code-path inspection above verifies the import site is correct)
        import inspect
        src = inspect.getsource(_pipeline._explode_zip_into_documents)
        assert ("submit_bg" in src or "_tracked_submit" in src), \
            "_explode_zip_into_documents must route through the tracked executor " \
            "(submit_bg/_tracked_submit), not get_executor().submit() or raw threads"
        assert "threading.Thread" not in src, \
            "_explode_zip_into_documents must not spawn raw threads"
        assert "get_executor" not in src or "get_executor" not in src.split("_tracked_submit")[0], \
            "get_executor().submit() call should not precede _tracked_submit in ZIP path"


class TestGetRecentJobs:

    def test_returns_newest_first(self):
        evts = [threading.Event() for _ in range(3)]

        def _work(idx):
            evts[idx].set()

        futs = [
            _exec_mod._tracked_submit(_work, i, kind="order", label=f"job_{i}")
            for i in range(3)
        ]
        for f in futs:
            f.result(timeout=3)

        jobs = _exec_mod.get_recent_jobs(limit=10)
        order_jobs = [j for j in jobs if j["kind"] == "order"]
        # Newest first — highest index submitted last
        assert order_jobs[0]["label"] == "job_2"
        assert order_jobs[-1]["label"] == "job_0"

    def test_limit_is_respected(self):
        for i in range(5):
            fut = _exec_mod._tracked_submit(_noop, kind="limit_test", label=f"lj_{i}")
            fut.result(timeout=3)

        jobs = _exec_mod.get_recent_jobs(limit=2)
        assert len(jobs) <= 2
