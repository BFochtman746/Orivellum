---
name: Mobile app improvements
description: Durable patterns, pitfalls, and decisions made while building the Orivellum Expo mobile app.
---

## URL construction

Direct fetch calls in mobile always use:
```js
const domain = process.env.EXPO_PUBLIC_DOMAIN;
const res = await fetch(`https://${domain}/api/...`);
```
Generated react-query hooks (from `@workspace/api-client-react`) handle this automatically.

## Missing generated hooks

Not every backend endpoint has a generated hook. When a hook does not exist:
- For simple GET: use `useQuery` + direct fetch (see `app/library/[id].tsx` knowledge fetch)
- Endpoints added manually to backend routes after the last `orval` codegen run will not appear in the client until `pnpm exec orval --config orval.config.ts` is run from `lib/api-spec/`

Known missing hooks (as of this session):
- `useGetDocumentKnowledge` — use direct fetch to `/api/library/{doc_id}/knowledge`

## Navigation patterns

- Tab routes: `router.push('/library/' + id)` navigates inside the tab stack
- Work detail → chat: `router.push('/chat/' + id as any)` on native; `router.push('/chat?id=' + id)` on web
- `(router as any)` cast needed when route doesn't match known TS types

## Component patterns

- `KnowledgeRow` in `work/[id].tsx` — `onReviewed` prop triggers refetch after approve/reject
- `WorkCard` in `works.tsx` — `onStartChat` prop; handler lives in `WorksScreen`, creates conversation then navigates
- `ServerDot` in `_layout.tsx` — small green/amber/red dot on home tab icon; polls system health every 15s

## Styles

- `newChatBtn` / `newChatBtnText` — green action button in conversations tab list header
- `chatBtn` / `chatBtnText` — small chat shortcut in WorkCard footer

## What NOT to do

- Do not call `isLiquidGlassAvailable` inside `NativeTabLayout` — it is only used as the top-level router switch
- Do not duplicate `useRouter()` inside the same component scope — each sub-component has its own hook call which is fine
