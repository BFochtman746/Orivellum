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
    # Full text — novels are stored without truncation (see pipeline.py)
    sample_text = result.full_text
    entities_saved = 0
    for phrase in _cap_phrases(sample_text):
        if entities_saved >= _MAX_ENTITIES:
            break
        # Skip if same as doc title or very short
        if phrase.lower() == doc_title.lower() or len(phrase) < 4:
            continue
        # create_knowledge_item() returns a str (the item ID), not a dict.
        kid = db.create_knowledge_item(
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
        # Also persist to the entities table so the graph layer has real rows
        try:
            eid = db.upsert_entity(phrase, "concept")
            db.create_entity_mention(eid, doc_id, work_id, knowledge_id=kid)
        except Exception as _e:
            logger.debug("entity graph write non-fatal: %s", _e)

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


def _call_llm_sync(prompt: str, base_url: str, model: str, timeout: int,
                   db: "OrivellumDB | None" = None) -> str | None:
    """Make a synchronous (blocking) call to the LLM endpoint.

    Routes through the central ``llm_call`` gateway.  Returns the raw text
    response, or None on any failure.  Safe to call from a background thread.
    """
    from orivellum.capabilities.llm import llm_call
    result = llm_call(
        [{"role": "user", "content": prompt}],
        base_url=base_url, model=model,
        timeout=timeout, purpose="harvest.llm", db=db,
    )
    if not result.ok or result.text is None:
        logger.warning(
            "LLM call failed during knowledge extraction: %s",
            result.error or "no response",
        )
    return result.text


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
                db: "OrivellumDB",
                kind: str | None = None) -> int:
    """LLM-powered knowledge extraction for a single document.

    Sends up to _MAX_LLM_CHUNKS text segments to the local AI endpoint and
    writes the resulting entities, claims, and relationships to the DB.

    Template resolution order (highest priority first):
      1. User-defined extraction template matching (kind, work_id) — most specific.
      2. User-defined extraction template matching kind (work_id=NULL).
      3. User-defined extraction template matching work_id (kind_label=NULL).
      4. Active MCOS prompt-registry template for slot 'harvest.extract'.
      5. Hardcoded ``_EXTRACT_PROMPT`` constant.

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

    # ── Template resolution ────────────────────────────────────────────────────
    # Priority 1–3: user-defined extraction templates stored in the DB.
    # Priority 4: active MCOS prompt-registry template (slot 'harvest.extract').
    # Priority 5: hardcoded fallback — never crashes the harvest.
    template = _EXTRACT_PROMPT
    _using_custom_template = False
    try:
        et = db.get_template_for_doc(kind=kind, work_id=work_id)
        if et:
            # Build the effective prompt from the custom template.
            # Custom templates must use {title} and {chunk} placeholders.
            # field_hints, when present, are appended as bullet guidance.
            hints: list[str] = et.get("field_hints") or []
            hints_block = ""
            if hints:
                hints_block = "\n\nExtraction guidance for this document type:\n" + \
                              "\n".join(f"  • {h}" for h in hints)
            # Append the JSON output structure reminder so the parser can handle
            # responses from any custom template that still uses our JSON schema.
            template = (
                et["system_prompt"].rstrip()
                + hints_block
                + "\n\n"
                + "Return ONLY valid JSON with this structure:\n"
                + '{{\n'
                + '  "entities": [{{"name": "...", "description": "..."}}],\n'
                + '  "claims": [{{"text": "..."}}],\n'
                + '  "relationships": [{{"subject": "...", "predicate": "...", "object": "..."}}\n'
                + ']\n}}\n\n'
                + "Document title: {title}\n\nChunk:\n{chunk}"
            )
            _using_custom_template = True
            logger.debug(
                "llm_harvest: using custom template %r (id=%s) for kind=%s work=%s",
                et.get("name"), et.get("id", "")[:8], kind, work_id,
            )
    except Exception as _te:
        logger.debug("llm_harvest: template lookup failed (%s) — using defaults", _te)

    if not _using_custom_template:
        # Fall back through MCOS prompt registry then hardcoded constant.
        # Same never-break rule as chat.base.  The template MUST keep the
        # {title}/{chunk} placeholders and its literal JSON braces doubled ({{ }}).
        try:
            active = db.get_active_prompt("harvest.extract")
            if active:
                template = active
        except Exception:
            template = _EXTRACT_PROMPT

    for seg in segments:
        chunk_text = seg.text[:_MAX_CHUNK_CHARS].strip()
        if not chunk_text:
            continue

        try:
            prompt = template.format(title=doc_title, chunk=chunk_text)
        except Exception as exc:
            # Bad DB template (e.g. stray unescaped brace) — never break harvest.
            logger.warning("harvest.extract template format failed (%s) — "
                           "falling back to default", exc)
            prompt = _EXTRACT_PROMPT.format(title=doc_title, chunk=chunk_text)

        raw = _call_llm_sync(prompt, base_url, model, timeout, db=db)
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
            # create_knowledge_item() returns a str (the item ID), not a dict.
            kid = db.create_knowledge_item(
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
            # Persist to entities table so the graph layer has real rows
            try:
                eid = db.upsert_entity(name, "concept",
                                       meta={"description": desc} if desc else None)
                db.create_entity_mention(eid, doc_id, work_id, knowledge_id=kid)
            except Exception as _e:
                logger.debug("llm entity graph write non-fatal: %s", _e)

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
            # Persist both subject and object as entities with an edge
            try:
                sid = db.upsert_entity(subject, "concept")
                oid = db.upsert_entity(obj, "concept")
                db.create_entity_mention(sid, doc_id, work_id)
                db.create_entity_mention(oid, doc_id, work_id)
                db.create_entity_edge(sid, oid, predicate)
            except Exception as _e:
                logger.debug("llm relationship graph write non-fatal: %s", _e)

    logger.info(
        "LLM-harvested %d knowledge items for doc %s (work=%s, chunks=%d)",
        created, doc_id, work_id, len(segments),
    )
    return created


# ── Fiction / novel chapter-level extraction ──────────────────────────────────

# Maximum characters of chapter text sent to the LLM per chapter.
# A 6 k char window captures ~900–1 200 words — enough for a full scene
# intro, character beats, and setting without blowing the context budget.
_MAX_CHAPTER_CHARS = 6_000

_FICTION_CHAPTER_PROMPT = """\
You are a literary analyst. Extract structured knowledge from this chapter of a novel.
Return ONLY valid JSON with this exact structure and absolutely no other text:

{{
  "characters": [{{"name": "...", "role": "protagonist|antagonist|supporting|mentioned", "description": "..."}}],
  "events": [{{"text": "...", "significance": "major|minor"}}],
  "settings": [{{"name": "...", "description": "..."}}],
  "relationships": [{{"subject": "...", "predicate": "...", "object": "..."}}],
  "themes": [{{"text": "..."}}],
  "foreshadowing": [{{"text": "..."}}]
}}

Rules:
- Up to 8 characters. Keep descriptions to 20 words or fewer. Use "role": "mentioned" for characters only named in passing.
- Up to 6 events. Mark the 2-3 most pivotal as "major"; the rest are "minor".
- Up to 4 settings or locations that appear in this chapter.
- Up to 5 relationships between named characters (subject + predicate + object).
- Up to 3 themes (one sentence each, drawn from this chapter's content).
- Up to 3 foreshadowing hints, symbols, or unresolved questions introduced here.
- Extract ONLY what appears in the chapter text. Do not invent or interpolate.
- Output ONLY the JSON object. No markdown, no commentary, no explanation.

Novel title: {title}
Chapter: {chapter_title}

Chapter text (excerpt):
{chunk}
"""


def llm_harvest_by_chapters(
    doc_id: str,
    work_id: str | None,
    doc_title: str,
    db: "OrivellumDB",
) -> int:
    """Fiction-aware LLM knowledge extraction for chapter-structured documents.

    Queries ``book_chapters`` for *doc_id*, then runs a fiction-optimised
    prompt on each chapter independently, storing knowledge items tagged
    with ``chapter_id`` so they can be searched per-chapter and surfaced
    in chapter health dashboards.

    Characters → *character* kind + entity graph
    Events     → *event* kind (major/minor annotated in meta)
    Settings   → *setting* kind
    Relationships → *relationship* kind + entity edge
    Themes     → *theme* kind
    Foreshadowing → *foreshadowing* kind

    All items get ``review_status='ai_auto'`` so the user can approve or
    reject them in the review queue before they influence searches.

    Returns total knowledge items created.  Silently skips on LLM failure
    so the document is never left in a broken state.
    """
    from orivellum.api._deps import get_config  # local import to avoid cycles

    try:
        cfg = get_config()
    except Exception:
        logger.warning("llm_harvest_by_chapters: could not load config — skipping")
        return 0

    base_url = cfg.serving.base_url
    model    = getattr(cfg.serving, "workhorse_model", None) or cfg.serving.model
    timeout  = getattr(cfg.serving, "extraction_timeout_sec", _EXTRACTION_TIMEOUT_SEC)

    # ── Fetch chapters ────────────────────────────────────────────────────────
    try:
        with db._lock:
            chapter_rows = db._conn.execute(
                """SELECT id, seq, title, text
                   FROM book_chapters
                   WHERE source_doc_id = ?
                   ORDER BY seq""",
                (doc_id,),
            ).fetchall()
    except Exception as exc:
        logger.warning("llm_harvest_by_chapters: chapter query failed: %s", exc)
        return 0

    if not chapter_rows:
        logger.debug("llm_harvest_by_chapters: no chapters for doc %s", doc_id)
        return 0

    _llm_meta = {"source": "llm", "extraction": "chapter"}
    total_created = 0

    for ch_row in chapter_rows:
        chapter_id    = ch_row["id"]
        chapter_seq   = ch_row["seq"]
        chapter_title = ch_row["title"] or f"Chapter {chapter_seq + 1}"
        chapter_text  = (ch_row["text"] or "")[:_MAX_CHAPTER_CHARS].strip()

        if not chapter_text:
            continue

        try:
            prompt = _FICTION_CHAPTER_PROMPT.format(
                title=doc_title,
                chapter_title=chapter_title,
                chunk=chapter_text,
            )
        except Exception as exc:
            logger.debug("llm_harvest_by_chapters: prompt format error ch%d: %s",
                         chapter_seq, exc)
            continue

        raw = _call_llm_sync(prompt, base_url, model, timeout, db=db)
        if not raw:
            continue

        extraction = _parse_extraction(raw)
        if not extraction:
            continue

        created = 0

        # ── Characters ───────────────────────────────────────────────────────
        for char in (extraction.get("characters") or [])[:8]:
            if not isinstance(char, dict):
                continue
            name = (char.get("name") or "").strip()
            desc = (char.get("description") or "").strip()
            role = (char.get("role") or "supporting").strip()
            if not name:
                continue
            text = f"{name} ({role}): {desc}" if desc else f"{name} ({role})"
            kid = db.create_knowledge_item(
                work_id=work_id, kind="character", text=text,
                subject=name, predicate="is", obj=role,
                confidence=0.90, source_doc_id=doc_id,
                review_status="ai_auto", meta=_llm_meta,
                chapter_id=chapter_id,
            )
            created += 1
            try:
                eid = db.upsert_entity(
                    name, "character",
                    meta={"role": role, "description": desc} if desc else {"role": role},
                )
                db.create_entity_mention(eid, doc_id, work_id, knowledge_id=kid)
            except Exception:
                pass

        # ── Events ───────────────────────────────────────────────────────────
        for evt in (extraction.get("events") or [])[:6]:
            if not isinstance(evt, dict):
                continue
            text = (evt.get("text") or "").strip()
            significance = (evt.get("significance") or "minor").strip()
            if not text:
                continue
            db.create_knowledge_item(
                work_id=work_id, kind="event", text=text,
                subject=chapter_title, predicate="contains", obj=significance,
                confidence=0.85 if significance == "major" else 0.75,
                source_doc_id=doc_id, review_status="ai_auto",
                meta={**_llm_meta, "significance": significance},
                chapter_id=chapter_id,
            )
            created += 1

        # ── Settings ─────────────────────────────────────────────────────────
        for setting in (extraction.get("settings") or [])[:4]:
            if not isinstance(setting, dict):
                continue
            name = (setting.get("name") or "").strip()
            desc = (setting.get("description") or "").strip()
            if not name:
                continue
            text = f"{name}: {desc}" if desc else name
            db.create_knowledge_item(
                work_id=work_id, kind="setting", text=text,
                subject=name, predicate="is_setting_in", obj=chapter_title,
                confidence=0.80, source_doc_id=doc_id,
                review_status="ai_auto", meta=_llm_meta,
                chapter_id=chapter_id,
            )
            created += 1

        # ── Relationships ─────────────────────────────────────────────────────
        for rel in (extraction.get("relationships") or [])[:5]:
            if not isinstance(rel, dict):
                continue
            subj = (rel.get("subject") or "").strip()
            pred = (rel.get("predicate") or "").strip()
            obj  = (rel.get("object") or "").strip()
            if not (subj and pred and obj):
                continue
            text = f"{subj} {pred} {obj}"
            db.create_knowledge_item(
                work_id=work_id, kind="relationship", text=text,
                subject=subj, predicate=pred, obj=obj,
                confidence=0.80, source_doc_id=doc_id,
                review_status="ai_auto", meta=_llm_meta,
                chapter_id=chapter_id,
            )
            created += 1
            try:
                sid = db.upsert_entity(subj, "character")
                oid = db.upsert_entity(obj, "character")
                db.create_entity_edge(sid, oid, pred)
            except Exception:
                pass

        # ── Themes ───────────────────────────────────────────────────────────
        for theme in (extraction.get("themes") or [])[:3]:
            if not isinstance(theme, dict):
                continue
            text = (theme.get("text") or "").strip()
            if not text:
                continue
            db.create_knowledge_item(
                work_id=work_id, kind="theme", text=text,
                subject=chapter_title, predicate="explores_theme", obj=None,
                confidence=0.75, source_doc_id=doc_id,
                review_status="ai_auto", meta=_llm_meta,
                chapter_id=chapter_id,
            )
            created += 1

        # ── Foreshadowing ─────────────────────────────────────────────────────
        for fsh in (extraction.get("foreshadowing") or [])[:3]:
            if not isinstance(fsh, dict):
                continue
            text = (fsh.get("text") or "").strip()
            if not text:
                continue
            db.create_knowledge_item(
                work_id=work_id, kind="foreshadowing", text=text,
                subject=chapter_title, predicate="foreshadows", obj=None,
                confidence=0.70, source_doc_id=doc_id,
                review_status="ai_auto", meta=_llm_meta,
                chapter_id=chapter_id,
            )
            created += 1

        if created:
            logger.info(
                "llm_harvest_by_chapters: %d items, doc %s ch%d (%s)",
                created, doc_id[:8], chapter_seq, chapter_title[:40],
            )
        total_created += created

    logger.info(
        "llm_harvest_by_chapters: %d total items, %d chapters, doc %s",
        total_created, len(chapter_rows), doc_id,
    )
    return total_created
