---
name: Daylight token system (WP2)
description: Premium Daylight default theme, design/tokens.json source of truth, ui-preferences sync contract, and the build/test gotchas that bit during WP2.
---

# Daylight token system

## Source of truth
- `design/tokens.json` (DTCG-style) is the palette authority: raw → semantic (per-mode via `$extensions["orivellum.modes"]`) → per-app `component.accent` AND `component.accent-ink`. `tests/test_design_tokens.py` enforces CSS parity against `gd-tokens.css`, WCAG contrast pairs, per-app ink-on-accent ≥4.5, and that each per-app `--gd-accent-soft` rgba is its own accent's RGB at ≤0.2 alpha.
- **Why:** the architect review caught hull per-app accent-ink colors living only in CSS — anything not in tokens.json + parity tests silently drifts.
- Daylight per-app rules must NOT override `--gd-accent-ink` (they inherit the theme block's white); hull per-app rules MUST set it. Tests encode this asymmetry.

## UI preferences sync contract
- `PUT /api/system/settings/ui-preferences` **merges** partial records; clients send only keys explicitly present in localStorage. `theme.ts` hydrates from GET on init (server fills keys this device never set; local explicit wins) and queues mirrors until hydration settles.
- **Why:** replace semantics + full-record PUTs meant one device's defaults clobbered another device's saved choices, and fresh installs never restored.
- E2E reruns: PUT `{}` no longer clears the record — reset by PUTting explicit defaults for all four keys.

## Build/test gotchas (hard-won)
- The dev-mode Replit JSX tagger breaks on generic JSX (`<Comp<T> ...>`) — vite 500s with a babel parse error while `tsc`/prod build stay green. Never use explicit type arguments in JSX; let inference work.
- Playwright: install `playwright-core` as a devDep of orivellum-ui and run scripts from inside that dir (module resolution); launch with `executablePath: CHROMIUM_BIN`. In dev, CSS arrives via JS imports — a transform 500 makes all `--gd-*` vars empty while the boot script (inline in index.html) still works, which looks like a "CSS-only" failure.
- `scripts/ui_baseline_metrics.py` counts hex literals in comments too (allowlist: index.css + gd-tokens.css only). test_design_tokens greps raw text for `fonts.googleapis` / the font-smoothing property name — never write those literals anywhere in UI source, even comments.
- @fontsource: import `latin-400`-style subset files, not the full css, to keep the CSS gzip gate happy.
- CSS gzip baseline was surgically re-baselined (27,019→28,977, note field in baseline/metrics.json) for spec-mandated font self-hosting; JS/hex/route ratchets stay frozen — never re-collect the whole baseline for a single-metric exception.
- `src/styles/legacy-aliases.css` maps VELLUM/shadcn vars onto `--gd-*` via `html:root` / `html:root.dark` (specificity 0-1-1 beats index.css `:root`). Temporary — WP3 deletes it.
