"""Tests for the Forge design-standards gates (a11y, performance, contrast,
design quality) added with the SiteCraft/Artisan upgrade."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orivellum.capabilities.forge.gates_design import (
    contrast_ratio,
    gate_a11y,
    gate_contrast,
    gate_design_quality,
    gate_performance,
    relative_luminance,
)

GOOD_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="description" content="A fine page.">
  <title>Home</title>
</head>
<body>
  <nav><a href="index.html">Home</a></nav>
  <main>
    <h1>One heading</h1>
    <img src="a.png" alt="First" width="640" height="480">
    <img src="b.png" alt="Second" width="640" height="480" loading="lazy">
    <form><label for="q">Search</label><input id="q" type="text"></form>
    <button type="button">Act</button>
  </main>
  <footer>fin</footer>
</body>
</html>
"""

BAD_PAGE = """<!DOCTYPE html>
<html>
<head><title>Bad</title></head>
<body>
  <h1>First</h1>
  <h1>Second</h1>
  <img src="a.png">
  <img src="b.png">
  <div onclick="go()">Click me</div>
  <input type="text">
</body>
</html>
"""


def _write(tmp: str, name: str, content: str) -> Path:
    p = Path(tmp) / name
    p.write_text(content, encoding="utf-8")
    return p


class TestContrastMath(unittest.TestCase):
    def test_luminance_bounds(self):
        self.assertAlmostEqual(relative_luminance("#000000"), 0.0, places=5)
        self.assertAlmostEqual(relative_luminance("#ffffff"), 1.0, places=5)
        self.assertIsNone(relative_luminance("nope"))

    def test_ratio_black_on_white(self):
        self.assertAlmostEqual(contrast_ratio("#000", "#fff"), 21.0, places=1)

    def test_ratio_symmetric(self):
        self.assertEqual(contrast_ratio("#333333", "#eeeeee"), contrast_ratio("#eee", "#333"))


class TestA11yGate(unittest.TestCase):
    def test_clean_page_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "index.html", GOOD_PAGE)
            r = gate_a11y(Path(tmp))
        self.assertEqual(r["status"], "passed")

    def test_violations_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "index.html", BAD_PAGE)
            r = gate_a11y(Path(tmp))
        self.assertEqual(r["status"], "conditional")
        d = r["detail"]
        self.assertIn("lang", d)
        self.assertIn("<h1>", d)
        self.assertIn("alt", d)
        # never blocked — repair loop handles these
        self.assertNotEqual(r["status"], "blocked")

    def test_single_quoted_and_unquoted_attributes_accepted(self):
        """html.parser handles quote styles a regex would false-flag."""
        page = (
            "<!DOCTYPE html><html lang=en><head>"
            "<meta name='viewport' content='width=device-width'>"
            "<meta name='description' content='x'><title>t</title></head>"
            "<body><main><h1>One</h1>"
            "<img src=a.png alt='pic' width=10 height=10></main></body></html>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "index.html", page)
            self.assertEqual(gate_a11y(Path(tmp))["status"], "passed")
            self.assertEqual(gate_performance(Path(tmp))["status"], "passed")

    def test_mixed_labeled_and_unlabeled_controls(self):
        """One good label must not mask other unlabeled controls."""
        page = (
            '<!DOCTYPE html><html lang="en"><head>'
            '<meta name="viewport" content="w"><meta name="description" content="x">'
            "<title>t</title></head><body><main><h1>One</h1>"
            '<form><label for="a">A</label><input id="a" type="text">'
            '<input id="b" type="text"><input type="hidden" name="h"></form>'
            "</main></body></html>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "index.html", page)
            r = gate_a11y(Path(tmp))
        self.assertEqual(r["status"], "conditional")
        self.assertIn("1 form control(s)", r["detail"])


class TestPerformanceGate(unittest.TestCase):
    def test_clean_page_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "index.html", GOOD_PAGE)
            r = gate_performance(Path(tmp))
        self.assertEqual(r["status"], "passed")

    def test_missing_dims_and_lazy_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "index.html", BAD_PAGE)
            r = gate_performance(Path(tmp))
        self.assertEqual(r["status"], "conditional")
        self.assertIn("width/height", r["detail"])
        self.assertIn("lazy", r["detail"])
        self.assertIn("description", r["detail"])

    def test_every_below_fold_image_must_be_lazy(self):
        """One lazy image must not excuse the rest."""
        page = (
            '<!DOCTYPE html><html lang="en"><head>'
            '<meta name="viewport" content="w"><meta name="description" content="x">'
            "<title>t</title></head><body><main><h1>One</h1>"
            '<img src="a.png" alt="a" width="1" height="1">'
            '<img src="b.png" alt="b" width="1" height="1" loading="lazy">'
            '<img src="c.png" alt="c" width="1" height="1">'
            "</main></body></html>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "index.html", page)
            r = gate_performance(Path(tmp))
        self.assertEqual(r["status"], "conditional")
        self.assertIn("1 below-the-fold image(s)", r["detail"])


class TestContrastGate(unittest.TestCase):
    def test_good_pair_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "design-tokens.css",
                ":root { --color-text: #1a1a1a; --color-background: #faf8f4; }",
            )
            r = gate_contrast(Path(tmp))
        self.assertEqual(r["status"], "passed")

    def test_low_contrast_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "design-tokens.css",
                ":root { --color-text: #999999; --color-background: #aaaaaa; }",
            )
            r = gate_contrast(Path(tmp))
        self.assertEqual(r["status"], "conditional")
        self.assertIn("4.5:1", r["detail"])

    def test_missing_tokens_conditional(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = gate_contrast(Path(tmp))
        self.assertEqual(r["status"], "conditional")

    def test_alpha_hex_not_treated_as_opaque(self):
        """8-digit (alpha) hex tokens cannot be verified — must be flagged,
        not silently truncated to their first six digits."""
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "design-tokens.css",
                ":root { --color-text: #1a1a1aff; --color-background: #faf8f4; }",
            )
            r = gate_contrast(Path(tmp))
        self.assertEqual(r["status"], "conditional")
        self.assertIn("cannot verify", r["detail"])


class TestDesignQualityGate(unittest.TestCase):
    def test_token_discipline_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "design-tokens.css",
                ":root { --color-text:#111; --color-background:#fefefe; "
                "--font-display:'Fraunces'; --font-body:'Inter'; }",
            )
            _write(
                tmp,
                "styles.css",
                "body { color: var(--color-text); background: var(--color-background); "
                "font-family: var(--font-body), sans-serif; } "
                "h1 { font-family: 'Fraunces', serif; }",
            )
            r = gate_design_quality(Path(tmp))
        self.assertEqual(r["status"], "passed")

    def test_too_many_fonts_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "design-tokens.css", ":root { --color-text:#111; }")
            _write(
                tmp,
                "styles.css",
                "h1{font-family:'Alpha'} h2{font-family:'Beta'} p{font-family:'Gamma'}",
            )
            r = gate_design_quality(Path(tmp))
        self.assertEqual(r["status"], "conditional")
        self.assertIn("typeface", r["detail"])

    def test_purple_gradient_trope_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "design-tokens.css", ":root { --color-text:#111; }")
            _write(
                tmp,
                "styles.css",
                ".hero { background: linear-gradient(135deg, #8b5cf6, #6d28d9); }",
            )
            r = gate_design_quality(Path(tmp))
        self.assertEqual(r["status"], "conditional")
        self.assertIn("purple", r["detail"])

    def test_on_palette_purple_gradient_allowed(self):
        """A purple BRAND (colors present in the token sheet) is legitimate."""
        with tempfile.TemporaryDirectory() as tmp:
            _write(
                tmp,
                "design-tokens.css",
                ":root { --color-text:#111; --color-primary:#8b5cf6; --color-accent:#6d28d9; }",
            )
            _write(
                tmp,
                "styles.css",
                "body{color:var(--color-text)} "
                ".hero { background: linear-gradient(135deg, #8b5cf6, #6d28d9); }",
            )
            r = gate_design_quality(Path(tmp))
        self.assertNotIn("purple", r.get("detail", ""))

    def test_hardcoded_hex_flood_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "design-tokens.css", ":root { --color-text:#111; }")
            colors = " ".join(f".c{i}{{color:#0{i}0a{i}f}}" for i in range(1, 9))
            colors += " ".join(f".d{i}{{background:#{i}{i}00aa}}" for i in range(1, 8))
            _write(tmp, "styles.css", colors)
            r = gate_design_quality(Path(tmp))
        self.assertEqual(r["status"], "conditional")
        self.assertIn("hardcoded", r["detail"])


class TestGateWiring(unittest.TestCase):
    def test_design_gates_run_in_pipeline(self):
        """run_quality_gates includes the four new design gates."""
        from orivellum.capabilities.forge.gates import run_quality_gates

        with tempfile.TemporaryDirectory() as tmp:
            _write(tmp, "index.html", GOOD_PAGE)
            _write(tmp, "styles.css", "body{color:var(--color-text)}")
            _write(tmp, "app.js", "console.log('ok');")
            _write(
                tmp,
                "design-tokens.css",
                ":root { --color-text:#1a1a1a; --color-background:#faf8f4; --font-body:'Inter'; }",
            )
            summary = run_quality_gates(Path(tmp))
        names = {g["name"] for g in summary["gates"]}
        for expected in ("a11y", "performance", "contrast", "design_quality"):
            self.assertIn(expected, names)
        # a clean minimal build must not be blocked by the new gates
        self.assertIn(summary["status"], ("passed", "conditional"))
        blocked = [g for g in summary["gates"] if g["status"] == "blocked"]
        self.assertEqual(blocked, [])


if __name__ == "__main__":
    unittest.main()
