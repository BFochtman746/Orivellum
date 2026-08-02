---
name: Governance review queue
description: Durable concurrency and item-key principles for the unified review inbox.
---

# Governance review queue

- Review items from heterogeneous tables share one inbox via namespaced keys `<type>:<row id>`. A literal colon between two FastAPI path params works as long as ids never contain colons.
- **Atomic claim rule:** every resolver (and defer) must claim/validate ownership with a conditional `UPDATE`/`DELETE` (`WHERE resolved=0` / `WHERE review_status='ai_auto'`, rowcount check) under `db._lock` before applying side effects; losers get 409/404.
  **Why:** stale UI cards and concurrent requests otherwise overturn finished human decisions or double-apply effects (e.g. two Works created from one suggestion). Code review + completion review both flagged read-then-act races here.
  **How to apply:** any new resolver type, or any endpoint that "finalizes" a human decision, follows the same claim-first pattern; unconditional db helper methods (e.g. status setters) are not race-safe on their own.
- Human decisions are final: an approved/rejected item must never flip back via another surface; deferrals must not be creatable for already-resolved items.
- Auto-populated suggestions need an idempotence guard (e.g. `json_extract(meta,'$.source_id')` uniqueness check) or reprocessing duplicates them.
