---
name: Forge Website Factory integration and design standards
description: Full-stack integration of Forge into Orivellum — DB tables, Python capabilities, FastAPI routes, React frontend — plus the design-standards layer (WCAG/perf/design gates, design brief, token constitution).
---

# Forge Website Factory — native Orivellum integration

## What was built
Replaced the standalone Node.js forge-factory with a Python-native capability module.

## DB schema v103–v106
- `forge_projects`: id, work_id (nullable FK), name, brief, status, build_dir, config JSON, timestamps
- `forge_jobs`: id, project_id, type (PLAN/DESIGN/BUILD/VERIFY/REPAIR), status, plan_job_id, design_job_id, target_job_id, build_dir
- `forge_events`: append-only event ledger; SSE streams from this table
- `forge_artifacts`: site-plan, visual-design, gate-report with SHA256 dedup (UNIQUE job_id+artifact_type)

## Python module: src/orivellum/capabilities/forge/
- `planner.py`: llm_call() → JSON site plan
- `visual.py`: llm_call() → 3 visual concepts (palette, typography, layout)
- `agent.py`: tool-calling build agent (write_file/read_file/list_files/run), MAX_ROUNDS=30
- `gates.py`: 6 quality gates: structure, tokens, html_valid, js_syntax, links, scope
- `pipeline.py`: phase runners; entry = `run_forge_job(db, cfg, project_id, job_id)`

## API: src/orivellum/api/routes/forge.py → /api/forge/*
- Jobs run via BackgroundTasks → _run_job_bg → run_forge_job
- SSE: GET /projects/:id/jobs/:id/events — polls forge_events, sends __done__ sentinel at terminal state
- Preview: GET /jobs/:id/preview/:path — FileResponse from build_dir (jailed)
- Approve/reject: POST .../approve | .../reject

## Frontend: artifacts/orivellum-ui/src/pages/forge/
- /forge: hub, project cards with status badges, new-project dialog
- /forge/:projectId: pipeline stepper, EventSource SSE log, concept picker, preview iframe

## Key decisions
- Build dirs: `data/forge-builds/{project_id}/{job_id}/` (no git worktree)
- All LLM via llm_call() → logged in llm_calls, visible in MCOS
- project.config_data (parsed config JSON) stores latest plan_job_id / design_job_id
- update_forge_project accepts config_update=dict to merge into config JSON
- Node.js forge-factory kept at artifacts/forge-factory/ for reference, not wired as a service

**Why:** User asked for full native integration so Forge uses Orivellum's LLM gateway, DB, and auth instead of running as a separate Node.js service with its own dashboard.

## Design-standards layer (Aug 2026 upgrade)
- `gates_design.py`: 4 extra gates appended to the 6 originals — a11y, performance, contrast, design_quality. **All conditional, never blocked** — the REPAIR loop consumes their findings; a blocked design gate would brick otherwise-working builds.
- a11y/perf checks use stdlib `html.parser` (`_PageScan`), not regexes — regexes false-flagged single-quoted/unquoted attributes, and per-control label association needs element-level data (one `<label for>` must not mask other unlabeled inputs; every below-fold img must be lazy, not just one).
- Contrast gate is **token-level only** (`--color-text` vs `--color-background` in design-tokens.css); 4/8-digit alpha hex is reported as unverifiable, never truncated to 6 digits. Say "token-level" in messaging — it is not a page-wide guarantee.
- Purple-gradient trope detector is **palette-aware advisory**: only flags gradients whose purple stops are NOT in design-tokens.css. A purple brand is legitimate; off-palette purple gradients are the stock AI look.
- Planner emits `design_brief` (non_negotiables/identity/primary_cta/inspiration) and `_enforce_plan_constraints()` programmatically caps sections at 6/page — prompt rules alone aren't reliable with small local models. `design_brief` must be included in visual.py's plan_summary or it never reaches concept generation.
- Concepts carry `rationale` (rendered in ConceptCard) + full token sets (7 palette roles, type scale, spacing/radius/shadow); DESIGN_SYSTEM demands 3 distinct layout archetypes and bans tropes.
- Tests: tests/test_forge_design_gates.py.
