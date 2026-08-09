"""Regression tests for GET /api/books — the Books page / Writing hub list.

The endpoint once crashed (500) referencing a non-existent column; the fix
counts chapters with a subquery.  Until now that fix was only verified against
an empty database.  These tests create a Work + book pipeline + real
book_chapters rows and assert the counts, stage labels, and filtering that the
Books page and the Writing hub stage tiles depend on.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS


def _make_app(tmp: str):
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db, cfg


def _make_chapters(db, work_id: str, count: int) -> str:
    """Create a document and *count* extracted chapters for a Work.

    Returns the doc id.  Chapters land with pipeline_id=NULL (the real
    extraction order) — create_book_pipeline() links them.
    """
    doc = db.create_document(title="Manuscript.docx", source="/tmp/m.docx",
                             kind="manuscript", work_id=work_id)
    chapters = [
        {"seq": i, "level": 1, "title": f"Chapter {i}", "text": f"Body {i}"}
        for i in range(1, count + 1)
    ]
    db.upsert_book_chapters(doc["id"], work_id, chapters)
    return doc["id"]


class TestBooksList(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db, _cfg = _make_app(self._tmp.name)
        self.client = TestClient(self.app, headers=AUTH_HEADERS)

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_database_returns_no_books(self):
        resp = self.client.get("/api/books")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["books"], [])

    def test_book_with_chapters_reports_correct_count_and_stage(self):
        work = self.db.create_work(title="My Novel", work_type="writing")
        _make_chapters(self.db, work["id"], 3)
        pipeline = self.db.create_book_pipeline(work["id"], "My Novel")
        # Sanity: the canonical aggregation sees the linked chapters
        self.assertEqual(pipeline["chapter_count"], 3)

        resp = self.client.get("/api/books")
        self.assertEqual(resp.status_code, 200)
        books = resp.json()["books"]
        self.assertEqual(len(books), 1)
        book = books[0]
        self.assertEqual(book["id"], work["id"])
        self.assertEqual(book["title"], "My Novel")
        self.assertEqual(book["chapter_count"], 3)
        self.assertEqual(book["pipeline_id"], pipeline["id"])
        self.assertEqual(book["pipeline_status"], "B0")
        self.assertEqual(book["stage_label"], "Intake")
        self.assertEqual(book["doc_count"], 1)

    def test_endpoint_count_matches_canonical_aggregation(self):
        """/api/books must agree with get_book_pipeline_for_work (the
        canonical chapter aggregation used by the pipeline panel)."""
        work = self.db.create_work(title="Reference Book", work_type="writing")
        _make_chapters(self.db, work["id"], 5)
        self.db.create_book_pipeline(work["id"], "Reference Book")

        canonical = self.db.get_book_pipeline_for_work(work["id"])
        book = self.client.get("/api/books").json()["books"][0]
        self.assertEqual(book["chapter_count"], canonical["chapter_count"])
        self.assertEqual(book["chapter_count"], 5)

    def test_works_without_a_pipeline_are_absent(self):
        with_pipeline = self.db.create_work(title="Book A", work_type="writing")
        without_pipeline = self.db.create_work(title="Plain Research", work_type="research")
        _make_chapters(self.db, with_pipeline["id"], 2)
        # Chapters WITHOUT a pipeline must not surface the work either
        _make_chapters(self.db, without_pipeline["id"], 4)
        self.db.create_book_pipeline(with_pipeline["id"], "Book A")

        books = self.client.get("/api/books").json()["books"]
        ids = {b["id"] for b in books}
        self.assertIn(with_pipeline["id"], ids)
        self.assertNotIn(without_pipeline["id"], ids)
        self.assertEqual(len(books), 1)

    def test_stage_label_tracks_pipeline_advancement(self):
        work = self.db.create_work(title="Advancing Book", work_type="writing")
        _make_chapters(self.db, work["id"], 1)
        pipeline = self.db.create_book_pipeline(work["id"], "Advancing Book")
        # Move the pipeline to a later stage directly (state machine is
        # covered elsewhere; here we only care that the list reflects it).
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE book_pipelines SET status='B5' WHERE id=?",
                (pipeline["id"],),
            )
            self.db._conn.commit()

        book = self.client.get("/api/books").json()["books"][0]
        self.assertEqual(book["pipeline_status"], "B5")
        self.assertEqual(book["stage_label"], "Chapter Drafting")

    def test_reprocessing_a_document_does_not_double_count(self):
        """upsert_book_chapters replaces rows idempotently — a reprocess must
        not inflate the count on the Books page."""
        work = self.db.create_work(title="Reprocessed Book", work_type="writing")
        doc_id = _make_chapters(self.db, work["id"], 3)
        self.db.create_book_pipeline(work["id"], "Reprocessed Book")

        # Reprocess: same doc re-extracts, now with 4 chapters
        chapters = [
            {"seq": i, "level": 1, "title": f"Chapter {i}", "text": f"New body {i}"}
            for i in range(1, 5)
        ]
        self.db.upsert_book_chapters(doc_id, work["id"], chapters)

        # Fresh rows must be linked to the existing pipeline automatically —
        # nothing else runs after a reprocess to adopt orphans.
        book = self.client.get("/api/books").json()["books"][0]
        self.assertEqual(book["chapter_count"], 4)
        canonical = self.db.get_book_pipeline_for_work(work["id"])
        self.assertEqual(canonical["chapter_count"], 4)


if __name__ == "__main__":
    unittest.main()
