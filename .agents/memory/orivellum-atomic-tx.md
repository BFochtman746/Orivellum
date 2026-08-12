---
name: db.atomic() + governed_write composition
description: Why atomic() must BEGIN IMMEDIATE explicitly and why read_conn joins the open transaction — silent partial commits otherwise.
---

# The outermost-savepoint trap (fixed Aug 2026)

`governed_write` nests inside `db.atomic()` via SAVEPOINT. But if the FIRST write inside an atomic block is a governed_write, its `SAVEPOINT` statement itself opened the SQLite transaction (pysqlite only implicit-BEGINs on DML) — and RELEASE of an outermost savepoint COMMITS. Result: each governed_write inside atomic committed immediately and `atomic()`'s rollback undid nothing.

**Fix:** `atomic()` now executes `BEGIN IMMEDIATE` explicitly before yielding (when not already in a transaction). Never remove that.

# read_conn inside atomic

`db.read_conn()` is a separate per-thread committed-data connection — it cannot see the open atomic transaction's writes. Store methods that write-then-read (create → get) returned None/stale inside atomic. **Fix:** `read_conn()` returns `self._conn` when the calling thread is inside its own atomic block (it already holds the lock).

**How to apply:** compose multi-store operations with `with db.atomic():` freely; governed_writes ride along via savepoints and reads see in-flight state. Verify atomicity with fault-injection tests (mock a late step to raise, assert nothing persisted) — see tests/test_collections_domains.py ConversionTests.
