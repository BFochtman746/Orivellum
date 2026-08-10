---
name: Project Workbench
description: Agentic build/edit/repair system for Excel + code projects with immutable version history and hash-verified archives.
---

# Project Workbench

User's explicit product expectation (Aug 2026): Orivellum must build, edit, repair
and save FINISHED projects in Excel and coding — "like Tasklet for Excel, like
Replit Agent for coding" — with versioning until complete, then archive the
versions. Treat this as the north star for the Workbench; parse-level checks are
a floor, not the bar. Follow-ups queued: run runner's six proof gates on xlsx
versions; execute generated tests for code versions.

## Design rules (durable)
- **Claim before mutate:** every mutating op (build/iterate/revert/archive/delete)
  must atomically claim via `db.claim_wb_build()` (conditional UPDATE building 0→1);
  never read-then-check the building flag. `require_active=False` only for delete.
- **Files before row:** publish a version by staging dir + `Path.replace()` rename,
  inserting the wb_versions row between copy and rename (`_publish_version`);
  rename failure deletes the row. A verified row must always have its files.
- **Archive integrity gate:** archiving re-hashes every file on disk against
  files_json and refuses on any mismatch/missing — never a silently-wrong zip.
- **Build contract with the LLM:** one Python script, reads ./inputs/ (previous
  version), writes the COMPLETE new project state to ./out/ (not a diff).
  Runs in the Workshop sandbox (reuses `_SANDBOX_RUNNER`, `_sandbox_env`,
  `_sandbox_preexec`, `_clean_script` from workshop.py). Sandbox is an
  accident guard, not a hostile-code boundary (documented in module docstring).
- **Failed build = no version:** error lands on wb_projects.last_error; the last
  good version stays the truth. Revert copies old versions FORWARD as new
  versions (history append-only).
- UI badge says "Checks passed", not "Verified" — review flagged overstating.

## Where things live
- capability: src/orivellum/capabilities/workbench.py; routes: /api/workbench
  (routes/workbench.py, registered in api/app.py `_route_modules`)
- schema v115 (wb_projects, wb_versions); files at data/workbench/{pid}/v{n}/,
  archives at data/workbench/archives/
- UI: /workbench + /workbench/:projectId; Studio hub tile (Wrench icon)
