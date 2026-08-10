"""Tests for re-transcribing an existing Library audio document
(POST /api/studio/transcribe/library/{doc_id} — Studio Transcription tool).

The extraction pipeline is never invoked — process_document is mocked.
Coverage:

  - unknown doc → 404
  - non-audio doc → 422
  - missing content_path → 400
  - stored file gone from disk → 404
  - success path: job runs pipeline, job status carries fresh text/engine/word_count/doc_id
  - transcript header "[Audio transcript: …]" is stripped from job text
  - pipeline leaves doc in error state → job errors with the doc's message
  - duplicate: second request while a job for the same doc is active → 409
"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

from orivellum.api.routes import studio as studio_routes


def _make_app(tmp: str):
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


from contextlib import contextmanager


@contextmanager
def _client(app, db, cfg):
    from orivellum.api import _deps
    with TestClient(app, headers=AUTH_HEADERS) as client:
        _deps.init(db=db, cfg=cfg)
        yield client


def _wait_for_terminal(client, job_id: str, timeout: float = 5.0) -> dict:
    """Poll the status endpoint until the job reaches a terminal state."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/studio/transcribe/{job_id}/status").json()
        if s["state"] in ("done", "error", "cancelled"):
            return s
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} never finished: {s}")


class LibraryRetranscribeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db, self.cfg = _make_app(self._tmp.name)
        # Isolate the shared in-memory job registry per test.
        self._jobs_patch = mock.patch.object(studio_routes, "_transcribe_jobs", {})
        self._jobs_patch.start()

    def tearDown(self):
        self._jobs_patch.stop()
        self._tmp.cleanup()

    def _make_audio_doc(self, with_file: bool = True, kind: str = "audio",
                        content_path: str | None = "lecture.mp3"):
        if with_file and content_path:
            lib = Path(self.cfg.data_dir) / "library"
            lib.mkdir(parents=True, exist_ok=True)
            (lib / content_path).write_bytes(b"ID3" + b"\x00" * 32)
        doc = self.db.create_document(
            title="Lecture 1", kind=kind, content_path=content_path)
        # Fresh docs default to readiness "imported" (extraction pending);
        # mark ready so the in-flight-extraction guard doesn't trip.
        self.db.update_document_extracted(
            doc["id"], "old transcript", 2, readiness="ready")
        return doc["id"]

    # ── validation ────────────────────────────────────────────────────────────

    def test_unknown_doc_404(self):
        with _client(self.app, self.db, self.cfg) as client:
            r = client.post("/api/studio/transcribe/library/nope")
            self.assertEqual(r.status_code, 404)

    def test_non_audio_doc_422(self):
        doc_id = self._make_audio_doc(kind="pdf")
        with _client(self.app, self.db, self.cfg) as client:
            r = client.post(f"/api/studio/transcribe/library/{doc_id}")
            self.assertEqual(r.status_code, 422)
            self.assertIn("audio", r.json()["detail"])

    def test_missing_content_path_400(self):
        doc_id = self._make_audio_doc(content_path=None)
        with _client(self.app, self.db, self.cfg) as client:
            r = client.post(f"/api/studio/transcribe/library/{doc_id}")
            self.assertEqual(r.status_code, 400)

    def test_missing_file_on_disk_404(self):
        doc_id = self._make_audio_doc(with_file=False)
        with _client(self.app, self.db, self.cfg) as client:
            r = client.post(f"/api/studio/transcribe/library/{doc_id}")
            # NOTE: the app's global 404 handler rewrites the detail, so only
            # the status code is asserted here.
            self.assertEqual(r.status_code, 404)

    # ── success path ──────────────────────────────────────────────────────────

    def test_retranscribe_success_updates_doc_and_job(self):
        doc_id = self._make_audio_doc()

        def fake_pipeline(doc_id, file_path, kind, work_id, title, db):
            self.assertEqual(kind, "audio")
            db.update_document_extracted(
                doc_id, "[Audio transcript: Lecture 1]\nhello fresh world", 3,
                readiness="ready")
            with db._lock:
                import json
                db._conn.execute(
                    "UPDATE documents SET meta=? WHERE id=?",
                    (json.dumps({"transcription": "faster-whisper (large-v3-turbo)"}),
                     doc_id))
                db._conn.commit()

        with mock.patch("orivellum.capabilities.pipeline.process_document",
                        side_effect=fake_pipeline):
            with _client(self.app, self.db, self.cfg) as client:
                r = client.post(f"/api/studio/transcribe/library/{doc_id}")
                self.assertEqual(r.status_code, 200)
                job_id = r.json()["job_id"]
                s = _wait_for_terminal(client, job_id)

        self.assertEqual(s["state"], "done")
        self.assertEqual(s["doc_id"], doc_id)
        # Header stripped from the job's display text
        self.assertEqual(s["text"], "hello fresh world")
        self.assertEqual(s["engine"], "faster-whisper (large-v3-turbo)")
        self.assertEqual(s["word_count"], 3)
        # Document itself carries the fresh transcript
        fresh = self.db.get_document(doc_id)
        self.assertEqual(fresh["readiness"], "ready")
        self.assertIn("hello fresh world", fresh["extracted_text"])

    def test_pipeline_error_surfaces_on_job(self):
        doc_id = self._make_audio_doc()

        def failing_pipeline(doc_id, file_path, kind, work_id, title, db):
            db.update_document_extracted(
                doc_id, "", 0, readiness="error",
                error_message="No transcription engine available")

        with mock.patch("orivellum.capabilities.pipeline.process_document",
                        side_effect=failing_pipeline):
            with _client(self.app, self.db, self.cfg) as client:
                r = client.post(f"/api/studio/transcribe/library/{doc_id}")
                self.assertEqual(r.status_code, 200)
                s = _wait_for_terminal(client, r.json()["job_id"])

        self.assertEqual(s["state"], "error")
        self.assertIn("No transcription engine", s["error"])

    def test_retranscribe_replaces_stale_knowledge(self):
        """Knowledge harvested from the OLD transcript is removed (with its
        vectors and FTS rows); human-approved items survive; knowledge from
        the NEW transcript exists afterwards."""
        doc_id = self._make_audio_doc()
        work = self.db.create_work("Lectures")
        wid = work["id"]
        old_auto = self.db.create_knowledge_item(
            wid, "fact", "the sky is green", source_doc_id=doc_id,
            review_status="ai_auto")
        kept_approved = self.db.create_knowledge_item(
            wid, "fact", "water is wet", source_doc_id=doc_id,
            review_status="approved")
        self.db.store_vector(old_auto, "knowledge", b"\x00" * 16, 4)

        def fake_pipeline(doc_id, file_path, kind, work_id, title, db):
            db.create_knowledge_item(
                wid, "fact", "the sky is blue", source_doc_id=doc_id,
                review_status="ai_auto")
            db.update_document_extracted(
                doc_id, "the sky is blue", 4, readiness="ready")
            with db._lock:
                import json
                db._conn.execute(
                    "UPDATE documents SET meta=? WHERE id=?",
                    (json.dumps({"transcription": "faster-whisper (base)"}),
                     doc_id))
                db._conn.commit()

        with mock.patch("orivellum.capabilities.pipeline.process_document",
                        side_effect=fake_pipeline):
            with _client(self.app, self.db, self.cfg) as client:
                r = client.post(f"/api/studio/transcribe/library/{doc_id}")
                self.assertEqual(r.status_code, 200)
                s = _wait_for_terminal(client, r.json()["job_id"])
        self.assertEqual(s["state"], "done")

        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT id, text FROM knowledge WHERE source_doc_id=?",
                (doc_id,)).fetchall()
            texts = {r["text"] for r in rows}
            fts = self.db._conn.execute(
                "SELECT knowledge_id FROM knowledge_fts WHERE knowledge_id=?",
                (old_auto,)).fetchall()
            vecs = self.db._conn.execute(
                "SELECT id FROM vectors WHERE object_type='knowledge' AND object_id=?",
                (old_auto,)).fetchall()
        self.assertNotIn("the sky is green", texts)   # stale auto removed
        self.assertIn("water is wet", texts)          # approved preserved
        self.assertIn("the sky is blue", texts)       # fresh harvest present
        self.assertEqual(fts, [])                     # FTS row gone
        self.assertEqual(vecs, [])                    # vector gone
        self.assertIn(kept_approved, {r["id"] for r in rows})

    def test_no_engine_metadata_only_errors(self):
        """Pipeline "succeeds" with a placeholder (no ASR engine) → job errors."""
        doc_id = self._make_audio_doc()

        def placeholder_pipeline(doc_id, file_path, kind, work_id, title, db):
            # ready, but meta has no "transcription" key — engine never ran
            db.update_document_extracted(
                doc_id, "Audio file: lecture.mp3\nNote: engine offline", 6,
                readiness="ready")

        with mock.patch("orivellum.capabilities.pipeline.process_document",
                        side_effect=placeholder_pipeline):
            with _client(self.app, self.db, self.cfg) as client:
                r = client.post(f"/api/studio/transcribe/library/{doc_id}")
                self.assertEqual(r.status_code, 200)
                s = _wait_for_terminal(client, r.json()["job_id"])

        self.assertEqual(s["state"], "error")
        self.assertIn("transcription engine", s["error"].lower())

    def test_doc_already_processing_409(self):
        """Doc in readiness 'imported' (extraction already in flight) → 409."""
        doc_id = self._make_audio_doc()
        self.db.update_document_extracted(doc_id, "", 0, readiness="imported")
        with _client(self.app, self.db, self.cfg) as client:
            r = client.post(f"/api/studio/transcribe/library/{doc_id}")
            self.assertEqual(r.status_code, 409)

    def test_cancel_before_start_leaves_doc_untouched(self):
        """Cancel that lands before the worker's destructive reset → job
        'cancelled' and the document keeps its stored transcript."""
        doc_id = self._make_audio_doc()
        self.db.update_document_extracted(
            doc_id, "original transcript", 2, readiness="ready")

        pipeline_ran = threading.Event()

        def tracked_pipeline(doc_id, file_path, kind, work_id, title, db):
            pipeline_ran.set()

        with mock.patch("orivellum.capabilities.pipeline.process_document",
                        side_effect=tracked_pipeline):
            with _client(self.app, self.db, self.cfg) as client:
                # Block the executor path: pre-set cancel before the worker
                # can run by patching _tracked_submit to set cancel first.
                real_submit = studio_routes._run_retranscribe_job

                def submit_with_cancel(job_id, doc_id_, file_path, db):
                    studio_routes._transcribe_jobs[job_id]["cancel"].set()
                    real_submit(job_id, doc_id_, file_path, db)

                with mock.patch.object(studio_routes, "_run_retranscribe_job",
                                       side_effect=submit_with_cancel):
                    r = client.post(f"/api/studio/transcribe/library/{doc_id}")
                    self.assertEqual(r.status_code, 200)
                    s = _wait_for_terminal(client, r.json()["job_id"])

        self.assertEqual(s["state"], "cancelled")
        self.assertFalse(pipeline_ran.is_set())
        fresh = self.db.get_document(doc_id)
        self.assertEqual(fresh["extracted_text"], "original transcript")
        self.assertEqual(fresh["readiness"], "ready")

    def test_duplicate_active_job_409(self):
        doc_id = self._make_audio_doc()
        release = threading.Event()

        def slow_pipeline(doc_id, file_path, kind, work_id, title, db):
            release.wait(timeout=5)
            db.update_document_extracted(doc_id, "x", 1, readiness="ready")

        with mock.patch("orivellum.capabilities.pipeline.process_document",
                        side_effect=slow_pipeline):
            with _client(self.app, self.db, self.cfg) as client:
                r1 = client.post(f"/api/studio/transcribe/library/{doc_id}")
                self.assertEqual(r1.status_code, 200)
                try:
                    r2 = client.post(f"/api/studio/transcribe/library/{doc_id}")
                    self.assertEqual(r2.status_code, 409)
                finally:
                    release.set()
                _wait_for_terminal(client, r1.json()["job_id"])


if __name__ == "__main__":
    unittest.main()
