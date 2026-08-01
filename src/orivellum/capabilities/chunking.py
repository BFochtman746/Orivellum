"""Chunking — split extracted text into DB-storable segments.

Strategy:
- Target ~500 words per chunk, ~50-word overlap between consecutive chunks
- Respect page boundaries when possible (one page → one or more chunks)
- Write chunks + FTS index via db.add_chunk()
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.capabilities.extraction import ExtractionResult
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

_TARGET_WORDS = 500
_OVERLAP_WORDS = 50


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


def chunk_and_store(result: "ExtractionResult", doc_id: str, db: "OrivellumDB") -> int:
    """Chunk *result* and write all chunks to the DB. Returns chunk count."""
    db.delete_chunks(doc_id)  # clear any previous extraction
    stored = 0

    if result.pages:
        for seg in result.pages:
            for chunk_text in _sliding_chunks(seg.text):
                chunk_text = chunk_text.strip()
                if len(chunk_text) < 20:
                    continue
                db.add_chunk(doc_id=doc_id, text=chunk_text, page=seg.page)
                stored += 1
    elif result.full_text:
        for chunk_text in _sliding_chunks(result.full_text):
            chunk_text = chunk_text.strip()
            if len(chunk_text) < 20:
                continue
            db.add_chunk(doc_id=doc_id, text=chunk_text, page=0)
            stored += 1

    logger.info("Stored %d chunks for doc %s", stored, doc_id)
    return stored
