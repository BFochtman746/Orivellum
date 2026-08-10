"""Browser-notification feed: ring buffer semantics + poll endpoint.

Replaces the retired mobile push channel — workers emit() events and the PWA
polls GET /api/system/notifications. No browsers or engines involved here.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SESSION_SECRET", "test-orivellum-api-key-12345")

from fastapi.testclient import TestClient

from orivellum.api import notifications as notif
from tests.conftest import AUTH_HEADERS


class NotificationRingTests(unittest.TestCase):
    def setUp(self):
        notif._reset_for_tests()

    def tearDown(self):
        notif._reset_for_tests()

    def test_emit_and_list_after(self):
        notif.emit("document_ready", "Document ready", "Doc A", url="/library/a")
        notif.emit("audiobook_ready", "Audiobook ready", "Book B", url="/studio")
        events, latest = notif.list_after(0)
        self.assertEqual(latest, 2)
        self.assertEqual([e["id"] for e in events], [1, 2])
        self.assertEqual(events[0]["kind"], "document_ready")
        self.assertEqual(events[1]["url"], "/studio")

        events, latest = notif.list_after(1)
        self.assertEqual([e["id"] for e in events], [2])
        self.assertEqual(latest, 2)

    def test_latest_id_returned_even_when_no_new_events(self):
        notif.emit("document_ready", "t", "b")
        events, latest = notif.list_after(99)
        self.assertEqual(events, [])
        self.assertEqual(latest, 1)

    def test_ring_evicts_oldest_beyond_cap(self):
        for i in range(notif._MAX_EVENTS + 25):
            notif.emit("document_ready", f"t{i}", "")
        events, latest = notif.list_after(0)
        self.assertEqual(len(events), notif._MAX_EVENTS)
        self.assertEqual(latest, notif._MAX_EVENTS + 25)
        # Oldest surviving event is exactly latest - cap + 1.
        self.assertEqual(events[0]["id"], latest - notif._MAX_EVENTS + 1)

    def test_emit_truncates_long_fields(self):
        notif.emit("k", "T" * 500, "B" * 5000, url="/x" + "y" * 5000)
        events, _ = notif.list_after(0)
        self.assertEqual(len(events[0]["title"]), 120)
        self.assertEqual(len(events[0]["body"]), 300)
        self.assertEqual(len(events[0]["url"]), 500)


class NotificationEndpointTests(unittest.TestCase):
    def setUp(self):
        notif._reset_for_tests()
        from orivellum.api import _deps
        from orivellum.api.app import app
        from orivellum.configuration.config import OrivellumConfig
        from orivellum.database.db import OrivellumDB

        self._prev_db = _deps._DB
        self._prev_cfg = _deps._CFG
        self._tmp = tempfile.TemporaryDirectory()
        cfg = OrivellumConfig(data_dir=self._tmp.name)
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        _deps.init(db=self.db, cfg=cfg)
        self.client = TestClient(app, raise_server_exceptions=False, headers=AUTH_HEADERS)

    def tearDown(self):
        from orivellum.api import _deps

        self.db.close()
        _deps._DB = self._prev_db
        _deps._CFG = self._prev_cfg
        self._tmp.cleanup()
        notif._reset_for_tests()

    def test_requires_auth(self):
        bare = TestClient(self.client.app, raise_server_exceptions=False)
        resp = bare.get("/api/system/notifications")
        self.assertEqual(resp.status_code, 401)

    def test_returns_boot_id_and_events_after_cursor(self):
        notif.emit("document_ready", "Document ready", "Doc A", url="/library/a")
        notif.emit("audiobook_ready", "Audiobook ready", "Book B", url="/studio")

        resp = self.client.get("/api/system/notifications")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["boot_id"], notif.BOOT_ID)
        self.assertEqual(data["latest_id"], 2)
        self.assertEqual(len(data["notifications"]), 2)

        resp = self.client.get("/api/system/notifications?after=2")
        data = resp.json()
        self.assertEqual(data["notifications"], [])
        self.assertEqual(data["latest_id"], 2)

    def test_pipeline_ready_emits_document_ready(self):
        """Real call-site test: process_document emits into this feed the
        moment a document reaches 'ready' (extract/chunk/harvest mocked; the
        shared executor is stubbed so no background thread outlives the test)."""
        from unittest.mock import MagicMock, patch

        from orivellum.capabilities import pipeline
        from orivellum.capabilities.extraction import ExtractionResult

        created = self.db.create_document(
            title="My Doc", kind="text", work_id=None, sha256="a" * 64
        )
        doc_id = created["id"] if isinstance(created, dict) else created
        src = Path(self._tmp.name) / "mydoc.txt"
        src.write_text("hello world", encoding="utf-8")

        result = ExtractionResult(kind="text", full_text="hello world " * 20, word_count=40)
        stub_executor = MagicMock()
        with (
            patch.object(pipeline, "extract", return_value=result),
            patch.object(pipeline, "chunk_and_store"),
            patch.object(pipeline, "harvest"),
            patch("orivellum.api.executor.get_executor", return_value=stub_executor),
        ):
            pipeline.process_document(doc_id, str(src), "text", None, "My Doc", self.db)

        self.assertEqual(self.db.get_document(doc_id)["readiness"], "ready")
        data = self.client.get("/api/system/notifications").json()
        kinds = [(n["kind"], n["url"]) for n in data["notifications"]]
        self.assertIn(("document_ready", f"/library/{doc_id}"), kinds)
        ready = next(n for n in data["notifications"] if n["kind"] == "document_ready")
        self.assertIn("My Doc", ready["body"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
