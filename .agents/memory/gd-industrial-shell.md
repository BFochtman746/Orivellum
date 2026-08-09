---
name: GD-industrial shell foundation
description: New web PWA design foundation — token layer, Home Screen launcher, app frame, legacy-shell transition rules.
---

# GD-industrial shell foundation

- Palette spec (LCH, contrast table, deuteranopia pass) lives at `artifacts/orivellum-ui/design/GD-INDUSTRIAL.md`; tokens in `src/styles/gd-tokens.css` on `<html data-theme="hull"|"daylight">`, per-app accent tint on `data-app`. No component using the GD layer writes a hex.
- Shell selection is route-driven: `src/lib/apps.ts` maps path prefixes → app; `Shell` in App.tsx wraps app-owned paths in `AppFrame`, everything else (and legacy mode) in the old `AppLayout`. `/` is the Home Screen launcher with no shell.
- **The old dashboard moved from `/` to `/dashboard`.** Every "Today"/brand/home link in the legacy shell must point to `/dashboard`, never `/` — `/` is the Home Screen and a `/` link ejects users out of legacy mode. **Why:** review caught rail/sheet Today links still on `/` after the move.
- **AppFrame's content host must mirror AppLayout's content surface exactly** (`overflow-auto + max-w-[1400px] + responsive padding`, inside an `@container` element) so unmigrated pages keep their scrolling contract. **Why:** a bare `overflow:hidden` host clipped long detail pages on deep links.
- Legacy escape hatch: localStorage `orivellum-legacy-shell` ("1" = old sidebar for all routes). Set by the Home Screen "Legacy console" row; cleared by tapping any app tile or the frame's Home button.
- Hard rules carried across themes: violet = machine-written text only; danger red on exactly one control per screen; ambient ribbon (color + width + text label) instead of toasts; 48px thumb targets; stencil face (Allerta Stencil) restricted to wordmark/app names.
