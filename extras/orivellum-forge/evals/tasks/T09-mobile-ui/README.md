# T09 — Mobile UI Repair (iPhone Viewport)

**Capability tested:** Mobile layout — fix a UI defect visible only at 390px width.  
**Language:** TypeScript (React + Vite)  
**Difficulty:** Hard  
**Expected repair cycles:** 1  
**Expected time to gate:** < 10 minutes

---

## Background

The seed project is a React app. The `NavBar` component has a horizontal menu that overflows at mobile widths — links get clipped outside the viewport. A Playwright WebKit test (iPhone 15 preset) captures a screenshot showing the overflow. The test exists and is currently failing.

## Task prompt

```
The Playwright test `e2e/navbar.spec.ts::overflows at mobile width` is failing.
The NavBar component in `src/NavBar.tsx` overflows horizontally on iPhone-sized viewports.

Fix the layout so the navigation links wrap or collapse appropriately at 390px width.
The Playwright WebKit test must pass after your fix.

Requirements:
- No JavaScript-based layout hacks — use CSS (flexbox/grid) only
- All desktop layout tests must still pass
- No changes to the NavBar's rendered links or their href values
```

## Non-goals

- No hamburger menu implementation (out of scope)
- No changes to any other component
- No new CSS frameworks

## Acceptance criteria

1. `npx playwright test --project=webkit e2e/navbar.spec.ts` exits 0
2. `npx playwright test --project=chromium e2e/navbar.spec.ts` exits 0 (no desktop regression)
3. No `overflow: hidden` or `display: none` used to hide the overflow (must actually fix it)
4. `tsc --noEmit` passes
5. Only `src/NavBar.tsx` and/or `src/NavBar.css` are modified
