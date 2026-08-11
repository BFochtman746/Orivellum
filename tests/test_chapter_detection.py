"""Chapter-detection doctrine tests (audit D-08).

Locks in the two-line heading pattern ("Chapter 1" \\n "The Storm Begins"),
scene-break counting with overlap suppression, markdown priority, and the
short-section merge behavior of ``capabilities/chapters.py``.
"""

from __future__ import annotations

import unittest

from orivellum.capabilities.chapters import extract_chapters

BODY = "word " * 60  # comfortably above min_section_words


class TwoLineHeadingTests(unittest.TestCase):
    def test_two_line_chapter_title_is_captured(self):
        text = f"Chapter 1\nThe Storm Begins\n\n{BODY}\nChapter 2\nAsh Falls\n\n{BODY}"
        chapters = extract_chapters(text)
        self.assertEqual(len(chapters), 2)
        self.assertEqual(chapters[0].title, "Chapter 1: The Storm Begins")
        self.assertEqual(chapters[1].title, "Chapter 2: Ash Falls")

    def test_inline_title_still_works(self):
        text = f"Chapter 1: The Storm Begins\n\n{BODY}\nChapter 2 — Ash Falls\n\n{BODY}"
        chapters = extract_chapters(text)
        self.assertEqual(len(chapters), 2)
        self.assertIn("The Storm Begins", chapters[0].title)

    def test_next_structural_heading_is_not_stolen_as_title(self):
        # "Chapter 2" directly after "Chapter 1" must not become chapter 1's title
        text = f"Chapter 1\nChapter 2\n{BODY}"
        chapters = extract_chapters(text)
        self.assertEqual(chapters[0].title, "Chapter 1")

    def test_roman_numerals_and_parts(self):
        text = f"Part I\nThe Descent\n\n{BODY}\nChapter IV\n\n{BODY}"
        chapters = extract_chapters(text)
        self.assertEqual(chapters[0].title, "Part I: The Descent")
        self.assertEqual(chapters[1].title, "Chapter IV")


class SceneBreakTests(unittest.TestCase):
    def test_scene_breaks_counted(self):
        text = f"Chapter 1\nStorm\n\n{BODY}\n* * *\n{BODY}\n* * *\n{BODY}"
        chapters = extract_chapters(text)
        self.assertEqual(chapters[0].scene_count, 3)

    def test_overlapping_markers_not_double_counted(self):
        # Match-removal: "\n# # #\n" must count once and must not also feed
        # the "\n###\n" pattern. Tested on _count_scenes directly because a
        # bare "# # #" line reads as a markdown heading to the extractor.
        from orivellum.capabilities.chapters import _count_scenes

        self.assertEqual(_count_scenes(f"{BODY}\n# # #\n{BODY}"), 2)
        self.assertEqual(_count_scenes(f"{BODY}\n* * *\n{BODY}\n***\n{BODY}"), 3)

    def test_chapter_without_breaks_is_one_scene(self):
        text = f"Chapter 1\nStorm\n\n{BODY}"
        chapters = extract_chapters(text)
        self.assertEqual(chapters[0].scene_count, 1)


class StrategyPriorityTests(unittest.TestCase):
    def test_markdown_headings_take_priority(self):
        text = f"# The Storm\n\n{BODY}\n## Aftermath\n\n{BODY}"
        chapters = extract_chapters(text)
        self.assertEqual(chapters[0].title, "The Storm")
        self.assertEqual(chapters[0].level, 1)
        self.assertEqual(chapters[1].level, 2)

    def test_short_sections_merge_into_previous(self):
        text = f"# One\n\n{BODY}\n# Stub\n\ntiny\n# Three\n\n{BODY}"
        chapters = extract_chapters(text)
        titles = [c.title for c in chapters]
        self.assertNotIn("Stub", titles)
        self.assertEqual(len(chapters), 2)

    def test_empty_text_returns_nothing(self):
        self.assertEqual(extract_chapters(""), [])
        self.assertEqual(extract_chapters("   \n\n  "), [])

    def test_word_counts_populated(self):
        text = f"Chapter 1\nStorm\n\n{BODY}"
        chapters = extract_chapters(text)
        self.assertGreaterEqual(chapters[0].word_count, 55)


if __name__ == "__main__":
    unittest.main()
