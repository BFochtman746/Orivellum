---
name: Orivellum memory + recall system
description: How inference-based memory capture, temporal versioning, conversation embedding, and recall queries work.
---

# Orivellum Memory + Recall System

## Single-row-per-key temporal versioning
`user_memory` has a UNIQUE index on `key` (cannot have multiple rows per key in SQLite without
recreating the table). The temporal versioning approach uses **single-row-per-key** with:
- `prev_value` — the immediately previous value (NULL for first write)
- `superseded_at` — timestamp when the value last changed (not an archive flag)
- `upsert_memory_fact(key, value, source_conv_id)` — UPDATE in-place, carry old→prev_value

**Why:** A multi-row approach (with superseded_at as a soft-delete flag) was originally designed
but broke on the UNIQUE index. The single-row approach is correct for this schema.

## Conversation chunks
Schema v65 adds `conversation_chunks` table (id, conv_id, text, created_at) and `vectors`
stores embeddings with `object_type='conv_chunk'`. Each user+assistant exchange is stored
as one chunk after the reply is finalized.

`embed_conversation_exchange(conv_id, user_text, assistant_text, db)` in embeddings.py:
- Always stores the text chunk (for FTS fallback)
- Stores the vector only when the embeddings endpoint is up

## Background processing after every reply
`_post_reply_background(db, conv_id, user_text, assistant_text)` runs in a daemon thread after
EVERY assistant reply (both streaming and non-streaming paths). It calls:
1. `embed_conversation_exchange` — store + embed the exchange
2. `_infer_memory_facts` — LLM extraction with quality gate (confidence ≥ 0.75, max 3 facts)

The old trigger-phrase gate (`_maybe_capture_memory` with `_MEMORY_PATTERNS`) was removed.

## Recall intent
Pattern: "where are we on X", "what did we decide about X", "what's our status on X", etc.
Handler: `_handle_recall_query(db, user_text, base_url, model)` — searches conv_chunks (semantic
+ FTS fallback) + memory facts + knowledge, synthesizes with LLM, returns sources list.
GET /api/memory endpoint returns all current facts for the UI memory panel.

## UI
Chat sidebar has a Sparkles button that toggles `MemoryPanel`. The panel fetches `/api/memory`
(query key "memory-facts", staleTime 30s) and renders facts with prev_value history.
`recall` intent shows "✨ Memory recall" badge like other intent badges.

## search_conversation_chunks uses LEFT JOIN
The conversations JOIN must be LEFT JOIN — chunks may reference conversations that were deleted
or test data. Using INNER JOIN caused silent no-results in tests.
