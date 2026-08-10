"""Chapter and section extraction from plain extracted text.

Parses markdown-style headings and DOCX-style structural patterns
to produce an ordered list of chapter/section objects that map the
document's hierarchy.  Results are stored in the book_chapters table
so downstream features (completeness scoring, gap detection, research
mapping) can operate on structured objects rather than flat text.

Two-line chapter pattern:
  Many novels use the form:
      Chapter 1
      The Storm Begins
  where the number is on one line and the title is on the next.
  The ``_CHAPTER_LINE`` + ``_peek_next_line`` logic handles this.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ── Heading patterns ──────────────────────────────────────────────────────────

# Markdown headings: # Title, ## Title, ### Title
_MD_HEADING = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

# DOCX-style: ALL CAPS lines ≤80 chars that look like chapter titles,
# optionally preceded by "Chapter N" or "Part N"
_CAPS_HEADING = re.compile(
    r"^(?:(?:chapter|part|section|appendix)\s+[\d\w]+\s*[:\-–—]?\s*)?([A-Z][A-Z\s\d,\'\"\-–—:]{3,79})$",
    re.MULTILINE,
)

# "Chapter N" / "Part N" lines (case-insensitive)
# Captures: group(1)=label, group(2)=number, group(3)=inline-title (optional)
_CHAPTER_LINE = re.compile(
    r"^(chapter|part|section|appendix)\s+([\divxlc]+|[a-z]+)(?:\s*[:\-–—]\s*(.+))?$",
    re.IGNORECASE | re.MULTILINE,
)

# Scene-break markers (longest-first so overlapping patterns aren't counted twice).
_SCENE_BREAKS = ("\n* * *\n", "\n# # #\n", "\n***\n", "\n---\n", "\n###\n")


# ── Scene counting ────────────────────────────────────────────────────────────


def _count_scenes(text: str) -> int:
    """Count scene breaks within chapter text. Returns at least 1.

    Each matched marker is removed from a working copy of the text before
    the next pattern runs, preventing overlapping matches from inflating
    the count.
    """
    working = text
    count = 0
    for pat in _SCENE_BREAKS:
        n = working.count(pat)
        if n:
            count += n
            working = working.replace(pat, "\n")
    return max(1, count + 1)


# ── Two-line title peek ────────────────────────────────────────────────────────


def _peek_next_line(text: str, pos: int) -> str | None:
    """Return the next non-empty line after *pos* as a candidate chapter title.

    Returns None if the next line looks like another structural heading
    (starts with #, matches _CHAPTER_LINE, or is ALL-CAPS > 4 chars), is
    longer than 100 chars, or is empty.
    """
    rest = text[pos:].lstrip("\r\n")
    nl = rest.find("\n")
    first_line = (rest[:nl] if nl != -1 else rest).strip()
    if not first_line or len(first_line) > 100:
        return None
    if first_line.startswith("#"):
        return None
    if _CHAPTER_LINE.match(first_line):
        return None
    # ALL-CAPS line longer than 4 chars is likely another structural heading
    if first_line == first_line.upper() and len(first_line) > 4 and first_line.isalpha():
        return None
    return first_line


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class ExtractedChapter:
    seq: int
    level: int  # 1 = H1/chapter, 2 = H2/section, 3 = H3/subsection
    title: str
    text: str
    word_count: int = field(init=False)
    scene_count: int = field(init=False)

    def __post_init__(self) -> None:
        self.word_count = len(self.text.split())
        self.scene_count = _count_scenes(self.text)


# ── Public API ────────────────────────────────────────────────────────────────


def extract_chapters(text: str, min_section_words: int = 20) -> list[ExtractedChapter]:
    """Extract chapter/section structure from plain extracted text.

    Strategy (in priority order):
    1. Markdown headings (#, ##, ###)
    2. "Chapter N" / "Part N" lines — with two-line title look-ahead
    3. ALL-CAPS lines that look like titles (DOCX fallback)

    Returns an ordered list of ExtractedChapter.  Sections with fewer
    than `min_section_words` of body text are merged into the preceding
    section rather than emitted as standalone entries, so shallow
    heading hierarchies don't produce dozens of empty chapters.
    """
    if not text or not text.strip():
        return []

    headings = _find_headings(text)

    if not headings:
        return []

    chapters = _split_into_chapters(text, headings, min_section_words)
    return chapters


# ── Internal helpers ──────────────────────────────────────────────────────────


def _find_headings(text: str) -> list[tuple[int, int, str]]:
    """Return list of (offset, level, title) sorted by offset."""
    found: dict[int, tuple[int, int, str]] = {}

    # 1. Markdown headings
    for m in _MD_HEADING.finditer(text):
        level = len(m.group(1))
        title = m.group(2).strip()
        found[m.start()] = (m.start(), level, title)

    # Only fall back to other patterns if no markdown headings were found
    if not found:
        # 2. Explicit chapter/part lines
        for m in _CHAPTER_LINE.finditer(text):
            label = m.group(1).capitalize()
            num = m.group(2)
            name = m.group(3)
            if name:
                title = f"{label} {num}: {name.strip()}"
            else:
                # No inline title — look at the very next non-empty line
                # for the two-line pattern: "Chapter 1\nThe Beginning"
                next_title = _peek_next_line(text, m.end())
                title = f"{label} {num}" + (f": {next_title}" if next_title else "")
            found[m.start()] = (m.start(), 1, title)

    if not found:
        # 3. ALL-CAPS fallback
        for m in _CAPS_HEADING.finditer(text):
            raw = m.group(1).strip()
            # Heuristic: skip very short or very long matches
            if 4 <= len(raw) <= 80 and not raw.isdigit():
                title = raw.title()  # convert to Title Case for readability
                found[m.start()] = (m.start(), 1, title)

    return sorted(found.values(), key=lambda x: x[0])


def _split_into_chapters(
    text: str,
    headings: list[tuple[int, int, str]],
    min_section_words: int,
) -> list[ExtractedChapter]:
    """Slice `text` at heading positions and build ExtractedChapter objects."""
    chapters: list[ExtractedChapter] = []

    for i, (offset, level, title) in enumerate(headings):
        # Body text runs from after this heading line to the next heading (or end)
        # Find end of the heading line first
        eol = text.find("\n", offset)
        body_start = eol + 1 if eol != -1 else offset + len(title) + level + 1

        if i + 1 < len(headings):
            body_end = headings[i + 1][0]
        else:
            body_end = len(text)

        body = text[body_start:body_end].strip()

        # Merge very short sections into the previous chapter's text
        if len(body.split()) < min_section_words and chapters:
            prev = chapters[-1]
            chapters[-1] = ExtractedChapter(
                seq=prev.seq,
                level=prev.level,
                title=prev.title,
                text=(prev.text + "\n\n" + body).strip(),
            )
            continue

        chapters.append(
            ExtractedChapter(
                seq=i,
                level=level,
                title=_clean_title(title),
                text=body,
            )
        )

    # Re-number seqs after possible merges
    for idx, ch in enumerate(chapters):
        ch.seq = idx

    return chapters


def _clean_title(title: str) -> str:
    """Strip excess punctuation and whitespace from a detected heading."""
    title = title.strip(" \t\r\n#*_-–—")
    # Collapse internal whitespace
    title = re.sub(r"\s{2,}", " ", title)
    return title
