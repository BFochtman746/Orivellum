---
name: GENESIS Book Origination System
description: Ten-gate pre-writing workflow wired into Orivellum as a Works feature (schema v92).
---

# GENESIS integration

## What it is
A ten-gate (G0-G9) book origination workflow ported from the standalone `genesis.py` CLI.
Each gate requires explicit author sign-off before the next opens. Decisions are recorded in a tamper-evident SHA-256 hash chain (ledger).

## Schema (v92)
- `genesis_books` — one per Work (work_id UNIQUE); state tracks current open gate
- `genesis_stages` — PENDING/PASSED/FAILED per stage, per book
- `genesis_artifacts` — markdown content per stage (stored in DB, no filesystem)
- `genesis_ledger` — append-only hash chain; each row has id (UUID), seq, kind, payload, prev_hash, hash

## Capability package
`src/orivellum/capabilities/genesis/`
- `gates.py` — STAGES list, ledger_append (generates UUID for id), get_stage_status, next_open_stage
- `templates.py` — embedded G0-G9 markdown templates (TEMPLATE_CONTENT dict keyed by slug)
- `seal.py` — compute_seal (requires G0-G8 PASSED + G9 artifact filled), verify_ledger
- `codex.py` — BRAINSTORM_CODEX embedded; get_codex_for_stage(code) returns relevant sections

## API routes
`src/orivellum/api/routes/genesis.py` — registered in app.py
- POST   /api/works/{id}/genesis                  — init (idempotent)
- GET    /api/works/{id}/genesis                  — full status + all 10 stages
- GET    /api/works/{id}/genesis/stages/{code}    — artifact content + status
- PATCH  /api/works/{id}/genesis/stages/{code}    — save artifact (upsert)
- POST   /api/works/{id}/genesis/stages/{code}/gate — gate decision (pass/fail)
- POST   /api/works/{id}/genesis/seal             — seal package → READY_FOR_B0
- GET    /api/works/{id}/genesis/verify           — verify ledger chain
- GET    /api/works/{id}/genesis/techniques       — brainstorm codex (optional ?stage=G4)

Auth: handled by global middleware in app.py — no per-route require_auth needed.
Uses `from orivellum.api._deps import get_db` (NOT `orivellum.api.auth`).

## Web UI
`artifacts/orivellum-ui/src/pages/works/genesis-tab.tsx`
- Genesis tab added to Works detail (detail.tsx) with Film→Scroll icon
- GateStrip: clickable G0-G9 progress buttons
- StageEditor: markdown textarea, Save, Pass Gate / Fail Gate buttons, Codex drawer
- Pass Gate disabled when <<FILL>> placeholders remain in content
- Gate dialogs collect author + note; decisions are append-only
- SealDialog: final G9 author sign-off → manifest displayed
- Ledger verify button: GET /genesis/verify → inline result

**Why:** Gate ordering invariant and append-only ledger are the core safety guarantees.

## Key design decisions
- Artifacts stored in DB (not filesystem) — portable and API-accessible
- ledger_append must generate a UUID for the `id` column (TEXT PRIMARY KEY)
- ON CONFLICT for genesis_artifacts uses `excluded.updated_at` (not `excluded.at`)
- Init is idempotent: POST returns existing book if work_id already has one
- Seal requires G0-G8 PASSED + G9 artifact filled; G9 stage is marked PASSED by seal
