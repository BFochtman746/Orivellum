---
name: Document lifecycle design
description: How document lifecycle (draft/canonical/superseded/reference) is stored and used in Orivellum.
---

## Rule
`lifecycle` is a column on the `objects` table, not `documents`. Queries that need lifecycle must JOIN:
```sql
SELECT d.*, COALESCE(o.lifecycle, 'draft') AS lifecycle
FROM documents d LEFT JOIN objects o ON o.id = d.id
```

## Key facts
- `create_document()` calls `_create_object("document", lifecycle="draft")` — all new docs start as draft.
- Schema v48 migrated all existing document objects from `lifecycle='active'` → `lifecycle='draft'`.
- `update_document_lifecycle(doc_id, lifecycle)` is the canonical setter; when lifecycle='canonical' it auto-demotes all other same-work/same-kind docs to 'draft' (skips 'superseded' and 'deleted').
- Valid values: `draft`, `canonical`, `superseded`, `reference` (plus legacy `active` tolerated at DB level).
- API: `PATCH /api/library/{doc_id}/lifecycle` with body `{"lifecycle": "..."}`.
- Version-relationship suggestions are written to the `suggestions` table (kind='version_relationship', meta JSON has doc_a_id/doc_b_id) when a similar-named doc is imported to the same Work.

**Why:**
Lifecycle was always in the objects table (since v1) but never surfaced to the application. The JOIN approach avoids adding a redundant column to the documents table.

**How to apply:**
Any time you add a new query for documents, include the objects JOIN. Never check lifecycle directly on `documents` rows — it isn't there.
