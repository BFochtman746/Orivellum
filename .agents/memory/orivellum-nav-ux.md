---
name: Orivellum navigation UX decisions
description: Cross-page navigation and UX decisions for works/library/chat flow
---

## Navigation rules
- Works Documents tab cards → navigate to `/library/:docId` (cursor-pointer + onClick)
- Library detail header badge "Linked Work" → navigate to `/works/:workId` (clickable button)
- Library index cards → navigate to `/library/:docId` (onClick already in index.tsx)
- Chat conversations → `/chat?id=convId`

## Timestamp field
- Works table has no own `created_at`; use `obj_created` (from objects join).
  Display in UI: `work.obj_created || work.created_at` — fallback for safety.
- Library documents have their own `created_at` field (direct on documents table).

## Source path display
- `doc.source` is a full filesystem path; display only `source.split("/").pop()`.

## Knowledge filter chips
- Both Works detail KnowledgeTab and Library detail KnowledgeTabContent have
  All / AI Review / Approved / Dismissed filter chips.
- `knFilter` state lives at component level (not inside render callbacks) to
  satisfy React hooks rules.
- Library detail uses a separate `KnowledgeTabContent` component (extracted to
  avoid IIFE/hooks violation pattern).

## Chat context injection
- Rejected knowledge items are excluded from `_build_system_prompt()` context.
- Filter: fetch `limit * 4` items then slice after filtering out `rejected`.

**Why:** Dismissed items should not influence AI replies; only auto (rule-based)
and approved items should be injected.
