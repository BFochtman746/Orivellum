---
name: Chat context injection
description: How knowledge items are injected into the LLM system prompt
---

# Chat context injection

## Rule
`_build_system_prompt(db, conv)` in `conversations.py` injects work context when `conv.work_id` is set.

**Why:** This is what makes Orivellum "sovereign" — the LLM answers from the user's own knowledge, not just training data.

## How it works
1. Fetches work title via `db.get_work(work_id)`
2. Fetches top 8 knowledge items via `db.list_knowledge(work_id=work_id, limit=_CONTEXT_KNOWLEDGE)`
3. Formats each item as `[kind] text[:300]`
4. Prepends to base system prompt

## Gaps / future work
- Items are selected by insertion order, not relevance. Keyword-based scoring would improve quality.
- Currently injects ALL status items. Should exclude `rejected` items from injection.
- `_CONTEXT_KNOWLEDGE = 8` is a conservative limit. Could increase for longer-context models.
