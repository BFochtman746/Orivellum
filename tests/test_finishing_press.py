"""PRESS self-tests, restored into the suite (audit D-08).

Covers the immutable style lock, chapter-number rendering, the epigraph
abstain contract, pre-flight verification, and the hash-chained ledger.
Everything runs against a temp press.db via ``press.configure``.
"""

from __future__ import annotations

import tempfile
import unittest

from orivellum.capabilities.finishing import press
from orivellum.capabilities.finishing.gateway import MockGateway


def _styled_book(title="Ash and Silence"):
    b = press.create_book(title, "Author X", series="Job Cycle")
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

    # ── epigraph contract ────────────────────────────────────────────────

    def test_epigraph_slot_respects_policy_off(self):
        b = press.create_book("No Epigraphs", "Author X")
        press.update_style(b["slug"], {"epigraphs": "off"})
        with self.assertRaises(ValueError):
            press.add_chapter(b["slug"], 1, "One", words=1000, has_epigraph=True)

    def test_quoted_epigraph_abstains(self):
        slug = _styled_book("Quote Refuser")
        press.add_chapter(slug, 1, "The Storm", words=3000, has_epigraph=True)
        result = press.draft_epigraph(slug, 1, soul="grief", want_quote=True)
        self.assertEqual(result["status"], "ABSTAINED")

    def test_original_epigraph_draft_and_approve(self):
        slug = _styled_book("Original Only")
        press.add_chapter(slug, 1, "The Storm", words=3000, has_epigraph=True)
        result = press.draft_epigraph(slug, 1, soul="grief", in_world="The Uz Fragments")
        self.assertEqual(result["status"], "UNVERIFIED_DRAFT")
        press.approve_epigraph(slug, 1, "Author X")
        book = press.get_book(slug)
        self.assertEqual(book["chapters"][0]["epigraph_status"], "APPROVED")

    # ── pre-flight verify ────────────────────────────────────────────────

    def test_verify_requires_contiguous_titled_chapters(self):
        slug = _styled_book("Gappy Book")
        press.lock_style(slug, "Author X")
        press.add_chapter(slug, 1, "One", words=2000)
        press.add_chapter(slug, 3, "Three", words=2000)  # gap at 2
        press.set_matter(slug, front=True, back=True)
        vr = press.verify(slug)
        self.assertFalse(vr["passed"])
        self.assertFalse(vr["checks"]["chapters_contiguous"])

    def test_verify_blocks_unapproved_epigraph(self):
        slug = _styled_book("Unapproved Epigraph")
        press.lock_style(slug, "Author X")
        press.add_chapter(slug, 1, "One", words=2000, has_epigraph=True)
        press.draft_epigraph(slug, 1, soul="loss")
        press.set_matter(slug, front=True, back=True)
        vr = press.verify(slug)
        self.assertFalse(vr["checks"]["epigraph_policy"])

    def test_build_package_blocked_on_failed_preflight(self):
        slug = _styled_book("Not Ready")
        # style not locked, no chapters
        with self.assertRaises(ValueError):
            press.build_package(slug, "publisher", "production")
        # submission manuscript format is the only pre-typeset exception
        pkg = press.build_package(slug, "publisher", "submission")
        self.assertEqual(pkg["spec"]["format"], "standard-manuscript-format")

    def test_page_estimate_rounds_to_even(self):
        slug = _styled_book("Page Count")
        press.lock_style(slug, "Author X")
        press.add_chapter(slug, 1, "One", words=301)  # 2 body pages
        press.set_matter(slug, front=True, back=True)
        vr = press.verify(slug)
        self.assertEqual(vr["estimated_pages"] % 2, 0)

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
