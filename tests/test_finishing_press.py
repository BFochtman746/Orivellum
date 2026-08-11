"""PRESS self-tests (audit D-08, consolidated for LAW 1 / task: one manuscript).

Chapters are never typed into PRESS — they are read from ``book_chapters``
in the main database, with word counts computed from the actual prose.
These tests seed a minimal temp ``orivellum.db`` alongside the temp
``press.db`` to exercise that read path, plus the legacy-table migration.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from orivellum.capabilities.finishing import press
from orivellum.capabilities.finishing.gateway import MockGateway

WORK_ID = "work-test-1"


def _seed_main_db(data_dir: str, chapters: list[tuple[int, str, str]], work_id: str = WORK_ID):
    """Create a minimal main DB with only the table PRESS reads."""
    conn = sqlite3.connect(str(Path(data_dir) / "orivellum.db"))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS book_chapters "
        "(id TEXT PRIMARY KEY, work_id TEXT, seq INTEGER, title TEXT, text TEXT)"
    )
    for seq, title, text in chapters:
        conn.execute(
            "INSERT INTO book_chapters (id,work_id,seq,title,text) VALUES (?,?,?,?,?)",
            (f"{work_id}-ch{seq}", work_id, seq, title, text),
        )
    conn.commit()
    conn.close()


def _words(n: int) -> str:
    return " ".join(["word"] * n)


def _styled_book(title="Ash and Silence", work_id=WORK_ID):
    b = press.create_book(title, "Author X", series="Job Cycle", work_id=work_id)
    press.update_style(
        b["slug"],
        {
            "trim": "6x9",
            "body_font": "Garamond",
            "heading_font": "Trajan",
            "body_size": "11pt",
            "leading": "14pt",
            "chapter_style": "words",
            "epigraphs": "on",
        },
    )
    return b["slug"]


class PressTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        press.configure(self._tmp.name)
        press.cmd_init()
        _seed_main_db(
            self._tmp.name,
            [
                (0, "The Storm", _words(3000)),
                (1, "The Calm", _words(2500)),
            ],
        )

    def tearDown(self):
        self._tmp.cleanup()

    # ── chapter-number rendering ─────────────────────────────────────────

    def test_chapter_header_styles(self):
        self.assertEqual(press.chapter_header("arabic", 12), "Chapter 12")
        self.assertEqual(press.chapter_header("roman", 12), "Chapter XII")
        self.assertEqual(press.chapter_header("words", 12), "Chapter Twelve")
        self.assertEqual(press.chapter_header("words", 21), "Chapter Twenty-One")
        self.assertEqual(press.chapter_header("roman", 49), "Chapter XLIX")

    # ── style lock ───────────────────────────────────────────────────────

    def test_style_lock_is_immutable(self):
        slug = _styled_book()
        press.lock_style(slug, "Author X")
        with self.assertRaises(PermissionError):
            press.update_style(slug, {"body_font": "Comic Sans"})

    def test_lock_refuses_incomplete_style(self):
        b = press.create_book("Half Styled", "Author X")
        press.update_style(b["slug"], {"trim": "6x9"})
        with self.assertRaises(ValueError):
            press.lock_style(b["slug"], "Author X")

    def test_invalid_chapter_style_rejected(self):
        b = press.create_book("Bad Style", "Author X")
        with self.assertRaises(ValueError):
            press.update_style(b["slug"], {"chapter_style": "hieroglyphic"})

    # ── one manuscript (LAW 1) ───────────────────────────────────────────

    def test_chapters_come_from_book_chapters_with_computed_words(self):
        slug = _styled_book()
        book = press.get_book(slug)
        chs = book["chapters"]
        self.assertEqual([c["number"] for c in chs], [1, 2])
        self.assertEqual(chs[0]["title"], "The Storm")
        self.assertEqual(chs[0]["words"], 3000)  # counted from text, not typed
        self.assertEqual(chs[1]["words"], 2500)

    def test_unlinked_book_has_no_chapters(self):
        b = press.create_book("Orphan", "Author X")  # no work_id
        book = press.get_book(b["slug"])
        self.assertEqual(book["chapters"], [])

    def test_link_work_attaches_real_chapters(self):
        b = press.create_book("Late Link", "Author X")
        self.assertEqual(press.get_book(b["slug"])["chapters"], [])
        book = press.link_work(b["slug"], WORK_ID)
        self.assertEqual(len(book["chapters"]), 2)

    # ── epigraph contract ────────────────────────────────────────────────

    def test_epigraph_slot_respects_policy_off(self):
        b = press.create_book("No Epigraphs", "Author X", work_id=WORK_ID)
        press.update_style(b["slug"], {"epigraphs": "off"})
        with self.assertRaises(ValueError):
            press.set_epigraph_slot(b["slug"], 1, has_epigraph=True)

    def test_epigraph_slot_requires_real_chapter(self):
        slug = _styled_book("Slot On Ghost")
        with self.assertRaises(KeyError):
            press.set_epigraph_slot(slug, 99, has_epigraph=True)

    def test_epigraph_slot_requires_linked_work(self):
        b = press.create_book("Unlinked Slots", "Author X")
        press.update_style(b["slug"], {"epigraphs": "on"})
        with self.assertRaises(ValueError):
            press.set_epigraph_slot(b["slug"], 1, has_epigraph=True)

    def test_quoted_epigraph_abstains(self):
        slug = _styled_book("Quote Refuser")
        press.set_epigraph_slot(slug, 1, has_epigraph=True)
        result = press.draft_epigraph(slug, 1, soul="grief", want_quote=True)
        self.assertEqual(result["status"], "ABSTAINED")

    def test_original_epigraph_draft_and_approve(self):
        slug = _styled_book("Original Only")
        press.set_epigraph_slot(slug, 1, has_epigraph=True)
        result = press.draft_epigraph(slug, 1, soul="grief", in_world="The Uz Fragments")
        self.assertEqual(result["status"], "UNVERIFIED_DRAFT")
        press.approve_epigraph(slug, 1, "Author X")
        book = press.get_book(slug)
        self.assertEqual(book["chapters"][0]["epigraph_status"], "APPROVED")

    # ── pre-flight verify ────────────────────────────────────────────────

    def test_verify_reflects_real_contiguity(self):
        _seed_main_db(self._tmp.name, [(0, "One", _words(100)), (2, "Three", _words(100))], "gappy")
        slug = _styled_book("Gappy Book", work_id="gappy")
        press.lock_style(slug, "Author X")
        press.set_matter(slug, front=True, back=True)
        vr = press.verify(slug)
        self.assertFalse(vr["passed"])
        self.assertFalse(vr["checks"]["chapters_contiguous"])

    def test_verify_flags_empty_chapter_text(self):
        _seed_main_db(self._tmp.name, [(0, "One", _words(100)), (1, "Two", "")], "hollow")
        slug = _styled_book("Hollow Book", work_id="hollow")
        press.lock_style(slug, "Author X")
        vr = press.verify(slug)
        self.assertFalse(vr["checks"]["chapters_have_text"])
        self.assertTrue(vr["checks"]["chapters_contiguous"])

    def test_verify_requires_linked_work(self):
        b = press.create_book("Unlinked Verify", "Author X")
        vr = press.verify(b["slug"])
        self.assertFalse(vr["checks"]["linked_to_work"])
        self.assertFalse(vr["checks"]["has_chapters"])

    def test_verify_word_count_computed_from_text(self):
        slug = _styled_book("Counted Book")
        press.lock_style(slug, "Author X")
        press.set_matter(slug, front=True, back=True)
        vr = press.verify(slug)
        self.assertEqual(vr["word_count"], 5500)

    def test_verify_fails_on_orphan_epigraph_slot(self):
        # Slot on chapter 2, then the manuscript shrinks to one chapter.
        _seed_main_db(self._tmp.name, [(0, "One", _words(100)), (1, "Two", _words(100))], "shrink")
        slug = _styled_book("Shrinking Book", work_id="shrink")
        press.set_epigraph_slot(slug, 2, has_epigraph=True)
        conn = sqlite3.connect(str(Path(self._tmp.name) / "orivellum.db"))
        conn.execute("DELETE FROM book_chapters WHERE work_id='shrink' AND seq=1")
        conn.commit()
        conn.close()
        vr = press.verify(slug)
        self.assertFalse(vr["checks"]["epigraph_slots_valid"])
        self.assertFalse(vr["passed"])
        book = press.get_book(slug)
        self.assertEqual(book["orphan_epigraph_slots"], [2])
        # stale slots must be removable even though the chapter is gone
        press.set_epigraph_slot(slug, 2, has_epigraph=False)
        self.assertTrue(press.verify(slug)["checks"]["epigraph_slots_valid"])

    def test_relink_surfaces_stale_slots(self):
        _seed_main_db(self._tmp.name, [(0, "Solo", _words(100))], "tiny")
        slug = _styled_book("Relinked Book")  # starts on WORK_ID (2 chapters)
        press.set_epigraph_slot(slug, 2, has_epigraph=True)
        press.link_work(slug, "tiny")  # chapter 2 no longer exists
        vr = press.verify(slug)
        self.assertFalse(vr["checks"]["epigraph_slots_valid"])

    def test_relink_never_reattaches_approved_epigraph_to_other_work(self):
        # Both Works have a chapter 1 — the dangerous same-number case.
        _seed_main_db(self._tmp.name, [(0, "Other Opening", _words(4000))], "other-work")
        slug = _styled_book("Two Works")  # linked to WORK_ID, also has chapter 1
        press.lock_style(slug, "Author X")
        press.set_matter(slug, front=True, back=True)
        press.set_epigraph_slot(slug, 1, has_epigraph=True)
        press.draft_epigraph(slug, 1, soul="grief")
        press.approve_epigraph(slug, 1, "Author X")
        self.assertTrue(press.verify(slug)["passed"])
        press.link_work(slug, "other-work")
        vr = press.verify(slug)
        # slot was authored for WORK_ID's chapter 1, not other-work's
        self.assertFalse(vr["checks"]["epigraph_slots_valid"])
        self.assertFalse(vr["passed"])
        with self.assertRaises(ValueError):
            press.seal_package(slug, "publisher", "production", "Author X")
        # the submission-format exception must NOT bypass stale-slot safety
        with self.assertRaises(ValueError):
            press.seal_package(slug, "publisher", "submission", "Author X")
        with self.assertRaises(ValueError):
            press.build_package(slug, "publisher", "submission")
        # the approved text must not silently attach to the new Work's ch 1
        book = press.get_book(slug)
        self.assertFalse(book["chapters"][0]["has_epigraph"])
        self.assertEqual(book["chapters"][0]["epigraph_text"], "")
        self.assertEqual(book["orphan_epigraph_slots"], [1])
        # recreating the slot for the new Work starts clean
        press.set_epigraph_slot(slug, 1, has_epigraph=True)
        book = press.get_book(slug)
        self.assertEqual(book["chapters"][0]["epigraph_status"], "")
        self.assertEqual(book["orphan_epigraph_slots"], [])
        with self.assertRaises(ValueError):
            press.approve_epigraph(slug, 1, "Author X")  # nothing drafted yet

    def test_verify_blocks_unapproved_epigraph(self):
        slug = _styled_book("Unapproved Epigraph")
        press.lock_style(slug, "Author X")
        press.set_epigraph_slot(slug, 1, has_epigraph=True)
        press.draft_epigraph(slug, 1, soul="loss")
        press.set_matter(slug, front=True, back=True)
        vr = press.verify(slug)
        self.assertFalse(vr["checks"]["epigraph_policy"])

    def test_build_package_blocked_on_failed_preflight(self):
        slug = _styled_book("Not Ready")
        # style not locked
        with self.assertRaises(ValueError):
            press.build_package(slug, "publisher", "production")
        # submission manuscript format is the only pre-typeset exception
        pkg = press.build_package(slug, "publisher", "submission")
        self.assertEqual(pkg["spec"]["format"], "standard-manuscript-format")

    def test_page_estimate_rounds_to_even(self):
        _seed_main_db(self._tmp.name, [(0, "One", _words(301))], "pagey")  # 2 body pages
        slug = _styled_book("Page Count", work_id="pagey")
        press.lock_style(slug, "Author X")
        press.set_matter(slug, front=True, back=True)
        vr = press.verify(slug)
        self.assertEqual(vr["estimated_pages"] % 2, 0)

    # ── migration ────────────────────────────────────────────────────────

    def test_legacy_press_chapter_migrates_without_loss(self):
        # Recreate the old duplicate table with typed rows, then re-init.
        conn = press._connect()
        conn.execute(
            "CREATE TABLE press_chapter (book TEXT, number INTEGER, title TEXT, "
            "words INTEGER, has_epigraph INTEGER DEFAULT 0, "
            "epigraph_text TEXT DEFAULT '', epigraph_status TEXT DEFAULT '')"
        )
        conn.execute(
            "INSERT INTO press_chapter VALUES ('old-book',1,'One',9999,1,'Ash falls.','APPROVED')"
        )
        conn.execute("INSERT INTO press_chapter VALUES ('old-book',2,'Two',9999,0,'','')")
        conn.commit()
        conn.close()
        press.cmd_init()
        conn = press._connect()
        slots = conn.execute("SELECT * FROM press_epigraph WHERE book='old-book'").fetchall()
        self.assertEqual(len(slots), 1)  # only the epigraph-bearing row carries over
        self.assertEqual(slots[0]["epigraph_status"], "APPROVED")
        legacy = conn.execute("SELECT COUNT(*) c FROM press_chapter_legacy").fetchone()
        self.assertEqual(legacy["c"], 2)  # nothing silently lost
        # idempotent re-run
        press.cmd_init()

    # ── ledger ───────────────────────────────────────────────────────────

    def test_ledger_chain_is_intact_and_ordered(self):
        slug = _styled_book("Ledger Book")
        press.lock_style(slug, "Author X")
        conn = press._connect()
        rows = conn.execute(
            "SELECT seq, kind, payload, prev_hash, hash FROM press_ledger "
            "WHERE scope=? ORDER BY seq",
            (f"book:{slug}",),
        ).fetchall()
        self.assertGreaterEqual(len(rows), 3)  # created, style.set, style.locked
        prev = press.GENESIS_HASH
        import json

        for r in rows:
            self.assertEqual(r["prev_hash"], prev)
            body = press._canon(
                {"seq": r["seq"], "kind": r["kind"], "payload": json.loads(r["payload"])}
            )
            self.assertEqual(r["hash"], press._sha(prev + body))
            prev = r["hash"]


class MockGatewayContractTests(unittest.TestCase):
    def test_mock_abstains_on_quote_request(self):
        res = MockGateway().original_epigraph({"want_quote": True})
        self.assertEqual(res.status, "ABSTAINED")
        self.assertEqual(res.text, "")
        self.assertEqual(res.attribution, "")

    def test_mock_original_is_unverified_draft(self):
        res = MockGateway().original_epigraph({"soul": "grief"})
        self.assertEqual(res.status, "UNVERIFIED_DRAFT")
        self.assertTrue(res.text)

    def test_mock_covers_are_drafts_without_assets(self):
        versions = MockGateway().cover_versions({"title": "T"}, n=3)
        self.assertEqual(len(versions), 3)
        for v in versions:
            self.assertEqual(v.status, "DRAFT")
            self.assertEqual(v.asset_ref, "")


if __name__ == "__main__":
    unittest.main()
