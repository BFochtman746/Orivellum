---
name: System diagnostic tool
description: Full system health check — DB integrity, orphans, stuck records, config, services, data quality, nightshift, pipeline health.
---

# Orivellum System Diagnostic Tool

## What it does
Runs 57+ checks across every subsystem and produces a structured report (JSON + Markdown).

## How to apply
- **CLI:** `uv run python scripts/run_diagnostics.py` — prints Markdown to stdout
- **CLI with VACUUM:** `uv run python scripts/run_diagnostics.py --vacuum --out diag.md`
- **API:** `GET /api/system/diagnostics?vacuum=false`
- **Web UI:** System page → "System Diagnostic" card → "Run Diagnostic" or "+ VACUUM"

## Schema version key
The DB tracks schema version in `settings.value WHERE key='schema_version'` — NOT `PRAGMA user_version` (which stays at 0).

## Key fix facts
- `outbox` table uses `dispatched_at`, not `delivered_at`
- Governance review queue table is `review_deferrals`, not `review_queue`
- `pipeline_artifacts` v63 uses FK to `book_pipelines(id)` with ON DELETE CASCADE

## Files
- `src/orivellum/capabilities/diagnostics.py` — engine (run_full_diagnostic)
- `scripts/run_diagnostics.py` — CLI wrapper
- `src/orivellum/api/routes/system.py` — GET /api/system/diagnostics endpoint
- `artifacts/orivellum-ui/src/pages/system/index.tsx` — DiagnosticsCard component
