---
name: Governance review queue
description: Unified /api/review inbox design — item-key namespacing, atomic claim pattern, deferrals, ZIP auto-suggestions.
---

# Governance review queue

- `GET /api/review/queue` aggregates four sources into one inbox: knowledge `review_status='ai_auto'`, `pending_reclassify`, unexpired `suggestions`, unresolved `doc_dupes`. Sorted confidence ASC (most uncertain first; None→0.5).
- Item ids are namespaced `<type>:<row id>` (knowledge/reclassify/suggestion/duplicate). Resolve route is `POST /api/review/{item_type}:{item_id}/resolve` — FastAPI/Starlette handles the literal colon between two path params fine as long as ids contain no colon.
- **Atomic claim rule:** every resolver must claim ownership before applying side effects — conditional `DELETE`/`UPDATE … WHERE resolved=0` with rowcount check under `db._lock`; losers get 409/404. Without this, concurrent approvals of one work_assignment suggestion each create a Work.
  **Why:** code review caught the read-then-act race; SQLite + threadpool routes make it real.
- Duplicate approve validates `canonical_doc_id` ∈ pair (400 otherwise); `db.resolve_near_duplicate` silently defaults to doc_a-canonical, so the route must validate before delegating. UI has a "Keep on approve" picker on duplicate cards.
- Defer = 7-day snooze via `review_deferrals` table (schema v54, item_key PK); queue excludes keys with `deferred_until > now`.
- ZIP explode auto-creates a `work_assignment` suggestion when an archive with no work_id yields >2 children; dedup-guarded by `json_extract(meta,'$.archive_doc_id')`.
- Web: `/review` page + "Review Queue" nav item with count badge polling `?limit=1` every 60 s (queryKey `review-queue-count`, invalidated on resolve). `/governance` page kept as the knowledge-specific deep-review tool.
