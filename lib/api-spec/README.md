# Orivellum API Spec

This directory contains the OpenAPI 3.0 specification for the Orivellum API and the [Orval](https://orval.dev) configuration that generates typed client code from it.

## File layout

| Path | Purpose |
|---|---|
| `openapi.yaml` | Hand-maintained OpenAPI 3.0 spec covering all API routes |
| `orval.config.ts` | Orval configuration — two targets: React Query hooks and Zod schemas |
| `README.md` | This file |

## Regenerating the client

Run from **this directory** (`lib/api-spec/`):

```bash
pnpm exec orval --config orval.config.ts
```

This regenerates two packages:

| Package | Output path | Contents |
|---|---|---|
| `@workspace/api-client-react` | `lib/api-client-react/src/generated/` | React Query hooks (`useGetWork`, `useListConversations`, …) + `api.ts` fetcher functions |
| `@workspace/api-zod` | `lib/api-zod/src/generated/api.ts` | Zod v3 validators for every request/response schema |

Regeneration must be re-run whenever `openapi.yaml` changes and the updated output committed together with the spec change.

## Adding new routes

1. Add the path entry to `openapi.yaml` under `paths:` with a unique `operationId`.
2. Add any new request/response schemas under `components/schemas:`.
3. Run the regenerate command above.
4. Import and use the generated hook in the UI (e.g. `import { useGetMyNewRoute } from "@workspace/api-client-react"`).
5. Commit `openapi.yaml` and the regenerated files together.

## Route coverage

The spec covers **84 path entries** across the following tag groups:

- `health` — liveness check
- `dashboard` — summary, activity, nudges
- `works` — CRUD, documents, knowledge, tasks, graph, gaps, completeness, pipeline, brainstorm, learning, quiz, chapters, compass, duplicates, search, evidence
- `entities` — list + detail with mention counts
- `conversations` — CRUD, messages, web-search toggle, continue, search
- `library` — CRUD, search, duplicates, lifecycle, knowledge, versions, chapters, chunks, upload, download, reprocess, smart-organize
- `knowledge` — list, search, get, review, delete, ask, create
- `learning` — summary, concepts, questions, seed, assess, reset
- `review` — unified review queue and resolve
- `projects` — CRUD + concepts
- `studio` — TTS (text + document), image gen, OCR, outputs, status
- `system` — models, health, capabilities, nightshift, jobs, user-memory, settings, embeddings, stats, tools, hardware, LLM health, web-search status, governance, suggestions, briefing
- `actions` — list, runs, preview, execute
- `mcos` — benchmarks, runs, telemetry, regressions, prompts, RAG config/sweep
- `backups` — list, create, verify, download
- `files` — list, upload

## Notes

- Auto-generation from FastAPI's `/openapi.json` is planned but not yet wired into the pipeline.
  Until then, keep `openapi.yaml` in sync manually when routes are added.
- Orval is pinned to **v8.23** and always generates **Zod v3** syntax (`zodV4: false` in config).
  Do not upgrade Orval without also updating the zod target config.
- The `customFetch` mutator at `lib/api-client-react/src/custom-fetch.ts` injects the session
  cookie automatically — no manual auth setup needed in hook consumers.
