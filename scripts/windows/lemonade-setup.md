# Orivellum — Lemonade Server Setup Guide

> **System**: Nimo's Office & Gaming AI PC — AMD Ryzen AI Max+ 395 (Strix Halo)  
> **LLM backend**: Lemonade Server (AMD's official NPU + iGPU inference stack)

## What is Lemonade?

Lemonade Server is AMD's official local LLM inference tool for Ryzen AI hardware. Unlike Ollama (which uses ROCm and manual GPU-layer flags), **Lemonade automatically schedules workloads across the NPU, iGPU, and CPU** — you just install and pull models.

- NPU (XDNA2, 50 TOPS) handles 7–14 B parameter models at low latency and low power
- iGPU (40 RDNA 3.5 CUs, ≈ RTX 4070 Laptop) handles 32–70 B parameter models
- Unified 128 GB LPDDR5X means no separate VRAM — models up to 70 B fit entirely on-chip

---

## Step 1 — Install Lemonade Server

1. Visit **https://lemonade-server.ai** and download the Windows installer
2. Run the installer — Lemonade registers itself to start automatically at login
3. After install, verify it's running:

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/v0/health -UseBasicParsing
# Should return: {"status":"ok"} or similar
```

---

## Step 2 — Pull models

```powershell
# Best daily driver — 70 B on iGPU, 128 K context (~40 GB of 112 GB available)
lemonade pull llama3.3-70b

# Fast NPU model — 14 B, runs on XDNA2, sub-second first token
lemonade pull phi4

# Coder — 32 B on iGPU
lemonade pull qwen2.5-coder-32b

# Embeddings for semantic search
lemonade pull nomic-embed-text
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

## Step 3 — Start Orivellum

```powershell
# From the project root — no GPU env vars needed, Lemonade handles it:
.\scripts\start.ps1
```

Open **http://localhost:8080/orivellum-ui/** in your browser.

---

## Model recommendations for Ryzen AI Max+ 395

| Model | Size | Hardware used | Use case |
|-------|------|---------------|----------|
| `llama3.3-70b` | ~40 GB | iGPU (RDNA 3.5) | Primary chat, 128 K context |
| `phi4` | ~8 GB | NPU (XDNA2) | Fast Q&A, extraction, reasoning |
| `qwen2.5-7b` | ~4 GB | NPU | Near-instant responses |
| `qwen2.5-coder-32b` | ~18 GB | iGPU | Code generation |
| `nomic-embed-text` | 274 MB | NPU/CPU | Semantic search embeddings |

**Orivellum config (`config.yaml`) is already tuned for these models.**

---

## Key differences from Ollama

| | Ollama | Lemonade |
|--|--------|----------|
| GPU acceleration setup | Manual env vars (`HSA_OVERRIDE_GFX_VERSION`, etc.) | Automatic — Lemonade schedules NPU/iGPU |
| Default API port | 11434 | **8000** |
| API format | Ollama + OpenAI-compat | OpenAI-compat (primary) + Ollama-compat bridge on 11434 |
| NPU support | No | **Yes — XDNA2 first-class** |
| Model pull | `ollama pull` | `lemonade pull` |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Connection refused on port 8000 | Lemonade not running — check system tray or run `lemonade serve` |
| Model not found error | Run `lemonade list` to see pulled model names; update `config.yaml` to match exactly |
| Slow first response | Normal — model loading into unified RAM; subsequent responses are faster |
| Embeddings not working | Lemonade's embedding endpoint may need `nomic-embed-text` pulled first; Orivellum falls back to keyword search if unavailable |
| Want to switch to Ollama instead | Change `base_url` in `config.yaml` to `http://127.0.0.1:11434/v1` and use Ollama model names |

---

## References

- Lemonade docs: https://lemonade-server.ai/docs/
- AMD Lemonade blog: https://www.amd.com/en/developer/resources/technical-articles/unlocking-a-wave-of-llm-apps-on-ryzen-ai-through-lemonade-server.html
- Ryzen AI developer hub: https://developer.amd.com/playbooks/lemonade-getting-started/
