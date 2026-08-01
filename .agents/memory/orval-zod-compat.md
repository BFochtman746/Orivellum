---
name: Orval 8.23 Zod compatibility
description: Orval 8.23 generates Zod v4 syntax unconditionally; config options to avoid build failures.
---

## The problem
Orval 8.23 generates `z.looseObject()` and `z.int()` — Zod v4 APIs. If the catalog pins `zod@^3.x`, every typecheck after codegen fails with `Property 'looseObject' does not exist`.

There is NO `zodV4: false` config option in Orval 8.23 that works. The `generate.zodV4` path is not recognized.

## Fix
1. Pin `zod: ^4.0.0` in pnpm-workspace.yaml catalog.
2. Run `pnpm install` to pull Zod v4.

## Split-barrel TS2308 duplicate exports
In `mode: "split"`, Orval generates a barrel `generated/api.ts` that re-exports from per-operation files. When two operations share path-param shapes with the same derived name (e.g. `GetWorkKnowledgeParams` from multiple `/works/{workId}/...` operations), the barrel exports the name twice → `TS2308 already exported`.

**Fix**: Set `mode: "single"` for the zod target output. This puts all Zod schemas in one flat file with no barrel collisions.

## Correct orval.config.ts zod section
```ts
output: {
  workspace: apiZodSrc,
  client: "zod",
  target: "generated/api.ts",   // single file path, not a dir
  mode: "single",               // NOT "split"
  clean: true,
  ...
}
```

## lib/api-zod/src/index.ts
After switching to single mode, `generated/types/` no longer exists. Index must be:
```ts
export * from "./generated/api";
```
