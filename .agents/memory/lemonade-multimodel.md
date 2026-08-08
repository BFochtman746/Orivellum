---
name: Lemonade multi-model & NPU/GPU split
description: How Nimo runs chat on the NPU and background AI on the iGPU concurrently.
---

# Lemonade multi-model & NPU/GPU split

- Lemonade default is `max_loaded_models=1` **per model type** (LLM / embedding / reranking / transcription each have an independent LRU pool). Only LLMs ever swap-thrash; embedder + reranker are always safe.
- **Rule:** the recommended Nimo config is `lemonade config set max_loaded_models=2` (persistent) so the NPU chat model and GPU workhorse stay warm together. `3` only without the 120B reasoner (memory: 23+13+63+8 ≈ 107 GB, no KV headroom).
- **Why the split needs no code:** chat resolves model via DB `workhorse_model_override` (user picks `gpt-oss-20b-NPU` in the System page picker), while `llm_harvest`/background extraction read `cfg.serving.workhorse_model` directly — so chat→NPU and harvest→iGPU diverge automatically. Do NOT "fix" harvest to honor the DB override; the divergence is the feature.
- NPU exclusivity: whispercpp / flm / ryzenai-llm are mutually exclusive on the NPU — NPU whisper transcription contends with an NPU chat model (CPU faster-whisper fallback covers it).
- LM Studio second-server routing was evaluated and deliberately deferred: solves the same contention as the NPU split but adds an always-on dependency; revisit only if imports still stall during chat.
- **How to apply:** setup instructions live in `scripts/windows/lemonade-setup.md` (Step 3 + "Maximize the hardware" section).
