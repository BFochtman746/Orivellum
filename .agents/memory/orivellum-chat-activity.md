---
name: Truthful chat activity strip (WP4)
description: Server-journaled activity events are the ONLY source of activity steps; terminalization rules; continuation recovery; aria-live copy pitfalls.
---

# Truthful chat activity & continuity (WP4)

**Rule:** The activity strip/drawer render only server-emitted events — `{activity:{stage,state,...}}` SSE frames (journaled, kind `activity`) and code-gen `{code_progress}` frames. The client never invents steps, and NEVER synthesizes step completion: a step is terminalized only by a server `done`/`failed` event. Stream-end `finally` blocks fade the strip but must not map open steps to `done:true` — a lost connection would paint success on unconfirmed stages.

**Why:** WP4 gate — "activity display never claims actions the server didn't emit." An earlier finally-block `done:true` sweep made `generation:start` show a checkmark after a network drop; architect review flagged it as a truth-contract violation.

**How to apply:**
- Client fold is pure (`pages/chat/activity.ts`): `applyActivityEvent`/`stepsFromEvents`; done/failed-without-start appends an already-completed row. Code-gen: `stepsFromCodeProgress(events, finished)` — a later stage frame completes the earlier stage (sequential pipeline), but the FINAL stage is done only when the job finished.
- Replay: `gen-replay.ts` `foldEvents` collects `activity` AND `code_progress` payloads; `recoverPendingGen` rebuilds steps from both and must clear the stale live-stream fade timer first.
- Continuation (`/continue`) is journal-wrapped and emits `job_id` first — `handleContinue` must record `setPendingGen` and render `parsed.activity`, and on a non-abort error with a job id recover via journal replay instead of toasting an error.
- Raw `<think>`/reasoning is never rendered — only a "Reasoned privately" indicator pill (`data-testid="reasoning-indicator"`).

**e2e pitfall:** aria-live/sr-only copy must not substring-match visible labels asserted with `getByText(...)` in Playwright (case-insensitive substring) — a live region saying "Message not delivered…" broke strict mode against the visible "Not delivered" badge.
