---
name: Orivellum Windows compatibility
description: Known Windows incompatibilities found and fixed; setup scripts created.
---

## What was fixed

### Python code
- `extraction.py` `_probe_tesseract()` — removed `bash -lc 'which tesseract'` (fails on Windows); added `sys.platform == 'win32'` guard; Windows path checked at `C:\Program Files\Tesseract-OCR\tesseract.exe`.
- `studio.py` `_probe_tesseract_cmd()` — same fix as extraction.py.
- `studio.py` espeak-ng `FileNotFoundError` — message now branches on `sys.platform` to give Windows download link vs nix-env hint.
- `health.py` `psutil.disk_usage('/')` — now uses `C:\\` on Windows, `/` elsewhere.

### Nix/bash patterns to avoid
Any `["bash", "-lc", ...]` subprocess call or `/nix/store` path scan must be guarded with `sys.platform != "win32"`.

## Scripts created
- `scripts/setup-windows.ps1` — one-shot installer: checks/installs Python, Node, pnpm, uv, Tesseract, Poppler, FFmpeg, espeak-ng; runs `uv sync` + `pnpm install`. Uses winget where available, direct GitHub release downloads otherwise.
- `scripts/start.ps1` — PowerShell equivalent of `scripts/dev.sh`; supports `-Mobile`, `-ApiPort`, `-WebPort` params; health-checks API before starting web; cleans up child processes on exit.

**Why:** The original codebase assumed Nix/Linux throughout. Windows users got silent failures (no useful error) for OCR, TTS, and disk stats. The probe functions used `bash` which doesn't exist on Windows.

**How to apply:** Any new subprocess call to an external binary must guard Unix-specific fallback logic behind `sys.platform != "win32"` and document the Windows PATH requirement in `scripts/setup-windows.ps1`.
