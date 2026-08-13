---
name: Daylight token system
description: Premium Daylight default theme, design/tokens.json source of truth, ui-preferences sync contract, and UI toolchain gotchas.
---

# Daylight token system

## Source of truth
- `design/tokens.json` is the palette authority: raw → semantic (per-mode) → per-app accent AND accent-ink. Parity + contrast tests enforce it against the CSS.
- **Why:** a review caught hull per-app accent-ink colors living only in CSS — any color not in tokens.json + parity tests silently drifts and can regress contrast undetected.
- **How to apply:** when adding any themed color, add it to tokens.json first and extend the parity/contrast tests; per-app soft tints must be the app's own accent at low alpha. Daylight per-app rules inherit the theme-block white ink (must NOT override it); hull per-app rules must set their tinted ink.

## UI preferences sync contract
- The ui-preferences endpoint MERGES partial records inside one atomic DB transaction; clients send only keys the user explicitly set on that device, and hydrate from GET on init (server fills unset keys; local explicit wins; mirrors queue until hydration settles).
- **Why:** replace semantics + full-record PUTs let one device's defaults clobber another device's saved choices; a non-atomic read-merge-write lost keys under concurrent PUTs.
- **How to apply:** never PUT a full default-filled record; never read-then-write the settings JSON outside `db.atomic()`. Resetting the record means PUTting explicit defaults for all keys (empty PUT is a no-op).

## Toolchain gotchas
- The dev-mode JSX tagger breaks on generic JSX (`<Comp<T> ...>`) — vite 500s while tsc/prod build stay green; in dev, CSS arrives via JS imports, so a transform 500 leaves every `--gd-*` var empty while the inline index.html boot script still works (looks like a CSS-only failure). Avoid explicit type arguments in JSX.
- The UI baseline hex scanner counts hex literals inside comments too; the token test suite greps raw source for the Google Fonts hostname and the font-smoothing property name — never write those literals anywhere in UI source, even comments.
- @fontsource: import latin-subset files, not the full css, to keep the CSS gzip budget.
- Size-budget baselines may be surgically re-baselined for spec-mandated growth (with a note field documenting why); never re-collect the whole baseline for a single-metric exception — that loosens the frozen ratchets.
- The legacy alias sheet bridges unmigrated pages via `html:root` / `html:root.dark` selectors (specificity 0-1-1 beats `:root`); it is temporary and scheduled for deletion in the next work package.
