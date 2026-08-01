---
name: Orivellum codegen workflow
description: How to regenerate the TypeScript API client and Zod schemas from the OpenAPI spec.
---

## Command
```
cd lib/api-spec && pnpm exec orval --config orval.config.ts
```

## What it generates
- `lib/api-client-react/src/generated/api.ts` — React Query hooks + fetch functions
- `lib/api-zod/src/generated/api.ts` — Zod v3 schemas (mode: single to avoid TS2308)

## When to run
After any change to `lib/api-spec/openapi.yaml` — new endpoints, new schema fields, new request/response shapes. Always run before writing frontend code that uses new backend fields.

**Why:** The generated client is a static snapshot; it does not auto-update when the FastAPI routes change. Stale types cause silent `as any` workarounds that accumulate.

## Zod note
Config pins `zodV4: false` — the project uses Zod v3 (`zod@^3.25.x` in the workspace catalog). Do not upgrade to v4 without also updating the orval config.
