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

# Canonical cap matching pipeline.py's update_document_extracted call:
#   extracted_text = result.full_text[:_EXTRACTED_TEXT_CAP]
# Chunk char offsets (Unicode code-points) are only stored when they fall
# within this window; chunks beyond it receive NULL offsets and are handled
# by the standard per-chunk embedding fallback.
_EXTRACTED_TEXT_CAP = 100_000

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


def _word_char_offsets(text: str) -> tuple[list[str], list[int], list[int]]:
    """Return ``(words, starts, ends)`` with byte offsets for each word in *text*.

    O(n) single-pass scan; handles arbitrary whitespace (tabs, newlines,
    multiple spaces) correctly.  ``starts[i]`` is the index of the first
    character of ``words[i]`` in *text*; ``ends[i]`` is one past the last.
    """
    words: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        # skip whitespace
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        s = i
        while i < n and not text[i].isspace():
            i += 1
        words.append(text[s:i])
        starts.append(s)
        ends.append(i)
    return words, starts, ends


def _sliding_chunks(text: str, target: int = _TARGET_WORDS,
                    overlap: int = _OVERLAP_WORDS) -> list[str]:
    """Split *text* into overlapping word-window chunks (text only, no spans)."""
    return [c for c, _, _ in _sliding_chunks_with_spans(text, target, overlap)]


def _sliding_chunks_with_spans(
    text: str,
    target: int = _TARGET_WORDS,
    overlap: int = _OVERLAP_WORDS,
) -> list[tuple[str, int, int]]:
    """Split *text* into overlapping word-window chunks, returning char spans.

    Returns a list of ``(chunk_text, char_start, char_end)`` where
    ``char_start`` / ``char_end`` are byte offsets **within *text*** pointing
    to the first character of the first word and one-past-last character of the
    last word in each chunk.  Internal whitespace is preserved (the returned
    text is a contiguous slice of the input, not a space-joined reconstruction).
    """
    words, word_starts, word_ends = _word_char_offsets(text)
    if not words:
        return []
    if len(words) <= target:
        return [(text[word_starts[0]:word_ends[-1]], word_starts[0], word_ends[-1])]
    results: list[tuple[str, int, int]] = []
    start = 0
    while start < len(words):
        end = min(start + target, len(words))
        c_start = word_starts[start]
        c_end = word_ends[end - 1]
        results.append((text[c_start:c_end], c_start, c_end))
        if end >= len(words):
            break
        start = end - overlap
    return results


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
        from orivellum.configuration.config import load_config

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


def _build_page_boundaries(pages: list) -> list[tuple[int, int, int]]:
    """Return ``(page_num, approx_start, approx_end)`` tuples for page-number lookup.

    When chunks are derived from ``result.full_text`` (not page-by-page),
    this lets us assign an approximate page number to each chunk based on
    cumulative page-text lengths.  The estimate is within ``n_pages`` code-points
    of the true boundary — acceptable for the page-number display column, which
    is metadata and not used for retrieval or embedding.
    """
    bounds: list[tuple[int, int, int]] = []
    cursor = 0
    for seg in pages:
        text_len = len(seg.text or "")
        if text_len:
            bounds.append((seg.page, cursor, cursor + text_len))
            cursor += text_len + 1  # +1 for a rough separator gap
    return bounds


def _find_page_for_offset(
    char_start: int, page_boundaries: list[tuple[int, int, int]]
) -> int:
    """Return the estimated page number for a chunk starting at *char_start*."""
    for page_num, start, end in page_boundaries:
        if start <= char_start < end:
            return page_num
    return page_boundaries[-1][0] if page_boundaries else 0


def chunk_and_store(result: "ExtractionResult", doc_id: str, db: "OrivellumDB") -> int:
    """Chunk *result* and write all chunks to the DB. Returns chunk count.

    **Offset invariant**: ``char_start`` / ``char_end`` are Unicode code-point
    offsets within ``result.full_text``.  The pipeline stores
    ``documents.extracted_text = result.full_text[:_EXTRACTED_TEXT_CAP]``, so
    any non-NULL offset stored here is a valid position in ``extracted_text``.

    **Design**: chunks are always derived from ``result.full_text`` when it is
    available — never from page text searched inside ``full_text``.  This
    eliminates fingerprint-search fragility (CRLF normalization, repeated
    prefixes, separator-style differences between extractor and aggregator).
    Page numbers are assigned via a cumulative length estimate; they are
    display metadata only and do not affect retrieval or embedding.

    **Cap rule**: offsets are stored only when the chunk span is *wholly*
    contained within ``[0, _EXTRACTED_TEXT_CAP)``.  Chunks that start before
    the cap but end after it, and chunks that start at or beyond the cap,
    both receive ``NULL`` offsets so the late-chunking encoder skips them and
    the standard per-chunk fallback handles them — no partial late embeddings.
    """
    db.delete_chunks(doc_id)
    stored = 0
    target, overlap = _resolve_chunk_params(db)

    if result.full_text:
        # Primary path: chunk full_text directly.
        # char offsets are always valid positions in extracted_text; no
        # fingerprint search → immune to CRLF / separator-style differences.
        page_boundaries = _build_page_boundaries(result.pages) if result.pages else []

        for chunk_text, char_start, char_end in _sliding_chunks_with_spans(
            result.full_text, target, overlap
        ):
            chunk_text = chunk_text.strip()
            if len(chunk_text) < 20:
                continue

            page = _find_page_for_offset(char_start, page_boundaries) if page_boundaries else 0

            # Store offsets only for chunks WHOLLY within extracted_text window.
            # Straddling chunks (char_end > cap) → NULL both → standard fallback.
            if char_start < _EXTRACTED_TEXT_CAP and char_end <= _EXTRACTED_TEXT_CAP:
                cs: int | None = char_start
                ce: int | None = char_end
            else:
                cs = ce = None

            db.add_chunk(
                doc_id=doc_id, text=chunk_text, page=page,
                char_start=cs, char_end=ce,
            )
            stored += 1

    elif result.pages:
        # Fallback: no full_text — page-based chunking; offsets NULL.
        # Late chunking is unavailable (extracted_text also absent).
        for seg in result.pages:
            seg_text = seg.text or ""
            if not seg_text.strip():
                continue
            for chunk_text, _, _ in _sliding_chunks_with_spans(seg_text, target, overlap):
                chunk_text = chunk_text.strip()
                if len(chunk_text) < 20:
                    continue
                db.add_chunk(doc_id=doc_id, text=chunk_text, page=seg.page)
                stored += 1

    logger.info("Stored %d chunks for doc %s", stored, doc_id)
    return stored
