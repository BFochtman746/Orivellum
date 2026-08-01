---
name: Orivellum schema quirks
description: Non-obvious SQLite schema facts for the Orivellum database.
---

## works table has no own timestamp
The `works` table columns: `id, title, work_type, description, status, meta` — **no created_at, no updated_at**.

All timestamps for works come from the `objects` table via JOIN:
```sql
SELECT w.*, o.created_at, o.updated_at
FROM works w JOIN objects o ON o.id = w.id
```

**Why:** The blueprint uses a governed objects root table. Every domain entity's id is also an objects.id. All lifecycle metadata (timestamps, lifecycle, permissions) lives in objects.

## Affected queries
- `recent_activity()` — must join works with objects for created_at
- `dashboard_summary()` — already correctly joins: `FROM works w JOIN objects o ON o.id=w.id`
- Any raw `SELECT ... FROM works ORDER BY created_at` will fail with OperationalError

## Tables that DO have own created_at
documents, knowledge, conversations, messages, tasks — all have their own `created_at` column.

## chunks.id and knowledge.id are FKs to objects(id)
Both `chunks` and `knowledge` use `id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE`.
You cannot INSERT a chunk or knowledge row using a plain `uuid4()` — the id must first be registered via `create_object("chunk")` / `create_object("knowledge")`.

**Why:** The schema treats every entity as a governed object so lifecycle/provenance are universal.

**How to apply:** `add_chunk()` must call `self.create_object("chunk")` to get the id before inserting into chunks. `create_knowledge_item()` already does this correctly via `create_object("knowledge")`.
