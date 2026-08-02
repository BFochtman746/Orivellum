---
name: WRITING_ARCHITECT WR-02
description: Stdlib-only Python Book Production OS (BPOS) at writing_architect_pkg/; WR-00 and WR-02 both complete; how to run tests and key design decisions.
---

# WRITING_ARCHITECT WR-02

## Location
`writing_architect_pkg/` at the workspace root — a standalone Python package, not part of the Orivellum monorepo. Does NOT use pnpm or FastAPI.

## Version
0.2.0 — WR-00 forensics + WR-01 foundation + WR-02 research & evidence.

## Test command
```
cd writing_architect_pkg && uv run --with pytest pytest tests/ -v
```
28/28 pass. The package has zero third-party runtime dependencies (stdlib-only).

## WR-02 commands added
- `wa seed-sources DB BOOK_ID [--manifest PATH]`
- `wa question / source / claim / evidence / verify / accept-claim / conflict / research-status`
- `wa demo-wr02 DB BOOK_ID`

## Key policy decisions
- **T6 and T7 blocked at source intake** (`policy.check_source_tier_admissible()`), not just at accept-claim time. This means T7 rows never enter the DB.
- **Claim acceptance gate** (`policy.check_claim_acceptance_gate()`) is a 9-point pre-flight that runs *before* the DB trigger, so errors are human-readable.
- `wa verify` sets `claim.verifier`; the gate refuses acceptance if it is empty.

## WR-00 acceptance record
`wr00_baseline/WR00_ACCEPTANCE.md` — accepted by Brian Fochtman on 2026-08-02.

## What's next
WR-03 — Canon & continuity. Entry gate: WR-02 exit accepted. Build: canon_entity / canon_fact / timeline_event population; five continuity validators raising editorial_finding rows.

**Why:** Keep here because this package is out-of-band from Orivellum and easy to forget. The test command is non-obvious (uv run --with pytest because pytest is not in the project's deps).
