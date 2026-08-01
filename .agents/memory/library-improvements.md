---
name: Library page improvements
description: Key decisions and patterns for the Orivellum library pages
---

## Library index filters (web)
Three filter rows shown when showFilters is true:
1. Status: all / ready / processing / error
2. Type: all / [derived from available kinds] — shown only when >1 kind present
3. Work: all / Unlinked / [works that have docs] — shown only when worksWithDocs.length > 0

`workFilter` state: "all" | "__none__" (unlinked) | workId
- `worksWithDocs` is derived from `listResp.documents` work_id fields (not from works API)

## Duplicate upload handling
- Backend: SHA-256 dedup in `POST /api/library/import`; returns `{document, duplicate: true}` for dups
- ImportDialog: shows toast.info + navigates to existing doc (`navigateTo('/library/' + existingId)`)
- `navigateTo` comes from `useLocation()` INSIDE ImportDialog (not from parent scope)

## Backup download
- Added `GET /api/backups/{name}/download` endpoint returning `FileResponse`
- Frontend download button constructs URL from `import.meta.env.BASE_URL + /api/backups/:name/download`
- Same pattern used for files page download (`/api/download/{path:path}` already existed)

## Files page download
- Hover-reveal download icon per file row (opacity-0 group-hover:opacity-100)
- Calls `/api/download/{path}` with path = `currentPath/fileName` or just `fileName`
