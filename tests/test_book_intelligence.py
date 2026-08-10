"""Tests for the Book Intelligence view (GET /api/works/{id}/book-intelligence)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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


def _seed_doc(db, work_id, title, text, kind="docx"):
    doc = db.create_document(title=title, kind=kind, work_id=work_id)
    db.update_document_extracted(doc["id"], text, len(text.split()), readiness="ready")
    return doc["id"]


def _seed_chapter(db, work_id, doc_id, seq, title, text=""):
    from orivellum.database.db import _now

    oid = db._create_object("book_chapter")
    with db._lock:
        db._conn.execute(
            """INSERT INTO book_chapters(id, work_id, seq, level, title, text,
               source_doc_id, status, meta, created_at, updated_at)
               VALUES(?,?,?,1,?,?,?,'draft','{}',?,?)""",
            (oid, work_id, seq, title, text, doc_id, _now(), _now()),
        )
        db._conn.commit()
    return oid


class BookIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmp.name)
        self.client = TestClient(self.app, headers=AUTH_HEADERS)
        self.work = self.db.create_work(title="My Book", work_type="writing")
        self.work_id = self.work["id"]

    def tearDown(self):
        self._tmp.cleanup()

    def _get(self, work_id=None):
        r = self.client.get(f"/api/works/{work_id or self.work_id}/book-intelligence")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_404_for_unknown_work(self):
        r = self.client.get("/api/works/nope/book-intelligence")
        self.assertEqual(r.status_code, 404)

    def test_empty_work(self):
        body = self._get()
        self.assertIsNone(body["canonical"])
        self.assertEqual(body["versions"], [])
        self.assertEqual(body["outline"], [])
        self.assertTrue(any(g["kind"] == "no_documents" for g in body["gaps"]))
        self.assertIn("Link", body["next_action"])

    def test_canonical_auto_prefers_biggest_docx(self):
        small = _seed_doc(self.db, self.work_id, "draft-v1.docx", "word " * 100)
        big = _seed_doc(self.db, self.work_id, "draft-v2.docx", "word " * 1000)
        note = _seed_doc(self.db, self.work_id, "notes.txt", "word " * 5000, kind="text")
        body = self._get()
        self.assertEqual(body["canonical"]["id"], big)
        self.assertEqual(body["canonical"]["canonical_source"], "auto")
        # Versions sorted by word count desc
        self.assertEqual(body["versions"][0]["id"], note)
        # Two substantial versions + auto canonical → confirm-canonical gap
        self.assertTrue(any(g["kind"] == "canonical_unconfirmed" for g in body["gaps"]))
        self.assertIn("canonical", body["next_action"].lower())

    def test_declared_canonical_wins(self):
        small = _seed_doc(self.db, self.work_id, "old.docx", "word " * 100)
        big = _seed_doc(self.db, self.work_id, "new.docx", "word " * 1000)
        self.db.update_document_lifecycle(small, "canonical")
        body = self._get()
        self.assertEqual(body["canonical"]["id"], small)
        self.assertEqual(body["canonical"]["canonical_source"], "declared")
        self.assertFalse(any(g["kind"] == "canonical_unconfirmed" for g in body["gaps"]))

    def test_cross_kind_canonical_latest_declaration_wins(self):
        """Lifecycle demotion is per Work+kind, so a PDF and a DOCX can both be
        'canonical' — the Book view must honor the most recent declaration."""
        docx = _seed_doc(self.db, self.work_id, "book.docx", "word " * 1000, kind="docx")
        pdf = _seed_doc(self.db, self.work_id, "book.pdf", "word " * 800, kind="pdf")
        self.db.update_document_lifecycle(docx, "canonical")
        self.db.update_document_lifecycle(pdf, "canonical")  # later declaration
        body = self._get()
        self.assertEqual(body["canonical"]["id"], pdf)
        self.assertEqual(body["canonical"]["canonical_source"], "declared")
        self.assertTrue(any(g["kind"] == "canonical_conflict" for g in body["gaps"]))

    def test_outline_status_and_research_counts(self):
        doc = _seed_doc(self.db, self.work_id, "book.docx", "word " * 2000)
        _seed_chapter(self.db, self.work_id, doc, 1, "Quantum Mechanics", "text " * 500)
        _seed_chapter(self.db, self.work_id, doc, 2, "Wave Functions", "text " * 50)
        _seed_chapter(self.db, self.work_id, doc, 3, "Entanglement", "")
        # 3 knowledge items about quantum mechanics, none about the others
        for i in range(3):
            self.db.create_knowledge_item(
                work_id=self.work_id,
                kind="fact",
                text=f"Quantum mechanics fact number {i}",
                source_doc_id=doc,
            )
        body = self._get()
        by_title = {c["title"]: c for c in body["outline"]}
        self.assertEqual(by_title["Quantum Mechanics"]["chapter_status"], "present")
        self.assertEqual(by_title["Quantum Mechanics"]["knowledge_count"], 3)
        self.assertEqual(by_title["Wave Functions"]["chapter_status"], "incomplete")
        self.assertEqual(by_title["Entanglement"]["chapter_status"], "missing")
        self.assertEqual(by_title["Entanglement"]["knowledge_count"], 0)
        # Gaps include no-research + placeholder for Entanglement
        kinds = {g["kind"] for g in body["gaps"]}
        self.assertIn("no_research", kinds)
        self.assertIn("placeholder_chapter", kinds)
        # Missing intro/conclusion detected
        self.assertEqual(sum(1 for g in body["gaps"] if g["kind"] == "missing_section"), 2)
        # Next action targets the zero-research chapter first
        self.assertIn("no research", body["next_action"])

    def test_completeness_dimensions(self):
        doc = _seed_doc(self.db, self.work_id, "book.docx", "word " * 25000)
        for i in range(1, 6):
            _seed_chapter(self.db, self.work_id, doc, i, f"Alpha Topic {i}", "text " * 500)
        k_id = self.db.create_knowledge_item(
            work_id=self.work_id, kind="fact", text="Alpha Topic insight", source_doc_id=doc
        )
        self.db.update_knowledge_review_status(k_id, "approved")
        body = self._get()
        comp = body["completeness"]
        # 5 present chapters / 10 expected = 50%
        self.assertEqual(comp["structural_pct"], 50)
        # 25000 / 50000 words = 50%
        self.assertEqual(comp["content_pct"], 50)
        # 1 knowledge item reviewed of 1 = 100%
        self.assertEqual(comp["editorial_pct"], 100)
        self.assertLessEqual(comp["research_pct"], 100)

    def test_no_structure_gap(self):
        _seed_doc(self.db, self.work_id, "book.docx", "word " * 1000)
        body = self._get()
        self.assertTrue(any(g["kind"] == "no_structure" for g in body["gaps"]))
        self.assertIn("Reprocess", body["next_action"])


if __name__ == "__main__":
    unittest.main()
