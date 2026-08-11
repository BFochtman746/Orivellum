"""ATELIER self-tests, restored into the suite (audit D-08).

Covers the brand cascade lock discipline, spine/wrap math, cover generation
gating, design verification, and sealing.
"""

from __future__ import annotations

import tempfile
import unittest

from orivellum.capabilities.finishing import atelier

FULL_BRAND = {
    "body_font": "Garamond",
    "heading_font": "Trajan",
    "palette": "ash grey / ember orange",
    "imagery": "storm over ruined fields",
    "composition": "central figure, low horizon",
    "title_pos": "upper third",
    "author_pos": "lower band",
    "logo": "sigil bottom-left",
}


class AtelierTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        atelier.configure(self._tmp.name)
        atelier.cmd_init()

    def tearDown(self):
        self._tmp.cleanup()

    def _locked_series(self, name="Job Cycle", books=3):
        s = atelier.create_series(name, books=books)
        atelier.update_series_brand(s["slug"], FULL_BRAND)
        atelier.lock_series(s["slug"], "Author X")
        return s["slug"]

    # ── spine / wrap math ────────────────────────────────────────────────

    def test_spine_width_formula(self):
        # pages × factor + allowance
        self.assertAlmostEqual(atelier.spine_width(300, "cream"), 300 * 0.0025 + 0.06, places=6)

    def test_wrap_dimensions(self):
        w = atelier.wrap_dimensions("6x9", 300, "cream")
        spine = 300 * 0.0025 + 0.06
        self.assertAlmostEqual(w["full_cover_width"], 2 * 0.125 + 2 * 6.0 + spine, places=4)
        self.assertAlmostEqual(w["full_cover_height"], 9.0 + 0.25, places=4)
        self.assertTrue(w["spine_text_allowed"])  # 300 ≥ 79 pages

    def test_spine_text_gated_below_79_pages(self):
        w = atelier.wrap_dimensions("6x9", 60, "cream")
        self.assertFalse(w["spine_text_allowed"])

    # ── brand cascade discipline ─────────────────────────────────────────

    def test_locked_series_brand_is_immutable(self):
        slug = self._locked_series()
        with self.assertRaises(PermissionError):
            atelier.update_series_brand(slug, {"palette": "neon pink"})

    def test_covers_refused_until_series_locked(self):
        s = atelier.create_series("Unlocked", books=1)
        atelier.update_series_brand(s["slug"], FULL_BRAND)
        atelier.create_book(s["slug"], "Early Book")
        with self.assertRaises(PermissionError):
            atelier.generate_covers("early-book")

    def test_book_number_must_fit_series(self):
        slug = self._locked_series(books=2)
        with self.assertRaises(ValueError):
            atelier.create_book(slug, "Fourth Book", number=4)

    def test_unknown_trim_rejected(self):
        slug = self._locked_series("Trim Series")
        with self.assertRaises(ValueError):
            atelier.create_book(slug, "Odd Trim", trim="3x5")

    # ── cover generation + seal ──────────────────────────────────────────

    def test_generate_verify_seal_flow(self):
        slug = self._locked_series("Sealed Series")
        atelier.create_book(slug, "The Ash Court", pages=320)
        versions = atelier.generate_covers("the-ash-court", versions=3, gateway_name="mock")
        self.assertEqual(len(versions), 3)
        for v in versions:
            self.assertEqual(v["status"], "DRAFT")
            self.assertTrue(v["prompt"])

        vr = atelier.verify_design("the-ash-court")
        self.assertTrue(vr["passed"], vr["checks"])

        manifest = atelier.seal_design("the-ash-court", "Author X", versions[0]["version_id"])
        self.assertEqual(manifest["chosen_cover"], versions[0]["version_id"])
        self.assertTrue(manifest["package_sha256"])

    def test_seal_refused_without_cover_version(self):
        slug = self._locked_series("No Cover")
        atelier.create_book(slug, "Coverless")
        with self.assertRaises(ValueError):
            atelier.seal_design("coverless", "Author X", "deadbeef")


if __name__ == "__main__":
    unittest.main()
