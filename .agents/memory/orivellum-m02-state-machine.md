---
name: M0.2 State Machine
description: Sovereign Platform M0.2 — declarative state machine engine with governance findings blockers
---

## What was built
- `capabilities/enums.py` — MessageState, JobState, FindingState, FindingSeverity, BookState B0..B17, DocumentReadiness, DocumentLifecycle as `str, enum.Enum`
- `capabilities/state_machine.py` — `StateMachine`, `apply_transition`, `InvalidTransitionError`, `BlockedTransitionError`; pre-built `MESSAGE_SM`, `JOB_SM`, `BOOK_SM`
- Schema v58: `findings` table (id, object_id, object_type, kind, description, severity, state, created_at, resolved_at, resolved_by, meta)
- Schema v59: `messages.state TEXT DEFAULT 'done'` — existing rows seeded as done
- `db.py`: `create_finding`, `list_findings`, `get_finding`, `resolve_finding`
- `app.py`: `InvalidTransitionError` → 422, `BlockedTransitionError` → 409 handlers
- REST: `GET/POST /governance/findings`, `GET /governance/findings/{id}`, `PATCH /governance/findings/{id}/resolve`
- REST: `PATCH /system/jobs/{id}/state`, `GET /system/jobs/{id}`; db helpers `create_job/get_job/list_jobs/update_job_state`
- Frontend: `FindingsSection` on governance page (auto-hides when no open findings; shows blocking badge)

## Rules
- Only `apply_transition(db, sm, ...)` may change state — server is the authority
- `check_blockers=False` for backward/return transitions (always allowed)
- Only `severity in ('high', 'critical')` blocks; `warning`/`info` are advisory
- BookState forward: single step only; BOOK_SM enforces it
- messages.state should be written explicitly on creation (not relying on DEFAULT 'done')
- `_CASConflict` is an internal sentinel that causes governed_write to rollback before returning "conflict"

**Why:** AC-3 of M0.2 requires server authority — no client path can bypass assert_transition.

## What still needs wiring (as of 2026-08-03)
- apply_transition not yet called from actual conversation/job update routes
- BOOK_SM not yet called from document lifecycle PATCH endpoint
- messages.state not set during streaming pipeline (stays at 'done' default)
- Task #228: remaining ~28 db write helpers still use standalone self.audit() not governed_write
