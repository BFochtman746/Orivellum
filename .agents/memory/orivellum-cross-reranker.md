---
name: Cross-encoder reranker
description: Durable constraints for the Lemonade /rerank cross-encoder stage in retrieval.
---

# Cross-encoder reranker — durable constraints

- Lemonade's rerank API is llama.cpp-compatible: `POST {base_url}/rerank` with `{model, query, documents:[str]}` → `{results:[{index, relevance_score}]}`; only models labelled `reranking` with the `llamacpp` recipe work (e.g. bge-reranker-v2-m3-GGUF).
- **Rule:** config ships with `serving.reranker_model` set by default, so every reranker call path MUST be protected by the shared circuit breaker + single-flight probe in the rerank capability. **Why:** without single-flight admission, concurrent chat requests each pay the full network timeout when the model isn't pulled, saturating the FastAPI threadpool (caught in code review).
- **Rule:** treat malformed rerank responses (duplicate/missing indices, non-finite scores) as endpoint failures that open the cooldown — a malformed endpoint is as unusable as a down one.
- **Rule:** gate checks on boolean-ish DB settings must normalize (`strip().lower() == "true"`); use the capability's shared `cross_reranker_enabled(db)` helper, never inline string compares.
- **How to apply:** new retrieval surfaces should call `rerank_candidates()` (BM25 → cross-encoder → LLM-listwise fallback, RRF-fused, never drops candidates); for raw text lists use `cross_encoder_scores()` — it self-gates on config + breaker.
