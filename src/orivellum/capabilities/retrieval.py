"""Adaptive query-type classifier and retrieval strategy configurations.

Classifies each incoming chat message into one of four types:
  FACTUAL        — specific single-value lookups (who, what year, how many)
  SYNTHESIS      — broad summarization / theme extraction across many items
  COMPARISON     — two or more named entities to compare side-by-side
  CONVERSATIONAL — small-talk / meta questions; minimal retrieval needed

Each type maps to a :class:`RetrievalConfig` that controls:
  - How many knowledge items and document chunks to fetch (top_k_*)
  - The relative weight of BM25/FTS vs semantic vector hits (fts_weight /
    semantic_weight) passed into the hybrid-search functions
  - Whether to run per-entity sub-queries (multi_entity, COMPARISON only)

Classification is:
  1. Fast regex / keyword heuristics — unambiguous cases resolved in-process.
  2. Results are cached by message hash so retries and React StrictMode
     double-invocations never re-classify the same message.
  3. An LLM fallback is deliberately not included in v1: the rule-based
     pass handles the common cases with zero added latency, and a forced LLM
     call on every message would block the response path.  If needed in v2,
     the ``db`` parameter in :func:`classify_query` is reserved for it.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)


# ── Query types ────────────────────────────────────────────────────────────────


class QueryType(str, Enum):
    """The four query categories the adaptive retrieval router recognises."""

    FACTUAL = "FACTUAL"
    SYNTHESIS = "SYNTHESIS"
    COMPARISON = "COMPARISON"
    CONVERSATIONAL = "CONVERSATIONAL"


# ── Retrieval config ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RetrievalConfig:
    """Tuned retrieval parameters for one :class:`QueryType`.

    Attributes:
        top_k_knowledge: Max knowledge items to request from hybrid search.
        top_k_chunks:    Max document chunks to request from hybrid search.
        fts_weight:      Relative weight applied to BM25/FTS ranked hits
                         in the RRF fusion step.  Range 0–1; larger → exact
                         keyword matches rank higher.
        semantic_weight: Relative weight applied to cosine-similarity hits
                         in the RRF fusion step.  Range 0–1; larger → broad
                         conceptual matches rank higher.
        multi_entity:    When True, :func:`extract_comparison_entities` is
                         used to split the query into subjects and a separate
                         retrieval call is issued for each, then results are
                         merged and de-duplicated.  Used for COMPARISON only.
        label:           Short human-readable tag for logs and message meta.
    """

    top_k_knowledge: int
    top_k_chunks: int
    fts_weight: float
    semantic_weight: float
    multi_entity: bool
    label: str


# ── Per-type configs ───────────────────────────────────────────────────────────
#
# FACTUAL   — precision over recall: fewer items, heavy FTS bias so exact
#             proper nouns / numbers surface first.
# SYNTHESIS — recall over precision: many items, heavy semantic bias so
#             thematically related content surfaces even when wording differs.
# COMPARISON — per-entity retrieval; balanced weights; moderate counts.
# CONVERSATIONAL — minimal retrieval; context window not worth filling for
#             greetings or meta-questions that need no document evidence.

RETRIEVAL_CONFIGS: dict[QueryType, RetrievalConfig] = {
    QueryType.FACTUAL: RetrievalConfig(
        top_k_knowledge=5,
        top_k_chunks=3,
        fts_weight=0.70,
        semantic_weight=0.30,
        multi_entity=False,
        label="factual",
    ),
    QueryType.SYNTHESIS: RetrievalConfig(
        top_k_knowledge=15,
        top_k_chunks=8,
        fts_weight=0.35,
        semantic_weight=0.65,
        multi_entity=False,
        label="synthesis",
    ),
    QueryType.COMPARISON: RetrievalConfig(
        top_k_knowledge=10,
        top_k_chunks=6,
        fts_weight=0.50,
        semantic_weight=0.50,
        multi_entity=True,
        label="comparison",
    ),
    QueryType.CONVERSATIONAL: RetrievalConfig(
        top_k_knowledge=2,
        top_k_chunks=1,
        fts_weight=0.50,
        semantic_weight=0.50,
        multi_entity=False,
        label="conversational",
    ),
}


def get_retrieval_config(query_type: QueryType) -> RetrievalConfig:
    """Return the :class:`RetrievalConfig` for *query_type*."""
    return RETRIEVAL_CONFIGS[query_type]


# ── Classification cache ───────────────────────────────────────────────────────
#
# Process-level LRU bounded at _CACHE_MAX entries.  Keyed by a 16-char hex
# prefix of sha256(first 500 chars of text) — collision probability is
# negligible for a 512-entry cache of short strings.

_cache_lock = threading.Lock()
_classification_cache: dict[str, QueryType] = {}
_CACHE_MAX = 512


def _cache_key(text: str) -> str:
    return hashlib.sha256(text.encode()[:500]).hexdigest()[:16]


def _cache_get(text: str) -> QueryType | None:
    with _cache_lock:
        return _classification_cache.get(_cache_key(text))


def _cache_set(text: str, qt: QueryType) -> None:
    with _cache_lock:
        if len(_classification_cache) >= _CACHE_MAX:
            # Evict oldest half — dict preserves insertion order in Python ≥ 3.7
            keys = list(_classification_cache.keys())
            for k in keys[: _CACHE_MAX // 2]:
                del _classification_cache[k]
        _classification_cache[_cache_key(text)] = qt


# ── Rule-based classifier ──────────────────────────────────────────────────────
#
# Patterns are ordered from most-specific to least-specific; the first match wins.

# Conversational: greetings, meta-questions, affirmations.
_CONVERSATIONAL_RE = re.compile(
    r"^(?:hi|hello|hey|thanks|thank you|ok|okay|sure|cool|great|awesome"
    r"|good morning|good afternoon|good evening"
    r"|how are you|what(?:'s| is) up|what can you do"
    r"|who are you|tell me about yourself)\b",
    re.IGNORECASE,
)

# Synthesis: summarization, themes, broad overviews, listing across items.
_SYNTHESIS_RE = re.compile(
    r"\b(?:summar(?:ize|ise|y|ies)"
    r"|overview|themes?"
    r"|key\s+(?:points?|takeaways?|insights?|ideas?|lessons?)"
    r"|main\s+(?:points?|ideas?|themes?|arguments?|findings?)"
    r"|what\s+(?:are|were)\s+the\s+(?:main|key|central|core|major|primary)"
    r"|across\s+(?:all|the|my|every|each|both)"
    r"|tell\s+me\s+(?:everything|all)\s+(?:about|you\s+know)"
    r"|synthesize|synthesise|compile|aggregate"
    r"|list\s+(?:all|every|each)"
    r"|what\s+do\s+(?:you|i|we)\s+(?:know|have)\s+about"
    r"|give\s+me\s+(?:an?\s+)?overview"
    r"|walk\s+me\s+through)\b",
    re.IGNORECASE,
)

# Comparison: explicit compare/contrast, "vs", "difference between", chapter vs chapter.
_COMPARISON_RE = re.compile(
    r"\b(?:compare|contrast"
    r"|differences?\s+between"
    r"|versus|vs\.?"
    r"|how\s+does\s+.{1,60}\s+(?:differ|compare)\s+(?:from|to|with)"
    r"|what(?:'s| is)\s+the\s+difference\s+between"
    r"|similarities\s+and\s+differences"
    r"|chapter\s+\d+\s+(?:and|vs\.?|versus)\s+chapter\s+\d+"
    r"|part\s+\d+\s+(?:and|vs\.?|versus)\s+part\s+\d+"
    r"|book\s+\d+\s+(?:and|vs\.?|versus)\s+book\s+\d+)\b",
    re.IGNORECASE,
)

# Factual: interrogative + specific single-value noun phrase.
_FACTUAL_RE = re.compile(
    r"^(?:what\s+(?:year|date|time|day|month|version|number|page|chapter"
    r"|percentage|price|cost|count|total|amount|score|rating|rank)\b"
    r"|when\s+(?:was|did|were|is|are)\b"
    r"|who\s+(?:wrote|created|founded|invented|discovered|published"
    r"|authored|directed|designed|built|coined)\b"
    r"|how\s+many\b"
    r"|how\s+much\b"
    r"|where\s+(?:was|is|are|did|were)\b"
    r"|which\s+\w+\s+(?:is|was|are|were)\b"
    r"|what\s+(?:is|was|are|were)\s+the\s+"
    r"(?:name|title|author|year|date|source|isbn|publication|publisher"
    r"|edition|genre|setting|protagonist|antagonist|narrator))",
    re.IGNORECASE,
)

# Short-query threshold: queries ≤ this many whitespace-separated tokens that
# don't match the above patterns are treated as FACTUAL (precision lookup).
_SHORT_FACTUAL_THRESHOLD = 8


def classify_query(
    text: str,
    db: OrivellumDB | None = None,
) -> QueryType:
    """Classify *text* into a :class:`QueryType` using rule-based heuristics.

    Classification order (first match wins):
      1. CONVERSATIONAL — greeting / meta-question pattern
      2. COMPARISON     — explicit compare/contrast / "X vs Y"
      3. SYNTHESIS      — summarize / themes / list-all
      4. FACTUAL        — specific-value interrogative (who/when/how many…)
      5. FACTUAL        — short query (≤ 8 words) → precision lookup assumed
      6. SYNTHESIS      — all other longer queries → broad retrieval

    Results are cached by message hash (bounded LRU at 512 entries) so
    retries and React StrictMode double-invocations are free.

    Args:
        text: Raw user message.
        db:   Database handle (reserved for a future LLM-fallback; unused in v1).

    Returns:
        A :class:`QueryType` value.
    """
    if not text or not text.strip():
        return QueryType.CONVERSATIONAL

    cached = _cache_get(text)
    if cached is not None:
        return cached

    t = text.strip()

    if _CONVERSATIONAL_RE.match(t):
        result = QueryType.CONVERSATIONAL
    elif _COMPARISON_RE.search(t):
        result = QueryType.COMPARISON
    elif _SYNTHESIS_RE.search(t):
        result = QueryType.SYNTHESIS
    elif _FACTUAL_RE.match(t) or len(t.split()) <= _SHORT_FACTUAL_THRESHOLD:
        result = QueryType.FACTUAL
    else:
        result = QueryType.SYNTHESIS

    _cache_set(text, result)
    logger.debug("classify_query %r → %s", t[:80], result.value)
    return result


# ── COMPARISON entity extraction ───────────────────────────────────────────────


def extract_comparison_entities(text: str) -> list[str]:
    """Extract the subjects being compared from a COMPARISON-class query.

    Returns a list of one or two entity strings to use as separate sub-queries.
    Falls back to a single-element list containing the original text when no
    structured pattern is found, so the caller always gets at least one query.

    Patterns tried (in order):
      • "X vs Y" / "X versus Y"
      • "difference between X and Y"
      • "chapter N and chapter M"  / "part N and part M"
      • "compare X and Y" / "compare X to Y" / "compare X with Y"
    """
    # "X vs Y"
    m = re.search(r"(.+?)\s+vs\.?\s+(.+?)(?:\?|$)", text, re.IGNORECASE)
    if m:
        return [m.group(1).strip()[:80], m.group(2).strip()[:80]]

    # "difference between X and Y" / "compare X and Y"
    m = re.search(r"\bbetween\s+(.+?)\s+and\s+(.+?)(?:\?|$)", text, re.IGNORECASE)
    if m:
        return [m.group(1).strip()[:80], m.group(2).strip()[:80]]

    # "chapter N and chapter M"
    m = re.search(r"(chapter\s+\w+)\s+and\s+(chapter\s+\w+)", text, re.IGNORECASE)
    if m:
        return [m.group(1), m.group(2)]

    # "part N and part M"
    m = re.search(r"(part\s+\w+)\s+and\s+(part\s+\w+)", text, re.IGNORECASE)
    if m:
        return [m.group(1), m.group(2)]

    # "compare X (and|to|with) Y"
    m = re.search(
        r"\bcompare\s+(.+?)\s+(?:and|to|with)\s+(.+?)(?:\?|$)",
        text,
        re.IGNORECASE,
    )
    if m:
        return [m.group(1).strip()[:80], m.group(2).strip()[:80]]

    return [text]
