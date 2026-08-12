---
name: E2E navigation reality (post shell redesign)
description: How Playwright tests must reach pages now that the sidebar shell was replaced and the SPA serves at root.
---

# E2E navigation

- The dev SPA is served at the ROOT path through the proxy at `localhost:80`
  (page modules load from `/src/main.tsx`, no `/orivellum-ui` prefix), even
  though vite.config defaults `base` to `/orivellum-ui/`. Visiting
  `/orivellum-ui/...` renders the app's own 404 page.
- `e2e/helpers.ts` is stale on two counts: `BASE_PATH='/orivellum-ui'` and the
  sidebar-driven `goto()` NAV_MAP (sections like UNDERSTAND) — the sidebar was
  replaced by the Home-Screen launcher shell, so `goto()` cannot find section
  buttons. Older specs (e.g. 03-chat) that rely on it are broken.
- Working pattern (see e2e/tests/07-continuity-mobile.spec.ts): `page.goto`
  root, `ensureLoggedIn`, then in-app `history.pushState('/chat?id=…')` +
  dispatch `popstate`. Chat conversations are selected via the `?id=` search
  param — there is no `/chat/:id` route.
- Mobile-viewport tests: navigate at default viewport first, then
  `page.setViewportSize({width:390,height:844})` before the interactions
  under test.
- Simulate reconnect with `page.evaluate(() => window.dispatchEvent(new
  Event('online')))` — `context.setOffline(false)` alone doesn't fire it
  reliably enough to skip the wait for the 20 s flush interval.
