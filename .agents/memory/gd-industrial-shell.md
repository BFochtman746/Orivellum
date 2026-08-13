---
name: GD-industrial shell foundation
description: Web PWA design foundation — GD token layer, WP1 responsive shell (five destinations), legacy-shell transition rules.
---

# GD-industrial shell foundation

- Palette spec (LCH, contrast table, deuteranopia pass) lives at `artifacts/orivellum-ui/design/GD-INDUSTRIAL.md`; tokens in `src/styles/gd-tokens.css` on `<html data-theme="hull"|"daylight">`, per-app accent tint on `data-app`. No component using the GD layer writes a hex.
- WP1 (Aug 2026) replaced the eight-app launcher/`AppFrame` with ONE `ResponsiveShell` (`src/components/shell/`) wrapping EVERY route incl. `/` and NotFound. Registry is `src/lib/destinations.ts` (Home/Chat/Works/Library + MORE_GROUPS); `apps.ts` and `app-frame.tsx` are deleted (`connState` now exports from responsive-shell). Mobile <768 = bottom tab bar; 768+ = rail. `--shell-tabbar-h` on :root offsets fixed overlays (Read Aloud dock) above the tab bar. Verification: `artifacts/orivellum-ui/scripts/verify-wp1.mjs` (shell presence, 320px no-h-scroll, deep links, back button).
- **The old dashboard moved from `/` to `/dashboard`.** Every "Today"/brand/home link in the legacy shell must point to `/dashboard`, never `/` — `/` is the Home Screen and a `/` link ejects users out of legacy mode. **Why:** review caught rail/sheet Today links still on `/` after the move.
- **The shell's content host must mirror AppLayout's content surface exactly** (`overflow-auto + max-w-[1400px] + responsive padding`, inside an `@container` element) so unmigrated pages keep their scrolling contract. **Why:** a bare `overflow:hidden` host clipped long detail pages on deep links.
- Legacy escape hatch: localStorage `orivellum-legacy-shell` ("1" = old sidebar for all routes). Set by the Home Screen "Legacy console" row; cleared by tapping any app tile or the frame's Home button.
- Hard rules carried across themes: violet = machine-written text only; danger red on exactly one control per screen; ambient ribbon (color + width + text label) instead of toasts; 48px thumb targets; stencil face (Allerta Stencil) restricted to wordmark/app names.
