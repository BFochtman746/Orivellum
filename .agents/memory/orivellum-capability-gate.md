---
name: Capability gate (route/UI evidence audit)
description: Lessons from the capability-gate audit work — router introspection, baselines, and UI evidence scanning.
---

# Capability gate

- FastAPI's `include_router` wraps routers, so introspection that walks
  `app.routes` must handle the included-router indirection — do not assume the
  original `APIRouter` object is reachable; enumerate the mounted `APIRoute`s
  themselves.
- Audit baselines are shrink-only: the allowlist of known-unproven items may
  only lose entries over time; any new unproven capability fails the gate
  instead of growing the baseline.
- UI evidence scanning must be verb-aware: a frontend reference to a path only
  counts as evidence for the HTTP method it actually invokes (GET fetch does
  not prove a DELETE endpoint is used).
