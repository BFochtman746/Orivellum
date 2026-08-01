---
name: Chat global knowledge search
description: How _build_system_prompt retrieves knowledge for chat context injection
---

# Chat global knowledge search

## The rule
`_build_system_prompt()` in `src/orivellum/api/routes/conversations.py` ALWAYS searches the full knowledge database using the user's query. It does NOT require a Work to be linked.

**Why:** Users expect the AI to know about everything they've uploaded, regardless of which Work a conversation is linked to.

## How it works
1. `_build_messages()` passes `user_query=new_user_text` to `_build_system_prompt()`
2. Primary path: `db.search_knowledge(query, work_id=None)` + `db.search_chunks(query, work_id=None)` — relevance-ranked FTS across all works
3. Results are grouped by Work title (topic) in the system prompt, with the linked Work boosted to the top
4. Trusted statuses only: `{"auto", "approved"}` — `"ai_auto"` items excluded until user approves
5. Constants: `_CONTEXT_KNOWLEDGE = 12`, `_CONTEXT_CHUNKS = 5`
6. Recency fallback: when no query provided or FTS fails — uses `list_knowledge()` ordered by `created_at DESC`

## How to apply
- Do not revert to passing `work_id` to `search_knowledge` for the primary path — global search is intentional
- The `scope` parameter is still accepted but effectively overridden by query-based search when a query is present
- If adding new knowledge retrieval paths, maintain the `_TRUSTED` filter and the Work-grouping display format
