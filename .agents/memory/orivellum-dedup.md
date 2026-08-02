---
name: Near-duplicate detection
description: MinHash-based near-duplicate document detection — tables, pipeline hook, API, route ordering constraint.
---

## Rule
`GET /library/duplicates` and `POST /library/duplicates/{id}/resolve` MUST be registered **before** `GET /library/{doc_id}` in library.py. FastAPI matches routes in registration order; if the parameterized route comes first, "duplicates" is captured as a doc_id (404).

## Key facts
- MinHash implementation: `src/orivellum/capabilities/dedup.py` — `compute_and_store()`, `find_and_record_near_duplicates()`; 128 permutations, 5-word shingles; no external deps.
- Pipeline integration: step 4.6 in `process_document()` (pipeline.py). Runs in a daemon thread after readiness is set. Completely non-fatal.
- Tables: `minhash_sig(doc_id PK, sig BLOB, created_at)` and `doc_dupes(id PK, doc_a_id, doc_b_id, similarity, kind, resolved INT DEFAULT 0, resolution TEXT, created_at)` — both in schema v31.
- Schema v49 added `resolved` + `resolution` columns to `doc_dupes`.
- DB method: `resolve_near_duplicate(dupe_id, action)` — action ∈ {keep_both, mark_versions, mark_superseded}.
  - `mark_versions`: creates a `DERIVED_FROM` relationship; relationships.id FK → objects, so must call `_create_object("relationship")` first then INSERT into relationships.
  - `mark_superseded`: calls `update_document_lifecycle(doc_b_id, "superseded")`.
- `list_near_duplicates(resolved=False)` — filters on `dd.resolved=?`; default shows unresolved only.
- Backfill gap: existing docs don't get MinHash sigs until they're reprocessed; task #162 will add a scan endpoint.

**Why:**
Literal route segments are not automatically prioritized over path params in FastAPI/Starlette — registration order governs.

**How to apply:**
Any time you add a new literal sub-path under `/library/` (like `/library/export`, `/library/stats`), make sure it's registered before `GET /library/{doc_id}`.
