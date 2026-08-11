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

## Packages available (already in pyproject.toml)
openpyxl, python-pptx, python-docx, reportlab, matplotlib — all usable in generated scripts

## Sandbox (Aug 2026)
Workshop scripts run via `_SANDBOX_RUNNER`: scrubbed env (never parent os.environ — it holds secrets), `python -I`, POSIX rlimits (skipped on win32), and socket-layer denial (patch `socket`/`_socket` connection entry points, NOT import blocking — reportlab and python-pptx import urllib internals and break under import blocks). **Why:** best-effort guard against hallucinated network/exfil; explicitly not an adversarial boundary (no OS isolation on the Windows target). Regression tests generate real PDF/PPTX inside the sandbox.

## Sandbox filesystem boundary
- The shared sandbox runner's audit hook allowlists file access to cwd + Python install + ORIVELLUM_SANDBOX_ALLOW dirs; audit hooks are irremovable, portable to Windows (unlike preexec rlimits).
- Never ban ctypes imports in the sandbox — numpy (pulled in by openpyxl) imports ctypes at import time, so legit xlsx builds die instantly.
- Symlinks are denied twice: os.symlink is audit-blocked in-sandbox, and _snapshot/output consumption reject any symlink so a link can't launder outside bytes into a published version.
