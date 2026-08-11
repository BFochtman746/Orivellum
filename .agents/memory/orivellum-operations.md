---
name: Operations system (durable multi-step runner)
description: Design rules for the operations capability — registry, claim fencing, resume semantics
---

# Operations — durable multi-step runs

- Capability lives at `capabilities/operations/` (registry/builtin/store/runner/playbooks); routes at `/api/operations` (literal `/actions` & `/playbooks` before `/{op_id}`); UI page `/operations` in the Command app group.
- **Claim fencing rule:** every successful claim (start/resume) rotates `operations.run_token`; every step/operation transition is a conditional UPDATE that carries the token. A stale runner superseded by a newer resume silently no-ops. Never add a transition that skips the fence.
  **Why:** two racing resumes previously could interleave reset-then-claim and let a stale runner mark an op done with a step still pending.
- Claim is one atomic read→reset-failed-steps→CAS block under `db._lock`; the resume route must NOT reset steps itself.
- Pause takes effect at step boundaries or inside polling steps via `ctx.should_stop()` (checks state AND token); interrupted steps revert to `pending` and re-run from scratch — step actions must be idempotent-ish.
- Startup reconciliation (`_recover_interrupted_operations` in app.py lifespan) flips orphaned `running` ops to `paused` (never auto-resume). **Lesson:** inserting a helper directly above `async def lifespan` lands between `@asynccontextmanager` and the def — the decorator silently captures the helper and the code never runs. A test pins the helper as a plain function.
- `render_audiobook` step detaches on pause (doesn't cancel the render) and re-attaches via the work-start route's 409 path.
- Every one-shot action from `capabilities/actions` is auto-wrapped into the registry as `action:<name>` — new one-shot actions become operation steps for free.
- Playbooks are data-only (`playbooks.py`); starting one copies steps into the DB, so playbook edits never affect in-flight runs.
