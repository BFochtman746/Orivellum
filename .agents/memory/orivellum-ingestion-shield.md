---
name: Ingestion shield & chat abstention
description: Uplift Phases 3+4 — quarantine flow, spotlighting, mail gates, abstention directive; the invariants that keep the boundary tight.
---

# Ingestion shield (Uplift Phase 3) + chat abstention (Phase 4)

Core module: `capabilities/shield.py` — `screen()` tripwire (regex PATTERNS +
invisible-char threshold ≥5), `wrap()` fenced spotlighting,
`UNTRUSTED_SECTION_PREAMBLE`, `ABSTENTION_DIRECTIVE`, `GateDenied`,
`gate_send_mail` / `gate_send_reply`.

## Invariants (learned via architect FAIL, do not regress)
- **Quarantine must be enforced at every READ, not just at cleanup.** A doc
  can be quarantined after a prior clean indexing run, and cleanup can fail
  or race. Every retrieval path filters `COALESCE(d.quarantined,0)=0`:
  `search_chunks`, `search_chunks_filtered` (both branches),
  `search_knowledge` (LEFT JOIN via source_doc_id), semantic_search chunk AND
  knowledge SQL, trailer `book_text_from_work`, nightshift ctx backfill,
  minhash scan. Any NEW consumer of chunks/knowledge/extracted_text must add
  the same filter.
- **Semantic vector cache invalidates on vector COUNT only** — deleting
  chunks without their vectors leaves stale cached embeddings serving
  quarantined text. Quarantine cleanup deletes vectors first, then chunks.
- Quarantine state: 0 clean, 1 pending review, 2 reviewed-and-kept.
  Findings live in `documents.meta.shield`; a human release sets
  `meta.shield.released=true`, which the pipeline screen step checks so
  reprocess never re-quarantines.
- Quarantined docs get readiness "ready" (stored + inspectable in UI) but are
  never chunked/harvested/embedded/LLM'd; release = reprocess via the review
  resolver (atomic conditional UPDATE claim, same pattern as other types).
- Review queue item type "quarantine" uses confidence 0.0 so security items
  surface first; reopen route flips state 2→1.
- Mail gates are inactive until `mail_trusted_domains` setting is configured
  (back-compat); when set, reply target domain must be trusted AND drafted
  body must pass screen(). Reply path only has `sender_domain` in clear.
- Spotlighting: harvest prompts wrap `{chunk}` with shield fences (safe —
  wrapped AFTER template parsing, no str.format hazard); chat pinned/chapter
  blocks get UNTRUSTED_SECTION_PREAMBLE; WEB SOURCES header warns untrusted.
- Abstention: ABSTENTION_DIRECTIVE appended at all three knowledge_section
  build sites in the chat system prompt (always-on, no setting).

**Why:** injection can't be prevented in-model; the design is blast radius —
screen() is only an alarm, gates + read-time filtering are the boundary.
**How to apply:** whenever adding a feature that reads document text, chunks,
knowledge, or vectors, add the quarantined filter and a stale-artifact test
(see tests/test_ingestion_shield.py::test_search_excludes_quarantined_even_with_stale_chunks).
