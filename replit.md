# Orivellum

A sovereign, local-first knowledge management and AI assistant platform. All data and AI inference stay on your machine — nothing is sent to external cloud services.

---

## Run & Operate

| Command | What it does |
|---------|-------------|
| `uv run python -m orivellum.api.main` | Start the Python API server (default port 8080) |
| `PORT=5173 BASE_PATH=/ ORIVELLUM_API_URL=http://127.0.0.1:8080 pnpm --filter @workspace/orivellum-ui run dev` | Start the web frontend |
| `pnpm --filter @workspace/api-spec run codegen` | Regenerate React Query hooks + Zod schemas from OpenAPI spec |
| `pnpm --filter @workspace/api-spec run sync-client` | Regenerate hooks/schemas **and** rebuild `api-client-react` declarations — run this after any OpenAPI change |
| `pnpm run typecheck` | Full typecheck across all packages |
| `uv run python scripts/run_diagnostics.py` | Full system health check (add `--vacuum` to defrag the DB) |

---

## Stack

- **Backend**: Python 3.13, FastAPI, SQLite (via uv), schema v106
- **Frontend**: React + Vite, Tailwind CSS, shadcn/ui, TanStack Query, wouter routing
- **AI**: OpenAI-compatible local model server (Lemonade port 13305 by default; configurable)
- **Codegen**: Orval 8.23 → React Query hooks + Zod v4 schemas from `lib/api-spec/openapi.yaml`
- **pnpm workspace**: `artifacts/*`, `lib/*`, `lib/integrations/*`, `scripts`

---

## Where things live

| Area | Path |
|------|------|
| API routes | `src/orivellum/api/routes/` |
| Database (schema + migrations) | `src/orivellum/database/` |
| Capability modules | `src/orivellum/capabilities/` |
| Config system | `src/orivellum/configuration/config.py` |
| Web pages | `artifacts/orivellum-ui/src/pages/` |
| OpenAPI spec | `lib/api-spec/openapi.yaml` |
| Generated API client | `lib/api-client-react/` |
| Data + SQLite DB | `data/` (created on first run) |
| Forge build output | `data/forge-builds/{project_id}/{job_id}/` |

---

## Web routes

| Route | Page |
|-------|------|
| `/` | Home Screen (app launcher) |
| `/works` | Works index |
| `/works/:workId` | Work detail (docs, knowledge, tasks, conversations, pipeline) |
| `/works/:workId/intelligence` | MONARCH book intelligence view |
| `/chat` | Conversations / AI chat |
| `/library` | Document library |
| `/library/:docId` | Document detail (knowledge, chunks, overview) |
| `/books` | Book pipeline overview |
| `/finishing` | Document finishing / export |
| `/write` | Writing Desk (AI document workshop) |
| `/intake` | Intake pipeline |
| `/learn` | Learning loop |
| `/projects` | Learning projects |
| `/projects/:projectId` | Project detail (mastery, concepts) |
| `/topics` | Topics index |
| `/actions` | Actions queue |
| `/graph` | Knowledge graph explorer |
| `/forge` | Forge Website Factory hub |
| `/forge/:projectId` | Forge project detail (pipeline stepper, preview) |
| `/studio` | AI Studio (image gen, OCR, TTS) |
| `/review` | Unified review inbox |
| `/mcos` | MCOS calibration benchmarks |
| `/governance` | Prompt governance & PKLOS |
| `/backups` | Backup management |
| `/system` | System settings, AI status, diagnostics |

---

## API surface (Python FastAPI)

| Router | Prefix | Purpose |
|--------|--------|---------|
| health | `/api` | `GET /healthz`, `/version`, `/diagnostics`, `/configuration/effective` |
| system | `/api/system` | Models, tools, capabilities, AI extraction toggle, embeddings probe, briefing |
| library | `/api/library` | Import, list, search, reprocess, reprocess-all, PATCH, DELETE, knowledge & chunks, duplicates |
| works | `/api/works` | CRUD, documents, knowledge, tasks, conversations, stats, gaps, pipeline |
| genesis | `/api/works` | Book origination ledger (10-gate pipeline), gate recording, chain verification |
| knowledge | `/api/knowledge` | List, search, review (approve/reject), delete |
| conversations | `/api/conversations` | CRUD, stream messages via SSE |
| learning | `/api` | Work concepts, Q&A sessions, spaced repetition, analytics |
| projects | `/api` | Learning projects CRUD, concepts |
| topics | `/api` | Topics index |
| actions | `/api/actions` | Actions queue |
| review | `/api` | Unified review inbox — knowledge, reclassify, suggestions, duplicates |
| forge | `/api/forge` | Project CRUD, job start/approve/reject, SSE event stream, artifact retrieval, build file preview |
| write | `/api/write` | Writing Desk documents (AI-assisted gen, outline, export) |
| finishing | `/api/finishing` | Document finishing pipeline |
| intake | `/api` | Intake pipeline |
| mcos | `/api/mcos` | Benchmarks, runs, telemetry, regressions |
| pklos | `/api/pklos` | PKLOS authority/claims layer |
| claims | `/api/claims` | Claims CRUD |
| dashboard | `/api` | Summary, activity, task counts |
| studio | `/api/studio` | Image generation, OCR, TTS voices, outputs |
| files | `/api` | Upload, list, download |
| backups | `/api/backups` | Create, list, verify |
| graph | — | Knowledge graph topology |
| mcp | — | MCP server endpoints |

---

## Architecture decisions

- **SQLite only** — sovereign local-first; no Postgres, no external DB. Schema auto-migrates on startup (currently v106).
- **SSE streaming** — chat replies and Forge events stream token-by-token via Server-Sent Events; user messages are saved before the AI call so a network drop never loses input.
- **AI harvest pipeline** — rule-based extraction runs first (`harvest()`); LLM extraction (`llm_harvest()`) runs after doc is `ready`, gated by the `ai_extraction_enabled` setting; LLM items get `review_status = "auto"` and show a violet Sparkles badge.
- **All LLM calls via `llm_call()`** — every model call goes through the gateway in `capabilities/llm.py`, which logs to `llm_calls` table and surfaces in MCOS governance.
- **Forge Website Factory** — native Python capability (`capabilities/forge/`); PLAN → DESIGN → BUILD → VERIFY → REPAIR pipeline; build dirs under `data/forge-builds/`; approval-gated on PLAN and DESIGN; knowledge-driven planning when a Work is linked.
- **MONARCH book intelligence** — chapters, completeness, gap detection, dedup, governance, versioning, graph topology; pipeline advances via declarative state machine (`state_machine.py`); accessible at `/works/:id/intelligence`.
- **Nightshift daemon** — 14-pass nightly maintenance runner: vector orphan cleanup, VACUUM, sequential recovery, version-suggestion pass, and more. All passes run under `db._lock`.
- **PKLOS** — authority/verifier/resolver/enforcer/output-validator stack; `USER_ASSERTED`/`RETRIEVED` status names; `DERIVED` before `USER_DECLARED` in router.
- **Orval 8.23 + Zod v4** — always generates Zod v4 syntax; `zod@^4` is pinned in catalog; `mode:"single"` avoids split-barrel TS2308 errors.
- **Works → objects join** — `works` table has no own timestamp; `obj_created` comes from joining `objects` on `works.id = objects.id`.
- **Config priority**: env vars (`ORIVELLUM_*`) > `config.yaml` > built-in defaults.
- **VELLUM design system** — glass utilities, editorial typography, tier badges, page-header pattern; follow the pattern for all new pages.

---

## System prerequisites (for self-hosted)

```
Python ≥ 3.12    Node.js ≥ 20    pnpm ≥ 9    uv ≥ 0.4

# macOS
brew install tesseract poppler ffmpeg espeak-ng

# Ubuntu/Debian
sudo apt-get install -y tesseract-ocr poppler-utils ffmpeg espeak-ng
```

---

## Gotchas

- Run `uv sync` before `pnpm install`; the Python package must exist before codegen runs.
- After any OpenAPI spec change, run `pnpm --filter @workspace/api-spec run sync-client` (not just `codegen`) — this rebuilds both the generated hooks/schemas and the `api-client-react` declarations so TypeScript stays in sync.
- The Vite dev server requires `PORT`, `BASE_PATH`, and `ORIVELLUM_API_URL` — without `ORIVELLUM_API_URL` the `/api` proxy is disabled and all API calls 404.
- Tesseract, Poppler, FFmpeg, and espeak-ng must be installed at the OS level — `uv sync` cannot install them.
- `works` timestamps require joining through the `objects` table — do not query `works` for `created_at` directly.
- Any mutation that changes doc/knowledge/task/conversation count must invalidate `getGetWorkStatsQueryKey(workId)`.
- Forge build files are served from `data/forge-builds/` via `/api/forge/projects/:id/jobs/:id/preview/:path` — the route is jailed to the job's directory.
- The CORS origin regex must be exact domain (not wildcard `*.replit.dev`) and covers Tailscale `100.64–127.x.x` ranges.

---

## User preferences

*Populate as you build — explicit user instructions worth remembering across sessions.*
