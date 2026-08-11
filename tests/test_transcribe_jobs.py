"""Transcription upload limits + job-registry edge cases.

Covers the async transcription backend (POST /api/studio/transcribe):
- over-limit upload returns 413 and leaves no partial temp files
- wrong magic-bytes returns 415 with the spooled file cleaned up
- cancel-then-prune keeps the job entry until the worker writes a terminal state
- pruning evicts the oldest finished jobs by finished_at
- mid-run cancel (during extract, and racing in just before the second cancel
  check) always ends 'cancelled', never saves to the library, cleans the spool

No transcription engine is ever invoked: the size/MIME failures happen before a
job is created, and registry tests manipulate the module-level job dict directly.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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


class _TranscribeTestBase(unittest.TestCase):
    """Shared setup: temp app + clean job registry + captured temp spool dirs."""

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
        with self.studio._transcribe_jobs_lock:
            self._saved_jobs = dict(self.studio._transcribe_jobs)
            self.studio._transcribe_jobs.clear()

    def tearDown(self):
        from orivellum.api import _deps

        with self.studio._transcribe_jobs_lock:
            self.studio._transcribe_jobs.clear()
            self.studio._transcribe_jobs.update(self._saved_jobs)
        self.db.close()
        _deps._DB = self._prev_db
        _deps._CFG = self._prev_cfg
        self._tmp.cleanup()

    # ── helpers ────────────────────────────────────────────────────────────────

    def _upload(self, content: bytes, name: str = "clip.mp3"):
        """POST the bytes while forcing the route's spool dir under our temp
        root so leftover partial files are detectable deterministically.

        Patches ONLY the studio module's view of tempfile (via a delegating
        shim), never the global tempfile module — unrelated request machinery
        keeps its normal behavior."""
        spool_root = Path(self._tmp.name) / "spool"
        spool_root.mkdir(exist_ok=True)
        created: list[Path] = []
        real_tempfile = tempfile

        class _TempfileShim:
            def __getattr__(self, attr):
                return getattr(real_tempfile, attr)

            @staticmethod
            def mkdtemp(*args, **kwargs):
                kwargs["dir"] = str(spool_root)
                d = real_tempfile.mkdtemp(*args, **kwargs)
                created.append(Path(d))
                return d

        with patch.object(self.studio, "tempfile", _TempfileShim()):
            resp = self.client.post(
                "/api/studio/transcribe",
                files={"file": (name, content, "audio/mpeg")},
            )
        return resp, spool_root, created

    def _seed_job(self, jid: str, state: str, finished_at: float | None) -> None:
        self.studio._transcribe_jobs[jid] = {
            "state": state,
            "stage": state,
            "filename": f"{jid}.mp3",
            "cancel": threading.Event(),
            "text": None,
            "engine": None,
            "word_count": None,
            "doc_id": None,
            "error": None,
            **({"finished_at": finished_at} if finished_at is not None else {}),
        }


# ---------------------------------------------------------------------------
# Upload limits
# ---------------------------------------------------------------------------


class TestTranscribeUploadLimits(_TranscribeTestBase):
    def test_over_limit_returns_413_and_cleans_partial_file(self):
        # Shrink the cap so the test doesn't stream 500 MB.
        with patch.object(self.studio, "_MAX_TRANSCRIBE_BYTES", 4096):
            resp, spool_root, created = self._upload(b"ID3" + b"\x00" * 8192)
        self.assertEqual(resp.status_code, 413, resp.text)
        self.assertIn("too large", resp.json()["detail"].lower())
        # The spool dir was created, then fully removed — no partial bytes left.
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists(), "partial temp dir must be deleted on 413")
        self.assertEqual(list(spool_root.iterdir()), [])
        # No job was registered for the failed upload.
        with self.studio._transcribe_jobs_lock:
            self.assertEqual(self.studio._transcribe_jobs, {})

    def test_wrong_magic_bytes_returns_415_and_cleans_up(self):
        # .mp3 extension but content that matches neither ID3 nor bare-MPEG magic.
        resp, spool_root, created = self._upload(b"definitely not audio" * 100)
        self.assertEqual(resp.status_code, 415, resp.text)
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists(), "spooled file must be deleted on 415")
        self.assertEqual(list(spool_root.iterdir()), [])
        with self.studio._transcribe_jobs_lock:
            self.assertEqual(self.studio._transcribe_jobs, {})

    def test_unsupported_extension_returns_422_without_spooling(self):
        resp, spool_root, created = self._upload(b"ID3" + b"\x00" * 100, name="notes.txt")
        self.assertEqual(resp.status_code, 422)
        # Rejected before any temp dir is created.
        self.assertEqual(created, [])

    def test_empty_upload_returns_422_and_cleans_up(self):
        resp, spool_root, created = self._upload(b"")
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].exists())
        self.assertEqual(list(spool_root.iterdir()), [])

    def test_valid_upload_registers_job_without_touching_engines(self):
        """Happy path with the worker dispatch mocked: a signed MP3 gets a job
        entry in 'running' state and is handed to the executor — no engine,
        ffmpeg, or network is ever involved (CI runners have none)."""
        with patch("orivellum.api.executor._tracked_submit") as submit:
            resp, spool_root, created = self._upload(b"ID3" + b"\x00" * 512)
        self.assertEqual(resp.status_code, 200, resp.text)
        job_id = resp.json()["job_id"]
        submit.assert_called_once()
        # Worker args: (fn, job_id, tmp_path, orig_name, save_to_library, db, cfg)
        args = submit.call_args[0]
        self.assertIs(args[0], self.studio._run_transcribe_job)
        self.assertEqual(args[1], job_id)
        with self.studio._transcribe_jobs_lock:
            job = self.studio._transcribe_jobs.get(job_id)
        self.assertIsNotNone(job)
        self.assertEqual(job["state"], "running")
        self.assertEqual(job["stage"], "queued")
        # Spooled file still on disk for the (mocked) worker to consume.
        self.assertTrue(Path(args[2]).exists())


# ---------------------------------------------------------------------------
# Job registry — cancel/prune interaction
# ---------------------------------------------------------------------------


class TestTranscribeJobRegistry(_TranscribeTestBase):
    def test_cancel_sets_cancelling_and_survives_prune(self):
        """A cancelled-but-not-yet-terminal job must NOT be pruned: the worker
        still needs the entry to write its terminal state into."""
        self._seed_job("victim", "running", None)
        resp = self.client.delete("/api/studio/transcribe/victim")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["state"], "cancelling")

        # Flood the registry with finished jobs so pruning definitely triggers.
        base = time.time()
        with self.studio._transcribe_jobs_lock:
            for i in range(self.studio._MAX_TRANSCRIBE_JOBS + 10):
                self._seed_job(f"done-{i}", "done", base + i)
            self.studio._prune_transcribe_jobs()
            self.assertIn(
                "victim",
                self.studio._transcribe_jobs,
                "cancelling job was pruned before the worker finished",
            )
            self.assertEqual(self.studio._transcribe_jobs["victim"]["state"], "cancelling")
            self.assertTrue(self.studio._transcribe_jobs["victim"]["cancel"].is_set())

    def test_cancelled_job_prunable_only_after_terminal_state(self):
        self._seed_job("victim", "cancelling", None)
        base = time.time()
        with self.studio._transcribe_jobs_lock:
            for i in range(self.studio._MAX_TRANSCRIBE_JOBS + 5):
                self._seed_job(f"done-{i}", "done", base + 1000 + i)
            self.studio._prune_transcribe_jobs()
            self.assertIn("victim", self.studio._transcribe_jobs)
            # Worker writes the terminal state with an OLD finished_at —
            # now it is the oldest finished job and eligible for eviction.
            self.studio._transcribe_jobs["victim"].update(
                {"state": "cancelled", "finished_at": base}
            )
            self.studio._prune_transcribe_jobs()
            self.assertNotIn("victim", self.studio._transcribe_jobs)

    def test_running_jobs_never_pruned(self):
        base = time.time()
        with self.studio._transcribe_jobs_lock:
            for i in range(5):
                self._seed_job(f"run-{i}", "running", None)
            for i in range(self.studio._MAX_TRANSCRIBE_JOBS + 10):
                self._seed_job(f"done-{i}", "done", base + i)
            self.studio._prune_transcribe_jobs()
            for i in range(5):
                self.assertIn(f"run-{i}", self.studio._transcribe_jobs)

    def test_prune_evicts_oldest_finished_by_finished_at(self):
        cap = self.studio._MAX_TRANSCRIBE_JOBS
        base = time.time()
        with self.studio._transcribe_jobs_lock:
            # Insert in shuffled-ish order so dict order != finished_at order.
            for i in reversed(range(cap + 7)):
                self._seed_job(f"job-{i}", "done", base + i)
            self.studio._prune_transcribe_jobs()
            remaining = set(self.studio._transcribe_jobs)
            self.assertEqual(len(remaining), cap)
            # The 7 with the SMALLEST finished_at are gone; newest survive.
            for i in range(7):
                self.assertNotIn(f"job-{i}", remaining)
            for i in range(7, cap + 7):
                self.assertIn(f"job-{i}", remaining)

    def test_prune_treats_missing_finished_at_as_oldest(self):
        """A terminal job that somehow lacks finished_at sorts first (0.0) and
        gets evicted before jobs with real timestamps — never crashes."""
        cap = self.studio._MAX_TRANSCRIBE_JOBS
        base = time.time()
        with self.studio._transcribe_jobs_lock:
            self._seed_job("no-ts", "error", None)
            for i in range(cap):
                self._seed_job(f"done-{i}", "done", base + i)
            self.studio._prune_transcribe_jobs()
            self.assertNotIn("no-ts", self.studio._transcribe_jobs)
            self.assertEqual(len(self.studio._transcribe_jobs), cap)

    def test_cancel_unknown_job_404(self):
        resp = self.client.delete("/api/studio/transcribe/nope")
        self.assertEqual(resp.status_code, 404)

    def _spool(self, name: str) -> tuple[Path, Path]:
        tmp_dir = Path(self._tmp.name) / name
        tmp_dir.mkdir()
        tmp_path = tmp_dir / "upload.mp3"
        tmp_path.write_bytes(b"ID3" + b"\x00" * 64)
        return tmp_dir, tmp_path

    @staticmethod
    def _fake_result():
        from types import SimpleNamespace

        page = SimpleNamespace(text="hello transcribed world")
        return SimpleNamespace(
            meta={"transcription": "fake-engine"},
            pages=[page],
            full_text="hello transcribed world",
            word_count=3,
        )

    def test_cancel_while_extract_running_ends_cancelled_and_never_saves(self):
        """DELETE arriving while the worker is inside extract(): the finished
        transcript must be discarded — terminal 'cancelled', no library
        document even with save_to_library=True, spool removed."""
        tmp_dir, tmp_path = self._spool("midrun-spool")
        self._seed_job("midrun", "running", None)
        extract_entered = threading.Event()
        release = threading.Event()

        def fake_extract(path, kind, db=None):
            extract_entered.set()
            if not release.wait(timeout=10):
                raise AssertionError("test never released extract()")
            return self._fake_result()

        from orivellum.api._deps import get_config

        with (
            patch("orivellum.capabilities.extraction.extract", side_effect=fake_extract),
            patch("orivellum.capabilities.persist.register_and_index") as register,
        ):
            worker = threading.Thread(
                target=self.studio._run_transcribe_job,
                args=("midrun", tmp_path, "upload.mp3", True, self.db, get_config()),
            )
            worker.start()
            try:
                self.assertTrue(extract_entered.wait(timeout=10), "worker never reached extract")
                resp = self.client.delete("/api/studio/transcribe/midrun")
                self.assertEqual(resp.status_code, 200)
                self.assertEqual(resp.json()["state"], "cancelling")
            finally:
                release.set()
                worker.join(timeout=10)
        self.assertFalse(worker.is_alive(), "worker thread did not finish")
        job = self.studio._transcribe_jobs["midrun"]
        self.assertEqual(job["state"], "cancelled")
        self.assertIsNotNone(job.get("finished_at"))
        register.assert_not_called()
        self.assertFalse(tmp_path.exists(), "worker must delete the spooled file")
        self.assertFalse(tmp_dir.exists(), "worker must remove the empty spool dir")

    def test_cancel_landing_after_transcript_before_save_discards_it(self):
        """Cancel racing in AFTER extract() returns but BEFORE the worker
        re-acquires the lock (the second cancel check): the transcript is
        never stored on the job and never saved to the library."""
        tmp_dir, tmp_path = self._spool("race-spool")
        self._seed_job("race", "running", None)

        def fake_extract(path, kind, db=None):
            # the user's cancel lands exactly as the transcript completes
            resp = self.client.delete("/api/studio/transcribe/race")
            assert resp.status_code == 200
            return self._fake_result()

        from orivellum.api._deps import get_config

        with (
            patch("orivellum.capabilities.extraction.extract", side_effect=fake_extract),
            patch("orivellum.capabilities.persist.register_and_index") as register,
        ):
            self.studio._run_transcribe_job(
                "race", tmp_path, "upload.mp3", True, self.db, get_config()
            )
        job = self.studio._transcribe_jobs["race"]
        self.assertEqual(job["state"], "cancelled")
        self.assertIsNotNone(job.get("finished_at"))
        self.assertIsNone(job["text"], "cancelled job must not keep the transcript")
        register.assert_not_called()
        self.assertFalse(tmp_path.exists())
        self.assertFalse(tmp_dir.exists())

    def test_worker_honors_preset_cancel_and_cleans_tmp(self):
        """If cancel is set before the worker starts, it writes the terminal
        'cancelled' state and removes the spooled file without transcribing."""
        tmp_dir = Path(self._tmp.name) / "worker-spool"
        tmp_dir.mkdir()
        tmp_path = tmp_dir / "upload.mp3"
        tmp_path.write_bytes(b"ID3" + b"\x00" * 64)
        self._seed_job("pre-cancelled", "cancelling", None)
        self.studio._transcribe_jobs["pre-cancelled"]["cancel"].set()

        from orivellum.api._deps import get_config

        self.studio._run_transcribe_job(
            "pre-cancelled",
            tmp_path,
            "upload.mp3",
            False,
            self.db,
            get_config(),
        )
        job = self.studio._transcribe_jobs["pre-cancelled"]
        self.assertEqual(job["state"], "cancelled")
        self.assertIsNotNone(job.get("finished_at"))
        self.assertFalse(tmp_path.exists(), "worker must delete the spooled file")
        self.assertFalse(tmp_dir.exists(), "worker must remove the empty spool dir")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
