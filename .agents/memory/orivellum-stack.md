---
name: Orivellum stack decisions
description: Language, framework, DB, and architecture decisions for the Orivellum build.
---

## Stack
- **Backend**: Python 3.13 / FastAPI / SQLite (WAL mode, foreign keys, RLock)
- **Frontend**: React + Vite artifact at `/` (orivellum-ui)
- **API**: FastAPI served via `uv run python -m orivellum.api.main`, reads `$PORT`
- **Package**: `src/orivellum/` — installed as editable via `uv sync`
- **Codegen**: Orval 8.23, OpenAPI spec at `lib/api-spec/openapi.yaml`
- **DB file**: `data/orivellum.db` — 37 migrations applied on first clean boot

## Startup order (enforced in lifespan)
config → resolve DB path → open orivellum.db → run migrations → wire deps → start API

**Why:** Monarch had a bug where build_system() ran before DB opened, potentially creating legacy monarch.db. The new lifespan enforces correct order.

## Workflow commands
- API Server: `uv run python -m orivellum.api.main` (PORT=8080, PYTHONPATH=src)
- Frontend: `pnpm --filter @workspace/orivellum-ui run dev`
