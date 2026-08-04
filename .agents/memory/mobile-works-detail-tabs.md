---
name: Mobile Works Detail Tabs
description: All tabs in work/[id].tsx and key per-tab implementation notes
---

## Tab list (in order)
overview | docs | knowledge | tasks | conversations | gaps | learn | book | brainstorm

## Brainstorm tab (#293)
- Key: `'brainstorm'`, label: `'Ideas'`
- API: `POST /api/works/{workId}/brainstorm` (start), `GET` (history), `POST .../ideas/{id}/approve` (promote to knowledge)
- Component: `BrainstormTab` + `IdeaCard` — both defined inline in work/[id].tsx before GapsTab
- Pareto-front ideas shown first with Feather "zap" icon; others collapsible

## Book health card in OverviewTab (#205)
- Fetches `GET /api/works/{workId}/book-intelligence` on mount (non-blocking — card hidden if fetch fails)
- Shows: completeness.overall %, knowledge reviewed bar, gaps count, next_action
- Lives above the "Start Discussion" CTA in OverviewTab

**Why:** Non-critical data should never block the overview from rendering; fetch-then-show pattern keeps UX fast.
