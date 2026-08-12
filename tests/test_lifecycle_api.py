"""Tests for document lifecycle management.

Covers:
- New documents default to lifecycle='draft' (not 'active')
- Schema v48: existing 'active' document objects become 'draft'
- db.update_document_lifecycle() transitions all four states
- Canonical auto-demotes other same-work/same-kind docs to 'draft'
- Docs already 'superseded' are not touched by canonical promotion
- PATCH /api/library/{docId}/lifecycle endpoint — happy path
- PATCH /api/library/{docId}/lifecycle — rejects invalid lifecycle values
- PATCH /api/library/{docId}/lifecycle — 404 for unknown doc
- Version-suggestion duplicate suppression
- Lifecycle filter on GET /api/library
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

# ── Test-app factory ──────────────────────────────────────────────────────────


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


# ── DB-level lifecycle tests ──────────────────────────────────────────────────


class TestDocumentLifecycleDefaults(unittest.TestCase):
    def test_new_document_defaults_to_draft(self):
        """create_document() must set lifecycle='draft', not 'active'."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            doc = db.create_document(title="report.pdf", kind="pdf")
            # lifecycle must come back as 'draft' via the objects JOIN
            fetched = db.get_document(doc["id"])
            self.assertIsNotNone(fetched, "get_document must find the doc")
            self.assertEqual(
                fetched.get("lifecycle"),
                "draft",
                f"Expected 'draft', got {fetched.get('lifecycle')!r}",
            )
            db.close()

    def test_list_documents_exposes_lifecycle(self):
        """list_documents() must return a lifecycle field on every row."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            db.create_document(title="a.pdf", kind="pdf")
            db.create_document(title="b.pdf", kind="pdf")
            docs = db.list_documents()
            self.assertGreater(len(docs), 0)
            for d in docs:
                self.assertIn("lifecycle", d, "lifecycle must be in every document dict")
            db.close()


class TestDocumentLifecycleTransitions(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._app, self._db = _make_app(self._tmpdir.name)

    def tearDown(self):
        self._db.close()
        self._tmpdir.cleanup()

    def _make_work_doc(self, title: str = "doc.pdf", kind: str = "pdf"):
        work = self._db.create_work(title="Test Work")
        doc = self._db.create_document(title=title, kind=kind, work_id=work["id"])
        return work["id"], doc["id"]

    def test_set_lifecycle_canonical(self):
        _, doc_id = self._make_work_doc()
        ok = self._db.update_document_lifecycle(doc_id, "canonical", actor="author")
        self.assertTrue(ok)
        fetched = self._db.get_document(doc_id)
        self.assertEqual(fetched["lifecycle"], "canonical")

    def test_set_lifecycle_superseded(self):
        _, doc_id = self._make_work_doc()
        ok = self._db.update_document_lifecycle(doc_id, "superseded", actor="author")
        self.assertTrue(ok)
        fetched = self._db.get_document(doc_id)
        self.assertEqual(fetched["lifecycle"], "superseded")

    def test_set_lifecycle_reference(self):
        _, doc_id = self._make_work_doc()
        ok = self._db.update_document_lifecycle(doc_id, "reference", actor="author")
        self.assertTrue(ok)
        fetched = self._db.get_document(doc_id)
        self.assertEqual(fetched["lifecycle"], "reference")

    def test_canonical_demotes_other_same_work_same_kind_to_draft(self):
        """Declaring one doc canonical must demote all other same-work/same-kind docs."""
        work = self._db.create_work(title="Work A")
        wid = work["id"]
        doc_a = self._db.create_document(title="v1.pdf", kind="pdf", work_id=wid)
        doc_b = self._db.create_document(title="v2.pdf", kind="pdf", work_id=wid)
        doc_c = self._db.create_document(title="v3.pdf", kind="pdf", work_id=wid)

        # Promote doc_a to canonical
        self._db.update_document_lifecycle(doc_a["id"], "canonical", actor="author")

        a = self._db.get_document(doc_a["id"])
        b = self._db.get_document(doc_b["id"])
        c = self._db.get_document(doc_c["id"])
        self.assertEqual(a["lifecycle"], "canonical")
        self.assertEqual(b["lifecycle"], "draft", "doc_b must be demoted to draft")
        self.assertEqual(c["lifecycle"], "draft", "doc_c must be demoted to draft")

    def test_canonical_does_not_touch_superseded_docs(self):
        """Docs already 'superseded' must stay 'superseded' when another is declared canonical."""
        work = self._db.create_work(title="Work B")
        wid = work["id"]
        doc_a = self._db.create_document(title="v1.pdf", kind="pdf", work_id=wid)
        doc_b = self._db.create_document(title="v2.pdf", kind="pdf", work_id=wid)

        # Mark doc_b as superseded first
        self._db.update_document_lifecycle(doc_b["id"], "superseded", actor="author")

        # Now declare doc_a canonical
        self._db.update_document_lifecycle(doc_a["id"], "canonical", actor="author")

        a = self._db.get_document(doc_a["id"])
        b = self._db.get_document(doc_b["id"])
        self.assertEqual(a["lifecycle"], "canonical")
        self.assertEqual(
            b["lifecycle"],
            "superseded",
            "superseded docs must not be touched by canonical promotion",
        )

    def test_invalid_lifecycle_raises(self):
        _, doc_id = self._make_work_doc()
        with self.assertRaises(ValueError):
            self._db.update_document_lifecycle(doc_id, "nonexistent_value", actor="author")

    def test_unknown_doc_returns_false(self):
        ok = self._db.update_document_lifecycle("no-such-id", "draft", actor="author")
        self.assertFalse(ok)

    def test_lifecycle_filter_in_list_documents(self):
        """list_documents(lifecycle='canonical') must return only canonical docs."""
        work = self._db.create_work(title="Work Filter")
        wid = work["id"]
        doc_a = self._db.create_document(title="main.pdf", kind="pdf", work_id=wid)
        doc_b = self._db.create_document(title="draft.pdf", kind="pdf", work_id=wid)
        self._db.update_document_lifecycle(doc_a["id"], "canonical", actor="author")

        canonical_docs = self._db.list_documents(work_id=wid, lifecycle="canonical")
        draft_docs = self._db.list_documents(work_id=wid, lifecycle="draft")
        self.assertEqual(len(canonical_docs), 1)
        self.assertEqual(canonical_docs[0]["id"], doc_a["id"])
        self.assertEqual(len(draft_docs), 1)
        self.assertEqual(draft_docs[0]["id"], doc_b["id"])


# ── API endpoint tests ────────────────────────────────────────────────────────


class TestLifecycleEndpoint(unittest.TestCase):
    def test_patch_lifecycle_happy_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            doc = db.create_document(title="rep.pdf", kind="pdf")
            resp = client.patch(
                f"/api/library/{doc['id']}/lifecycle",
                json={"lifecycle": "canonical"},
            )
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["lifecycle"], "canonical")
            self.assertIn("document", body)
            self.assertEqual(body["document"]["lifecycle"], "canonical")
            db.close()

    def test_patch_lifecycle_invalid_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            doc = db.create_document(title="rep.pdf", kind="pdf")
            resp = client.patch(
                f"/api/library/{doc['id']}/lifecycle",
                json={"lifecycle": "bogus"},
            )
            self.assertEqual(resp.status_code, 400)
            db.close()

    def test_patch_lifecycle_unknown_doc(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            resp = client.patch(
                "/api/library/no-such-id/lifecycle",
                json={"lifecycle": "draft"},
            )
            self.assertEqual(resp.status_code, 404)
            db.close()

    def test_get_library_lifecycle_filter(self):
        """GET /api/library?lifecycle=canonical must return only canonical docs."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            doc_a = db.create_document(title="canon.pdf", kind="pdf")
            doc_b = db.create_document(title="draft.pdf", kind="pdf")
            db.update_document_lifecycle(doc_a["id"], "canonical", actor="author")

            resp = client.get("/api/library?lifecycle=canonical")
            self.assertEqual(resp.status_code, 200)
            ids = [d["id"] for d in resp.json()["documents"]]
            self.assertIn(doc_a["id"], ids)
            self.assertNotIn(doc_b["id"], ids)
            db.close()


# ── Version-suggestion duplicate suppression ─────────────────────────────────


class TestVersionSuggestionDedup(unittest.TestCase):
    def test_duplicate_version_suggestion_not_created(self):
        """Importing a doc similar to an existing one twice should create exactly one suggestion."""
        import base64
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
            work = db.create_work(title="Dedup Work")
            wid = work["id"]

            tiny_pdf = base64.b64encode(b"%PDF-1.4 tiny").decode()

            with patch("orivellum.api.routes.library.process_document"):
                # First import — creates "report_v1.pdf"
                client.post(
                    "/api/library/import",
                    json={
                        "filename": "report_v1.pdf",
                        "content_b64": tiny_pdf,
                        "work_id": wid,
                    },
                )
                # Second import — "report_v2.pdf" is similar to "report_v1.pdf"
                client.post(
                    "/api/library/import",
                    json={
                        "filename": "report_v2.pdf",
                        "content_b64": base64.b64encode(b"%PDF-1.4 other").decode(),
                        "work_id": wid,
                    },
                )
                # Third import — same pair again (different SHA but same stem pattern)
                # Should not create a second suggestion for the same pair
                client.post(
                    "/api/library/import",
                    json={
                        "filename": "report_v1.pdf",  # same name → duplicate SHA → not a new doc
                        "content_b64": tiny_pdf,
                        "work_id": wid,
                    },
                )

            with db._lock:
                count = db._conn.execute(
                    "SELECT COUNT(*) FROM suggestions WHERE work_id=? AND kind='version_relationship'",
                    (wid,),
                ).fetchone()[0]

            # There should be exactly one version-relationship suggestion for this pair
            self.assertEqual(count, 1, f"Expected 1 version suggestion, got {count}")
            db.close()


if __name__ == "__main__":
    unittest.main()
