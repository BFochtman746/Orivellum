"""Rule-based knowledge harvesting from extraction results.

No LLM required — uses heuristics to surface:
  - document-level summary item
  - section headings as "concept" items
  - key sentences (long, punctuated sentences near the start of pages)
  - capitalised multi-word noun phrases as potential entity mentions
  - bold/italic runs captured as headings by the extractor

Each item is written to the knowledge table via db.create_knowledge_item().
The items are tagged with review_status='auto' so a future LLM pass can
promote or reject them without cluttering human-curated knowledge.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.capabilities.extraction import ExtractionResult
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# Sentences shorter than this are usually headers/fragments — skip them
_MIN_SENTENCE_WORDS = 8
# Cap excerpts harvested per document
_MAX_EXCERPTS = 30
_MAX_ENTITIES = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SENT_SPLIT = re.compile(r'(?<=[.!?])[ \t]+(?=[A-Z])')
_CAP_PHRASE = re.compile(
    r'\b([A-Z][a-z]+(?:[ ]+[A-Z][a-z]+){1,5})\b'
)
_STOP_PHRASES = frozenset({
    "In The", "Of The", "For The", "To The", "And The",
    "This Is", "It Is", "There Is", "We Are",
})


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT_SPLIT.split(text) if s.strip()]


def _is_good_sentence(s: str) -> bool:
    words = s.split()
    return (len(words) >= _MIN_SENTENCE_WORDS
            and s.endswith((".", "!", "?"))
            and not s.isupper())


def _cap_phrases(text: str) -> list[str]:
    seen: set[str] = set()
    phrases: list[str] = []
    for m in _CAP_PHRASE.finditer(text):
        p = m.group(1)
        if p not in _STOP_PHRASES and p not in seen:
            seen.add(p)
            phrases.append(p)
    return phrases


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def harvest(result: "ExtractionResult", doc_id: str,
            work_id: str | None, doc_title: str,
            db: "OrivellumDB") -> int:
    """Harvest knowledge from *result* and write to DB.

    Returns count of items created.
    """
    created = 0

    # 1. Document-level summary node
    summary = (
        f"{doc_title} — {result.kind.upper()} document, {result.word_count:,} words."
    )
    db.create_knowledge_item(
        work_id=work_id,
        kind="summary",
        text=summary,
        subject=doc_title,
        predicate="is",
        obj=f"{result.kind} document",
        confidence=1.0,
        source_doc_id=doc_id,
    )
    created += 1

    # 2. Section headings → concept items
    for heading in result.headings[:30]:
        heading = heading.strip()
        if not heading or len(heading) > 200:
            continue
        db.create_knowledge_item(
            work_id=work_id,
            kind="concept",
            text=heading,
            subject=heading,
            predicate="section in",
            obj=doc_title,
            confidence=0.85,
            source_doc_id=doc_id,
        )
        created += 1

    # 3. Key sentence excerpts — from the first few pages
    excerpts_saved = 0
    for seg in result.pages[:10]:
        if excerpts_saved >= _MAX_EXCERPTS:
            break
        for sent in _sentences(seg.text):
            if excerpts_saved >= _MAX_EXCERPTS:
                break
            if _is_good_sentence(sent) and len(sent) < 600:
                db.create_knowledge_item(
                    work_id=work_id,
                    kind="excerpt",
                    text=sent,
                    subject=doc_title,
                    predicate="states",
                    obj=None,
                    confidence=0.65,
                    source_doc_id=doc_id,
                )
                excerpts_saved += 1
                created += 1

    # 4. Capitalised proper-noun phrases → entity mentions
    # Sample from the full text to avoid O(N) on huge docs
    sample_text = result.full_text[:50_000]
    entities_saved = 0
    for phrase in _cap_phrases(sample_text):
        if entities_saved >= _MAX_ENTITIES:
            break
        # Skip if same as doc title or very short
        if phrase.lower() == doc_title.lower() or len(phrase) < 4:
            continue
        db.create_knowledge_item(
            work_id=work_id,
            kind="entity",
            text=phrase,
            subject=phrase,
            predicate="mentioned in",
            obj=doc_title,
            confidence=0.5,
            source_doc_id=doc_id,
        )
        entities_saved += 1
        created += 1

    logger.info(
        "Harvested %d knowledge items for doc %s (work=%s)", created, doc_id, work_id
    )
    return created
