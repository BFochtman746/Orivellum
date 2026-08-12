---
name: Collections & work-deletion cascades
description: Import-provenance collection table rules and the objects-ghost hazard when deleting works
---

# Collections (import provenance)

- `collection` table (singular) is provenance only — NEVER a subject. `db.assert_not_collection(id, context)` is the enforced refusal, called by curriculum seeding routes, book-pipeline entry, and all three harvest entry points (`harvest`, `llm_harvest`, `llm_harvest_by_chapters`).
- Demoted batch Works reuse their old work id as the collection id, so stale references stay resolvable as provenance.
- ZIP explode get-or-creates a collection keyed on `"{zipname} sha256:{sha}"` source_ref; folder watch keys on `"folder:{dir}"`. Counts are recomputed live in `list_collections()` — the stored `document_count` is only a snapshot.

# Deleting works: the objects-ghost hazard

**Rule:** deleting a `works` row cascades to ~40 dependent tables, but any cascade child whose own `id REFERENCES objects(id) ON DELETE CASCADE` (tasks, publications, book_pipelines, book_chapters) loses its child row while its `objects` parent survives — governed-object ghosts.

**Why:** SQLite cascades only travel parent→child; the child's separate objects-parent edge is untouched. Caught by architect review of migration v144.

**How to apply:** to remove object-backed rows, delete their `objects` rows (cascade removes the child) — never delete the child table row directly or rely on a works-side cascade. After any bulk delete, `PRAGMA foreign_key_check` in a test.

# Pre-migration verified backup (Phase 0)

- `_run_migrations` takes a VERIFIED backup before applying pending migrations to any existing DB (schema_version > 0): SQLite online-backup copy → integrity_check + doc count + sampled sha compared against live → fail closed. Fresh DBs skip it. Prunes to newest 3 backups.
