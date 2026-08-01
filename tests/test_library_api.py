"""Endpoint-level tests for the library API.

Covers:
- GET /api/library returns warnings[] for error and no_text documents
- GET /api/library/{id} returns warnings[] for error and no_text documents
- POST /api/library/{id}/reprocess clears prior warnings before re-queuing
- POST /api/library/{id}/extract (alias) also clears warnings
"""
from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Test app factory — uses a real temp DB, bypasses background extraction
# ---------------------------------------------------------------------------

def _make_app(tmp: str):
    """Return a configured FastAPI test app wired to a temp DB."""
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_failed_doc(db, readiness: str, warning_kind: str) -> str:
    """Create a document in a failure state with one warning. Returns doc_id."""
    doc = db.create_document(title="bad.txt", kind="text", work_id=None)
    doc_id = doc["id"]
    db.update_document_extracted(doc_id, "", 0, readiness=readiness,
                                 error_message="forced failure")
    db.add_extraction_warning(doc_id, kind=warning_kind, detail="forced detail")
    return doc_id


# ---------------------------------------------------------------------------
# GET /api/library — list endpoint includes warnings for failed docs
# ---------------------------------------------------------------------------

class TestLibraryListWarnings(unittest.TestCase):

    def test_error_doc_has_warnings_in_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True)

            doc_id = _seed_failed_doc(db, "error", "file_not_found")

            resp = client.get("/api/library")
            self.assertEqual(resp.status_code, 200)
            docs = resp.json()["documents"]
            target = next((d for d in docs if d["id"] == doc_id), None)
            self.assertIsNotNone(target, "Seeded doc must appear in list")
            self.assertIn("warnings", target, "warnings key must be present")
            self.assertEqual(len(target["warnings"]), 1)
            self.assertEqual(target["warnings"][0]["kind"], "file_not_found")
            self.assertEqual(target["warnings"][0]["detail"], "forced detail")
            db.close()

    def test_no_text_doc_has_warnings_in_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True)

            doc_id = _seed_failed_doc(db, "no_text", "no_readable_text")

            resp = client.get("/api/library")
            self.assertEqual(resp.status_code, 200)
            docs = resp.json()["documents"]
            target = next((d for d in docs if d["id"] == doc_id), None)
            self.assertIsNotNone(target)
            self.assertEqual(len(target["warnings"]), 1)
            self.assertEqual(target["warnings"][0]["kind"], "no_readable_text")
            db.close()

    def test_ready_doc_has_empty_warnings_in_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True)

            doc = db.create_document(title="ok.txt", kind="text", work_id=None)
            db.update_document_extracted(doc["id"], "hello world", 2,
                                         readiness="ready")

            resp = client.get("/api/library")
            self.assertEqual(resp.status_code, 200)
            docs = resp.json()["documents"]
            target = next((d for d in docs if d["id"] == doc["id"]), None)
            self.assertIsNotNone(target)
            self.assertEqual(target.get("warnings"), [],
                             "Ready docs must have empty warnings array")
            db.close()


# ---------------------------------------------------------------------------
# GET /api/library/{id} — detail endpoint includes warnings for failed docs
# ---------------------------------------------------------------------------

class TestLibraryDetailWarnings(unittest.TestCase):

    def test_error_doc_detail_has_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True)

            doc_id = _seed_failed_doc(db, "error", "pipeline_exception")

            resp = client.get(f"/api/library/{doc_id}")
            self.assertEqual(resp.status_code, 200)
            doc = resp.json()["document"]
            self.assertIn("warnings", doc)
            self.assertEqual(len(doc["warnings"]), 1)
            self.assertEqual(doc["warnings"][0]["kind"], "pipeline_exception")
            db.close()

    def test_no_text_doc_detail_has_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True)

            doc_id = _seed_failed_doc(db, "no_text", "no_readable_text")

            resp = client.get(f"/api/library/{doc_id}")
            self.assertEqual(resp.status_code, 200)
            doc = resp.json()["document"]
            self.assertEqual(len(doc["warnings"]), 1)
            self.assertEqual(doc["warnings"][0]["kind"], "no_readable_text")
            db.close()

    def test_ready_doc_detail_has_empty_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True)

            base_doc = db.create_document(title="good.txt", kind="text", work_id=None)
            db.update_document_extracted(base_doc["id"], "content", 1, readiness="ready")

            resp = client.get(f"/api/library/{base_doc['id']}")
            self.assertEqual(resp.status_code, 200)
            doc = resp.json()["document"]
            self.assertEqual(doc.get("warnings"), [])
            db.close()


# ---------------------------------------------------------------------------
# POST /api/library/{id}/reprocess — clears prior warnings
# ---------------------------------------------------------------------------

class TestReprocessClearsWarnings(unittest.TestCase):

    def _make_file_backed_doc(self, db, tmp: str) -> tuple:
        """Create a doc with a real file so reprocess can find it."""
        p = Path(tmp) / "retry.txt"
        p.write_text("Some text content here.", encoding="utf-8")
        doc = db.create_document(
            title="retry.txt",
            source=str(p),
            kind="text",
            work_id=None,
            content_path=str(p),
        )
        doc_id = doc["id"]
        db.update_document_extracted(doc_id, "", 0, readiness="error",
                                     error_message="old failure")
        db.add_extraction_warning(doc_id, kind="file_not_found", detail="old detail")
        return doc_id, p

    def test_reprocess_clears_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True)

            doc_id, _ = self._make_file_backed_doc(db, tmp)

            # Confirm warning exists before reprocess
            self.assertEqual(len(db.get_extraction_warnings(doc_id)), 1)

            # Patch process_document so the background task doesn't run
            with patch("orivellum.api.routes.library.process_document"):
                resp = client.post(f"/api/library/{doc_id}/reprocess")

            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["ok"])

            # Warnings must be cleared
            self.assertEqual(db.get_extraction_warnings(doc_id), [],
                             "Warnings must be cleared when reprocess is called")
            db.close()

    def test_extract_alias_clears_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True)

            doc_id, _ = self._make_file_backed_doc(db, tmp)

            with patch("orivellum.api.routes.library.process_document"):
                resp = client.post(f"/api/library/{doc_id}/extract")

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(db.get_extraction_warnings(doc_id), [],
                             "extract alias must also clear prior warnings")
            db.close()


if __name__ == "__main__":
    unittest.main()
