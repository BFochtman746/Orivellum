---
name: Orivellum media capabilities
description: TTS, OCR, PDF/DOCX/XLSX extraction — what's implemented and how it works
---

## Text-to-Speech (POST /api/studio/tts)
- Neural only: Kokoro ONNX locally (+ optional AI server `/audio/speech` and premium sidecar)
- espeak was REMOVED from all audible paths Aug 2026 ("no robot voice" policy — see orivellum-no-robot-voice.md); when no neural engine is available the API returns 503 and clients pause-and-retry
- Kokoro model assets (`kokoro-v0_19.onnx`, `voices.bin`) are NOT in git — fetched via `scripts/fetch_tts_model.sh` / `.ps1`
- Returns `audio/mpeg` (MP3)

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
