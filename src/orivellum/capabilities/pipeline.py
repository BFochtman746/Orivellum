"""End-to-end document processing pipeline.

Call `process_document()` after a file is imported to the library.
Runs: extract → chunk → harvest → update document readiness.

This function is designed to be called in a background thread so the
HTTP response returns immediately after file storage.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from orivellum.capabilities.extraction import extract
from orivellum.capabilities.chunking import chunk_and_store
from orivellum.capabilities.knowledge_harvest import harvest, llm_harvest

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)


def resolve_file_path(file_path: str, doc_id: str, db: "OrivellumDB") -> Path | None:
    """Return the file as a Path, falling back to content_path from the DB.

    This makes reprocessing after a server restart safe: even if the original
    absolute path is stale, content_path (relative to lib_root) still works.
    """
    p = Path(file_path)
    if p.exists():
        return p

    # Fallback: resolve from content_path stored in the document record
    doc = db.get_document(doc_id)
    if not doc:
        return None
    content_path = doc.get("content_path")
    if not content_path:
        return None

    # content_path is relative to data_dir/library
    try:
        from orivellum.api._deps import get_config
        cfg = get_config()
        lib_root = Path(cfg.data_dir) / "library"
        fallback = lib_root / content_path
        if fallback.exists():
            logger.info("Resolved %s via content_path fallback: %s", doc_id[:8], fallback)
            return fallback
    except Exception:
        pass
    return None


def process_document(doc_id: str, file_path: str, kind: str,
                     work_id: str | None, title: str,
                     db: "OrivellumDB") -> None:
    """Extract, chunk, and harvest a single document.

    Safe to call from a daemon thread — catches and logs all exceptions.
    Stores a descriptive error_message on the document when anything fails
    so callers can surface the reason in the UI.
    """
    logger.info("Processing doc %s (%s) kind=%s", doc_id, title, kind)
    try:
        path = resolve_file_path(file_path, doc_id, db)
        if path is None:
            msg = f"File not found: {file_path}"
            logger.warning("Doc %s — %s", doc_id, msg)
            db.update_document_extracted(doc_id, "", 0,
                                         readiness="error",
                                         error_message=msg)
            return

        # Step 1: extract text
        result = extract(path, kind)
        if not result.ok:
            msg = f"Extraction produced no readable text (kind={kind})"
            logger.warning("Doc %s — %s", doc_id, msg)
            db.update_document_extracted(doc_id, "", 0,
                                         readiness="no_text",
                                         error_message=msg)
            return

        # Step 2: chunk and index
        chunk_and_store(result, doc_id, db)

        # Step 3: harvest knowledge (rule-based, always runs)
        harvest(result, doc_id=doc_id, work_id=work_id,
                doc_title=title, db=db)

        # Step 4: mark document ready — happens BEFORE the optional LLM step so
        # the document is usable even if the AI service is slow or unavailable.
        db.update_document_extracted(
            doc_id,
            extracted_text=result.full_text[:100_000],  # cap stored text
            word_count=result.word_count,
            readiness="ready",
        )
        logger.info("Doc %s processed — %d words, ready", doc_id, result.word_count)

        # Step 5 (optional): LLM-powered harvest — runs after readiness is set so
        # latency here never blocks the document from appearing as ready.
        if db.get_setting("ai_extraction_enabled", "false").lower() == "true":
            logger.info("AI extraction enabled — running llm_harvest for doc %s", doc_id)
            try:
                llm_harvest(result, doc_id=doc_id, work_id=work_id,
                            doc_title=title, db=db)
            except Exception as llm_exc:
                # Never let an LLM failure touch the ready document
                logger.warning(
                    "llm_harvest failed for doc %s (non-fatal): %s", doc_id, llm_exc
                )

    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        logger.error("Pipeline failed for doc %s: %s", doc_id, msg, exc_info=True)
        try:
            db.update_document_extracted(doc_id, "", 0,
                                         readiness="error",
                                         error_message=msg)
        except Exception:
            pass
