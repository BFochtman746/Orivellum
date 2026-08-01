# Orivellum

A sovereign, local-first knowledge management and AI assistant platform. All data and AI inference stay on your machine — nothing is sent to external cloud services.

---

## Run & Operate

| Command | What it does |
|---------|-------------|
| `uv run python -m orivellum.api.main` | Start the Python API server (default port 8080) |
| `PORT=5173 BASE_PATH=/ ORIVELLUM_API_URL=http://127.0.0.1:8080 pnpm --filter @workspace/orivellum-ui run dev` | Start the web frontend |
| `pnpm --filter @workspace/mobile run dev` | Start the Expo mobile dev server |
| `pnpm --filter @workspace/api-spec run codegen` | Regenerate React Query hooks + Zod schemas from OpenAPI spec |
| `pnpm --filter @workspace/api-spec run sync-client` | Regenerate hooks/schemas **and** rebuild `api-client-react` declarations — run this after any OpenAPI change |
| `pnpm run typecheck` | Full typecheck across all packages |

---

## Stack

- **Backend**: Python 3.13, FastAPI, SQLite (via uv), schema v39
- **Frontend**: React + Vite, Tailwind CSS, shadcn/ui, TanStack Query, wouter routing
- **Mobile**: Expo (React Native) with Expo Router
- **AI**: OpenAI-compatible local model server (Lemonade port 13305 by default; configurable)
- **Codegen**: Orval 8.23 → React Query hooks + Zod v4 schemas from `lib/api-spec/openapi.yaml`
- **pnpm workspace**: `artifacts/*`, `lib/*`, `lib/integrations/*`, `scripts`

---

## Where things live

| Area | Path |
|------|------|
| API routes | `src/orivellum/api/routes/` |
| Database (schema + migrations) | `src/orivellum/database/` |
| Config system | `src/orivellum/configuration/config.py` |
| Web pages | `artifacts/orivellum-ui/src/pages/` |
| OpenAPI spec | `lib/api-spec/openapi.yaml` |
| Generated API client | `lib/api-client-react/` |
| Mobile screens | `artifacts/mobile/app/` |
| Data + SQLite DB | `data/` (created on first run) |

---

## Web routes

| Route | Page |
|-------|------|
| `/` | Dashboard |
| `/works` | Works index |
| `/works/:workId` | Work detail (docs, knowledge, tasks, conversations) |
| `/chat` | Conversations / AI chat |
| `/library` | Document library |
| `/library/:docId` | Document detail (knowledge, chunks, overview) |
| `/files` | Studio file outputs |
| `/projects` | Learning projects |
| `/projects/:projectId` | Project detail |
| `/studio` | AI Studio (image gen, OCR) |
| `/backups` | Backup management |
| `/system` | System settings and AI status |

---

## API surface (Python FastAPI)

| Router | Prefix | Key endpoints |
|--------|--------|---------------|
| health | `/api` | `GET /healthz`, `/version`, `/diagnostics`, `/configuration/effective` |
| system | `/api/system` | Models, tools, capabilities, AI extraction toggle, briefing |
| library | `/api/library` | Import, list, search, reprocess, PATCH, DELETE, knowledge & chunks |
| works | `/api/works` | CRUD, documents, knowledge, tasks, conversations, stats |
| knowledge | `/api/knowledge` | List, search, review (approve/reject), delete |
| conversations | `/api/conversations` | CRUD, stream messages via SSE |
| projects | `/api/projects` | CRUD, concepts |
| dashboard | `/api/dashboard` | Summary, activity |
| files | `/api/files` | Upload, list, download |
| backups | `/api/backups` | Create, list, verify |
| studio | `/api/studio` | Image generation, OCR, voices, outputs |

---

## Architecture decisions

- **SQLite only** — sovereign local-first; no Postgres, no external DB. Schema auto-migrates on startup (v39).
- **SSE streaming** — chat replies stream token-by-token via Server-Sent Events; message is saved before the AI call so a network drop never loses user input.
- **AI harvest pipeline** — rule-based extraction runs first (`harvest()`); LLM extraction (`llm_harvest()`) runs after doc is `ready`, gated by the `ai_extraction_enabled` setting; LLM items get `review_status = "auto"` and show a violet Sparkles badge.
- **Orval 8.23 + Zod v4** — always generates Zod v4 syntax; `zod@^4` is pinned in catalog; `mode:"single"` avoids split-barrel TS2308 errors.
- **Works → objects join** — `works` table has no own timestamp; `obj_created` comes from joining `objects` on `works.id = objects.id`.
- **Config priority**: env vars (`ORIVELLUM_*`) > `config.yaml` > built-in defaults.

---

## System prerequisites (for self-hosted)

```
Python ≥ 3.12    Node.js ≥ 20    pnpm ≥ 9    uv ≥ 0.4

# macOS
brew install tesseract poppler

# Ubuntu/Debian
sudo apt-get install -y tesseract-ocr poppler-utils
```

---

## Gotchas

- Run `uv sync` before `pnpm install`; the Python package must exist before codegen runs.
- After any OpenAPI spec change, run `pnpm --filter @workspace/api-spec run sync-client` (not just `codegen`) — this rebuilds both the generated hooks/schemas and the `api-client-react` declarations so TypeScript stays in sync.
- The Vite dev server requires `PORT`, `BASE_PATH`, and `ORIVELLUM_API_URL` — without `ORIVELLUM_API_URL` the `/api` proxy is disabled and all API calls 404.
- Tesseract and Poppler must be installed at the OS level — `uv sync` cannot install them.
- `works` timestamps require joining through the `objects` table — do not query `works` for `created_at` directly.
- Any mutation that changes doc/knowledge/task/conversation count must invalidate `getGetWorkStatsQueryKey(workId)`.

---

## User preferences

*Populate as you build — explicit user instructions worth remembering across sessions.*
