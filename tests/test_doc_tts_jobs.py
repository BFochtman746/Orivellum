"""Document-TTS job registry pruning.

The audiobook narration registry (_doc_tts_jobs in routes/studio.py) must not
grow without bound on a long-running server: only the newest
_MAX_DOC_TTS_JOBS *terminal* jobs are kept, running/cancelling jobs are never
evicted (their worker thread still writes into those entries), and every
terminal transition records finished_at so eviction order is well-defined.

No TTS engine is ever invoked — registry tests manipulate the module-level
job dict directly; the worker test short-circuits via a pre-set cancel event.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("SESSION_SECRET", "test-orivellum-api-key-12345")

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


class _DocTTSTestBase(unittest.TestCase):
    """Shared setup: temp app + clean doc-TTS job registry."""

    def setUp(self):
        from orivellum.api import _deps
        from orivellum.api.routes import studio as studio_mod

        self.studio = studio_mod
        # Save the process-global dependency container BEFORE re-initing it so
        # teardown can hand the previous DB/config back to whatever test file
        # runs next (closing our temp DB must never poison other suites).
        self._prev_db = _deps._DB
        self._prev_cfg = _deps._CFG
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmp.name)
        self.client = TestClient(self.app, raise_server_exceptions=False, headers=AUTH_HEADERS)
        with self.studio._doc_tts_jobs_lock:
            self._saved_jobs = dict(self.studio._doc_tts_jobs)
            self.studio._doc_tts_jobs.clear()

    def tearDown(self):
        from orivellum.api import _deps

        with self.studio._doc_tts_jobs_lock:
            self.studio._doc_tts_jobs.clear()
            self.studio._doc_tts_jobs.update(self._saved_jobs)
        self.db.close()
        _deps._DB = self._prev_db
        _deps._CFG = self._prev_cfg
        self._tmp.cleanup()

    # ── helpers ────────────────────────────────────────────────────────────────

    def _seed_job(
        self,
        jid: str,
        state: str,
        finished_at: float | None,
        doc_id: str = "doc-1",
        started_at: float | None = None,
    ) -> None:
        self.studio._doc_tts_jobs[jid] = {
            "state": state,
            "doc_id": doc_id,
            "doc_title": "Test Doc",
            "started_at": started_at if started_at is not None else time.time(),
            "segments_done": 0,
            "cached_segments": 0,
            "total_segments": 3,
            "cancel": threading.Event(),
            "mp3_path": None,
            "filename": None,
            "error": None,
            **({"finished_at": finished_at} if finished_at is not None else {}),
        }


class TestActiveDocTTSJobs(_DocTTSTestBase):
    """GET /studio/tts/document/active — re-attach discovery for a re-opened
    Studio. A document render deliberately survives navigation away from the
    page; this endpoint is how the UI finds it again."""

    def test_lists_only_non_terminal_jobs_newest_first(self):
        base = time.time()
        with self.studio._doc_tts_jobs_lock:
            self._seed_job("old-run", "running", None, doc_id="doc-a", started_at=base - 60)
            self._seed_job("new-run", "running", None, doc_id="doc-b", started_at=base)
            self._seed_job("halfway", "cancelling", None, doc_id="doc-c", started_at=base - 30)
            self._seed_job("finished", "done", base, doc_id="doc-d")
            self._seed_job("crashed", "failed", base, doc_id="doc-e")
        resp = self.client.get("/api/studio/tts/document/active")
        self.assertEqual(resp.status_code, 200)
        jobs = resp.json()["jobs"]
        self.assertEqual([j["job_id"] for j in jobs], ["new-run", "halfway", "old-run"])
        # Re-attach needs the doc to match on and the progress to show —
        # segments done/total and the reused-segment count.
        mine = jobs[0]
        self.assertEqual(mine["doc_id"], "doc-b")
        self.assertIn("segments_done", mine)
        self.assertIn("total_segments", mine)
        self.assertIn("cached_segments", mine)
        # The threading.Event must never be serialised.
        self.assertNotIn("cancel", mine)

    def test_empty_registry_returns_empty_list(self):
        resp = self.client.get("/api/studio/tts/document/active")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["jobs"], [])

    def test_start_conflicts_with_running_render_for_same_document(self):
        """POST /studio/tts/document must refuse a duplicate render for a
        document that is already rendering and hand back the live job_id so
        the client re-attaches (remount discovery racing a Generate click)."""
        with self.studio._doc_tts_jobs_lock:
            self._seed_job("live", "running", None, doc_id="doc-1")
        resp = self.client.post(
            "/api/studio/tts/document",
            json={"doc_id": "doc-1", "voice": "af_heart"},
        )
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["detail"]["job_id"], "live")

    def test_start_conflict_ignores_terminal_and_other_doc_jobs(self):
        """Terminal jobs for the same doc, or running jobs for OTHER docs,
        must not block a new render — only 404/422 from the missing test doc
        should surface (never a 409)."""
        with self.studio._doc_tts_jobs_lock:
            self._seed_job("old-done", "done", time.time(), doc_id="doc-1")
            self._seed_job("other-live", "running", None, doc_id="doc-2")
        resp = self.client.post(
            "/api/studio/tts/document",
            json={"doc_id": "doc-1", "voice": "af_heart"},
        )
        self.assertNotEqual(resp.status_code, 409)


class TestDocTTSJobRegistry(_DocTTSTestBase):
    def test_cancel_sets_cancelling_and_survives_prune(self):
        """A cancelled-but-not-yet-terminal job must NOT be pruned: the worker
        still needs the entry to write its terminal state into."""
        self._seed_job("victim", "running", None)
        resp = self.client.delete("/api/studio/tts/document/victim")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["state"], "cancelling")

        # Flood the registry with finished jobs so pruning definitely triggers.
        base = time.time()
        with self.studio._doc_tts_jobs_lock:
            for i in range(self.studio._MAX_DOC_TTS_JOBS + 10):
                self._seed_job(f"done-{i}", "done", base + i)
            self.studio._prune_doc_tts_jobs()
            self.assertIn(
                "victim",
                self.studio._doc_tts_jobs,
                "cancelling job was pruned before the worker finished",
            )
            self.assertEqual(self.studio._doc_tts_jobs["victim"]["state"], "cancelling")
            self.assertTrue(self.studio._doc_tts_jobs["victim"]["cancel"].is_set())

    def test_cancelled_job_prunable_only_after_terminal_state(self):
        self._seed_job("victim", "cancelling", None)
        base = time.time()
        with self.studio._doc_tts_jobs_lock:
            for i in range(self.studio._MAX_DOC_TTS_JOBS + 5):
                self._seed_job(f"done-{i}", "done", base + 1000 + i)
            self.studio._prune_doc_tts_jobs()
            self.assertIn("victim", self.studio._doc_tts_jobs)
            # Worker writes the terminal state with an OLD finished_at —
            # now it is the oldest finished job and eligible for eviction.
            self.studio._doc_tts_jobs["victim"].update({"state": "cancelled", "finished_at": base})
            self.studio._prune_doc_tts_jobs()
            self.assertNotIn("victim", self.studio._doc_tts_jobs)

    def test_running_jobs_never_pruned(self):
        base = time.time()
        with self.studio._doc_tts_jobs_lock:
            for i in range(5):
                self._seed_job(f"run-{i}", "running", None)
            for i in range(self.studio._MAX_DOC_TTS_JOBS + 10):
                self._seed_job(f"done-{i}", "done", base + i)
            self.studio._prune_doc_tts_jobs()
            for i in range(5):
                self.assertIn(f"run-{i}", self.studio._doc_tts_jobs)

    def test_prune_evicts_oldest_finished_by_finished_at(self):
        cap = self.studio._MAX_DOC_TTS_JOBS
        base = time.time()
        with self.studio._doc_tts_jobs_lock:
            # Insert in reversed order so dict order != finished_at order.
            for i in reversed(range(cap + 7)):
                self._seed_job(f"job-{i}", "done", base + i)
            self.studio._prune_doc_tts_jobs()
            remaining = set(self.studio._doc_tts_jobs)
            self.assertEqual(len(remaining), cap)
            # The 7 with the SMALLEST finished_at are gone; newest survive.
            for i in range(7):
                self.assertNotIn(f"job-{i}", remaining)
            for i in range(7, cap + 7):
                self.assertIn(f"job-{i}", remaining)

    def test_prune_covers_all_terminal_states(self):
        """ "done", "error", "failed", and "cancelled" all count as finished;
        the worker uses "failed", the pre-flight clone gate uses "error"."""
        cap = self.studio._MAX_DOC_TTS_JOBS
        base = time.time()
        states = ["done", "error", "failed", "cancelled"]
        with self.studio._doc_tts_jobs_lock:
            for i in range(cap + 4):
                self._seed_job(f"t-{i}", states[i % 4], base + i)
            self.studio._prune_doc_tts_jobs()
            self.assertEqual(len(self.studio._doc_tts_jobs), cap)
            for i in range(4):  # the four oldest are evicted regardless of state
                self.assertNotIn(f"t-{i}", self.studio._doc_tts_jobs)

    def test_prune_treats_missing_finished_at_as_oldest(self):
        """A terminal job that somehow lacks finished_at sorts first (0.0) and
        gets evicted before jobs with real timestamps — never crashes."""
        cap = self.studio._MAX_DOC_TTS_JOBS
        base = time.time()
        with self.studio._doc_tts_jobs_lock:
            self._seed_job("no-ts", "failed", None)
            for i in range(cap):
                self._seed_job(f"done-{i}", "done", base + i)
            self.studio._prune_doc_tts_jobs()
            self.assertNotIn("no-ts", self.studio._doc_tts_jobs)
            self.assertEqual(len(self.studio._doc_tts_jobs), cap)

    def test_burst_of_jobs_bounded_without_new_submission(self):
        """More jobs than the cap all running at once, then ALL finishing with
        no later submission: the registry must still shrink to the cap. This is
        why terminal transitions go through _finish_doc_tts_job (which prunes)
        rather than relying on registration-time pruning alone."""
        cap = self.studio._MAX_DOC_TTS_JOBS
        n = cap + 15
        for i in range(n):
            self._seed_job(f"burst-{i}", "running", None)
        # Finish every job through the terminal helper — mixed outcomes.
        states = ["done", "failed", "cancelled", "error"]
        for i in range(n):
            self.studio._finish_doc_tts_job(f"burst-{i}", states[i % 4])
        with self.studio._doc_tts_jobs_lock:
            self.assertEqual(len(self.studio._doc_tts_jobs), cap)
            # Oldest finishers evicted; newest survive with finished_at set.
            for i in range(n - cap, n):
                job = self.studio._doc_tts_jobs[f"burst-{i}"]
                self.assertIn(job["state"], self.studio._DOC_TTS_TERMINAL)
                self.assertIsNotNone(job.get("finished_at"))
            for i in range(n - cap):
                self.assertNotIn(f"burst-{i}", self.studio._doc_tts_jobs)

    def test_finish_helper_rejects_non_terminal_state(self):
        self._seed_job("j1", "running", None)
        with self.assertRaises(AssertionError):
            self.studio._finish_doc_tts_job("j1", "running")

    def test_cancel_cannot_overwrite_terminal_state(self):
        """Cancel racing job completion: once the worker has written a terminal
        state, DELETE must NOT overwrite it with 'cancelling' (the worker has
        exited — such an entry would never become prunable again)."""
        base = time.time()
        for state in ("done", "failed", "error", "cancelled"):
            jid = f"finished-{state}"
            self._seed_job(jid, state, base)
            resp = self.client.delete(f"/api/studio/tts/document/{jid}")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["state"], state)
            with self.studio._doc_tts_jobs_lock:
                job = self.studio._doc_tts_jobs[jid]
                self.assertEqual(job["state"], state)
                self.assertFalse(job["cancel"].is_set())
                # Still terminal → still prunable.
                self.assertIn(job["state"], self.studio._DOC_TTS_TERMINAL)

    def test_cancel_unknown_job_404(self):
        resp = self.client.delete("/api/studio/tts/document/nope")
        self.assertEqual(resp.status_code, 404)

    def test_worker_preset_cancel_writes_finished_at(self):
        """If cancel is set before the worker's first segment, it records the
        terminal 'cancelled' state WITH finished_at — making it prunable."""
        from orivellum.api._deps import get_config

        self._seed_job("pre-cancelled", "cancelling", None)
        self.studio._doc_tts_jobs["pre-cancelled"]["cancel"].set()

        body = self.studio.DocumentTTSRequest(doc_id="doc-x", voice="af_heart")
        self.studio._run_doc_tts_job(
            "pre-cancelled",
            body,
            ["hello world"],
            "hello world",
            {"title": "Test Doc"},
            self.db,
            get_config(),
        )
        job = self.studio._doc_tts_jobs["pre-cancelled"]
        self.assertEqual(job["state"], "cancelled")
        self.assertIsNotNone(job.get("finished_at"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
