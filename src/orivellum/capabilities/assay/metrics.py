"""ASSAY voice metrics — the measurable voice envelope (A4/M0).

Everything here is deterministic and computable from prose alone:

* sentence-length distribution (mean, sd, long/short ratio)
* lexical register bands (latinate ratio, dialogue ratio, type/token ratio)
* imagery density per 1,000 words (concrete-sensory lexicon hits)
* per-character diction fingerprints (attributed dialogue statistics)

"A voice spec you cannot compute against is a wish" — these functions ARE
the spec.  The target envelope is produced by running them over the
author's reference passages and storing the result as a per-work baseline;
chapters are then compared with z-scores against that envelope.
"""

from __future__ import annotations

import math
import re
from collections import Counter

# Sentence-length band edges (words).  Overridable via instrument thresholds.
LONG_SENTENCE_WORDS = 25
SHORT_SENTENCE_WORDS = 7

_SENT_SPLIT = re.compile(r"(?<=[.!?])[\"'\u201d\u2019]?\s+")
_WORD = re.compile(r"[A-Za-z][A-Za-z'\-]*")
# Latinate register heuristic: polysyllabic suffixes typical of formal register.
_LATINATE = re.compile(r"(?:tion|sion|ment|ance|ence|ity|ous|ive|ate|ify|ology|ism|able|ible)$")
# Dialogue: straight or curly double-quoted spans.
_DIALOGUE = re.compile(r"[\"\u201c]([^\"\u201d]{2,600})[\"\u201d]")

# Concrete-sensory lexicon for imagery density.  Deliberately small and
# domain-leaning (dust, stone, ash — an Iron Age register); extend via the
# instrument's thresholds ("imagery_lexicon_extra").
IMAGERY_LEXICON = frozenset(
    (
        "ash",
        "dust",
        "stone",
        "rock",
        "sand",
        "clay",
        "iron",
        "bronze",
        "blood",
        "bone",
        "flesh",
        "skin",
        "hair",
        "fire",
        "flame",
        "ember",
        "smoke",
        "shadow",
        "dark",
        "darkness",
        "light",
        "sun",
        "moon",
        "star",
        "dawn",
        "dusk",
        "water",
        "river",
        "rain",
        "well",
        "spring",
        "sea",
        "salt",
        "wind",
        "storm",
        "thunder",
        "cloud",
        "sky",
        "bread",
        "wine",
        "oil",
        "honey",
        "milk",
        "fig",
        "olive",
        "vine",
        "wheat",
        "barley",
        "thorn",
        "root",
        "branch",
        "wool",
        "linen",
        "leather",
        "rope",
        "tent",
        "wall",
        "gate",
        "door",
        "threshold",
        "hearth",
        "lamp",
        "sword",
        "spear",
        "shield",
        "harp",
        "lyre",
        "drum",
        "horn",
        "dog",
        "sheep",
        "goat",
        "ox",
        "donkey",
        "camel",
        "lion",
        "serpent",
        "bird",
        "raven",
        "dove",
        "locust",
        "worm",
        "red",
        "crimson",
        "scarlet",
        "gold",
        "silver",
        "grey",
        "gray",
        "black",
        "white",
        "green",
        "blue",
        "purple",
        "cold",
        "heat",
        "warm",
        "bitter",
        "sweet",
        "sour",
        "dry",
        "wet",
        "rough",
        "smooth",
        "sharp",
        "dull",
        "heavy",
    )
)


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences (simple terminal-punctuation heuristic)."""
    parts = [s.strip() for s in _SENT_SPLIT.split(text or "")]
    return [s for s in parts if s and _WORD.search(s)]


def words_of(text: str) -> list[str]:
    return _WORD.findall(text or "")


def sentence_length_stats(
    text: str,
    *,
    long_words: int = LONG_SENTENCE_WORDS,
    short_words: int = SHORT_SENTENCE_WORDS,
) -> dict:
    """Mean/sd sentence length in words + the long/short sentence ratio."""
    sentences = split_sentences(text)
    lengths = [len(words_of(s)) for s in sentences]
    n = len(lengths)
    if n == 0:
        return {"sentences": 0, "mean": 0.0, "sd": 0.0, "long_short_ratio": 0.0}
    mean = sum(lengths) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in lengths) / n) if n > 1 else 0.0
    n_long = sum(1 for x in lengths if x >= long_words)
    n_short = sum(1 for x in lengths if x <= short_words)
    return {
        "sentences": n,
        "mean": round(mean, 2),
        "sd": round(sd, 2),
        "long_short_ratio": round(n_long / max(n_short, 1), 3),
    }


def register_bands(text: str) -> dict:
    """Lexical register bands: latinate ratio, dialogue ratio, type/token ratio."""
    tokens = [w.lower() for w in words_of(text)]
    n = len(tokens)
    if n == 0:
        return {"latinate_ratio": 0.0, "dialogue_ratio": 0.0, "type_token_ratio": 0.0}
    latinate = sum(1 for w in tokens if len(w) > 6 and _LATINATE.search(w))
    dialogue_chars = sum(len(m.group(1)) for m in _DIALOGUE.finditer(text))
    return {
        "latinate_ratio": round(latinate / n, 4),
        "dialogue_ratio": round(min(dialogue_chars / max(len(text), 1), 1.0), 4),
        "type_token_ratio": round(len(set(tokens)) / n, 4),
    }


def imagery_density(text: str, extra_lexicon: list[str] | None = None) -> dict:
    """Concrete-sensory lexicon hits per 1,000 words, with the hit words."""
    lexicon = IMAGERY_LEXICON | {w.lower() for w in (extra_lexicon or [])}
    tokens = [w.lower() for w in words_of(text)]
    n = len(tokens)
    hits = [w for w in tokens if w in lexicon]
    per_1k = (len(hits) / n * 1000) if n else 0.0
    top = [w for w, _ in Counter(hits).most_common(10)]
    return {"per_1000_words": round(per_1k, 2), "hits": len(hits), "top_words": top}


def dialogue_spans(text: str) -> list[tuple[int, str]]:
    """All double-quoted dialogue spans as (offset, quote) pairs."""
    return [(m.start(1), m.group(1)) for m in _DIALOGUE.finditer(text or "")]


def diction_fingerprints(
    text: str, character_names: list[str], *, window: int = 160
) -> dict[str, dict]:
    """Per-character diction fingerprint from attributed dialogue.

    Attribution heuristic: a quoted span belongs to the character whose name
    appears nearest to it within ``window`` characters (before or after).
    Spans with no name nearby are skipped — a wrong attribution is worse
    than a missing one.
    """
    fingerprints: dict[str, dict] = {}
    if not character_names:
        return fingerprints
    lines: dict[str, list[str]] = {name: [] for name in character_names}
    for offset, quote in dialogue_spans(text):
        best_name, best_dist = None, window + 1
        lo = max(0, offset - window)
        hi = min(len(text), offset + len(quote) + window)
        vicinity = text[lo:hi]
        for name in character_names:
            for m in re.finditer(re.escape(name), vicinity):
                dist = abs((lo + m.start()) - offset)
                if dist < best_dist:
                    best_name, best_dist = name, dist
        if best_name is not None:
            lines[best_name].append(quote)
    for name, quotes in lines.items():
        if not quotes:
            continue
        joined = " ".join(quotes)
        tokens = [w.lower() for w in words_of(joined)]
        distinctive = [
            w for w, c in Counter(t for t in tokens if len(t) > 4).most_common(8) if c >= 2
        ]
        fingerprints[name] = {
            "lines": len(quotes),
            "mean_line_words": round(len(tokens) / len(quotes), 2),
            "distinctive_words": distinctive,
        }
    return fingerprints


def compute_voice_metrics(
    text: str,
    *,
    character_names: list[str] | None = None,
    thresholds: dict | None = None,
) -> dict:
    """The full measurable voice envelope for one passage/chapter."""
    th = thresholds or {}
    return {
        "word_count": len(words_of(text)),
        "sentence_lengths": sentence_length_stats(
            text,
            long_words=int(th.get("long_sentence_words", LONG_SENTENCE_WORDS)),
            short_words=int(th.get("short_sentence_words", SHORT_SENTENCE_WORDS)),
        ),
        "register": register_bands(text),
        "imagery": imagery_density(text, th.get("imagery_lexicon_extra")),
        "diction_fingerprints": diction_fingerprints(text, character_names or []),
    }


# Metric paths compared against the envelope, with the baseline keys that
# hold their target mean and tolerance (sd multiples).
_COMPARABLE: list[tuple[str, ...]] = [
    ("sentence_lengths", "mean"),
    ("sentence_lengths", "sd"),
    ("sentence_lengths", "long_short_ratio"),
    ("register", "latinate_ratio"),
    ("register", "dialogue_ratio"),
    ("register", "type_token_ratio"),
    ("imagery", "per_1000_words"),
]


def _dig(d: dict, path: tuple[str, ...]) -> float:
    cur: object = d
    for p in path:
        if not isinstance(cur, dict) or p not in cur:
            return 0.0
        cur = cur[p]
    return float(cur) if isinstance(cur, (int, float)) else 0.0


def compare_to_envelope(
    metrics: dict, envelope: dict, *, max_deviation_pct: float = 35.0
) -> list[dict]:
    """Compare chapter metrics against the target envelope.

    A metric deviates when it differs from the envelope target by more than
    ``max_deviation_pct`` percent (relative; absolute for near-zero targets).
    Returns a list of deviation dicts — empty means inside the envelope.
    """
    deviations: list[dict] = []
    for path in _COMPARABLE:
        target = _dig(envelope, path)
        actual = _dig(metrics, path)
        if target == 0.0 and actual == 0.0:
            continue
        denom = abs(target) if abs(target) > 1e-9 else 1.0
        pct = abs(actual - target) / denom * 100.0
        if pct > max_deviation_pct:
            deviations.append(
                {
                    "metric": ".".join(path),
                    "target": target,
                    "actual": actual,
                    "deviation_pct": round(pct, 1),
                }
            )
    return deviations
