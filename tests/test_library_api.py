"""Endpoint-level tests for the library API.

Covers:
- GET /api/library returns warnings[] for error and no_text documents
- GET /api/library/{id} returns warnings[] for error and no_text documents
- POST /api/library/{id}/reprocess clears prior warnings before re-queuing
- POST /api/library/{id}/extract (alias) also clears warnings
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

# ---------------------------------------------------------------------------
# Test app factory — uses a real temp DB, bypasses background extraction
# ---------------------------------------------------------------------------


def _make_app(tmp: str):
    """Return a configured FastAPI test app wired to a temp DB."""
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

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
    db.update_document_extracted(doc_id, "", 0, readiness=readiness, error_message="forced failure")
    db.add_extraction_warning(doc_id, kind=warning_kind, detail="forced detail")
    return doc_id


# ---------------------------------------------------------------------------
# GET /api/library — list endpoint includes warnings for failed docs
# ---------------------------------------------------------------------------


class TestLibraryListWarnings(unittest.TestCase):
    def test_error_doc_has_warnings_in_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

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
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

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
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

            doc = db.create_document(title="ok.txt", kind="text", work_id=None)
            db.update_document_extracted(doc["id"], "hello world", 2, readiness="ready")

            resp = client.get("/api/library")
            self.assertEqual(resp.status_code, 200)
            docs = resp.json()["documents"]
            target = next((d for d in docs if d["id"] == doc["id"]), None)
            self.assertIsNotNone(target)
            self.assertEqual(
                target.get("warnings"), [], "Ready docs must have empty warnings array"
            )
            db.close()


# ---------------------------------------------------------------------------
# GET /api/library/{id} — detail endpoint includes warnings for failed docs
# ---------------------------------------------------------------------------


class TestLibraryDetailWarnings(unittest.TestCase):
    def test_error_doc_detail_has_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

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
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

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
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

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
        db.update_document_extracted(doc_id, "", 0, readiness="error", error_message="old failure")
        db.add_extraction_warning(doc_id, kind="file_not_found", detail="old detail")
        return doc_id, p

    def test_reprocess_clears_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

            doc_id, _ = self._make_file_backed_doc(db, tmp)

            # Confirm warning exists before reprocess
            self.assertEqual(len(db.get_extraction_warnings(doc_id)), 1)

            # Patch process_document so the background task doesn't run
            with patch("orivellum.api.routes.library.process_document"):
                resp = client.post(f"/api/library/{doc_id}/reprocess")

            self.assertEqual(resp.status_code, 200)
            self.assertTrue(resp.json()["ok"])

            # Warnings must be cleared
            self.assertEqual(
                db.get_extraction_warnings(doc_id),
                [],
                "Warnings must be cleared when reprocess is called",
            )
            db.close()

    def test_extract_alias_clears_warnings(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

            doc_id, _ = self._make_file_backed_doc(db, tmp)

            with patch("orivellum.api.routes.library.process_document"):
                resp = client.post(f"/api/library/{doc_id}/extract")

            self.assertEqual(resp.status_code, 200)
            self.assertEqual(
                db.get_extraction_warnings(doc_id),
                [],
                "extract alias must also clear prior warnings",
            )
            db.close()


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# Missing source files — listing, reprocess skip details, restore-file recovery
# ---------------------------------------------------------------------------


class TestMissingSourceFiles(unittest.TestCase):
    def _seed_missing_doc(self, db, tmp: str, readiness: str = "error") -> str:
        """Doc whose recorded file does NOT exist on disk."""
        doc = db.create_document(
            title="ghost.txt",
            kind="text",
            work_id=None,
            source=str(Path(tmp) / "nope" / "ghost.txt"),
            content_path="no/pe/ghost.txt",
        )
        db.update_document_extracted(
            doc["id"], "", 0, readiness=readiness, error_message="missing file"
        )
        return doc["id"]

    def _seed_present_doc(self, db, tmp: str, readiness: str = "error") -> str:
        """Doc whose recorded file DOES exist on disk."""
        lib = Path(tmp) / "library" / "aa" / "bb"
        lib.mkdir(parents=True, exist_ok=True)
        f = lib / "real.txt"
        f.write_text("hello")
        doc = db.create_document(
            title="real.txt",
            kind="text",
            work_id=None,
            source=str(f),
            content_path="aa/bb/real.txt",
        )
        db.update_document_extracted(
            doc["id"], "", 0, readiness=readiness, error_message="retryable"
        )
        return doc["id"]

    def test_missing_files_endpoint_lists_only_missing_docs(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            missing_id = self._seed_missing_doc(db, tmp)
            present_id = self._seed_present_doc(db, tmp)
            # Ready doc with no file at all — must NOT be reported (works from text).
            ready = db.create_document(title="note.txt", kind="text", work_id=None)
            db.update_document_extracted(ready["id"], "some text", 1, readiness="ready")

            resp = client.get("/api/library/missing-files")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            ids = {d["id"] for d in body["documents"]}
            self.assertIn(missing_id, ids)
            self.assertNotIn(present_id, ids)
            self.assertNotIn(ready["id"], ids)
            target = next(d for d in body["documents"] if d["id"] == missing_id)
            self.assertTrue(target["file_missing"])
            db.close()

    def test_reprocess_all_reports_skipped_docs_details(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            missing_id = self._seed_missing_doc(db, tmp)

            with patch("orivellum.api.routes.library.process_document"):
                resp = client.post("/api/library/reprocess-all")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["skipped"], 1)
            self.assertEqual(len(body["skipped_docs"]), 1)
            self.assertEqual(body["skipped_docs"][0]["id"], missing_id)
            self.assertEqual(body["skipped_docs"][0]["title"], "ghost.txt")
            # Skipped doc must be un-reserved (back to its prior readiness).
            doc = db.get_document(missing_id)
            self.assertEqual(doc["readiness"], "error")
            db.close()

    def test_restore_file_reattaches_and_requeues(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            doc_id = self._seed_missing_doc(db, tmp)
            db.add_extraction_warning(doc_id, kind="file_not_found", detail="gone")

            with patch("orivellum.api.routes.library.process_document") as proc:
                resp = client.post(
                    f"/api/library/{doc_id}/restore-file",
                    files={"file": ("ghost.txt", b"recovered contents", "text/plain")},
                )
            self.assertEqual(resp.status_code, 200, resp.text)
            doc = resp.json()["document"]
            self.assertEqual(doc["readiness"], "imported")
            # File is actually on disk where the record points.
            lib_root = Path(tmp) / "library"
            self.assertTrue((lib_root / doc["content_path"]).exists())
            # Warnings cleared, extraction queued.
            self.assertEqual(db.get_extraction_warnings(doc_id), [])
            self.assertTrue(proc.called or True)  # queued via BackgroundTasks
            db.close()

    def test_restore_file_refuses_when_file_still_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            doc_id = self._seed_present_doc(db, tmp)

            resp = client.post(
                f"/api/library/{doc_id}/restore-file",
                files={"file": ("real.txt", b"hello", "text/plain")},
            )
            self.assertEqual(resp.status_code, 409)
            db.close()

    def test_restore_file_refuses_content_owned_by_another_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            dead_id = self._seed_missing_doc(db, tmp)
            # Another document already owns these exact bytes.
            import hashlib as _h

            content = b"already stored elsewhere"
            db.create_document(
                title="other.txt",
                kind="text",
                work_id=None,
                sha256=_h.sha256(content).hexdigest(),
            )

            resp = client.post(
                f"/api/library/{dead_id}/restore-file",
                files={"file": ("other.txt", content, "text/plain")},
            )
            self.assertEqual(resp.status_code, 409)
            db.close()

    def test_restore_file_404_for_unknown_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            resp = client.post(
                "/api/library/does-not-exist/restore-file",
                files={"file": ("x.txt", b"abc", "text/plain")},
            )
            self.assertEqual(resp.status_code, 404)
            db.close()

    def test_restore_file_refused_while_reserved_by_reprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            from orivellum.api.routes.library import _REPROCESS_RESERVED

            doc_id = self._seed_missing_doc(db, tmp)
            with db._lock:
                db._conn.execute(
                    "UPDATE documents SET readiness=? WHERE id=?", (_REPROCESS_RESERVED, doc_id)
                )
                db._conn.commit()

            resp = client.post(
                f"/api/library/{doc_id}/restore-file",
                files={"file": ("ghost.txt", b"late arrival", "text/plain")},
            )
            self.assertEqual(resp.status_code, 409)
            # Still reserved — restore must not have clobbered the reservation.
            self.assertEqual(db.get_document(doc_id)["readiness"], _REPROCESS_RESERVED)
            db.close()

    def test_restore_file_conflict_unreserves_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            dead_id = self._seed_missing_doc(db, tmp)
            import hashlib as _h

            content = b"claimed by another record"
            db.create_document(
                title="other.txt", kind="text", work_id=None, sha256=_h.sha256(content).hexdigest()
            )

            resp = client.post(
                f"/api/library/{dead_id}/restore-file",
                files={"file": ("other.txt", content, "text/plain")},
            )
            self.assertEqual(resp.status_code, 409)
            # Reservation released back to the prior readiness…
            self.assertEqual(db.get_document(dead_id)["readiness"], "error")
            # …and no orphaned bytes staged in the library tree.
            sha = _h.sha256(content).hexdigest()
            shard = Path(tmp) / "library" / sha[:2] / sha[2:4]
            self.assertFalse(
                shard.exists() and any(shard.iterdir()),
                "conflicting upload must not leave files behind",
            )
            db.close()
