---
name: Safari HTTP compatibility
description: Browser APIs that fail in Safari over plain HTTP (Tailscale/LAN) and how they are fixed
---

# Safari HTTP compatibility

## The rule
`crypto.randomUUID()`, `crypto.subtle`, and `navigator.clipboard.writeText()` are **secure-context APIs** — Safari blocks them over plain `http://`. Tailscale and LAN access without TLS hits this.

## Fixes applied
- `artifacts/orivellum-ui/src/lib/uuid.ts` — `randomUUID()` polyfill: tries `crypto.randomUUID()`, falls back to `crypto.getRandomValues()`, last-resort `Math.random()`
- Same file — `copyToClipboard()`: tries `navigator.clipboard.writeText()`, falls back to `execCommand('copy')` via a hidden textarea
- All `crypto.randomUUID()` and `navigator.clipboard.writeText()` calls in `chat/index.tsx` use these helpers

**Why:** User accesses the app over Tailscale (`http://100.x.x.x:5173`), which is plain HTTP. Safari enforces secure-context restrictions strictly.

## How to apply
Any new code that uses a Web Crypto or clipboard API must import from `@/lib/uuid` or wrap with a try/catch + execCommand fallback. Do not use `crypto.randomUUID()` or `navigator.clipboard` directly.
