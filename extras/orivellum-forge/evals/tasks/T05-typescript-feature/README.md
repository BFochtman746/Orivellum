# T05 — Add a Feature Within Scope

**Capability tested:** Feature addition with type safety — TypeScript project, strict types, no scope drift.  
**Language:** TypeScript  
**Difficulty:** Medium  
**Expected repair cycles:** 0–1  
**Expected time to gate:** < 6 minutes

---

## Background

The seed project is a small TypeScript utility library (`src/`) that has a `truncate(text, maxLen)` function. You need to add an `ellipsis` option.

## Task prompt

```
Add an optional `options` parameter to `truncate` in `src/string.ts`:

  truncate(text: string, maxLen: number, options?: { ellipsis?: string }): string

- Default ellipsis is "…" (U+2026)
- If text.length <= maxLen, return text unchanged
- If truncating, append the ellipsis to the truncated text
  (so total length = maxLen - ellipsis.length + ellipsis.length = maxLen... 
   actually: trim to maxLen - ellipsis.length, then append)
- If maxLen <= ellipsis.length, return ellipsis truncated to maxLen

Add tests in `src/__tests__/string.test.ts` for all the new cases.
Do not modify the existing test cases.
```

## Non-goals

- No changes to other `src/` files
- No new npm dependencies
- No changes to `tsconfig.json` or `package.json`
- No changes to existing test cases

## Acceptance criteria

1. `tsc --noEmit` passes
2. All tests pass
3. `truncate("hello", 3)` returns `"h…"` (length 2 + ellipsis = 3? no — trim to 2 then append: "he" + "…" = 3 chars total ✓)
4. `truncate("hello", 10)` returns `"hello"` (unchanged, fits)
5. `truncate("hello world", 8, { ellipsis: "..." })` returns `"hello..."` (5 + 3 = 8)
6. Existing tests still pass
