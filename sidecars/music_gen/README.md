# Music & SFX Generation Sidecar

Loopback-only FastAPI service that generates soundtrack beds and sound
effects for the Trailer Architect, using local models on the GPU.

## Models

| id | Model | License | Commercial use | Max length |
|----|-------|---------|----------------|-----------|
| `stable_audio_open` | Stable Audio Open 1.0 (Stability AI) | Stability AI Community License | Yes, for individuals/orgs under $1M annual revenue (verify current terms) | ~47 s |
| `musicgen` | MusicGen (Meta) | Code MIT / **weights CC-BY-NC 4.0** | **No — non-commercial only** | ~60 s |

The main Orivellum API blocks generation for a model until its license has
been acknowledged in the UI (stored as a DB setting). This sidecar assumes
the caller has done that check.

Stable Audio Open weights are **gated on Hugging Face** — accept the license
at https://huggingface.co/stabilityai/stable-audio-open-1.0 and log in with
`huggingface-cli login` (or set `HF_TOKEN`) before first use.

## Setup & run (Windows / Nimo)

```powershell
# One-time setup (creates .venv-music):
.\scripts\start-music-sidecar.ps1 -Setup
# For AMD GPU (ROCm) on Strix Halo:
.\scripts\start-music-sidecar.ps1 -Setup -TorchIndexUrl https://download.pytorch.org/whl/rocm6.4

# Start:
.\scripts\start-music-sidecar.ps1
```

Then in `config.yaml` set:

```yaml
serving:
  music_gen_url: "http://127.0.0.1:9884"
```

and restart Orivellum.

## Contract

- `GET /health` — `{ok, device, loaded_model, models: {<id>: {installed, loaded, max_duration_s, load_error}}}`
- `POST /v1/music` — `{prompt, duration_s, model, negative_prompt}` → `audio/wav`
  (400 bad input, 503 model unavailable)

Only one model is held in memory at a time; switching models unloads the
other first so VRAM stays bounded. Environment overrides: `MUSIC_GEN_PORT`
(default 9884), `STABLE_AUDIO_REPO`, `MUSICGEN_REPO` (default
`facebook/musicgen-small`; use `facebook/musicgen-medium` for higher quality
if VRAM allows).
