---
name: DeepFilterNet3 audio enhancement
description: DFN3 pre-transcription denoising — sidecar design, package pins, probe/marker rules.
---
DFN3 runs as step 0 before Whisper; DB setting `audio_enhance_enabled`; GET/PUT `/system/settings/audio-enhance`, POST `/system/audio-enhance/probe` (force); AudioEnhancementCard on the System page.

**No py3.12 wheels exist.** PyPI `DeepFilterLib` wheels stop at cp311 (June 2023); the source build needs Rust + maturin. The project requires Python >= 3.12, so `uv add deepfilternet` can NEVER work in the server interpreter — any guidance telling the user to run it is a dead end.

**Sidecar pattern (the fix):** run DFN3 in a pinned Python 3.11 env via `uv run --no-project --python 3.11 --with deepfilternet==0.5.6 --with torch==2.6.0 --with torchaudio==2.6.0 --with soundfile`, executing `scripts/dfn3_enhance.py` per file. **Why these pins:** 0.5.6 is the last release with prebuilt cp311 wheels on all OSes; torchaudio 2.7+ drops `AudioMetaData` (breaks `df.io`); soundfile is torchaudio 2.x's load/save backend. Env vars `UV_EXTRA_INDEX_URL=https://download.pytorch.org/whl/cpu` + `UV_INDEX_STRATEGY=unsafe-best-match` keep Linux from pulling CUDA torch; harmless on Windows.

**Probe rules:** passive probes (settings GET) must NEVER spawn subprocesses — first sidecar setup downloads ~300 MB, so only the forced probe ("Check again" button) runs it. Success persists in a marker file keyed to the pin spec (`XDG_CACHE_HOME/orivellum/dfn3-sidecar-ok`); a failed *enhancement run* (nonzero exit / launch error, but NOT timeout) invalidates memory + marker so the UI stops claiming "Active".

**API quirks:** `init_df()` returns 3 values on 0.5.6 and 4 on 0.5.7+ — unpack `result[0], result[1]`, never `a, b, _ = init_df()`. Windows subprocess calls need `creationflags=CREATE_NO_WINDOW` or each run flashes a console window.
