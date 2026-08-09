---
name: PWA stale-cache blank page
description: Why the production UI went blank after updates and the serving rules that prevent it.
---

# PWA stale-cache blank page

- Symptom: blank cream page at /orivellum-ui/ after a git pull + rebuild, survives refresh/restart. Server is fine ("Ready [OK]"); the client has a stale PWA cache.
- Root causes fixed in `_SPAStaticFiles` (api/app.py):
  1. No Cache-Control headers → browsers kept stale index.html referencing dead content-hashed assets.
  2. SPA 404-fallback returned index.html for missing `/assets/*.js` → browser executed HTML as a module script → silent blank page.
- **Rules:** `/assets/*` → `public, max-age=31536000, immutable`; index.html/sw.js/manifest/icons → `no-cache`; missing assets → real 404, never the shell; never cache any 404; with `html=True` a `404.html` returns a 404 *response* (doesn't raise) — handle both paths.
- User-side one-time recovery on an affected browser: hard refresh; if still blank, DevTools → Application → Service Workers → Unregister + Clear site data (iOS: remove/re-add home-screen PWA or Safari "Clear Website Data" for the host).
- Latent quirk noted: vite.config.ts `navigateFallbackDenylist: [/^\/api\//]` doesn't match `/orivellum-ui/api/` (harmless for non-navigation fetches, but remember if SW ever intercepts API navigations).
