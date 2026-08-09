---
name: GD app-hub pattern
description: How the five GD apps (Writing, Learning, Chat, Studio, Command) structure hubs and flip interiors to the dark token set.
---

# GD app-hub pattern

**The rule:** each GD app gets an entry hub built from GD primitives (`gd-tile` grid for primary destinations, `gd-row` list for secondary actions/sections, `gd-eyebrow` labels, `gd-panel` dashed empty states), and interior pages are cheaply reskinned by flipping to the existing `.dark` shadcn token set rather than restyling card-by-card.

**Why:** restyling multi-thousand-line settings/tool pages per app is not affordable; the dark token flip reads as "inside the app" instantly and is portal-safe.

**How to apply:**
- Use the shared hook `useGdDark()` (src/lib/useGdDark.ts): adds `dark` to `document.documentElement` while mounted (so portals — Select popovers, Sheets, toasts — inherit), cleans up on unmount, no-op in legacy shell. Also add `dark text-foreground` to the page root for flash-free first paint. Cover early returns (loading/not-found) too.
- Per-app accents ride `html[data-app='…']` set by AppFrame; no per-page work needed.
- Deep-linkable tools inside one page: drive selection from `?tool=`/`?id=` query params. CRITICAL: wouter's `useLocation` snapshot is pathname-only — query-string-only navigation does NOT re-render; use `useSearch()` from wouter v3 to react to `?param` changes on the same path.
- Ambient status tiles on hubs reuse existing endpoints with 30s `refetchInterval`; connectivity comes from the shared `useConnectivity()` hook only.
