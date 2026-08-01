---
name: Orivellum polling patterns
description: Where and how auto-refresh / refetchInterval is used throughout the app
---

## Rule: poll while processing, stop when done
All document-related queries use a conditional refetchInterval:
```ts
refetchInterval: (query) => {
  const docs = (query.state.data as any)?.documents ?? [];
  return docs.some((d: any) => d.readiness === 'imported') ? 4_000 : false;
}
```

## Current polling setup (web)
| Location | Interval | Condition |
|---|---|---|
| chat/index.tsx — AI health | 15s | always |
| system/index.tsx — AI health | 10s | always |
| dashboard.tsx — summary, activity, conversations | 30s | always |
| works/index.tsx — works list | 30s | always |
| works/detail.tsx — docs tab | 4s | while any doc is "imported" |
| works/detail.tsx — stats | 4s | while docs_by_readiness.imported > 0 |
| library/detail.tsx — document | 3s | while readiness === "imported" |
| library/index.tsx — library list | varies | conditional on processing |

## Current polling setup (mobile)
| Location | Interval |
|---|---|
| conversations screen | 15s |
| works list | 30s |
| library list | 30s |
| home dashboard (summary, activity) | 30s |

**Why:** Without polling, users see stale extraction state until they manually refresh.
Documents in "imported" state are mid-pipeline; polling stops automatically once they reach a terminal state (ready/error/no_text).
