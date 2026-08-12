---
name: Document Workshop system
description: Self-prompting AI document generator — clarify → code-gen → safe exec → critique loop
---

## What was built

`src/orivellum/capabilities/workshop.py`
- `plan_document()` — LLM generates 4-6 clarifying questions; stores session in `_SESSIONS` dict
- `execute_workshop()` — LLM writes Python script → subprocess sandbox (60s timeout, temp dir) → retry w/ LLM correction (max 2) → write.critic critique → `_register_output()`
- Three internal prompts: `_PLAN_SYSTEM`, `_CODEGEN_SYSTEM`, `_CRITIQUE_SYSTEM`
- Critique returns: scores {completeness, accuracy, design, professionalism}, strengths, gaps, suggestions, verdict

`src/orivellum/api/routes/generate.py` additions:
- `POST /api/generate/workshop/plan` → returns session_id + questions + detected_format + detected_intent
- `POST /api/generate/workshop/execute` → generates doc, returns download_url + critique JSON

`artifacts/orivellum-ui/src/pages/studio/index.tsx`:
- `DocumentWorkshopPanel` added above OutputsGallery
- 4-step flow: request → questions (answered inline) → generating spinner → result + download + critique
- Suggestions are clickable — clicking one pre-fills the request and resets to step 1

## Key design decisions

**Why:**
- Clarification-first prevents wasted generation on underspecified requests
- Code-gen (LLM writes Python) is more flexible than hard-coded templates
- Retry-with-LLM-fix handles syntax errors without exposing them to the user
- write.critic slot reused so prompt can be tuned via MCOS governance
- Output registered as ARTIFACT tier (no corpus pollution)

**How to apply:**
- Sessions are in-memory — lost on restart (ephemeral by design, workshop is a short interaction)
- Script execution uses `sys.executable` in a temp dir; no virtualenv or container needed
- Sandbox is subprocess with 60s timeout; output file found by checking OUTPUT_PATH then scanning dir
- Critique prompt falls back to `_CRITIQUE_SYSTEM` if no active write.critic in MCOS

## Download URLs from generate routes (Aug 2026)
Backend returns `download_url` already prefixed with `/api/...`, while the UI's `BASE` constant also ends in `/api`. Any `<a href={BASE + download_url}>` yields `/api/api/...` → 404 (the Scriptorium link shipped broken this way for months).
**How to apply:** always `download_url.replace(/^\/api/, "")` before prefixing with BASE; do the same for any future route that returns absolute `/api/...` paths.

## Manual OCR tool vs Library OCR
`POST /studio/ocr` runs Tesseract ONLY, but `/studio/status` reports `ocr.available=true` when the VLM vision path is up. UI tools that call the manual endpoint must gate on `ocr.tesseract_available`, not `ocr.available` — otherwise the button is enabled and the request 503s. The VLM path only runs inside Library extraction.

## Packages available (already in pyproject.toml)
openpyxl, python-pptx, python-docx, reportlab, matplotlib — all usable in generated scripts

## Sandbox (Aug 2026)
Workshop scripts run via `_SANDBOX_RUNNER`: scrubbed env (never parent os.environ — it holds secrets), `python -I`, POSIX rlimits (skipped on win32), and socket-layer denial (patch `socket`/`_socket` connection entry points, NOT import blocking — reportlab and python-pptx import urllib internals and break under import blocks). **Why:** best-effort guard against hallucinated network/exfil; explicitly not an adversarial boundary (no OS isolation on the Windows target). Regression tests generate real PDF/PPTX inside the sandbox.

## Sandbox filesystem boundary
- Generated-code sandboxes must split READ-ONLY (interpreter + dependencies) from WRITABLE (workdir + explicitly granted output dirs).
**Why:** blanket access under sys.prefix would let scripts tamper with installed packages — a host-integrity hole even on a single-operator machine.
- Process creation (subprocess/os.system/exec/fork/spawn) must be audit-denied: a child process does not inherit the audit hook, so any spawn is a full sandbox escape.
- Never ban ctypes imports in the sandbox — numpy (pulled in by openpyxl) imports ctypes at import time, killing legit builds.
- System mime.types tables must stay readable: stdlib mimetypes opens them on instantiation; they exist on CI runners but not this container, so the gap only shows in CI.
- Reject symlinks at every output-consumption point INCLUDING fallback candidate selection — a link can otherwise launder outside bytes into a published artifact.
