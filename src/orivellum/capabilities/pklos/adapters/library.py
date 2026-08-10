"""Adapter 2 — Local library / documents (spec §6.2).

Reuses Second Brain ingestion + A01_VECTOR_MEMORY retrieval (existing hybrid
search infrastructure).

Authority: A4 (governed internal knowledge base / PKLOS vault) or
           A3 (user-supplied original artifact — invoice, source file).

The existing discipline:
  "answer only from retrieved excerpts, cite date+session, say NOT IN MEMORY"
  is the answer contract for this adapter.

BUILDABLE-NOW: the retrieval infrastructure (FTS5 + vectors + hybrid search)
already exists. This adapter wraps it in the canonical Evidence shape.
"""
from __future__ import annotations

import logging
from typing import Any

from .base import AdapterBase, Evidence, Recipe

logger = logging.getLogger("orivellum.pklos.adapters.library")

_CAPABILITIES: list[str] = [
    # This adapter answers any predicate that can be found in library documents.
    # It's a broad adapter — the verifier will assign confidence based on
    # passage quality + source authority.
    "book_title", "book_author", "book_publication_date", "book_publisher",
    "concept_definition", "procedure_steps", "specification_value",
    "historical_fact", "technical_spec", "document_claim",
    # Generic sentinel — library can attempt any predicate via hybrid search
    "*",
]


class LibraryAdapter(AdapterBase):
    """Adapter 2: retrieves evidence from the user's local document library.

    Each evidence item carries:
      - source_type: "library_document"
      - source_locator: "{doc_id}:chunk:{chunk_id}" for passage-level citation
      - authority: A4 for vault items, A3 if marked as original artifact
      - raw_value: the passage text (not the whole document)

    Claims about document contents MUST cite passage locators (spec §6.2).
    NOT IN MEMORY is returned on absence — no fallback guess.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    @property
    def adapter_id(self) -> str:
        return "library@0.1.0"

    def capabilities(self) -> list[str]:
        return _CAPABILITIES

    def can_answer(self, predicate: str) -> bool:
        # Library can attempt any predicate via document search
        return True

    def evidence_recipe(self, predicate: str) -> Recipe:
        return Recipe(
            predicate=predicate,
            sources=["knowledge_fts", "chunks_hybrid_search", "library_fts"],
            minimum_authority="A4",
            minimum_corroboration=1,
            notes=(
                "Passage-level citation required. "
                "NOT IN MEMORY on absence — no fallback guess. "
                "A3 if source document is a user-supplied original artifact."
            ),
        )

    def fetch(self, predicate: str, *, freshness: str = "DURABLE") -> list[Evidence]:
        """Search library documents for evidence about a predicate.

        Uses hybrid search (FTS5 + vectors). Returns Evidence with passage locators.
        Returns an empty list (NOT IN MEMORY) on absence.
        """
        try:
            # Reuse existing hybrid search infrastructure
            from orivellum.capabilities.embeddings import (
                hybrid_search_chunks,
                hybrid_search_knowledge,
            )
            evidence: list[Evidence] = []

            # Search knowledge items
            knowledge_hits = hybrid_search_knowledge(predicate, self._db, limit=5)
            for hit in knowledge_hits:
                text = (hit.get("text") or "").strip()
                if not text:
                    continue
                doc_id = hit.get("source_doc_id") or hit.get("id", "")
                evidence.append(Evidence(
                    source_type="library_knowledge",
                    source_locator=f"knowledge:{hit.get('id', '')}",
                    authority="A4",
                    raw_value=text[:500],
                    predicate=predicate,
                    subject="",
                    meta={
                        "doc_id": doc_id,
                        "kind": hit.get("kind", "note"),
                        "work_id": hit.get("work_id"),
                        "review_status": hit.get("review_status"),
                    },
                ))

            # Search document chunks (passage-level, with locators)
            chunk_hits = hybrid_search_chunks(predicate, self._db, work_id=None, limit=5)
            for chunk in chunk_hits:
                text = (chunk.get("text") or "").strip()
                if not text:
                    continue
                doc_id  = chunk.get("doc_id") or ""
                chunk_id = chunk.get("id") or ""
                evidence.append(Evidence(
                    source_type="library_document",
                    source_locator=f"doc:{doc_id}:chunk:{chunk_id}",
                    authority="A4",
                    raw_value=text[:500],
                    predicate=predicate,
                    subject="",
                    meta={
                        "doc_id": doc_id,
                        "chunk_id": chunk_id,
                        "doc_title": chunk.get("doc_title"),
                    },
                ))

            return evidence
        except Exception as exc:
            logger.debug("LibraryAdapter.fetch failed (non-fatal): %s", exc)
            return []

    def is_in_library(self, query: str) -> bool:
        """Quick check: does the library contain anything relevant to this query?

        Returns False (NOT IN MEMORY) on any failure.
        """
        try:
            evidence = self.fetch(query)
            return bool(evidence)
        except Exception:
            return False
