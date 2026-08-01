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
from orivellum.capabilities.knowledge_harvest import harvest

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)


def process_document(doc_id: str, file_path: str, kind: str,
                     work_id: str | None, title: str,
                     db: "OrivellumDB") -> None:
    """Extract, chunk, and harvest a single document.

    Safe to call from a daemon thread — catches and logs all exceptions.
    """
    try:
        logger.info("Processing doc %s (%s) kind=%s", doc_id, title, kind)
        path = Path(file_path)
        if not path.exists():
            logger.warning("Doc file not found: %s", file_path)
            db.update_document_extracted(doc_id, "", 0, readiness="error")
            return

        # Step 1: extract text
        result = extract(path, kind)
        if not result.ok:
            logger.warning("Extraction produced no text for %s", doc_id)
            db.update_document_extracted(doc_id, "", 0, readiness="no_text")
            return

        # Step 2: chunk and index
        chunk_and_store(result, doc_id, db)

        # Step 3: harvest knowledge
        harvest(result, doc_id=doc_id, work_id=work_id,
                doc_title=title, db=db)

        # Step 4: mark document ready
        db.update_document_extracted(
            doc_id,
            extracted_text=result.full_text[:100_000],  # cap stored text
            word_count=result.word_count,
            readiness="ready",
        )
        logger.info("Doc %s processed — %d words, ready", doc_id, result.word_count)

    except Exception as exc:
        logger.error("Pipeline failed for doc %s: %s", doc_id, exc, exc_info=True)
        try:
            db.update_document_extracted(doc_id, "", 0, readiness="error")
        except Exception:
            pass
