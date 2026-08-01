---
name: Works detail editing & stats
description: Works detail page editing capabilities, stats bar, and quick actions added this session.
---

## Key decisions

- `useUpdateWork`, `useDeleteWork`, `useDeleteKnowledgeItem` hooks generated via Orval codegen
- Works detail has inline title/description edit (pencil icon), status Select dropdown (active/archived), and Delete button in breadcrumb row
- Stats bar uses `useGetWorkStats` — fields are `documents_by_kind`, `knowledge_by_kind`, `tasks_by_status` (all as `{kind: count}` maps), plus `conversation_count` (integer)
- Sum counts via `Object.values(...).reduce((a,b)=>a+b, 0)` since the endpoint groups by kind
- `QuickChatButton` in breadcrumb row starts a conversation linked to the work; invalidates conversations + stats query keys on success
- `getGetWorkStatsQueryKey` must be imported from api-client-react to invalidate stats

**Why:** Stats endpoint groups by kind rather than returning flat totals; the UI must sum them client-side.

**How to apply:** Any new feature that adds to a work's doc/knowledge/task/conversation count should call `queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) })` on success.
