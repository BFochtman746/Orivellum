# 2. All model calls go through one LLM gateway

Date: 2026-08-10 | Status: Accepted

## Context
The system calls a local Lemonade server for chat, harvest, embeddings, and reranking. Scattered HTTP calls made usage impossible to measure or swap.

## Decision
Every LLM call goes through llm_call()/record_llm_call(). No module talks to the model endpoint directly. Model choice per role (workhorse/reasoner/coder) resolves DB override first, then config.

## Consequences
Telemetry, benchmarks, and model swaps happen in one place. New code that bypasses the gateway is a review-blocking defect.
