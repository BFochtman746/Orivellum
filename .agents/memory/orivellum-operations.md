---
name: Operations system (durable multi-step runner)
description: Behavioral invariants for the operations capability — claim fencing, resume semantics, layering
---

# Operations — durable multi-step runs

- **Claim fencing:** every successful claim (start/resume) rotates the operation's run token; every step/operation transition is a conditional UPDATE carrying it, so a superseded runner silently no-ops. Never add a transition that skips the fence.
  **Why:** racing resumes could otherwise interleave and mark an op done with a step still pending.
- **Claim resets stranded steps atomically:** the claim itself (one locked block) resets failed/cancelled/running steps to pending. The resume route must never reset separately, and a claim from `paused` MUST reset — a user can resume before the paused worker reaches its checkpoint, and once the token rotates that worker can neither revert nor finish its step.
- Pause takes effect at step boundaries or inside polling steps via should_stop() (checks state AND token); interrupted steps revert to pending and re-run from scratch — step actions must be idempotent-ish.
- Startup reconciliation flips orphaned `running` ops to `paused`; never auto-resume.
- **Layering:** capabilities must not import orivellum.api (import-linter contract, baseline shrink-only). The operations capability gets notify/submit_bg/studio injected via its hooks module, configured by the operations router at import time.
- **Lesson:** inserting a helper directly above `async def lifespan` lands between `@asynccontextmanager` and the def — the decorator silently captures the helper and it never runs. A test pins the recovery helper as a plain function.
- The audiobook step detaches on pause (doesn't cancel the render) and re-attaches to the live render on resume; playbooks are data-only, and starting one copies steps into the DB so playbook edits never affect in-flight runs.
