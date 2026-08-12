---
name: E2E navigation reality (post shell redesign)
description: How Playwright tests must reach pages now that the sidebar shell was replaced and the SPA serves at root.
---

# E2E navigation

- The dev SPA is served at the ROOT path through the proxy — the proxy strips
  the artifact base path, so tests must not prefix routes with it (doing so
  hits the app's own 404 page).
- The shared e2e helpers were written for the old sidebar shell; after the
  Home-Screen launcher redesign their sidebar-driven navigation cannot find
  section buttons. Don't trust old helper navigation — verify against the
  current shell.
- Reliable pattern: load the root page, log in, then navigate in-app via
  `history.pushState` + a `popstate` dispatch. Chat conversations are selected
  via a `?id=` search param, not a path segment.
- Mobile-viewport tests: navigate at default viewport first, then shrink the
  viewport before the interactions under test.
- Simulate reconnect by dispatching a `window` `online` event —
  `context.setOffline(false)` alone won't skip the periodic flush wait.
