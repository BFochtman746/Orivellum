"""Tests for the background-job dashboard — executor retry and API endpoint.

Covers:
- _tracked_submit records kind/label/state/timing
- Failed jobs are marked correctly on exception
- get_recent_jobs strips private _-prefixed fields
- retry_job re-submits a failed job and creates a new entry
- retry_job raises on non-failed / missing / no-callable jobs
- POST /api/system/jobs/{id}/retry returns expected HTTP codes
"""

from __future__ import annotations

import threading
import time
import unittest

# ── helpers ──────────────────────────────────────────────────────────────────


def _reset_jobs():
    """Clear the module-level _jobs deque between tests."""
    from orivellum.api.executor import _jobs, _jobs_lock

    with _jobs_lock:
        _jobs.clear()


def _submit_and_wait(fn, *args, kind="background", label="", timeout=2.0, **kwargs):
    """Submit via _tracked_submit and block until the future completes."""
    from orivellum.api.executor import _tracked_submit

    future = _tracked_submit(fn, *args, kind=kind, label=label, **kwargs)
    future.result(timeout=timeout)


# ── TestTrackedSubmit ─────────────────────────────────────────────────────────


class TestTrackedSubmit(unittest.TestCase):
    """_tracked_submit records jobs and updates state correctly."""

    def setUp(self):
        _reset_jobs()

    def test_successful_job_is_marked_done(self):
        _submit_and_wait(lambda: None, kind="test", label="noop")

        from orivellum.api.executor import get_recent_jobs

        jobs = get_recent_jobs()
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["state"], "done")
        self.assertEqual(jobs[0]["kind"], "test")
        self.assertEqual(jobs[0]["label"], "noop")

    def test_failed_job_is_marked_failed_with_error(self):
        def _bad():
            raise ValueError("simulated failure")

        from orivellum.api.executor import _tracked_submit

        future = _tracked_submit(_bad, kind="test", label="will_fail")
        # consume the exception
        try:
            future.result(timeout=2.0)
        except ValueError:
            pass

        from orivellum.api.executor import get_recent_jobs

        jobs = get_recent_jobs()
        failed = [j for j in jobs if j["state"] == "failed"]
        self.assertEqual(len(failed), 1)
        self.assertIn("simulated failure", failed[0]["error"])

    def test_job_has_elapsed_time(self):
        done_event = threading.Event()
        _submit_and_wait(lambda: time.sleep(0.05), kind="test", label="timed")

        from orivellum.api.executor import get_recent_jobs

        jobs = get_recent_jobs()
        j = jobs[0]
        self.assertIsNotNone(j["finished_at"])
        self.assertGreater(j["finished_at"], j["started_at"])

    def test_private_fields_stripped_from_get_recent_jobs(self):
        _submit_and_wait(lambda: None, kind="test", label="check_private")

        from orivellum.api.executor import get_recent_jobs

        jobs = get_recent_jobs()
        self.assertGreater(len(jobs), 0)
        for j in jobs:
            for key in j:
                self.assertFalse(
                    key.startswith("_"),
                    f"Private field {key!r} must not be returned by get_recent_jobs()",
                )

    def test_retry_fn_stored_internally(self):
        """_retry_fn must be stored in the internal entry even though it's
        not returned by get_recent_jobs()."""
        _submit_and_wait(lambda: None, kind="test", label="has_retry_fn")

        from orivellum.api.executor import _jobs, _jobs_lock

        with _jobs_lock:
            entries = list(_jobs)
        self.assertEqual(len(entries), 1)
        # The internal entry must have _retry_fn set
        self.assertIn("_retry_fn", entries[0])
        self.assertIsNotNone(entries[0]["_retry_fn"])


# ── TestRetryJob ──────────────────────────────────────────────────────────────


class TestRetryJob(unittest.TestCase):
    """retry_job() re-submits a failed job and creates a new entry."""

    def setUp(self):
        _reset_jobs()

    def _make_failed_job(self, label="failing_job"):
        """Submit a job that always fails and return its id."""

        def _bad():
            raise RuntimeError("intentional test error")

        from orivellum.api.executor import _tracked_submit

        future = _tracked_submit(_bad, kind="test", label=label)
        try:
            future.result(timeout=2.0)
        except RuntimeError:
            pass

        from orivellum.api.executor import get_recent_jobs

        jobs = get_recent_jobs()
        failed = [j for j in jobs if j["state"] == "failed" and j["label"] == label]
        self.assertEqual(len(failed), 1, "Expected exactly one failed job")
        return failed[0]["id"]

    def test_retry_creates_new_entry(self):
        job_id = self._make_failed_job()

        from orivellum.api.executor import get_recent_jobs, retry_job

        retry_future = retry_job(job_id)
        # The retry itself will also fail (same fn) — consume the exception
        try:
            retry_future.result(timeout=2.0)
        except RuntimeError:
            pass

        jobs = get_recent_jobs()
        retry_entries = [j for j in jobs if "(retry)" in j["label"]]
        self.assertGreaterEqual(len(retry_entries), 1, "Retry must create a new job entry")

    def test_retry_raises_key_error_for_unknown_id(self):
        from orivellum.api.executor import retry_job

        with self.assertRaises(KeyError):
            retry_job("nonexistent-id-00000000-0000-0000-0000-000000000000")

    def test_retry_raises_value_error_for_done_job(self):
        _submit_and_wait(lambda: None, kind="test", label="done_job")

        from orivellum.api.executor import get_recent_jobs, retry_job

        done = [j for j in get_recent_jobs() if j["state"] == "done"]
        self.assertEqual(len(done), 1)

        with self.assertRaises(ValueError):
            retry_job(done[0]["id"])

    def test_retry_raises_runtime_error_when_no_callable_stored(self):
        """Simulates a job entry that predates retry support (no _retry_fn)."""
        import time as _time

        from orivellum.api.executor import _jobs, _jobs_lock, retry_job

        entry = {
            "id": "no-callable-id",
            "kind": "test",
            "label": "legacy_job",
            "state": "failed",
            "started_at": _time.time(),
            "finished_at": _time.time(),
            "error": "old error",
            "_retry_fn": None,  # explicitly absent
            "_retry_args": (),
            "_retry_kwargs": {},
        }
        with _jobs_lock:
            _jobs.append(entry)

        with self.assertRaises(RuntimeError):
            retry_job("no-callable-id")

    def test_retry_preserves_kind(self):
        job_id = self._make_failed_job(label="typed_job")

        from orivellum.api.executor import _jobs, _jobs_lock, get_recent_jobs, retry_job

        # Peek at the internal entry to confirm kind
        with _jobs_lock:
            orig = next(j for j in _jobs if j["id"] == job_id)
        expected_kind = orig["kind"]

        future = retry_job(job_id)
        try:
            future.result(timeout=2.0)
        except RuntimeError:
            pass

        jobs = get_recent_jobs()
        retry_entries = [j for j in jobs if "(retry)" in j["label"]]
        self.assertGreaterEqual(len(retry_entries), 1)
        self.assertEqual(retry_entries[0]["kind"], expected_kind)


# ── TestRetryEndpoint ─────────────────────────────────────────────────────────


class TestRetryEndpoint(unittest.TestCase):
    """POST /api/system/jobs/{id}/retry returns expected HTTP status codes."""

    def setUp(self):
        _reset_jobs()

    def _client(self):
        import tempfile

        from fastapi.testclient import TestClient

        from orivellum.api import _deps
        from orivellum.configuration.config import OrivellumConfig
        from orivellum.database.db import OrivellumDB
        from tests.conftest import AUTH_HEADERS

        with tempfile.TemporaryDirectory() as tmp:
            cfg = OrivellumConfig(data_dir=tmp)
            db = OrivellumDB.open(cfg.db_path)
            _deps.init(db=db, cfg=cfg)
            from orivellum.api.app import create_app

            app = create_app()
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            yield client

    def test_retry_unknown_id_returns_404(self):
        import tempfile

        from fastapi.testclient import TestClient

        from orivellum.api import _deps
        from orivellum.configuration.config import OrivellumConfig
        from orivellum.database.db import OrivellumDB
        from tests.conftest import AUTH_HEADERS

        with tempfile.TemporaryDirectory() as tmp:
            cfg = OrivellumConfig(data_dir=tmp)
            db = OrivellumDB.open(cfg.db_path)
            _deps.init(db=db, cfg=cfg)
            from orivellum.api.app import create_app

            app = create_app()
            client = TestClient(app, raise_server_exceptions=False, headers=AUTH_HEADERS)
            r = client.post("/api/system/jobs/no-such-id/retry")
            self.assertEqual(r.status_code, 404)

    def test_retry_done_job_returns_409(self):
        import tempfile

        from fastapi.testclient import TestClient

        from orivellum.api import _deps
        from orivellum.configuration.config import OrivellumConfig
        from orivellum.database.db import OrivellumDB
        from tests.conftest import AUTH_HEADERS

        with tempfile.TemporaryDirectory() as tmp:
            cfg = OrivellumConfig(data_dir=tmp)
            db = OrivellumDB.open(cfg.db_path)
            _deps.init(db=db, cfg=cfg)

            # Submit a successful job so we have a "done" entry
            _submit_and_wait(lambda: None, kind="test", label="done_for_endpoint")

            from orivellum.api.executor import get_recent_jobs

            done = [j for j in get_recent_jobs() if j["state"] == "done"]
            self.assertGreater(len(done), 0)
            done_id = done[0]["id"]

            from orivellum.api.app import create_app

            app = create_app()
            client = TestClient(app, raise_server_exceptions=False, headers=AUTH_HEADERS)
            r = client.post(f"/api/system/jobs/{done_id}/retry")
            self.assertEqual(r.status_code, 409)

    def test_retry_failed_job_returns_200(self):
        import tempfile

        from fastapi.testclient import TestClient

        from orivellum.api import _deps
        from orivellum.api.executor import _tracked_submit
        from orivellum.configuration.config import OrivellumConfig
        from orivellum.database.db import OrivellumDB
        from tests.conftest import AUTH_HEADERS

        with tempfile.TemporaryDirectory() as tmp:
            cfg = OrivellumConfig(data_dir=tmp)
            db = OrivellumDB.open(cfg.db_path)
            _deps.init(db=db, cfg=cfg)

            def _bad():
                raise RuntimeError("test failure for 200 retry")

            future = _tracked_submit(_bad, kind="test", label="fail_for_200")
            try:
                future.result(timeout=2.0)
            except RuntimeError:
                pass

            from orivellum.api.executor import get_recent_jobs

            failed = [j for j in get_recent_jobs() if j["state"] == "failed"]
            self.assertGreater(len(failed), 0)
            job_id = failed[0]["id"]

            from orivellum.api.app import create_app

            app = create_app()
            client = TestClient(app, raise_server_exceptions=False, headers=AUTH_HEADERS)
            r = client.post(f"/api/system/jobs/{job_id}/retry")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertTrue(body.get("ok"))


if __name__ == "__main__":
    unittest.main()
