---
name: Nightshift maintenance runner
description: Constraints learned while building the 10-pass nightly maintenance job — vector cleanup, VACUUM locking, recovery concurrency.
---

# Nightshift maintenance constraints

Rules for the nightly 10-pass maintenance runner (DB optimise, temp cleanup, orphan cleanup, stuck-doc recovery, harvest, gaps, evidence, embeddings, stats).

- **Vector orphan cleanup must be type-aware.** The `vectors` table stores BOTH `object_type='chunk'` and `object_type='knowledge'` rows. A DELETE checking only against `knowledge` wipes every chunk vector nightly and the 300-item backfill can never catch up — the semantic index silently degrades.
  **Why:** caught in code review 2026-08-02 before it shipped.
- **VACUUM runs on the main serialized connection while holding `db._lock`.** A second sqlite3 connection can VACUUM a WAL database but races the app's own writers and fails with SQLITE_BUSY after its timeout. Commit first (VACUUM can't run in a transaction).
- **Stuck-doc recovery is one sequential worker thread, not N parallel threads.** Parallel extraction pipelines contend on the single shared SQLite connection and saturate CPU/LLM on a single-user machine.
- Daemon scheduling uses `target += timedelta(days=1)` — `replace(day=day+1)` crashes on month end.
