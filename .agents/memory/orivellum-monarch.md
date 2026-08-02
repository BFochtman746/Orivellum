---
name: MONARCH Knowledge Intelligence implementation
description: All MONARCH-spec capabilities added in the batch implementation pass — chapters, dedup, completeness, gaps, governance, audit, versioning, graph, book intelligence page.
---

# MONARCH Knowledge Intelligence — implementation map

## Schema version
- Schema is now **v46** (doc_versions table added)
- book_chapters (v13), entities/edges/vectors (v29), doc_dupes/minhash_sig (v38), audit_log (v42) all existed in schema but had no operational code before this pass

## New capabilities (all in `src/orivellum/capabilities/`)

| File | Purpose |
|------|---------|
| `chapters.py` | Heading-based chapter/section extraction — markdown, "Chapter N", ALL-CAPS DOCX fallback |
| `dedup.py` | MinHash near-duplicate detection (pure Python, 128 perms) — writes to minhash_sig + doc_dupes |
| `completeness.py` | 5-dimension completeness scoring: structural / content / research / editorial / source |
| `gaps.py` | Research gap detection: uncovered chapters, weak coverage, no-structure docs + suggested queries |

## Pipeline hooks (in `pipeline.py`, after readiness is set)
- Step 4.5: `extract_chapters()` → `db.upsert_book_chapters()`
- Step 4.6: `compute_and_store()` + `find_and_record_near_duplicates()`
- Audit call: `db.audit("document.ready", ...)`

## New DB methods (in `db.py`)
- `db.audit()` — appends to audit_log; never raises
- `db.list_audit_log()` — newest-first, filterable
- `db.upsert_book_chapters()` — idempotent: deletes+reinserts objects rows
- `db.get_book_chapters()` — ordered by seq
- `db.list_near_duplicates()` — joins doc titles
- `db.create_document_version()` / `list_document_versions()` / `set_canonical_version()`

## New API endpoints

| Route | File |
|-------|------|
| GET /api/library/{id}/chapters | library.py |
| GET /api/library/{id}/versions | library.py |
| POST /api/library/{id}/versions | library.py |
| PATCH /api/library/{id}/versions/{vid}/canonical | library.py |
| GET /api/library/duplicates | library.py |
| GET /api/works/{id}/completeness | works.py |
| GET /api/works/{id}/gaps | works.py |
| GET /api/works/{id}/graph | works.py |
| GET /api/governance/pending | system.py |
| GET /api/governance/stats | system.py |
| GET /api/search | system.py — hybrid FTS across knowledge + chunks + doc titles |
| GET /api/system/audit-log | system.py |

## New UI pages/routes

| Route | Component |
|-------|-----------|
| /works/:id/intelligence | `pages/works/intelligence.tsx` — "book intelligence" single-view |
| /governance | `pages/governance/index.tsx` — AI-item review queue |

## New tabs in existing pages

| Page | New tabs |
|------|---------|
| Library detail (`/library/:id`) | Chapters, Versions |
| Works detail (`/works/:id`) | Completeness, Gaps |

## Key patterns / gotchas
- `book_chapters.id` references `objects(id)` — must INSERT into objects first (type="chapter") before inserting into book_chapters; idempotent delete cleans objects rows too
- `doc_versions` does NOT reference objects — plain standalone table
- `db.audit()` is safe to call anywhere; swallows exceptions silently
- MinHash sketch is 128×uint32 = 512 bytes stored as BLOB in minhash_sig.sig
- `extract_chapters()` returns empty list when text < 100 chars or no headings found — check before calling upsert
- Chapter extraction falls back: markdown → "Chapter N" lines → ALL-CAPS heuristic
- Completeness weights: structural 25%, content 25%, research 25%, editorial 15%, source 10%
- Gap detection threshold: 3+ knowledge items per chapter = "covered"
- Governance nav link added to "Review" phase in layout.tsx
- Intelligence page linked via "Intelligence" button in Works detail header (Brain icon)

**Why upsert_book_chapters deletes objects rows:**
book_chapters.id is a FK to objects(id). If you DELETE from book_chapters without cleaning objects, orphaned objects rows accumulate. The upsert method handles both tables atomically.
