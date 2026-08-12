---
name: E2E navigation principle
description: Durable rule for Playwright navigation in this project after shell redesigns.
---

# E2E navigation

- The dev proxy strips the artifact base path, so e2e tests navigate from the
  ROOT path; never trust helper constants or shell-specific navigation code
  written for an older shell — verify against the current UI, and prefer
  in-app `history.pushState` + `popstate` dispatch over clicking chrome that
  may have been redesigned.
- Simulate reconnect by dispatching a `window` `online` event — toggling
  Playwright's offline flag alone doesn't skip periodic-flush waits.
