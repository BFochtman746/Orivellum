# Orivellum — Lemonade Server Setup Guide

> **System**: Nimo's Office & Gaming AI PC — AMD Ryzen AI Max+ 395 (Strix Halo)  
> **LLM backend**: Lemonade Server (AMD's official NPU + iGPU inference stack)

## What is Lemonade?

Lemonade Server is AMD's official local LLM inference tool for Ryzen AI hardware. Unlike Ollama (which uses ROCm and manual GPU-layer flags), **Lemonade automatically schedules workloads across the NPU, iGPU, and CPU** — you just install and pull models.

- NPU (XDNA2, 50 TOPS) handles 7–20 B parameter models at low latency and low power
- iGPU (40 RDNA 3.5 CUs, ≈ RTX 4070 Laptop) handles the big models — up to 120 B MoE
- Unified 128 GB LPDDR5X means no separate VRAM — even gpt-oss-120b fits entirely on-chip

> **Why MoE models, not a dense 70B?** This machine's unified memory moves ~256 GB/s.
> A dense 70B model must read all 40 GB of weights for **every token** → ~4–5 tokens/sec.
> A Mixture-of-Experts (MoE) model like gpt-oss-120b only activates ~5 B parameters per
> token → **30–40 tokens/sec at higher quality**. On Strix Halo, MoE wins every time.

---

## Step 1 — Install Lemonade Server

1. Visit **https://lemonade-server.ai** and download the Windows installer
2. Run the installer — Lemonade registers itself to start automatically at login
3. After install, verify it's running:

```powershell
Invoke-WebRequest http://127.0.0.1:13305/api/v1/models -UseBasicParsing
# Should return: {"status":"ok"} or similar
```

---

## Step 2 — Pull models

Model names must match the Lemonade catalog **exactly** (they are also the API model IDs):

```powershell
# Workhorse — daily driver. MoE (~3B active), vision built in, ~23 GB, FAST
lemonade pull Qwen3.6-35B-A3B-GGUF

# Reasoner — best local reasoning. 120B MoE in native MXFP4, ~63 GB, still fast
lemonade pull gpt-oss-120b-mxfp-GGUF

# Coder — agentic coding, 256 K context, ~19 GB
lemonade pull Qwen3-Coder-30B-A3B-Instruct-GGUF

# Embeddings for semantic search — ~8 GB, always resident
lemonade pull Qwen3-Embedding-8B-GGUF

# RECOMMENDED: NPU chat model — runs chat on the NPU chip, leaving the
# ENTIRE iGPU free for embeddings, reranking, and background extraction.
# Select it as your chat model on the Orivellum System page (model picker).
lemonade pull gpt-oss-20b-NPU

# RECOMMENDED: reranker for higher search precision (~0.6 GB).
# Wired into Orivellum search, chat context, and web search — activates
# automatically once pulled (toggle on the System page).
lemonade pull bge-reranker-v2-m3-GGUF
```

Check what you have installed:
```powershell
lemonade list
```

Browse all available models:
```powershell
lemonade models
# or visit: https://lemonade-server.ai/models.html
```

---

## Step 3 — Turn on multi-model mode (one command, big win)

By default Lemonade keeps only **one** LLM loaded at a time. Every time chat and
a background AI task alternate models, Lemonade unloads one and reloads the
other — a multi-second swap you pay over and over. Raise the limit once:

```powershell
lemonade config set max_loaded_models=2
```

The setting is persistent (survives restarts). What it means:

- The limit is **per model type** — LLMs, embeddings, reranking, and
  transcription each get their own independent slots. Your embedder and
  reranker were never competing with chat; only LLMs swap.
- `2` lets your NPU chat model and the GPU workhorse stay warm **together** —
  chat on the NPU, background extraction on the iGPU, truly at the same time.
- Use `3` only if you skip the 120 B reasoner: workhorse (23 GB) + NPU chat
  (13 GB) + reasoner (63 GB) + embedder (8 GB) ≈ 107 GB, which leaves no
  headroom for KV caches. With `2`, the reasoner simply swaps in when asked.

**NPU caveat:** the NPU runs one engine at a time. If you transcribe audio via
an NPU Whisper backend while the NPU chat model is loaded, those two will
contend — Orivellum's local faster-whisper fallback (CPU) covers this fine.

---

## Step 4 — Start Orivellum

```powershell
# From the project root — no GPU env vars needed, Lemonade handles it:
.\scripts\start.ps1
```

Open **http://localhost:8080/orivellum-ui/** in your browser.

---

## Model recommendations for Ryzen AI Max+ 395

| Model | Size | Speed on this PC | Hardware | Use case |
|-------|------|------------------|----------|----------|
| `Qwen3.6-35B-A3B-GGUF` | ~23 GB | ~50–70 tok/s | iGPU | Primary chat + vision/OCR (MoE, 3 B active) |
| `gpt-oss-120b-mxfp-GGUF` | ~63 GB | ~30–40 tok/s | iGPU | Deep reasoning (MoE, 5.1 B active) |
| `Qwen3-Coder-30B-A3B-Instruct-GGUF` | ~19 GB | ~50–70 tok/s | iGPU | Code generation, 256 K context |
| `Qwen3-Embedding-8B-GGUF` | ~8 GB | instant | iGPU/CPU | Semantic search embeddings (SOTA) |
| `gpt-oss-20b-NPU` | ~13 GB | fast, low power | **NPU (XDNA2)** | Recommended chat driver: frees the whole iGPU for background AI |
| `bge-reranker-v2-m3-GGUF` | ~0.6 GB | instant | CPU | Recommended: +5–10 % search precision, auto-used once pulled |

For comparison, the previously recommended `llama3.3-70b` (dense) ran at only ~4–5 tok/s
on this hardware — and it has since been removed from the Lemonade catalog entirely.

**Memory budget:** workhorse + embedder stay resident (~31 GB); the reasoner (~63 GB) and
coder (~19 GB) load on demand. Everything fits within the ~112 GB allocatable to the iGPU.

**Orivellum config (`config.yaml`) is already tuned for these models.**

---

## Maximize the hardware: NPU chat + GPU background

Your machine has **two separate AI engines** — the iGPU and the NPU. The
highest-throughput configuration uses both at once:

1. Run Step 3 above (`max_loaded_models=2`) so both models stay loaded.
2. On the Orivellum **System page**, open the model picker and set the
   **chat model** to `gpt-oss-20b-NPU`.

That's it. The split happens automatically:

- **Chat** → your DB override → `gpt-oss-20b-NPU` → runs on the **NPU**
- **Background extraction, document harvesting** → config workhorse
  (`Qwen3.6-35B-A3B-GGUF`) → runs on the **iGPU**
- **Embeddings + reranker** → separate model-type slots, always resident,
  share the iGPU with negligible contention

Result: you can chat while documents import and index at full speed — neither
waits for the other. To switch back to maximum chat quality on the GPU, just
pick the workhorse in the model picker again; nothing else changes.

---

## Key differences from Ollama

| | Ollama | Lemonade |
|--|--------|----------|
| GPU acceleration setup | Manual env vars (`HSA_OVERRIDE_GFX_VERSION`, etc.) | Automatic — Lemonade schedules NPU/iGPU |
| Default API port | 11434 | **13305** (path `/api/v1`) |
| API format | Ollama + OpenAI-compat | OpenAI-compat (primary) + Ollama-compat bridge on 11434 |
| NPU support | No | **Yes — XDNA2 first-class** |
| Model pull | `ollama pull` | `lemonade pull` |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Connection refused on port 13305 | Lemonade not running — check system tray or run `lemonade serve` |
| Model not found error | Run `lemonade list` to see pulled model names; update `config.yaml` to match exactly |
| Slow first response | Normal — model loading into unified RAM; subsequent responses are faster |
| Embeddings not working | Lemonade's embedding endpoint needs `Qwen3-Embedding-8B-GGUF` pulled first; Orivellum falls back to keyword search if unavailable |
| Want to switch to Ollama instead | Change `base_url` in `config.yaml` to `http://127.0.0.1:11434/v1` and use Ollama model names |

---

## References

- Lemonade docs: https://lemonade-server.ai/docs/
- AMD Lemonade blog: https://www.amd.com/en/developer/resources/technical-articles/unlocking-a-wave-of-llm-apps-on-ryzen-ai-through-lemonade-server.html
- Ryzen AI developer hub: https://developer.amd.com/playbooks/lemonade-getting-started/
