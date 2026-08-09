---
name: Measurement layer (bench/telemetry/evalset)
description: Speed telemetry, native benchmarks, and retrieval golden-set eval — where they live and the math rules that must hold.
---

# Measurement layer (Uplift Phase 1)

Schema v109: `llm_calls` gained `ttft_ms`, `tok_per_s`, `streamed`; new tables `bench_runs` (one summary row per run) and `golden_queries` (retrieval judgments).

## Rules that must hold
- **NULL means "not measured", never zero.** Unavailable eval channels report `null` + error; unknown telemetry fields stay NULL.
- **Decode rate math:** the decode window starts AFTER the first token arrives, so the numerator must exclude it: `(n_tokens - 1) / decode_seconds`, min 2 tokens, window > 0.5 s. Shared helper `decode_tok_per_s()` in capabilities/llm.py — use it everywhere; an architect review failed a version that divided full token count by the window (inflates short replies badly).
- **One benchmark at a time.** `/api/bench/run` holds a module-level guard (409 on overlap) because overlapping probes corrupt each other's timings and eat executor workers. The guarded wrapper clears the flag in `finally`; UI polls `/api/bench/status` instead of guessing completion.
- Streaming chat telemetry: usage block from the final SSE chunk is preferred; delta count is the fallback token estimate (llama.cpp-family emits one delta per token). Usage-only chunks have no `choices` — skip them, don't index `choices[0]`.

## Where things live
- `capabilities/bench.py` — stream_probe, ttft sweep / generation / cache-probe experiments, bench_runs persistence, telemetry_summary (percentiles in Python; SQLite has none).
- `capabilities/evalset.py` — nDCG@k / Recall@k, golden CRUD, auto_seed_goldens (mid-text phrases; chunk goldens judged at DOC level via doc_id dedup), evaluate_retrieval over fts/semantic/hybrid.
- Routes: `/api/bench/*`; UI: MeasurementLabCard on the System page.
- Cache probe interpretation: warm TTFT not < 50% of cold TTFT → prefix caching broken, usually something volatile at the FRONT of the prompt.
