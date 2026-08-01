---
name: OpenAPI codegen workflow
description: How to regenerate the TypeScript API client from the OpenAPI spec
---

## Run codegen
From `lib/api-spec/`:
```
pnpm exec orval --config orval.config.ts
```
Generates both react-query hooks and zod schemas. Spec lives at `lib/api-spec/openapi.yaml`.

## Why: mode "single" for zod target
Orval 8.23 always generates Zod v4 syntax; catalog must pin `zod@^4`. Use `mode:"single"` for the zod target to avoid split-barrel TS2308 duplicates.

## When to run
- After any new endpoint is added to the backend
- After any schema changes (new fields, new models)
- The `lib/api-client-react` package is what the frontend imports from

## Cast pattern for extra DB fields
When backend returns extra fields not in the OpenAPI spec (e.g. `conv_count` from list_works), cast in frontend:
```tsx
(work as any).conv_count
```
Until codegen is re-run after the spec is updated.

## Key generated hooks
- `useGetSystemHealth` — /api/health; supports refetchInterval for live AI status
- `useGetWorkStats` — /api/works/:id/stats; now returns `documents_by_readiness`
- `useListWorks`, `useGetWork` — both return `conv_count` (added to DB query)
