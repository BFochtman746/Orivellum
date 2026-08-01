---
name: Orivellum media capabilities
description: TTS, OCR, PDF/DOCX/XLSX extraction — what's implemented and how it works
---

## Text-to-Speech (POST /api/studio/tts)
- Strategy 1: AI server's `/audio/speech` (OpenAI-compatible, used when Lemonade/LM Studio running)
- Strategy 2: `espeak-ng` CLI → `ffmpeg` WAV→MP3 conversion (always available offline)
- Voice map: af_heart→en+f4, af_bella→en+f1, am_adam→en+m1, bf_emma→en+f2, bm_george→en+m3
- Speed param (0.5–2.0) maps to espeak-ng words-per-minute (80–400 wpm)
- Returns `audio/mpeg` (MP3) in both paths
- Nix packages required: `espeak-ng`, `ffmpeg`

## Image OCR (POST /api/studio/ocr + extraction pipeline)
- Uses `pytesseract` + `tesseract5` nix package
- **Critical**: tesseract binary is NOT on the API server's process PATH by default on Replit/NixOS
- Both `studio.py` and `extraction.py` call `_probe_tesseract_cmd()` / `_probe_tesseract()` which:
  1. Checks `shutil.which('tesseract')` first
  2. Falls back to `bash -lc 'which tesseract'` (login shell has broader PATH)
  3. Falls back to iterating `/nix/store` at depth-1 for tesseract dirs
- Nix package: `tesseract5` (NOT `tesseract` — that name doesn't exist)

## PDF extraction (extraction.py `_extract_pdf`)
Three-tier fallback chain:
1. `pdfplumber` — best for text-layer PDFs
2. `pypdf` — handles edge cases pdfplumber misses
3. `markitdown` — final fallback for complex/scanned PDFs
Each tier only engages if the previous returned no text.

## DOCX extraction (extraction.py `_extract_docx`)
- Iterates raw XML body children to preserve document order
- Extracts both paragraphs (w:p) AND tables (w:tbl) — tables emitted as `[Table]\nTSV rows`
- Heading detection via w:pStyle/@w:val starting with "heading"
- **Why**: python-docx `.paragraphs` skips tables entirely

## XLSX extraction (extraction.py `_extract_excel`)
- Row cap: 5000 per sheet (was 500)
- Adds truncation note when capped: "(N rows — showing first 5000)"
- Empty row filter: `line.replace("\t","")` (was checking against `"\t" * len(cells)`)

## Studio UI (studio/index.tsx)
- TTSPanel: text input (10k char limit with counter), voice Select, speed Slider, Synthesize button, inline audio player with play/pause + download
- ImageGenPanel: prompt, width/height Select, Generate button, inline image preview + download; shows "AI OFFLINE" badge when health check fails
- OutputsGallery: polls every 15s, grid of recent audio/image/file outputs
