---
name: DeepFilterNet3 audio enhancement
description: DFN3 pre-transcription denoising — why the sidecar exists, pin rationale, probe/setup rules.
---
# DeepFilterNet3 sidecar — durable lessons

**No py3.12 wheels exist.** PyPI `DeepFilterLib` wheels stop at cp311 (June 2023); a source build needs Rust + maturin. The project requires Python >= 3.12, so installing deepfilternet into the server interpreter can NEVER work — any guidance telling the user to run a manual install is a dead end. The fix is a pinned Python 3.11 helper environment managed by `uv run`.

**Why the pins:** deepfilternet 0.5.6 is the last release with prebuilt cp311 wheels on all OSes; torchaudio must stay <= 2.6 (later versions drop `AudioMetaData`, which `df.io` imports); soundfile is torchaudio 2.x's load/save backend. On Linux the CPU torch index must be forced or uv pulls multi-GB CUDA builds.

**Setup/probe rules (why):**
- Passive availability checks must NEVER spawn subprocesses — first setup downloads ~300 MB, and a routine settings GET must stay instant. Trust in-memory state or an on-disk marker keyed to the pin spec.
- The one-time setup must run as a background job with polling, never inline in a request handler — it exceeds the server's request timeout (300 s) and the client would see a false failure.
- Never hold the state lock across the setup subprocess, or every passive check blocks behind it.
- A failed *enhancement run* (nonzero exit / launch error — but NOT a timeout, which may just be a long recording) must invalidate the ready state + marker, or the UI keeps claiming "Active" while silently passing through unenhanced audio.

**Live setup progress:** the setup streams uv's output (Popen, stderr merged) and parses lifecycle lines into coarse stages exposed as `setup_progress` while `setting_up`. Two rules: on timeout, kill the WHOLE process tree (`taskkill /T` on Windows, own process group + killpg on POSIX) — uv's Python child inherits the pipe and would keep the reader blocked past the deadline; and a zero exit code always wins over a raced timeout flag (a killed process never exits 0). When uv's env is already cached it prints almost nothing, so the stage can sit at "resolving" until done — that's expected, not a stall.

**Real cold-install output (observed live, non-TTY):** uv prints ALL `Downloading pkg (N MiB)` lines up front, then ` Downloaded pkg` (leading space) as each fetch completes, then `Installed N packages in …`. NO `Resolved`/`Prepared` lines appear in this flow — the stage machine must not depend on them, and the `Downloaded` completion lines must advance the detail or the UI sits on the last (smallest) package name for the entire torch fetch. Also: `uv cache clean` reports "Cache is currently in-use" while any `uv run` wrapper (e.g. the API-server workflow) is alive — needs `--force`; and evicting individual packages isn't enough to force a cold install because `uv run --with` reuses its `archive-v0` built environments.

**API quirks:** `init_df()` returns 3 values on 0.5.6 and 4 on 0.5.7+ — index the tuple, never fixed-arity unpack. Windows subprocess calls need `CREATE_NO_WINDOW` or each run flashes a console window.
