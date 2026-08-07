# T07 — Full-Stack Change

**Capability tested:** Multi-layer coordination — API change, UI change, and test fixture change that must all be consistent.  
**Language:** TypeScript (Vite + Express)  
**Difficulty:** Hard  
**Expected repair cycles:** 1–2  
**Expected time to gate:** < 12 minutes

---

## Background

The seed project is a small task tracker: an Express API backed by SQLite and a Vite/React frontend. Tasks have `id`, `title`, and `done`. You need to add a `priority` field (1–5, default 3).

## Task prompt

```
Add a `priority` field (integer 1–5, default 3) to tasks.

Changes needed:
1. `server/db.ts` — add `priority INTEGER NOT NULL DEFAULT 3` to the tasks table schema
   and update the migration to add the column if upgrading from an existing DB.
2. `server/routes/tasks.ts` — accept and return `priority` in create/update/get endpoints.
3. `client/src/TaskCard.tsx` — display the priority as "P1"–"P5" badge.
4. `server/tests/tasks.test.ts` — add tests for priority in create and update.

Do NOT change the route paths. Do NOT change the `done` field behavior.
```

## Non-goals

- No UI changes other than the priority badge on TaskCard
- No other database schema changes
- No new dependencies

## Acceptance criteria

1. `tsc --noEmit` passes in both `server/` and `client/`
2. `server/tests/tasks.test.ts` passes including new priority tests
3. `GET /tasks/:id` response includes `priority`
4. `POST /tasks` with `{"title":"x"}` creates a task with `priority: 3`
5. `POST /tasks` with `{"title":"x","priority":5}` creates a task with `priority: 5`
6. `POST /tasks` with `{"title":"x","priority":6}` returns 400
7. Playwright `e2e/task-card.spec.ts` shows the priority badge in the rendered card
