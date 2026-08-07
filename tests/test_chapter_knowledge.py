"""Tests for GET /api/works/{work_id}/chapters/{chapter_id}/knowledge.

Five acceptance criteria:
1. Items are returned in kind order (character → event → setting → theme →
   foreshadowing) and by confidence descending within each kind.
2. Rejected items (review_status='rejected') are excluded from the response.
3. A chapter_id that belongs to a *different* work returns 404.
4. The limit parameter is capped at 200; passing limit=500 still returns ≤200 rows.
5. A chapter with zero knowledge items returns HTTP 200 with an empty list.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS


# ─── App factory ─────────────────────────────────────────────────────────────

def _make_app(tmp: str):
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


# ─── Seed helpers ────────────────────────────────────────────────────────────

def _seed_doc(db, work_id: str, title: str = "Manuscript") -> str:
    doc = db.create_document(title=title, kind="docx", work_id=work_id)
    db.update_document_extracted(doc["id"], "placeholder text", 2, readiness="ready")
    return doc["id"]


def _seed_chapter(db, work_id: str, doc_id: str, seq: int = 0, title: str = "Chapter 1") -> str:
    from orivellum.database.db import _now
    oid = db._create_object("book_chapter")
    with db._lock:
        db._conn.execute(
            """INSERT INTO book_chapters
               (id, work_id, seq, level, title, text, source_doc_id, status, meta,
                created_at, updated_at)
               VALUES (?,?,?,1,?,?,?,'draft','{}',?,?)""",
            (oid, work_id, seq, title, "some chapter text", doc_id, _now(), _now()),
        )
        db._conn.commit()
    return oid


def _seed_knowledge(db, work_id: str, chapter_id: str, kind: str, text: str,
                    confidence: float = 0.8, review_status: str = "auto") -> str:
    return db.create_knowledge_item(
        work_id=work_id,
        kind=kind,
        text=text,
        confidence=confidence,
        review_status=review_status,
        chapter_id=chapter_id,
    )


# ─── Test class ──────────────────────────────────────────────────────────────

class TestChapterKnowledge(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app, self.db = _make_app(self._tmp.name)
        self.client = TestClient(self.app, headers=AUTH_HEADERS, raise_server_exceptions=True)

        # Primary work + document + chapter
        self.work = self.db.create_work(title="My Novel", work_type="writing")
        self.work_id = self.work["id"]
        self.doc_id = _seed_doc(self.db, self.work_id)
        self.chapter_id = _seed_chapter(self.db, self.work_id, self.doc_id, seq=0, title="Prologue")

    def tearDown(self):
        self._tmp.cleanup()

    def _url(self, chapter_id: str | None = None, work_id: str | None = None, **params) -> str:
        wid = work_id or self.work_id
        cid = chapter_id or self.chapter_id
        base = f"/api/works/{wid}/chapters/{cid}/knowledge"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items())
            base = f"{base}?{qs}"
        return base

    # ── 1. Kind ordering + confidence ordering ────────────────────────────────

    def test_items_ordered_by_kind_then_confidence(self):
        """Response knowledge list must be sorted kind ASC, confidence DESC."""
        # Insert in deliberately scrambled order
        _seed_knowledge(self.db, self.work_id, self.chapter_id, "event",     "Battle of the Bridge", confidence=0.9)
        _seed_knowledge(self.db, self.work_id, self.chapter_id, "character", "Alice (low)",           confidence=0.5)
        _seed_knowledge(self.db, self.work_id, self.chapter_id, "character", "Bob (high)",            confidence=0.95)
        _seed_knowledge(self.db, self.work_id, self.chapter_id, "setting",   "Dark Forest",           confidence=0.7)
        _seed_knowledge(self.db, self.work_id, self.chapter_id, "theme",     "Redemption",            confidence=0.6)

        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)
        items = r.json()["knowledge"]
        self.assertEqual(len(items), 5)

        kinds = [i["kind"] for i in items]
        # character comes before event, event before setting, setting before theme
        char_indices    = [i for i, k in enumerate(kinds) if k == "character"]
        event_indices   = [i for i, k in enumerate(kinds) if k == "event"]
        setting_indices = [i for i, k in enumerate(kinds) if k == "setting"]
        theme_indices   = [i for i, k in enumerate(kinds) if k == "theme"]

        self.assertTrue(max(char_indices)    < min(event_indices),   "character must precede event")
        self.assertTrue(max(event_indices)   < min(setting_indices), "event must precede setting")
        self.assertTrue(max(setting_indices) < min(theme_indices),   "setting must precede theme")

        # Within "character" the higher-confidence item appears first
        char_items = [i for i in items if i["kind"] == "character"]
        self.assertGreaterEqual(char_items[0]["confidence"], char_items[1]["confidence"])

    # ── 2. Rejected items are excluded ────────────────────────────────────────

    def test_rejected_items_excluded(self):
        """Items with review_status='rejected' must not appear in the response."""
        _seed_knowledge(self.db, self.work_id, self.chapter_id, "character", "Visible Alice",  review_status="auto")
        _seed_knowledge(self.db, self.work_id, self.chapter_id, "character", "Approved Bob",   review_status="approved")
        _seed_knowledge(self.db, self.work_id, self.chapter_id, "character", "Rejected Carol", review_status="rejected")
        _seed_knowledge(self.db, self.work_id, self.chapter_id, "event",     "AI event",       review_status="ai_auto")

        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)
        body = r.json()
        texts = [i["text"] for i in body["knowledge"]]

        self.assertIn("Visible Alice",   texts)
        self.assertIn("Approved Bob",    texts)
        self.assertIn("AI event",        texts)
        self.assertNotIn("Rejected Carol", texts)
        self.assertEqual(body["count"], 3)

    # ── 3. Cross-work chapter returns 404 ────────────────────────────────────

    def test_chapter_from_different_work_returns_404(self):
        """Requesting a chapter that belongs to another work must return 404."""
        other_work = self.db.create_work(title="Other Book", work_type="writing")
        other_doc  = _seed_doc(self.db, other_work["id"], title="Other Doc")
        other_ch   = _seed_chapter(self.db, other_work["id"], other_doc, seq=0, title="Intro")

        # Use self.work_id but the chapter_id from the other work — should 404
        r = self.client.get(self._url(chapter_id=other_ch))
        self.assertEqual(r.status_code, 404)

    # ── 4. Limit cap at 200 ────────────────────────────────────────────────────

    def test_limit_capped_at_200(self):
        """Even with limit=500 the endpoint returns at most 200 items."""
        # Seed 210 distinct knowledge items
        for i in range(210):
            _seed_knowledge(
                self.db, self.work_id, self.chapter_id,
                kind="character",
                text=f"Character number {i:03d}",
                confidence=round(0.5 + (i % 10) * 0.04, 2),
            )

        r = self.client.get(self._url(limit=500))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertLessEqual(len(body["knowledge"]), 200)
        self.assertLessEqual(body["count"], 200)

    # ── 5. Zero knowledge items returns empty list ────────────────────────────

    def test_empty_chapter_returns_empty_list(self):
        """A chapter with no knowledge items must return HTTP 200 with an empty list."""
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["knowledge"], [])
        self.assertEqual(body["count"], 0)
        self.assertEqual(body["chapter_id"], self.chapter_id)

    # ── Bonus: unknown work_id returns 404 ────────────────────────────────────

    def test_unknown_work_returns_404(self):
        r = self.client.get(f"/api/works/no-such-work/chapters/{self.chapter_id}/knowledge")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
