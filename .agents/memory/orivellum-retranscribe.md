---
name: Re-extraction knowledge hygiene
description: Why re-running process_document needs explicit stale-knowledge cleanup, and how the Studio Library re-transcribe path handles it.
---

# Re-extraction knowledge hygiene

**Rule:** stale-knowledge cleanup lives INSIDE the pipeline — `process_document` calls `db.delete_document_knowledge(doc_id)` after the new extraction succeeds and before shield/chunk/harvest. Entry points must NOT duplicate it.

**Why:** `create_knowledge_item` dedups by text hash, so unchanged facts silently keep their OLD rows — a post-run timestamp-cutoff deletion would delete still-valid facts, so cleanup must run BEFORE harvest. Doing it after `extract()` succeeds (not as part of the entry point's destructive reset) means a failed re-extraction never destroys knowledge that still matches the stored text, and every entry point (library reprocess/reprocess-all/explode-zips, nightshift recovery, Studio re-transcribe) gets the same hygiene automatically.

**How to apply:** `delete_document_knowledge()` (db.py) removes auto-derived rows (`review_status NOT IN ('approved',)`) plus their `knowledge_fts` rows and `vectors` entries, batched under the 999-var limit, and bumps the knowledge vector cache. Human-approved items are preserved deliberately. After rule harvest the pipeline calls `db.invalidate_gap_cache(work_id)` so cached gap/coverage results never reflect deleted knowledge. Cross-path safety comes from the extraction reservation registry (see orivellum-extraction-reservation.md).

Testing note: when testing this, mock `pipeline.extract` (return a real `ExtractionResult`) so the REAL pipeline runs — mocking `process_document` bypasses the hygiene entirely. Rule harvest emits only summary/entity rows for short texts, so assert on row replacement, not body facts.

Related quirk: the app has a global 404 exception handler (app.py) that rewrites every 404 detail to "Not found" — tests must assert status codes, never 404 detail text.
