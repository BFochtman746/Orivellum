"""Chunking — split extracted text into DB-storable segments.

Strategy:
- Target ~500 words per chunk, ~50-word overlap between consecutive chunks
- Respect page boundaries when possible (one page → one or more chunks)
- Write chunks + FTS index via db.add_chunk()
- Optionally enrich each chunk with a short AI-generated context prefix
  (Anthropic Contextual Retrieval technique) via generate_context_prefixes_for_doc()
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.capabilities.extraction import ExtractionResult
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# Max characters of the document's opening text sent as context to the LLM
# when generating per-chunk context prefixes.  Large enough to give meaningful
# context; small enough to keep prompt tokens low.
_CTX_EXCERPT_CHARS = 2_000

# Max characters of the chunk text sent to the LLM in the prefix-generation
# prompt.  Full chunk text can be up to ~3 000 chars (500 words); we send the
# first 600 chars to stay well within a small token budget.
_CTX_CHUNK_SAMPLE = 600

# Batch size for the nightshift context-prefix backfill (max chunks per run).
CTX_BACKFILL_BATCH = 20
CTX_BACKFILL_MAX = 100

_TARGET_WORDS = 500
_OVERLAP_WORDS = 50

# Hard bounds enforced regardless of stored settings.
_TARGET_MIN, _TARGET_MAX = 100, 2000


def _resolve_chunk_params(db: "OrivellumDB") -> tuple[int, int]:
    """Read chunk_target_words / chunk_overlap_words settings with safe int
    parsing + bounds clamping.

    target is clamped to [100, 2000]; overlap to [0, target//2].  Any parse
    error falls back to the built-in defaults (500 / 50).
    """
    try:
        target = int(db.get_setting("chunk_target_words", str(_TARGET_WORDS)))
    except (TypeError, ValueError):
        target = _TARGET_WORDS
    try:
        overlap = int(db.get_setting("chunk_overlap_words", str(_OVERLAP_WORDS)))
    except (TypeError, ValueError):
        overlap = _OVERLAP_WORDS
    target = max(_TARGET_MIN, min(_TARGET_MAX, target))
    overlap = max(0, min(target // 2, overlap))
    return target, overlap


def _words(text: str) -> list[str]:
    return text.split()


def _sliding_chunks(text: str, target: int = _TARGET_WORDS,
                    overlap: int = _OVERLAP_WORDS) -> list[str]:
    """Split *text* into overlapping word-window chunks."""
    words = _words(text)
    if not words:
        return []
    if len(words) <= target:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + target, len(words))
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = end - overlap
    return chunks


def generate_context_prefixes_for_doc(
    doc_id: str,
    db: "OrivellumDB",
    *,
    doc_title: str = "",
    doc_text_excerpt: str = "",
) -> int:
    """Generate AI context prefixes for all un-prefixed chunks of a document.

    Implements the Anthropic Contextual Retrieval technique: each chunk receives
    a short 1-2 sentence description of (a) which document it comes from and
    (b) what broader topic/section it belongs to.  The prefix is stored in
    ``chunks.context_prefix`` and later prepended to the raw chunk text when
    computing embeddings, improving retrieval accuracy for dense corpora.

    Gated by the ``ai_extraction_enabled`` DB setting (same gate as
    ``llm_harvest``).  Safe to call from a daemon thread — never raises.

    Returns the number of prefixes successfully generated.
    """
    try:
        if db.get_setting("ai_extraction_enabled", "false") != "true":
            return 0

        from orivellum.capabilities.llm import llm_call
        from orivellum.config import load_config

        cfg = load_config()

        with db._lock:
            rows = db._conn.execute(
                """SELECT id, text, page FROM chunks
                   WHERE doc_id=? AND context_prefix IS NULL AND length(text) > 40
                   ORDER BY page, rowid""",
                (doc_id,),
            ).fetchall()

        if not rows:
            return 0

        title_str = (doc_title or "Document").strip()
        excerpt = (doc_text_excerpt or "")[:_CTX_EXCERPT_CHARS].strip()

        generated = 0
        for i in range(0, len(rows), CTX_BACKFILL_BATCH):
            batch = list(rows[i : i + CTX_BACKFILL_BATCH])
            for row in batch:
                chunk_id = row["id"]
                chunk_sample = (row["text"] or "")[:_CTX_CHUNK_SAMPLE]
                prompt_parts = [f'Document: "{title_str}"']
                if excerpt:
                    prompt_parts.append(
                        f"Opening content:\n{excerpt}"
                    )
                prompt_parts.append(
                    f"Passage:\n{chunk_sample}\n\n"
                    "Write a 1-2 sentence context for this passage that states "
                    "which document it comes from and what broader topic or section "
                    "it covers. Start directly with the context — no preamble, "
                    "no labels, no bullet points."
                )
                messages = [{"role": "user", "content": "\n\n".join(prompt_parts)}]
                result = llm_call(
                    messages,
                    cfg=cfg,
                    db=db,
                    purpose="chunk.context_prefix",
                    timeout=20,
                    max_tokens=120,
                    temperature=0.0,
                )
                if result.ok and result.text:
                    prefix = result.text.strip()[:500]
                    db.update_chunk_context_prefix(chunk_id, prefix)
                    generated += 1

        if generated:
            logger.info(
                "Generated %d context prefix(es) for doc %s", generated, doc_id[:8]
            )
        return generated

    except Exception as exc:
        logger.debug(
            "generate_context_prefixes_for_doc: non-fatal error for doc %s: %s",
            doc_id[:8],
            exc,
        )
        return 0


def chunk_and_store(result: "ExtractionResult", doc_id: str, db: "OrivellumDB") -> int:
    """Chunk *result* and write all chunks to the DB. Returns chunk count."""
    db.delete_chunks(doc_id)  # clear any previous extraction
    stored = 0
    target, overlap = _resolve_chunk_params(db)

    if result.pages:
        for seg in result.pages:
            for chunk_text in _sliding_chunks(seg.text, target, overlap):
                chunk_text = chunk_text.strip()
                if len(chunk_text) < 20:
                    continue
                db.add_chunk(doc_id=doc_id, text=chunk_text, page=seg.page)
                stored += 1
    elif result.full_text:
        for chunk_text in _sliding_chunks(result.full_text, target, overlap):
            chunk_text = chunk_text.strip()
            if len(chunk_text) < 20:
                continue
            db.add_chunk(doc_id=doc_id, text=chunk_text, page=0)
            stored += 1

    logger.info("Stored %d chunks for doc %s", stored, doc_id)
    return stored
