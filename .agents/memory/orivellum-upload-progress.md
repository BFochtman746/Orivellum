---
name: Upload progress tracking
description: How file import progress is tracked and displayed in the web library.
---

# Upload progress tracking

## Web (`artifacts/orivellum-ui/src/pages/library/index.tsx`)
- `uploadPct` state (number | null) drives a progress bar in `ImportDialog`
- Phases: 0 on start → increments by chunk during base64 conversion (yielding every 512 × 8KB chunks) → 92 after loop → 95 after `btoa()` → 95 while waiting for server → 100 on mutation success → null (hidden) on close/error
- Progress bar: `h-1.5 rounded-full bg-primary` div with inline `width: ${uploadPct}%` and `transition-all duration-200`
- Shows "Preparing file…" / "Uploading…" / "Done" label above bar

## Mobile (`artifacts/mobile/app/(tabs)/library.tsx`)
- Upload reads file as base64 via `FileReader` and POSTs to `/api/library/import` via `mobileFetch`
- No byte-level progress tracking (FileReader doesn't expose it); shows "Uploading…" button label only

**Why:** Large PDFs (10MB+) take several seconds to base64-encode in the browser; without progress the dialog looks frozen.
