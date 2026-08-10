---
name: Book & trailer packaging exports
description: Design decisions for the Works packaging/export layer (EPUB build, trailer export, stale-run recovery, download auth)
---

# Book & trailer packaging exports

- Book export lives in `capabilities/book_package.py`: `package_readiness()` (never raises; returns ready flag + human-readable reasons) and `build_book_export()` → in-memory ZIP containing a stdlib-built EPUB 3 + per-chapter markdown + manifest.json.
  - **EPUB rules:** `mimetype` must be the FIRST zip entry, STORED (uncompressed); strip XML-1.0-invalid C0 control chars before `html.escape` (escaping alone is not enough); normalise whitespace-only titles.
  - **Memory bound:** everything is assembled in memory — 50 MB total-text cap raises ValueError rather than risking OOM.
- Trailer export: `shot_prompts` is a **dict** (`shot_00` → prompt) in current packages; iterate with `.items()`, keep list fallback for historical rows. Combined `both`/`all` envelopes get per-format subfolders.
- **Stale-run recovery pattern:** fire-and-forget background tasks die on restart, leaving DB rows 'running' forever. Startup recovery (`fail_stale_trailers` in lifespan) must be idle-threshold guarded (only fail rows whose `updated_at` is older than ~5 min) so a generation legitimately owned by another live process/reload is left alone — the runner touches the row on every phase change.
- **Download auth:** never use bare `window.open` for authenticated file downloads — it only carries the session cookie, and the PWA supports a localStorage Bearer fallback that would get 401. Use `downloadViaApi()` (book-tab.tsx): apiFetch → blob → object-URL anchor click, filename parsed from Content-Disposition.
- Unready download endpoints return 409 with the readiness reason in `detail` so the UI toast is self-explanatory.

**Why:** "Package not yet available" with no path forward was the symptom; the root causes were a missing book export step, orphaned trailer rows, and no status-aware messaging.
