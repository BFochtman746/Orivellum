---
name: MCOS calibration system
description: Design decisions for Orivellum's MCOS benchmarking/telemetry layers (Phases 0-1 built; 2-5 planned)
---

# MCOS (MONARCH Calibration & Optimization System)

Scaled-down build of the user's 14-layer MCOS spec (attached_assets/Pasted-Project-MONARCH-Calibration-*). Full spec is enterprise-scale; agreed plan is 5 phases. Phases 0+1 are built.

## Built (Phases 0-1)
- **LLM gateway**: `capabilities/llm.py` — ALL non-streaming chat-completion calls must go through `llm_call()` (never raises, returns LLMResult). Streaming paths (chat stream, write stream) keep their own loop but MUST call `record_llm_call()` in a finally covering every terminal path; client disconnect = ok=False error="client_disconnected". Purpose labels are dotted (`cognition.author`, `chat.stream`, `mcos.eval`...).
- **Telemetry**: `llm_calls` table (migration v51). Best-effort insert, never raises. New LLM call sites must pass `db` or telemetry silently skips.
- **Benchmarks**: migration v52 (benchmarks/benchmark_cases/eval_runs/eval_results). `capabilities/mcos.py`: 2 static suites (reasoning, instruction_following — INSERT OR IGNORE) + 2 dynamic (knowledge_qa, rag_retrieval — delete+regen on seed, version bump only on change). Retrieval suite scores WITHOUT LLM via chunk search; FTS queries must be tokenized to bare alphanumerics or FTS5 MATCH chokes on punctuation.
- **Run lifecycle rule**: the whole background worker body sits inside one try/except so a run can never stay `running` forever; `_finalize_run` retries 3×. Stale 'running' rows >30 min are reaped to failed before the 409 guard.
- **API**: `/api/mcos/*` routes (benchmarks, seed, run/{id}, run-all, runs, runs/{id}, telemetry). Web dashboard at `/mcos` ("Calibration" in sidebar), direct fetch (no orval codegen), 3s conditional polling while a run is running.
- **Nightshift**: pass 11 `_pass_mcos`, gated by setting `mcos_nightly_enabled` (default "true"); retrieval suites always run, llm suites only if `is_ai_reachable(cfg)` probe passes.

## Built (Phases 2-3)
- **Judges**: rule (0.5) + LLM judge (0.3, purpose "mcos.judge", strict JSON rubric, absent on failure, scores must pass math.isfinite BEFORE clamping — NaN survives min/max) + grounding (0.2, sentence/context word-overlap, absent w/o context). Consensus renormalizes over present finite judges. Retrieval cases use judge key "retrieval".
- **Regression → governance**: regressed finalize writes audit_log (actor 'mcos'); /api/mcos/regressions + /regressions/{run_id}/ack; ack MUST be a single atomic json_set UPDATE with regressed predicate (read-then-write raced with finalize and could erase meta). Governance page has a Benchmark Regressions section.

## Planned next phases
- Phase 4: prompt registry table + candidate-vs-active benchmarking; dashboard exists already.
- Phase 5: RAG calibration — chunk size/overlap currently hardcoded (500/50 words in chunking.py); make settings + sweep harness.

**Why scaled down:** single-user, single local OpenAI-compatible endpoint — multi-model benchmark center, speech/vision suites, and 8-judge board deliberately cut.
