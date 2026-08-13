# WP3 Migration Playbook — moving a screen onto the design system

Every migrated screen composes the WP2 token contract. Read this whole file
before touching a page. Deviations fail the automated cleanup gate.

## The contract — allowed styling vocabulary

A migrated page file may ONLY use:

1. **Shared primitives** from `@/components/primitives`:
   `Page`, `Section`, `Panel`, `ListRow`, `Field`, `ActionBar`, `Status`,
   `EmptyState`, `ErrorState`, `LoadingState`, `FilterSheet`, `ConfirmAction`.
   Read `src/components/primitives/*.tsx` to see props before using them.
2. **shadcn components** from `@/components/ui/*` (Button, Badge, Input,
   Select, Sheet, Dialog, Tabs, Skeleton, ScrollArea, …).
3. **Tailwind semantic classes**: `bg-background`, `bg-card`, `bg-muted`,
   `bg-sidebar`, `text-foreground`, `text-muted-foreground`, `text-primary`,
   `text-destructive`, `border-border`, `border-card-border`, `ring-ring`,
   `bg-primary text-primary-foreground`, `bg-secondary`, `bg-accent`, etc.
   Plus layout/spacing/typography utilities as usual.
4. **`--gd-*` tokens** via `var(--gd-…)` in inline styles or arbitrary
   classes when a semantic accent is needed:
   `--gd-accent`, `--gd-accent-ink`, `--gd-accent-soft`, `--gd-success`,
   `--gd-caution`, `--gd-danger`, `--gd-info`, `--gd-violet` (machine-written
   text ONLY), `--gd-bronze`, `--gd-olive`, `--gd-sonar`, `--gd-slate`,
   `--gd-*-soft`, `--gd-dim`, `--gd-shadow`, spacing/radius/motion tokens.
5. **`.gd-*` utility classes** from `styles/gd-tokens.css`:
   `gd-tile`, `gd-row`, `gd-panel`, `gd-chip`, `gd-iconbtn`, `gd-eyebrow`.
6. **Typography utilities**: `.eyebrow`, `.section-label-mono`, `.page-h1`
   (renamed from `.vellum-h1` in the foundation pass).

## FORBIDDEN in migrated files (the gate greps for these)

- `useGdDark` — delete the import and every conditional built on it. The
  resolved theme flips ALL tokens; a page never branches on theme. If a page
  truly needs the resolved theme (rare: canvas drawing, chart libs), use
  `useThemePreference().resolved` from `@/lib/theme` and read token values
  via `getComputedStyle`.
- Raw Vellum variables: `--paper`, `--paper-2`, `--card-raw`, `--ink-raw`,
  `--ink-soft`, `--ink-faint`, `--green-raw`, `--green-2`, `--green-soft`,
  `--gilt`, `--gilt-2`, `--gilt-soft`, `--gilt-line`, `--rust`, `--rust-soft`,
  `--line`, `--line-2`, `--vellum`, `--vellum-strong`, `--vellum-hi`,
  `--shadow-1`, `--shadow-2`, `--t-canon`, `--t-source`, `--t-artifact`,
  `--t-conv`, `--t-claim`, `--page-bg`, `--blur`.
- Legacy classes: `vellum-card`, `vellum-row`, `vellum-chip`, `vellum-seg`,
  `vellum-seg-btn`, `vellum-pagehead`, `vellum-h1`, `glass-vellum`,
  `glass-sheet`, `glass-card`, `glass-lens`, `grain`.
- **Hex color literals** — anywhere in the file, INCLUDING COMMENTS (the
  baseline scanner counts comments). Token vars only.
- Forcing appearance: never toggle `.dark`, `data-theme`, or `color-scheme`.
- The literal strings `fonts.googleapis` and `-webkit-font-smoothing`
  (token tests grep raw source — do not write them even in comments).
- Explicit JSX generic type arguments (`<Comp<T> …>`) — the dev-mode JSX
  tagger 500s on them. Let inference work.

## Translation table (typical replacements)

| Legacy                          | Replacement                                        |
|---------------------------------|----------------------------------------------------|
| `vellum-card` wrapper           | `<Panel>` or `rounded-lg border border-card-border bg-card` |
| `vellum-row`                    | `<ListRow>`                                        |
| `vellum-chip` / `vellum-seg`    | `gd-chip` with `data-active`, or shadcn Tabs      |
| `vellum-h1` / page header       | `<Page title eyebrow actions>`                    |
| `var(--ink-raw)`                | `text-foreground`                                  |
| `var(--ink-soft)`               | `text-muted-foreground`                            |
| `var(--ink-faint)`              | `var(--gd-dim)` (metadata only)                    |
| `var(--green-raw|--green-2)`    | `text-primary` / `var(--gd-primary)`               |
| `var(--gilt*)`                  | `var(--gd-bronze)` / `var(--gd-bronze-soft)`       |
| `var(--rust*)`                  | `text-destructive` / `var(--gd-danger[-soft])`     |
| `var(--line)` / `var(--line-2)` | `border-border` / `var(--gd-line-control)`         |
| `var(--shadow-1|-2)`            | `shadow-sm` or `var(--gd-shadow)`                  |
| glass surfaces                  | `bg-card` or `var(--gd-glass)` (nav layers only)   |
| dark-mode ternaries on gdDark   | delete — tokens already flip                       |

## Required states — every screen finishes all six

1. **Loading** — `<LoadingState>` (or Skeletons matching final layout).
2. **Empty** — `<EmptyState icon title description action>`; never bare text.
3. **Populated** — the normal render.
4. **Long/overflowing** — long titles `truncate`/`line-clamp-*`; lists never
   push actions off-screen; content reflows at 320px width (no fixed widths
   wider than the viewport, wrap chips/toolbars).
5. **Recoverable error** — `<ErrorState>` with a Retry that refetches; never
   a silent blank.
6. **Offline/queued** — reads: show cached data + rely on the shell ribbon;
   pages with mutations must disable/queue sends and label the state (see
   chat's outbox pattern for queued sends).

## Interaction rules

- Every tap target ≥44px (`min-h-11` on buttons/rows; `chat-icon-btn` style
  patterns for icon buttons).
- State is never color-alone — pair icon/label (`<Status>` dual-codes).
- `--gd-danger` on at most ONE control per screen.
- Destructive actions go through `<ConfirmAction>`.
- Keep `data-testid` attributes that exist; add them for new key controls.

## What migration is NOT

- Do NOT change data flow, hooks, query keys, mutations, outbox/streaming
  logic, or API calls unless your task explicitly says structural changes.
- Do NOT touch `vite.config.ts`, `e2e/wp2-theme-verify.mjs`,
  `src/lib/theme.ts`, `index.html`, `src/styles/gd-tokens.css`.
- Do NOT edit `src/index.css` or `src/styles/legacy-aliases.css` (the
  cleanup pass deletes legacy blocks once; utilities other pages still use
  must survive your pass).
- Do NOT add npm packages.

## Verification before you report done

```bash
cd /home/runner/workspace/artifacts/orivellum-ui
pnpm run typecheck        # must be clean

# Self-scan YOUR files (must print nothing):
grep -nE "useGdDark|vellum-(card|row|chip|seg|pagehead|h1)|glass-(vellum|sheet|card|lens)|var\(--(paper|card-raw|ink-|green-|gilt|rust|line[,)]|line-2|vellum|shadow-[12]|t-(canon|source|artifact|conv|claim)|page-bg|blur)|#[0-9a-fA-F]{6}" <your files>
```

Report: files changed, structural decisions made, any legacy pattern you
could not remove and why.
