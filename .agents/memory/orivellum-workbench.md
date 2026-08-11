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

## Portfolio layer (Aug 2026)
- Projects carry a `meta` JSON blob (needs assessment + close-out record); the
  raw blob must never leak through the API — routes expose it structured via
  a rundown endpoint and strip it from project payloads.
- Status lifecycle: active | shelved (put away, read-only, reactivatable) |
  archived (= completed, permanent). Reactivate is the only mutation allowed
  on shelved (plus delete); completing runs archive FIRST, then close-out —
  only a project that actually archived gets lessons written.
- Health score is deterministic-only (verdicts, findings, last_error,
  staleness) so it's safe to compute on every list request; no LLM.
- Close-out lessons become knowledge items (kind 'lesson', review_status
  'ai_auto', work_id=None); an offline model must NEVER block completion —
  the close-out then records the deterministic stats summary only.
- Any sync LLM route that writes project state must hold the build claim for
  the whole model call (claim → llm → write → release in finally); checking
  status up front is not enough — the 90 s call is a race window.
- All untrusted project text going into prompts is JSON-encoded as one block;
  hand-rolled delimiters (<<< >>>) are forgeable by content and were rejected
  in review. Model JSON is validated per-field with isinstance (non-list
  lists, non-string strings → clean 503, nothing stored).

## Where things live
- capability: src/orivellum/capabilities/workbench.py; routes: /api/workbench
  (routes/workbench.py, registered in api/app.py `_route_modules`)
- schema v115 (wb_projects, wb_versions); files at data/workbench/{pid}/v{n}/,
  archives at data/workbench/archives/
- UI: /workbench + /workbench/:projectId; Studio hub tile (Wrench icon)

## Six-gate proving (workbench_proof.py)
- Workbench xlsx builds run the Orivellum Runner's six gates in-process (engine+surgery loaded by absolute file path under private module names — NEVER sys.path insertion; an unrelated `runner` package could shadow it). `formulas` is a main-env dep, so no subprocess needed.
- Honesty rule for non-promoted proofs: gates that pass only after cache/order repairs certify the CANDIDATE, not the verbatim file → verdict `provable`, never `proven`. Imports stay verbatim (promote=False); only builds promote. **Why:** architect review caught imports being archived as "proven" when the archived bytes were never recalculated.
- Promotion must happen BEFORE `_snapshot()` hashes files, or version rows disagree with disk.
- Proof travels by bytes: a latest version without its own proof (analysis/revert copies) inherits an earlier proof only when the xlsx name+sha256 sets are identical.
- Archive gate: `latest_proof_status` + `UnprovenError` → route 409 `{code:"unproven"}`; UI force-confirm retries with `{force:true}`.

## Code project self-tests (build loop)
- Every code build (any language) must pass a generated test suite before a version publishes; non-Python files get file-verifying Python tests. There is deliberately no untested path to a good verdict.
- Never certify a pass from exit codes or printed output — generated suites can be no-ops, print fake "Ran 1 test / OK" text, or tamper with the harness in-process. Certification must come from a trusted process that never executes test/project code: static AST screen + separate untrusted runner + token only the trusted side holds.
- Run tests against an isolated copy of the output so a mutating test can never certify different bytes than the published ones.
