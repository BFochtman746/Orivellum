---
name: Mobile library detail
description: Knowledge review and work linking in the mobile library/[id].tsx screen.
---

# Mobile library detail

## Knowledge review
- Thumbs-up/thumbs-down buttons on each knowledge card (only shown for `ai_auto` and `approved` items)
- Calls `PATCH /api/knowledge/{itemId}/review` with `{ review_status: "approved" | "rejected" }`
- Uses `mobileFetch` from `@/lib/api`
- `reviewing` state tracks in-flight item ID to show spinner on correct button
- Rejected items render at 50% opacity

## Work linking
- "Link to Work" chip in the overview section opens a bottom-sheet `Modal`
- Modal fetches works via `useListWorks` (from `@workspace/api-client-react`)
- Selecting a work calls `PATCH /api/library/{docId}` with `{ work_id }`; passing `null` unlinks
- Invalidates `getGetDocument` query key on success

**Why:** Parity with the web library/detail.tsx and works/detail.tsx knowledge review UX.

**How to apply:** Any mutation that changes work assignment on a document should invalidate `getGetDocumentQueryKey`.
