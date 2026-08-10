---
name: Re-extraction knowledge hygiene
description: Why re-running process_document needs explicit stale-knowledge cleanup, and how the Studio Library re-transcribe path handles it.
---

# Re-extraction knowledge hygiene

**Rule:** any path that re-runs `process_document` on a document whose text may change must call `db.delete_document_knowledge(doc_id)` first.

**Why:** `process_document` replaces chunks but never removes knowledge rows sourced from the document. Worse, `create_knowledge_item` dedups by text hash, so unchanged facts silently keep their OLD rows — a post-run timestamp-cutoff deletion would delete still-valid facts. Cleanup must therefore happen BEFORE harvest, as part of the destructive reset (warnings cleared, readiness → imported). A pipeline failure after that point leaves a consistent "error, no transcript, no auto-knowledge" state that a re-run fully rebuilds.

**How to apply:** `delete_document_knowledge()` (db.py) removes auto-derived rows (`review_status NOT IN ('approved',)`) plus their `knowledge_fts` rows and `vectors` entries, batched under the 999-var limit, and bumps the knowledge vector cache. Human-approved items are preserved deliberately.

Status: the Studio re-transcribe worker (`_run_retranscribe_job` in studio.py) does this. Library `/reprocess`, `/reprocess-all`, and nightshift recovery still do NOT (queued as a project task), and there is no cross-path document-level extraction lock — the Studio route only guards with 409 on readiness "imported" or an active job for the same doc.

Related quirk: the app has a global 404 exception handler (app.py) that rewrites every 404 detail to "Not found" — tests must assert status codes, never 404 detail text.
