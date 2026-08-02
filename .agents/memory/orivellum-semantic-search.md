---
name: Semantic / hybrid search
description: How chunk-level semantic + hybrid retrieval works and the availability rules interactive paths must follow.
---

# Semantic / hybrid chunk search

- All embedding/search logic lives in `capabilities/embeddings.py` (not db.py): `embed_chunks_for_doc` (pipeline, daemon thread after readiness), `hybrid_search_chunks` (RRF), `semantic_search(object_type="chunk")`. Nightly backfill catches anything the inline pass missed.
- `/api/library/search` takes `mode=keyword|semantic|hybrid` (default hybrid); chat context chunk retrieval uses `hybrid_search_chunks`.

**Rule: interactive paths must keep BM25-level latency when embeddings are down.**
**Why:** the embeddings endpoint is an optional local service; with hybrid as the default, a 30s connect timeout on every search/chat turn is a platform-wide outage, not a degraded mode. A code review rejected the first version for exactly this.
**How to apply:** query-time embedding uses a short timeout (~4s) AND a failure cooldown (~60s circuit breaker in `embed_texts` — any failure skips network attempts until it expires; success resets it). Any new caller of `embed_texts` on a request path must pass the short timeout, never the backfill one. Tests patch `embed_texts`, so use `_reset_circuit_breaker()` when testing the real function.

- FTS5 ANDs multi-token queries — a two-word conceptual query often matches zero chunks, so hybrid must return semantic-only results when FTS is empty, and `mode=semantic` falls back to keyword when semantic returns nothing (embeddings down or below the 0.25 cosine floor). Empty result pages are treated as a bug.
