---
name: Lemonade model lineup (Strix Halo)
description: How model IDs are chosen/verified for the user's Ryzen AI Max+ 395 and where they must stay in sync.
---

# Lemonade model lineup — Ryzen AI Max+ 395 (128 GB)

Current lineup (Aug 2026, verified against the live Lemonade catalog):
- Workhorse + vision: `Qwen3.6-35B-A3B-GGUF` (~23 GB MoE, vision built in)
- Reasoner: `gpt-oss-120b-mxfp-GGUF` (~63 GB, native MXFP4)
- Coder: `Qwen3-Coder-30B-A3B-Instruct-GGUF` (~19 GB, 256K ctx)
- Embedder: `Qwen3-Embedding-8B-GGUF` (~8 GB Q8)
- Optional: `gpt-oss-20b-NPU`, `bge-reranker-v2-m3-GGUF`

**Why:** Strix Halo unified memory ≈256 GB/s → dense 70B ~4-5 tok/s; MoE models (3–5 B active) run 30–70 tok/s. `llama3.3-70b`, `phi4`, `nomic-embed-text` were removed from the Lemonade catalog entirely.

**How to apply:**
- Model names must match the Lemonade catalog keys EXACTLY (they double as API model IDs, with `-GGUF`/`-NPU` suffixes). Verify against `src/cpp/resources/server_models.json` in the lemonade-sdk/lemonade GitHub repo.
- Model IDs live in FIVE places that must stay in sync: `config.yaml` (wins over dataclass defaults!), `src/orivellum/configuration/config.py`, `scripts/windows/lemonade-setup.md`, `scripts/build_manual.py`, `scripts/generate_training_manual.py` (+ `scripts/setup-windows.ps1` pull hints).
- Rebuild manuals after changes: `uv run python scripts/build_manual.py` (→ docs/manual/Orivellum_Installation_Guide.docx) and `uv run python scripts/generate_training_manual.py` (→ orivellum_training_manual.pdf).
- config.yaml example format is `serving:` — a top-level `models:` block is NOT a supported loader path.
- DB overrides (`workhorse_model_override` etc.) shadow config; empty DB setting cannot suppress a non-empty config default (`stored or config` pattern).
- Gmail connector slug is `google-mail` (not `gmail`) for `listConnections`.
