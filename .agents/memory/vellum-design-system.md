---
name: VELLUM design system implementation
description: Design tokens, glass effects, typography, and component patterns applied to Orivellum web UI
---

## The Design Decision
Applied the VELLUM design system (from the user's reference HTML + MD spec) to all Orivellum web UI surfaces. Concept: "warm paper; glass surfaces are frosted vellum; type is set like a fine printed book."

## Active fonts
- `--app-font-sans`: 'Bricolage Grotesque' (UI labels, body, buttons)
- `--app-font-serif`: 'Fraunces' with opsz axis (titles, card headings, AI voice)
- `--app-font-mono`: 'Space Mono' (eyebrows, data, tier badges, telemetry)
All three are loaded via Google Fonts import in index.css line 1.

## Color tokens (added as raw CSS vars on :root)
Light:  --paper #F4EEE1, --gilt #9A7B2E, --rust #B2431E, --green-raw #274633, --green-2 #3C6A4B
Dark:   --gilt #C9A25A, --rust #D46A43, --green-raw #6FA982
Also: --gilt-line, --gilt-soft, --rust-soft, --green-soft, --vellum, --vellum-strong, --vellum-hi, --line, --line-2, --shadow-1, --shadow-2, --blur (16px).
Tailwind HSL foreground shifted from cool 220° hue to warm 36° sepia to match VELLUM's ink.
--radius changed from 0.25rem → 0.875rem (14px).

## Glass utilities (index.css appended section)
- `.glass-vellum`  — standard nav/toolbar glass (blur 16px, saturate 125%, --vellum tint)
- `.glass-sheet`   — elevated sheets/drawers (blur 22px, saturate 130%, --vellum-strong)
- `.glass-card`    — subtle card glass (blur 8px, saturate 110%)
- `.glass-lens::before` — gilt shimmer strip at top of glass surface
All degrade gracefully: @supports test + @media(prefers-reduced-transparency) fallback.

## Editorial utilities
- `.eyebrow` — Space Mono, 10px, 0.26em tracking, uppercase, gilt color
- `.gilt-rule` — 1px linear-gradient(90deg, var(--gilt-line), transparent)
- `.drop-cap::first-letter` — Fraunces 3em, rust color, opsz 144, floated left
- `.epigraph` — Fraunces italic, 14.5px, gilt-line left border
- `.vellum-h1` — Fraunces 400, clamp(28-36px), opsz 120, forest green
- `.vellum-card` / `.vellum-row` — interactive card and list-row patterns with spring transitions
- `.section-label-mono` — Space Mono section labels
- `.status-dot-pulse` — animated green connectivity dot
- `.ai-caret` — gilt streaming cursor animation
- `.brand-orivellum` / `.brand-accent` — sidebar brand name styling

## Tier badges
`.tier.tier-canon/source/artifact/conv/claim` CSS classes + Badge component variants.
Space Mono, 9.5px, 0.12em tracking, uppercase. Color-coded by VELLUM tier system.

## Component changes
- card.tsx: rounded-[16px], warm shadow (--shadow-1/2), gilt hover border, spring-like tap
- button.tsx: default=forest-green solid; outline=gilt ghost; destructive=rust; active:scale-[0.97]
- badge.tsx: added canon/source/artifact/conv/claim/gilt tier variants

## Page header pattern (apply to every new page)
```tsx
<span className="eyebrow mb-1">Subtitle eyebrow</span>
<h1 className="vellum-h1">Page Title</h1>
<div className="gilt-rule w-36" />
<p style={{ color: 'var(--ink-soft)', fontSize: '13px' }}>Description</p>
```

## Grain texture
body::before SVG fractalNoise grain overlay; mix-blend-mode:multiply light / screen dark; opacity 0.35/0.25; disabled by prefers-reduced-transparency.

## Pages updated so far
Login, Library, Works index, Works detail, Learn, System/Engine, Governance, sidebar brand (desktop + mobile drawer), mobile header (glass-vellum).

**Why:** User asked to apply their VELLUM design system reference + master iOS glass. This is the durable visual language for all future Orivellum UI work — always use the page header pattern and glass utilities on new screens.
