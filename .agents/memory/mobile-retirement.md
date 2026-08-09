---
name: Mobile app retirement
description: The Expo mobile app was fully removed Aug 2026; what remains and what must be rebuilt on the web PWA.
---
The Expo/React Native app (`artifacts/mobile/`) was deleted entirely; the web PWA is the user's only client (headless Windows server, iPhone via Add to Home Screen).

**Why:** user confirmed the PWA is the only interface they will use; maintaining two frontends was pure overhead.

**How to apply:**
- Never re-add `--mobile`/`-Mobile` handling to dev.sh/prod.sh/start.ps1/orivellum-boot.ps1 or `dev:mobile`/`dev-mobile` scripts.
- Features the user wants rebuilt on web (queued as tasks): weather card with 24 h hourly forecast (Open-Meteo, hourly=temperature_2m,weathercode,precipitation_probability), offline message queueing, sticky cross-page Read Aloud player.
- Expo push plumbing fully removed Aug 2026 (schema v111 dropped push_tokens; routes, capability, db methods, CORS entry all gone). There is currently NO proactive notification channel — "document ready" / "audiobook ready" / "gaps found" alerts were push-only; rebuild as PWA browser notifications if wanted.
- Editing .ps1 files with the Edit tool strips the UTF-8 BOM that ps1-check requires — re-add codecs.BOM_UTF8 after any ps1 edit.
