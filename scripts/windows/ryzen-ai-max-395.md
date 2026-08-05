# Orivellum — AMD Ryzen AI Max+ 395 Hardware Profile

> **Hardware confirmed**: Nimo's Office & Gaming AI PC (Strix Halo — gfx1150/gfx1151)

## Chip summary

| Attribute | Value |
|-----------|-------|
| CPU | 16 × Zen 5 cores, 32 threads, up to 5.1 GHz |
| GPU | 40 RDNA 3.5 Compute Units (2 560 shaders) ≈ RTX 4070 Laptop |
| NPU | XDNA2, 50 TOPS |
| Memory | 128 GB LPDDR5X unified (CPU + GPU + NPU share the same pool) |
| Allocatable to LLM | **≈ 112 GB** (OS + driver reserve ~16 GB) |
| Memory bandwidth | ~256 GB/s |
| LLM throughput | ~20–35 tok/s on a 70 B Q4_K_M model, full GPU offload |
| Max tested context | 256 K tokens (Gemma 4 on ROCm 7.2.2 + Ollama) |

---

## Step 1 — Install Ollama with ROCm

Download from **https://ollama.com/download/windows** and install normally.  
Ollama ≥ 0.9 ships with prebuilt gfx1150/gfx1151 kernels (PR #14445).  
If you are on an older build, update Ollama first.

---

## Step 2 — Enable GPU offloading

These three environment variables must be set **before** Ollama starts.  
`start.ps1` sets them automatically; for manual sessions add them to your
PowerShell profile (`$PROFILE`) or set them in System → Environment Variables.

```powershell
# Treat Strix Halo RDNA 3.5 (gfx1150/1151) as gfx1100 for ROCm kernel compat
$env:HSA_OVERRIDE_GFX_VERSION = "11.0.0"

# Push all transformer weight layers onto the iGPU (reduce if you hit OOM)
$env:OLLAMA_GPU_LAYERS = "99"

# Target device index 0 (the integrated GPU)
$env:HIP_VISIBLE_DEVICES = "0"
```

Verify GPU is active after pulling a model:

```powershell
# In a separate terminal — you should see VRAM climbing while the model loads
ollama run llama3.3:70b-instruct-q4_K_M "Hello"
# Then check: Get-Process ollama | Select-Object WorkingSet
```

---

## Step 3 — Pull the recommended models

All weights fit entirely in the 112 GB iGPU pool — no CPU offload, no memory pressure.

```powershell
# Primary chat model (128 K context, ~40 GB on-chip)
ollama pull llama3.3:70b-instruct-q4_K_M

# Reasoner / analytical tasks (~42 GB)
ollama pull qwen2.5:72b-instruct-q4_K_M

# Coding tasks (~20 GB — leaves room for two models simultaneously)
ollama pull qwen2.5-coder:32b-instruct-q4_K_M

# Embeddings — 335 M params, 1024-dim, 330 MB, always resident
ollama pull mxbai-embed-large
```

### Lighter alternatives (faster first token, still excellent)

| Model | Size | Notes |
|-------|------|-------|
| `qwen2.5:32b-instruct-q4_K_M` | ~20 GB | Very fast; good for quick Q&A |
| `phi4:14b-q8_0` | ~15 GB | Punches above its weight on reasoning |
| `gemma3:27b-instruct-q4_K_M` | ~17 GB | Strong instruction following |
| `nomic-embed-text` | 274 MB | Lighter embeddings fallback |

---

## Step 4 — Apply the Orivellum hardware profile

`config.yaml` in the project root is already tuned for this chip.  
Key values set:

| Setting | Value | Why |
|---------|-------|-----|
| `context_window` | 131 072 | llama3.3 70B supports 128 K; iGPU KV-cache never evicts |
| `timeout_sec` | 300 | 70 B at 25 tok/s — long reasoning stays within budget |
| `extraction_timeout_sec` | 120 | Background extraction gets generous time without blocking chat |
| `embedder_model` | `mxbai-embed-large` | Best recall/speed on a chip with this much unified memory |

---

## Step 5 — Start Orivellum

```powershell
# From the project root — AMD GPU env vars are set automatically:
.\scripts\start.ps1
```

Open **http://localhost:8080/orivellum-ui/** in your browser.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `OLLAMA_NO_GPU=1` in Ollama logs | HSA env vars not set before Ollama started; restart Ollama after setting them |
| Model loads but only CPU used | Confirm `HSA_OVERRIDE_GFX_VERSION=11.0.0` is set; check `ollama ps` for GPU % |
| OOM / crash on 70 B model | Reduce `OLLAMA_GPU_LAYERS` to 60–80 to allow partial CPU offload |
| Slow first token on large context | Normal — KV-cache fill for 128 K prefill takes a few seconds |
| Embeddings circuit breaker tripped | Open System → Embeddings in Orivellum and run "Test Connection" to reset |

---

## References

- AMD official LLM inference guide: https://rocm.blogs.amd.com/artificial-intelligence/ryzen-uma-llm/README.html
- Ollama gfx1150/1151 PR: https://github.com/ollama/ollama/pull/14445
- Strix Halo deep-dive: https://hyperion-consulting.io/en/insights/amd-strix-halo-llm-guide-ollama-lmstudio-llamacpp-ubuntu-24
- Nimo PC product page: https://nimopc.com
