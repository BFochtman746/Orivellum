---
name: db.atomic() + governed_write composition
description: Why atomic() must BEGIN IMMEDIATE explicitly and why read_conn joins the open transaction — silent partial commits otherwise.
---

# The outermost-savepoint trap

RELEASE of an OUTERMOST savepoint COMMITS in SQLite, and pysqlite only implicit-BEGINs on DML. So a savepoint-based nested-transaction pattern silently commits partial state whenever the savepoint is the first statement of the "transaction". **Rule:** the outer transaction manager must open the transaction explicitly (`BEGIN IMMEDIATE`) before any nested savepoint runs. **Why:** rollback-on-error is a no-op once RELEASE already committed — partial state survives silently, violating the no-silent-fallback doctrine.

# Reads inside an open transaction

A separate read connection (WAL reader) only sees committed data — write-then-read store methods (create → get) return None/stale inside an open transaction. **Rule:** while a thread owns the write transaction, route its reads through the writer connection. **How to apply:** compose multi-store operations inside one transaction freely, and prove atomicity with fault-injection tests (mock a late step to raise, assert nothing persisted).
