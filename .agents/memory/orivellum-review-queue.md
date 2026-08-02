---
name: Governance review queue
description: Durable concurrency and item-key principles for the unified review inbox.
---

# Governance review queue

- Review items from heterogeneous tables share one inbox via namespaced keys `<type>:<row id>`. A literal colon between two FastAPI path params works as long as ids never contain colons.
- **Atomic claim rule:** every resolver (and defer) must claim ownership with a conditional write (rowcount check) before applying side effects; losers get 409/404.
  **Why:** stale UI cards and concurrent requests otherwise overturn finished human decisions or double-apply effects (e.g. two Works created from one suggestion). Code review + completion review both flagged read-then-act races here.
  **How to apply:** any endpoint that finalizes a human decision follows claim-first — and the claim must live in the *shared db primitive*, not the route, or a legacy route resolving the same row bypasses it. Unconditional status-setter helpers are not race-safe on their own.
- Human decisions are final: an approved/rejected item must never flip back via another surface; deferrals must not be creatable for already-resolved items.
- Auto-populated suggestions need an idempotence guard keyed on their source, or reprocessing duplicates them.
