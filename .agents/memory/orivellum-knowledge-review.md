---
name: Knowledge review workflow
description: How approve/dismiss works for AI-extracted knowledge items
---

# Knowledge review workflow

## Rule
`PATCH /api/knowledge/{item_id}/review` with `{"review_status": "approved"|"rejected"|"auto"|"ai_auto"}` updates review_status in DB.

**Why:** Users need to curate AI-extracted (ai_auto) knowledge before it influences chat context. The review system gates which items are trusted.

## How to apply
- DB method: `db.update_knowledge_review_status(item_id, status)` — raises ValueError for invalid statuses, returns bool (found)
- Approve/reject buttons only shown for items with status `ai_auto`, `approved`, or `rejected` (not plain `auto` rule-based items)
- Rejected items render at 50% opacity; approved items lock the approve button
- Both `works/detail.tsx` KnowledgeTab and `library/detail.tsx` Knowledge tab have this UI
- Endpoint registered in openapi.yaml as `reviewKnowledgeItem`; codegen run after adding it

## Future consideration
Chat context injection currently injects all knowledge items regardless of review_status. Eventually filter to only `auto` and `approved` items, excluding `rejected` ones.
