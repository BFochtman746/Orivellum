---
name: Book Pipeline Slice
description: How the B0–B16 book production pipeline is wired into the Orivellum API and Works UI
---

## Rule
`book_pipelines` creates one record per Work. Call `db.create_book_pipeline(work_id, title)` — it is idempotent (returns existing if found) and links orphan `book_chapters` (pipeline_id IS NULL, same work_id) automatically.

## Routes added to works.py
- `POST /works/{id}/pipeline` — idempotent create at B0; requires `Body` import from fastapi
- `GET  /works/{id}/pipeline` — current state + chapter counts (total/extracted/drafted/approved)
- `POST /works/{id}/pipeline/advance` — one forward step via `BOOK_SM.apply_transition`; 409 if findings block, 422 at terminal

## State machine usage
```python
from orivellum.capabilities.state_machine import BOOK_SM, apply_transition
apply_transition(db, BOOK_SM, object_id=pipeline["id"], object_type="book_pipeline",
    table="book_pipelines", state_col="status",
    from_state=current, to_state=next_state, actor="user")
```
BOOK_SM is sequential — `BOOK_SM.allowed_from(state)` always returns exactly one state (or empty frozenset at terminal B16).

## UI
`PipelinePanel` component in `artifacts/orivellum-ui/src/pages/works/book-tab.tsx` sits at the top of `BookTab`. Uses `useMutation` for create + advance, invalidates `["pipeline", workId]` and `["book-intelligence", workId]` on success.

**Why:** `book_pipelines` had zero writers before this slice — schema and state machine existed but no API ever created or advanced a pipeline record.

**How to apply:** Any future book-pipeline feature (chapter contracting, WR-04 plan tree) should use the same three routes as the entry point. The pipeline_id links chapters; always check `pipeline_id IS NULL` when querying orphan chapters.
