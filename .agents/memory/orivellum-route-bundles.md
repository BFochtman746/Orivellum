---
name: Route bundles & PWA prompt updates
description: Code splitting rules, bundle/vitals CI gates, and the app-busy update-gating pattern for the Orivellum UI
---

# Code splitting rules

- Every route loads via React.lazy with destination-level Suspense; heavy deps load at point of use: TipTap → write route only, recharts → pacing only, markdown+highlight.js → `src/components/rich-markdown.tsx` (shared lazy renderer for chat AND workbench detail; Suspense fallback = plain `whitespace-pre-wrap` text).
- **Only default Rollup chunking.** Never hand-split node_modules with manualChunks. **Why:** a hand-made vendor split caused circular inter-chunk evaluation ("can't access 'forwardRef' of undefined"); the all-in-one vendor chunk it replaced cost Home ~850 KB gzip vs ~170 KB with default chunking.
- `virtual:pwa-register` (with `injectRegister: false`) statically imports **workbox-window** — it must be a direct devDependency of the UI package or the production build fails to resolve.

# Prompt-based update model

- `registerType: 'prompt'`; SW precaches shell only (index.html, `assets/entry-*.js`, css, woff2, icons); route chunks are CacheFirst by content hash.
- `applyUpdate()` refuses while the app-busy registry (`src/lib/app-busy.ts`) holds any reason. **Why:** an update reload mid-draft/stream/upload destroys work.
- **How to apply:** any new surface with unsaved/in-flight work must hold a busy reason AND release it on unmount. Debounced autosaves must go through the shared draft-autosave controller, never a hand-rolled setTimeout: saves are serialized (a stale older write can never land after a newer one), the busy hold is generation-tracked across overlapping saves, and unmount flush keeps the hold until the write is durable (server or outbox) — dispatching a request is not persistence.

# CI gates

- Bundle-budget gate: walk the vite manifest entry closure (static imports) for the initial-load budget; per-chunk budgets exempt the editor chunk plus chunks reachable only from it (importer-fixpoint).
- Web-vitals gate: vite preview + stubbed `/api/**`. The auth-status stub MUST report authenticated or the script silently measures the LOGIN form instead of Home — always assert an authenticated-only marker before collecting metrics.
