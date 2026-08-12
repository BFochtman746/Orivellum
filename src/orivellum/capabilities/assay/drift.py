"""ASSAY drift detectors — the four named failure modes (D14 signatures).

Each detector is a Tier-1 deterministic prose-signature check.  A detection
is a *signature match with quoted evidence*, not a verdict — D14 pairs these
signatures with Tier-2 confirmation before anything can count against a
chapter (see gates.py).

The four detectors, per the Standards Concordance:

* **Theology Lecture** — dialogue collapses into argued exposition:
  dialogue-to-exposition ratio below floor AND argumentative-register
  marker density above ceiling.
* **Catalog** — prose degrades into enumeration: comma-series runs and
  list density above ceiling.
* **Elihu** — the sudden-monologue register: sustained paragraphs of
  second-person address plus assertive modals (the signature is
  author-tunable via instrument thresholds).
* **Restoration** — resolution language appearing before its permitted
  chapter (default: prohibited before chapter 71).

Every detection carries a verbatim quote and character offset (LAW 3).
"""

from __future__ import annotations

import re

from .metrics import register_bands, split_sentences, words_of

# ── Signature lexicons (defaults; extendable via thresholds) ─────────────────

ARGUMENT_MARKERS = (
    "therefore",
    "thus",
    "hence",
    "it follows",
    "consequently",
    "for if",
    "must be",
    "cannot be",
    "one must",
    "we must conclude",
    "in other words",
    "that is to say",
    "the truth is",
    "consider that",
    "is it not",
)

ASSERTIVE_MODALS = (
    "surely",
    "must",
    "cannot",
    "will not",
    "shall",
    "certainly",
    "listen to me",
    "hear me",
    "i will answer",
    "know this",
    "mark this",
)

RESOLUTION_LEXICON = (
    "restored",
    "restoration",
    "redeemed",
    "redemption",
    "made whole",
    "whole again",
    "healed",
    "at peace",
    "comforted",
    "blessed him",
    "doubled",
    "twice as much",
    "happily",
    "all was well",
    "his latter days",
    "prospered",
    "recompense",
    "reward for his",
)


def _find_phrases(text: str, phrases: tuple[str, ...] | list[str]) -> list[tuple[int, str]]:
    """All case-insensitive whole-ish occurrences as (offset, verbatim) pairs."""
    hits: list[tuple[int, str]] = []
    low = text.lower()
    for phrase in phrases:
        start = 0
        p = phrase.lower()
        while True:
            i = low.find(p, start)
            if i < 0:
                break
            before_ok = i == 0 or not low[i - 1].isalpha()
            after = i + len(p)
            after_ok = after >= len(low) or not low[after].isalpha()
            if before_ok and after_ok:
                hits.append((i, text[i:after]))
            start = i + 1
    return sorted(hits)


def _quote_around(text: str, offset: int, span: int = 120) -> str:
    lo = max(0, offset - span // 2)
    return text[lo : lo + span].strip()


# ── Detectors ────────────────────────────────────────────────────────────────


def detect_theology_lecture(text: str, thresholds: dict | None = None) -> list[dict]:
    """Dialogue-to-exposition ratio floor + argumentative-register ceiling."""
    th = thresholds or {}
    min_dialogue_ratio = float(th.get("min_dialogue_ratio", 0.08))
    max_marker_per_1k = float(th.get("max_argument_markers_per_1000_words", 6.0))
    n_words = len(words_of(text))
    if n_words < 150:
        return []
    dialogue_ratio = register_bands(text)["dialogue_ratio"]
    markers = _find_phrases(text, tuple(th.get("argument_markers", ARGUMENT_MARKERS)))
    per_1k = len(markers) / n_words * 1000
    if dialogue_ratio < min_dialogue_ratio and per_1k > max_marker_per_1k:
        return [
            {
                "issue_type": "theology_lecture",
                "measures": {
                    "dialogue_ratio": dialogue_ratio,
                    "argument_markers_per_1000_words": round(per_1k, 2),
                },
                "quotes": [
                    {"offset": off, "quote": _quote_around(text, off)} for off, _ in markers[:5]
                ],
            }
        ]
    return []


def detect_catalog(text: str, thresholds: dict | None = None) -> list[dict]:
    """List density: long comma-series runs per 1,000 words above ceiling."""
    th = thresholds or {}
    min_series_items = int(th.get("min_series_items", 4))
    max_runs_per_1k = float(th.get("max_series_runs_per_1000_words", 3.0))
    n_words = len(words_of(text))
    if n_words < 150:
        return []
    runs: list[tuple[int, str]] = []
    # A "series run": min_series_items+ short comma-separated segments in a row.
    pattern = re.compile(
        r"(?:[^,.;!?\n]{2,40},\s+){" + str(min_series_items - 1) + r",}[^,.;!?\n]{2,40}"
    )
    for m in pattern.finditer(text):
        if m.group(0).count(",") >= min_series_items - 1:
            runs.append((m.start(), m.group(0)))
    per_1k = len(runs) / n_words * 1000
    if per_1k > max_runs_per_1k:
        return [
            {
                "issue_type": "catalog",
                "measures": {
                    "series_runs": len(runs),
                    "series_runs_per_1000_words": round(per_1k, 2),
                },
                "quotes": [{"offset": off, "quote": run[:200]} for off, run in runs[:5]],
            }
        ]
    return []


def detect_elihu(text: str, thresholds: dict | None = None) -> list[dict]:
    """The Elihu register: sustained assertive second-person monologue.

    Default signature — a paragraph run where BOTH hold for at least
    ``min_consecutive_paragraphs``: second-person density above floor and
    assertive-modal hits present.  The signature is intentionally
    author-tunable through the instrument's thresholds.
    """
    th = thresholds or {}
    min_second_person_per_100 = float(th.get("min_second_person_per_100_words", 4.0))
    min_paragraphs = int(th.get("min_consecutive_paragraphs", 3))
    modals = tuple(th.get("assertive_modals", ASSERTIVE_MODALS))
    paragraphs: list[tuple[int, str]] = []
    pos = 0
    for para in re.split(r"\n\s*\n", text or ""):
        idx = text.find(para, pos)
        paragraphs.append((idx, para))
        pos = idx + len(para)
    flagged: list[tuple[int, str]] = []
    run: list[tuple[int, str]] = []
    detections: list[dict] = []
    for off, para in paragraphs:
        tokens = [w.lower() for w in words_of(para)]
        if len(tokens) < 30:
            run = []
            continue
        second_person = sum(
            1 for w in tokens if w in ("you", "your", "yours", "thee", "thou", "thy")
        )
        per_100 = second_person / len(tokens) * 100
        has_modal = bool(_find_phrases(para, modals))
        if per_100 >= min_second_person_per_100 and has_modal:
            run.append((off, para))
            if len(run) >= min_paragraphs:
                flagged = list(run)
        else:
            run = []
    if flagged:
        detections.append(
            {
                "issue_type": "elihu",
                "measures": {"consecutive_paragraphs": len(flagged)},
                "quotes": [{"offset": off, "quote": para[:200]} for off, para in flagged[:4]],
            }
        )
    return detections


def detect_restoration(text: str, chapter_seq: int, thresholds: dict | None = None) -> list[dict]:
    """Resolution language appearing before its permitted chapter."""
    th = thresholds or {}
    prohibited_before = int(th.get("prohibited_before_chapter", 71))
    if chapter_seq >= prohibited_before:
        return []
    hits = _find_phrases(text, tuple(th.get("resolution_lexicon", RESOLUTION_LEXICON)))
    if not hits:
        return []
    return [
        {
            "issue_type": "restoration",
            "measures": {
                "chapter": chapter_seq,
                "prohibited_before_chapter": prohibited_before,
                "hits": len(hits),
            },
            "quotes": [{"offset": off, "quote": _quote_around(text, off)} for off, _ in hits[:6]],
        }
    ]


DETECTORS = {
    "drift.theology_lecture": detect_theology_lecture,
    "drift.catalog": detect_catalog,
    "drift.elihu": detect_elihu,
    # restoration takes chapter_seq — dispatched specially in __init__.py
}


def sentences_count(text: str) -> int:
    return len(split_sentences(text))
