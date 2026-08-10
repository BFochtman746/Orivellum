---
name: Security audit remediation patterns
description: Durable conventions established while fixing the Aug 2026 forensic audit (FA-01..FA-17)
---

# Audit remediation conventions (Aug 2026)

- **500-error discipline:** every unexpected-exception 500 goes through `internal_error(logger, exc, context)` in `orivellum/api/errors.py` — short ref id to the client, full traceback to logs. Never put `str(exc)` in a response detail, including inline extraction-failure markers embedded in returned text.
- **Path guards:** file-serving routes must resolve+confine with `Path.resolve().relative_to(root)` (never string `startswith` — prefix-sibling dirs bypass it) AND allowlist servable subtrees; deny `.db*`/`.sqlite*`/key files even inside allowed subtrees. Traversal tests must use percent-encoded `..%2F` — HTTP clients normalize literal `../` before the server sees it.
- **State transitions are compare-and-set:** `apply_transition` UPDATEs `WHERE id=? AND state=?` and raises `TransitionConflictError` (global handler → 409, `retryable: true`) on rowcount 0. `finalize_message` only writes terminal states and no-ops if already terminal. Streaming paths catch the conflict and log — never let it kill a stream.
- **Auth defense-in-depth:** ALL privileged routers carry `dependencies=[Depends(require_auth)]` in addition to the middleware. New routers must add it (only auth/health stay open). Tests that mount a router on a bare `FastAPI()` must send `tests.conftest.AUTH_HEADERS`.
  **Why:** the middleware is prefix-based; a mounting/normalization slip would silently expose everything.
- **require_auth gotcha:** its `Request` annotation must be a runtime import — with `from __future__ import annotations` + TYPE_CHECKING-only fastapi import, FastAPI treats the param as a body field and every protected route 422s.
- **Background jobs:** durable `bg_jobs` rows must be upserted BEFORE `executor.submit()` (a fast job can otherwise publish terminal state first and be resurrected as "running", then falsely orphan-reconciled after restart); upsert refuses to regress terminal states. Retry = atomic claim (flip failed→queued under the registry lock) with attempt cap; fallback threads are tracked and hard-capped, never bare daemon threads.
- **Check-then-write route sequences** (genesis gates/seal): hold `db._lock` (RLock, nesting safe) across read-check-write and make the final claim a CAS UPDATE.
- **Uploads:** streaming routes exempt from the body-size middleware must enforce their own byte ceiling inside the read loop (413, partial file cleaned by the outer handler).
- FA-11 (SESSION_SECRET doubling as login key) deliberately left to the user: set `ORIVELLUM_LOGIN_KEY` env or `login_key` DB setting; auth_keys.py logs a deprecation warning until then.
