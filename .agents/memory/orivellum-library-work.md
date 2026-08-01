---
name: Library document work assignment
description: PATCH endpoint + UI for linking/unlinking a library document to a Work.
---

## Rule
To assign or unlink a document's work, use `useUpdateDocument` from the generated client. The endpoint is `PATCH /api/library/{docId}` with body `{ work_id: string | null }`.

**Why:** There was no way to reassign a document to a different Work from the UI. The backend endpoint was added this session and codegen was run to produce the hook.

**How to apply:**
- Import `useUpdateDocument, useListWorks, getGetDocumentQueryKey` from `@workspace/api-client-react`
- Call `updateDoc.mutate({ docId, data: { work_id: value | null } })`
- On success, invalidate `getGetDocumentQueryKey(docId)` so the header work link refreshes
- Use `__none__` sentinel value in the Select to represent null (unlinked state)
- The overview tab renders a `<Select>` with all works as options; selecting "— Unlinked —" passes `null`
