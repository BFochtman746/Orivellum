---
name: Mobile parity & quality pass
description: What was added/fixed during the full mobile parity audit against the web app.
---

# Mobile parity & quality pass

## New tab pages (artifacts/mobile/app/(tabs)/)
- `projects.tsx` — Projects list with mastery rings, section groups (active/not-started/complete), pull-to-refresh, loading/error/empty states. Navigates to `/project/[id]`.
- `forge.tsx` — Forge hub with project cards, status badges, FAB + modal to create new project. Navigates to `/forge/[id]`.
- `mcos.tsx` — MCOS benchmarks with pass-rate circles, regression banner, per-benchmark and run-all trigger buttons.

## New stack pages (artifacts/mobile/app/)
- `project/[id].tsx` — Project detail with large mastery ring, filter chips (all/due/mastered), concept cards with animated mastery bars and level dots.
- `forge/[id].tsx` — Forge detail with pipeline stepper (Plan→Design→Build→Verify), approve/reject gate for awaiting_approval jobs, event log (polls every 5s), job history, start pipeline button.

## Fixes applied
- `graph.tsx` — Added useQuery preflight + loading skeleton + error state + empty state before rendering KnowledgeGraphView. Retry button in header on error.
- `memory.tsx` — Added back button (arrow-left Pressable) to the custom header; only shown on native (not web).
- `+not-found.tsx` — Full VELLUM redesign: icon box, serif title, body copy, "Go to Dashboard" CTA, "Go back" link. Uses useColors + fontSerif.
- `_layout.tsx` — Fixed governance notification deep-link (was pushing `/(tabs)`, now pushes `/governance`); added `SyncOverlay` component using `useColors()` instead of hardcoded dark colors; LoginScreen now uses `useColorScheme()` for light/dark adaptive colors.

## Navigation additions ((tabs)/_layout.tsx)
- Added to `NAV_ITEMS`: projects (compass), forge (globe), mcos (bar-chart-2)
- Added to `NativeAppLayout <Tabs>`: projects, forge, mcos
- Updated `useSectionLabel()` and `currentRoute()` for /projects, /forge, /mcos, /memory

## Stack registrations (_layout.tsx)
- `project/[id]` — transparent/blur header, back title "Projects"
- `forge/[id]` — transparent/blur header, back title "Forge"
- `memory` — headerShown: false (custom header with back button in the component itself)

**Why:** Ensures every major web feature is reachable from mobile, and all screens have proper navigation, loading, error, and empty states.
