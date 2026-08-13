---
name: WP5 route bundles & PWA prompt updates
description: Code splitting, byte budgets, prompt-based PWA update gating, and list-windowing decisions for the Orivellum UI
---

# Route bundles & performance budgets (WP5)

- Every route in App.tsx loads via React.lazy with destination-level Suspense; heavy deps load at point of use: TipTap → write route only, recharts → pacing only, markdown+highlight.js → `src/components/rich-markdown.tsx` (shared lazy renderer for chat AND workbench detail; Suspense fallback = plain `whitespace-pre-wrap` text).
- **Only default Rollup chunking.** Never hand-split node_modules with manualChunks — the old vendor split caused circular inter-chunk evaluation ("can't access 'forwardRef' of undefined"). The all-in-one vendor chunk it replaced cost Home 851 KB gzip; default chunking gets Home to ~168 KB.
- `virtual:pwa-register` (used with `injectRegister: false`) statically imports **workbox-window** — it must be a direct devDependency of the UI package or the production build fails to resolve.

# Prompt-based update model

- `registerType: 'prompt'`; SW precaches shell only (index.html, `assets/entry-*.js`, css, woff2, icons); route chunks are CacheFirst by content hash.
- `applyUpdate()` refuses while the app-busy registry (`src/lib/app-busy.ts`) holds any reason. Wired: chat draft + stream, write-desk autosave, library upload. **Why:** an update reload mid-draft/stream/upload destroys work.
- **How to apply:** any new surface with unsaved/in-flight work must `setBusyFlag`/`acquireBusy` AND release on unmount (effect cleanup). The write desk additionally flushes its pending debounced save on unmount (capture inside try — TipTap getJSON can throw after destroy).

# CI gates

- `scripts/check_bundle_budgets.mjs`: walks the vite manifest entry closure (static imports) for the Home budget; per-chunk budget exempts the editor chunk plus chunks reachable only from it (importer-fixpoint).
- `scripts/measure_web_vitals.mjs`: vite preview + stubbed `/api/**`. The stub MUST return `{"authenticated":true}` for `/api/auth/me` or the script measures the LOGIN form, not Home — it asserts no password input is present before collecting metrics.

# Known stale e2e

- Specs 01-doc-upload / 02-work-link / 05-duplicate / 06-tts-voice-stale fail on selectors removed by the WP3 library redesign (e.g. the "Import Documents" button no longer exists) — pre-existing, confirmed by stash-run on clean HEAD.
