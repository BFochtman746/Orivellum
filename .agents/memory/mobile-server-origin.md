---
name: Mobile configurable server origin
description: How the Expo app resolves its API origin (user-entered server vs Replit default) and the pitfalls that broke it
---

# Mobile configurable server origin

The Expo app's API origin is user-configurable (self-hosted server over
Tailscale, e.g. `http://100.92.116.70:8080`) via `lib/server.ts`.

**Rules:**
- `apiOrigin()` is the ONLY way to build API URLs in mobile app code. Never
  reference `process.env.EXPO_PUBLIC_DOMAIN` in app code — it is baked at
  bundle time and only correct for the Replit dev environment. (Exceptions:
  `lib/server.ts` DEFAULT_ORIGIN and `scripts/build.js`.)
- NEVER capture the origin in a plain module-level constant
  (`const API = \`${apiOrigin()}/api\`;`) — route modules are imported before
  `loadServerOrigin()` restores the stored value in `_layout.tsx`. Use lazy
  arrow constants (`const API = () => \`${apiOrigin()}/api\`;`) and call them.
- Startup order in `_layout.tsx`: `loadServerOrigin()` → `setBaseUrl()` →
  `loadToken()`; login validates the key against the entered origin BEFORE
  persisting, then re-calls `setBaseUrl(apiOrigin())`.

**Why:** the app previously hardwired `https://${EXPO_PUBLIC_DOMAIN}` in ~35
files, so Expo Go on the user's phone could not reach their Windows server.

**Windows/Expo Go:** `pnpm run dev:win` (`expo start --lan --port 19000`) —
the default `dev` script uses POSIX env prefixes that fail on Windows and
`--localhost` which the phone can't reach. `start.ps1 -Mobile` exports
`REACT_NATIVE_PACKAGER_HOSTNAME` = Tailscale IP (fallback LAN) and prints the
`exp://` URL. `orivellum-boot.ps1` starts mobile by default (`-NoMobile` opts
out).

**CORS note:** native Expo fetch has no CORS, so custom servers just work in
Expo Go. Expo *web* served from Replit cannot call a Tailscale API (exact-
origin CORS is a deliberate security decision; add origins via
`ORIVELLUM_ALLOWED_ORIGINS` if ever needed).

**Testing:** `expo-secure-store` is jest-mocked via moduleNameMapper;
`lib/server.ts` detects web with `typeof document !== 'undefined'` instead of
importing react-native so jsdom suites can load it.
