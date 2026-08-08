---
name: Forge Website Factory integration
description: Full-stack integration of Forge into Orivellum — DB tables, Python capabilities, FastAPI routes, React frontend.
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
