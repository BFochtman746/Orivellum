"""Task: prevent two extraction runs from corrupting the same document.

A single in-process reservation registry (pipeline.try_reserve_extraction /
release_extraction) is shared by every entry point that reaches
process_document.  Coverage:

  - two simultaneous process_document calls: exactly ONE runs the pipeline
  - the reservation is released after success, error and exception paths
  - ownership transfer: a pre-reserved token is released by process_document
  - token safety: a stale token cannot free a newer reservation
  - POST /library/{id}/reprocess returns 409 while a reservation is held
  - POST /studio/transcribe/library/{id} returns 409 while a reservation is held
  - reprocess route releases the reservation so a later reprocess succeeds
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from orivellum.capabilities import pipeline
from tests.conftest import AUTH_HEADERS


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


def _fake_result():
    from orivellum.capabilities.extraction import ExtractionResult

    return ExtractionResult(kind="text", full_text="hello world " * 20, word_count=40)


class ReservationRegistryTest(unittest.TestCase):
    """Pure registry semantics — no app needed."""

    def test_reserve_release_cycle(self):
        token = pipeline.try_reserve_extraction("doc-a")
        self.assertIsNotNone(token)
        self.assertTrue(pipeline.is_extraction_reserved("doc-a"))
        # Second claim while held fails
        self.assertIsNone(pipeline.try_reserve_extraction("doc-a"))
        pipeline.release_extraction("doc-a", token)
        self.assertFalse(pipeline.is_extraction_reserved("doc-a"))

    def test_stale_token_cannot_free_new_reservation(self):
        old = pipeline.try_reserve_extraction("doc-b")
        pipeline.release_extraction("doc-b", old)
        new = pipeline.try_reserve_extraction("doc-b")
        # Releasing with the OLD token must not free the NEW reservation
        pipeline.release_extraction("doc-b", old)
        self.assertTrue(pipeline.is_extraction_reserved("doc-b"))
        pipeline.release_extraction("doc-b", new)
        self.assertFalse(pipeline.is_extraction_reserved("doc-b"))

    def test_release_is_idempotent(self):
        token = pipeline.try_reserve_extraction("doc-c")
        pipeline.release_extraction("doc-c", token)
        pipeline.release_extraction("doc-c", token)  # no error
        self.assertFalse(pipeline.is_extraction_reserved("doc-c"))


class ConcurrentPipelineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db, self.cfg = _make_app(self._tmp.name)
        lib = Path(self.cfg.data_dir) / "library"
        lib.mkdir(parents=True, exist_ok=True)
        self.file = lib / "note.txt"
        self.file.write_text("hello world " * 20)
        self.doc = self.db.create_document(
            title="Note", kind="text", content_path="note.txt", source=str(self.file)
        )

    def tearDown(self):
        self._tmp.cleanup()

    def test_two_simultaneous_runs_only_one_executes(self):
        """The acceptance test: two concurrent process_document calls on the
        same doc — exactly one runs the pipeline, the other returns without
        touching anything."""
        started = threading.Event()
        proceed = threading.Event()
        calls: list[float] = []

        def _slow_extract(path, kind, db=None):
            calls.append(time.time())
            started.set()
            proceed.wait(timeout=5)
            return _fake_result()

        def _run():
            pipeline.process_document(
                doc_id=self.doc["id"],
                file_path=str(self.file),
                kind="text",
                work_id=None,
                title="Note",
                db=self.db,
            )

        with mock.patch.object(pipeline, "extract", side_effect=_slow_extract):
            t1 = threading.Thread(target=_run)
            t1.start()
            self.assertTrue(started.wait(timeout=5), "first run never reached extract()")
            # Second run while the first holds the reservation: must be a no-op
            t2 = threading.Thread(target=_run)
            t2.start()
            t2.join(timeout=5)
            self.assertFalse(t2.is_alive(), "duplicate run should return immediately")
            self.assertEqual(len(calls), 1, "second run must NOT reach extract()")
            proceed.set()
            t1.join(timeout=10)

        self.assertEqual(len(calls), 1)
        # Reservation released after the surviving run finished
        self.assertFalse(pipeline.is_extraction_reserved(self.doc["id"]))
        # And the surviving run completed normally
        doc = self.db.get_document(self.doc["id"])
        self.assertEqual(doc["readiness"], "ready")

    def test_reservation_released_on_pipeline_exception(self):
        with mock.patch.object(pipeline, "extract", side_effect=RuntimeError("boom")):
            pipeline.process_document(
                doc_id=self.doc["id"],
                file_path=str(self.file),
                kind="text",
                work_id=None,
                title="Note",
                db=self.db,
            )
        self.assertFalse(pipeline.is_extraction_reserved(self.doc["id"]))
        doc = self.db.get_document(self.doc["id"])
        self.assertEqual(doc["readiness"], "error")

    def test_prereserved_token_ownership_transfer(self):
        token = pipeline.try_reserve_extraction(self.doc["id"])
        self.assertIsNotNone(token)
        with mock.patch.object(pipeline, "extract", return_value=_fake_result()):
            pipeline.process_document(
                doc_id=self.doc["id"],
                file_path=str(self.file),
                kind="text",
                work_id=None,
                title="Note",
                db=self.db,
                reservation_token=token,
            )
        # process_document released the transferred token
        self.assertFalse(pipeline.is_extraction_reserved(self.doc["id"]))


class ReprocessRoute409Test(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db, self.cfg = _make_app(self._tmp.name)
        self.client = TestClient(self.app, headers=AUTH_HEADERS)
        lib = Path(self.cfg.data_dir) / "library"
        lib.mkdir(parents=True, exist_ok=True)
        (lib / "note.txt").write_text("hello world " * 20)
        doc = self.db.create_document(title="Note", kind="text", content_path="note.txt")
        self.db.update_document_extracted(doc["id"], "", 0, readiness="error")
        self.doc_id = doc["id"]

    def tearDown(self):
        self._tmp.cleanup()

    def test_reprocess_409_while_reserved(self):
        token = pipeline.try_reserve_extraction(self.doc_id)
        try:
            r = self.client.post(f"/api/library/{self.doc_id}/reprocess")
            self.assertEqual(r.status_code, 409)
        finally:
            pipeline.release_extraction(self.doc_id, token)

    def test_reprocess_runs_and_releases(self):
        with mock.patch.object(pipeline, "extract", return_value=_fake_result()):
            r = self.client.post(f"/api/library/{self.doc_id}/reprocess")
            self.assertEqual(r.status_code, 200)
        # BackgroundTasks ran during the request (TestClient) — released after
        self.assertFalse(pipeline.is_extraction_reserved(self.doc_id))
        doc = self.db.get_document(self.doc_id)
        self.assertEqual(doc["readiness"], "ready")
        # A second reprocess now succeeds again (force since it's ready)
        with mock.patch.object(pipeline, "extract", return_value=_fake_result()):
            r2 = self.client.post(f"/api/library/{self.doc_id}/reprocess?force=true")
            self.assertEqual(r2.status_code, 200)
        self.assertFalse(pipeline.is_extraction_reserved(self.doc_id))


class RetranscribeRoute409Test(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db, self.cfg = _make_app(self._tmp.name)
        self.client = TestClient(self.app, headers=AUTH_HEADERS)
        from orivellum.api.routes import studio as studio_routes

        self._jobs_patch = mock.patch.object(studio_routes, "_transcribe_jobs", {})
        self._jobs_patch.start()
        lib = Path(self.cfg.data_dir) / "library"
        lib.mkdir(parents=True, exist_ok=True)
        (lib / "lecture.mp3").write_bytes(b"ID3" + b"\x00" * 32)
        doc = self.db.create_document(title="Lecture", kind="audio", content_path="lecture.mp3")
        self.db.update_document_extracted(doc["id"], "old transcript", 2, readiness="ready")
        self.doc_id = doc["id"]

    def tearDown(self):
        self._jobs_patch.stop()
        self._tmp.cleanup()

    def test_retranscribe_409_while_reserved(self):
        token = pipeline.try_reserve_extraction(self.doc_id)
        try:
            r = self.client.post(f"/api/studio/transcribe/library/{self.doc_id}")
            self.assertEqual(r.status_code, 409)
        finally:
            pipeline.release_extraction(self.doc_id, token)
        # The rejected request must not have leaked a reservation of its own
        self.assertFalse(pipeline.is_extraction_reserved(self.doc_id))


if __name__ == "__main__":
    unittest.main()
