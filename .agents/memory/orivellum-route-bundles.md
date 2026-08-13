---
name: Route bundles & PWA prompt updates
description: Bundle-splitting decisions and the update-safety (app-busy) architecture for the Orivellum UI
---

# Decisions

- Routes are lazy; heavy libs (editor, charts, markdown/highlight) load at point of use through shared lazy wrappers rather than static imports.
- **Only default Rollup chunking — never hand-split node_modules.** **Why:** a manual vendor split caused circular inter-chunk evaluation crashes, and the all-in-one vendor chunk cost ~5x the initial-load budget. **How to apply:** if a chunk is too big, restructure imports (lazy boundaries), don't add manualChunks.
- Service worker precaches only the app shell; hashed route chunks are cached at runtime. Updates are prompt-based — nothing ever reloads on its own.

# Update-safety architecture

- A single global "app-busy" registry gates the update reload. The rule: **holding a busy reason means "a reload right now loses user work"; release only after the work is durable (server response or outbox write), never on dispatch.**
- Coverage is centralized, not per-feature: both API layers (the raw fetch wrapper and the generated client's mutator hook) hold a busy reason for every non-GET request automatically. Long-lived surfaces (drafts, streams, uploads) add their own reasons on top.
- Debounced autosaves go through a shared controller, never hand-rolled setTimeout. **Why:** hand-rolled versions get three races wrong — overlapping saves letting a stale write land last, an older save's completion releasing the hold while a newer edit is pending, and unmount dropping the last debounce window. The controller serializes writes, generation-tracks the hold, and flushes durably on dispose.

# CI gate lessons

- Bundle budgets must measure the entry's static-import closure from the build manifest, with editor-only chunks exempted transitively.
- A hermetic web-vitals check must stub the auth-status endpoint as authenticated AND assert an authenticated-only marker — otherwise it silently measures the login form.
