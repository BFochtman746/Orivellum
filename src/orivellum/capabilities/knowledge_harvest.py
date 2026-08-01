"""Knowledge harvesting from extraction results.

Two harvesting strategies:
  1. Rule-based (always runs): uses heuristics to surface summaries, headings,
     key sentences, and capitalised noun-phrase entity mentions.
  2. LLM-based (opt-in via `ai_extraction_enabled` setting): sends each chunk
     to a local OpenAI-compat endpoint and extracts structured entities, claims,
     and relationships with higher confidence scores.

Each item is written to the knowledge table via db.create_knowledge_item().
Items tagged with review_status='auto' so a future pass can promote or reject
them without cluttering human-curated knowledge.
"""
from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# LLM-powered harvesting (opt-in)
# ---------------------------------------------------------------------------

# Extraction prompt — asks for JSON only to make parsing reliable
_EXTRACT_PROMPT = """\
You are a precise knowledge extractor. Analyse the document chunk below and \
extract structured knowledge. Return ONLY valid JSON with this exact structure \
and no other text:

{{
  "entities": [{{"name": "...", "description": "..."}}],
  "claims": [{{"text": "..."}}],
  "relationships": [{{"subject": "...", "predicate": "...", "object": "..."}}]
}}

Rules:
- Up to 5 entities (named things: people, organisations, concepts, places, products).
- Up to 5 claims (factual statements asserted by the document).
- Up to 3 relationships (subject -> predicate -> object triples).
- Be factual. Do not invent anything not present in the chunk.
- "description" for entities should be 20 words or fewer.
- Output ONLY the JSON object. No markdown fences, no commentary.

Document title: {title}

Chunk:
{chunk}
"""

# How many pages/segments to send to the LLM per document
_MAX_LLM_CHUNKS = 5
# Maximum characters per chunk sent to LLM (keeps context manageable)
_MAX_CHUNK_CHARS = 2_000


# Dedicated per-call timeout for extraction (independent of the chat timeout).
# Kept short so a slow/absent AI never blocks the background thread for long.
_EXTRACTION_TIMEOUT_SEC = 30


def _call_llm_sync(prompt: str, base_url: str, model: str, timeout: int) -> str | None:
    """Make a synchronous (blocking) call to the LLM endpoint.

    Returns the raw text response, or None on any failure.
    Safe to call from a background thread.
    """
    try:
        import httpx  # noqa: PLC0415 — deferred to avoid startup cost
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("LLM call failed during knowledge extraction: %s", exc)
        return None


def _parse_extraction(raw: str) -> dict:
    """Parse the LLM JSON response, returning a safe dict on any error."""
    try:
        # Strip accidental markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            # Drop first and last fence lines
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            text = "\n".join(inner)
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def llm_harvest(result: "ExtractionResult", doc_id: str,
                work_id: str | None, doc_title: str,
                db: "OrivellumDB") -> int:
    """LLM-powered knowledge extraction for a single document.

    Sends up to _MAX_LLM_CHUNKS text segments to the local AI endpoint and
    writes the resulting entities, claims, and relationships to the DB.

    Returns the count of knowledge items created.
    Silently skips on any LLM or parse failure so the pipeline never breaks.
    """
    from orivellum.api._deps import get_config  # noqa: PLC0415

    try:
        cfg = get_config()
    except Exception:
        logger.warning("llm_harvest: could not load config — skipping")
        return 0

    base_url = cfg.serving.base_url
    model = cfg.serving.workhorse_model
    # Use the short extraction-specific timeout, not the general chat timeout.
    # Falls back to the module-level default if not set in config.
    timeout = getattr(cfg.serving, "extraction_timeout_sec", _EXTRACTION_TIMEOUT_SEC)

    created = 0
    segments = result.pages[:_MAX_LLM_CHUNKS]

    for seg in segments:
        chunk_text = seg.text[:_MAX_CHUNK_CHARS].strip()
        if not chunk_text:
            continue

        prompt = _EXTRACT_PROMPT.format(
            title=doc_title,
            chunk=chunk_text,
        )

        raw = _call_llm_sync(prompt, base_url, model, timeout)
        if not raw:
            continue

        extraction = _parse_extraction(raw)
        if not extraction:
            logger.debug("llm_harvest: empty/unparseable response for doc %s", doc_id)
            continue

        _llm_meta = {"source": "llm"}

        # --- Entities ---
        for ent in (extraction.get("entities") or [])[:5]:
            if not isinstance(ent, dict):
                continue
            name = (ent.get("name") or "").strip()
            desc = (ent.get("description") or "").strip()
            if not name:
                continue
            text = f"{name}: {desc}" if desc else name
            db.create_knowledge_item(
                work_id=work_id,
                kind="entity",
                text=text,
                subject=name,
                predicate="is",
                obj=desc or None,
                confidence=0.85,
                source_doc_id=doc_id,
                review_status="ai_auto",
                meta=_llm_meta,
            )
            created += 1

        # --- Claims ---
        for claim in (extraction.get("claims") or [])[:5]:
            if not isinstance(claim, dict):
                continue
            text = (claim.get("text") or "").strip()
            if not text:
                continue
            db.create_knowledge_item(
                work_id=work_id,
                kind="claim",
                text=text,
                subject=doc_title,
                predicate="claims",
                obj=None,
                confidence=0.80,
                source_doc_id=doc_id,
                review_status="ai_auto",
                meta=_llm_meta,
            )
            created += 1

        # --- Relationships ---
        for rel in (extraction.get("relationships") or [])[:3]:
            if not isinstance(rel, dict):
                continue
            subject = (rel.get("subject") or "").strip()
            predicate = (rel.get("predicate") or "").strip()
            obj = (rel.get("object") or "").strip()
            if not (subject and predicate and obj):
                continue
            text = f"{subject} {predicate} {obj}"
            db.create_knowledge_item(
                work_id=work_id,
                kind="relationship",
                text=text,
                subject=subject,
                predicate=predicate,
                obj=obj,
                confidence=0.75,
                source_doc_id=doc_id,
                review_status="ai_auto",
                meta=_llm_meta,
            )
            created += 1

    logger.info(
        "LLM-harvested %d knowledge items for doc %s (work=%s, chunks=%d)",
        created, doc_id, work_id, len(segments),
    )
    return created
