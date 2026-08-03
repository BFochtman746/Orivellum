---
name: PKLOS Layer 0 — Full verification stack
description: What was built, what's correct, what's not yet wired in the PKLOS Layer 0 enforcement foundation.
---

## What is built and passing (52 tests)

All in `src/orivellum/capabilities/pklos/`:
- `authority.py` — A0–A8 tiers (spec §3.1 exact labels), ClaimStatus states (USER_ASSERTED/RETRIEVED/PARTIALLY_VERIFIED/VERIFIED/CONFLICTED/STALE/INVALIDATED/UNAVAILABLE/ABSTAINED), ConflictType (7 types §3.5), TTLClass, ALLOWED_TRANSITIONS, SUBJECT_DEVICE_A01
- `authority_resolver.py` — per-predicate policy (preferred_sources, minimum_authority, minimum_corroboration, conflict_policy); INV-REQ-001: "adapterram" substring match blocks AdapterRAM from VRAM predicates
- `claim_verifier.py` — normalize_value (byte unit inference: ≥1 GiB bare int → bytes), corroborate, conflict engine (7 types), verify_single_assertion always returns USER_ASSERTED for A7
- `policy_enforcer.py` — PolicyEnforcer.enforce() + build_system_prompt_additions(); must compare against RequestClass.DETERMINISTICALLY_VERIFIABLE (not .CHECKABLE_FACT classmethod)
- `fact_router.py` — 7-class classifier (spec §5.1); DERIVED_FACT checked BEFORE USER_DECLARED_FACT; "can my machine run X" → DERIVED_FACT
- `output_validator.py` — OutputValidator; pattern-based atomic claim checker; ROUTE-REQ-002 recall measurement is future (R4)
- `adapters/base.py` — AdapterBase, Evidence, Recipe, AdapterRegistry
- `adapters/recollection.py` — Adapter 4: A7 user assertion → USER_ASSERTED
- `adapters/library.py` — Adapter 2: A4 library/vault → wraps hybrid_search_knowledge + hybrid_search_chunks

## Key schema/db facts

- Schema v61: added normalized_display_value, confidence_basis, observed_at, valid_from, valid_until, verification_rule, supersedes, contract_version, producer columns to `claims` table
- db.upsert_claim: A7/A8 → `USER_ASSERTED`; A0–A6 → `RETRIEVED`; higher authority wins, lower never downgrades
- db.search_claims_for_context: searches VERIFIED, PARTIALLY_VERIFIED, USER_ASSERTED, RETRIEVED, CURRENT; A8 always excluded
- db.get_claim_by_predicate: returns highest-priority status (VERIFIED > PARTIALLY_VERIFIED > USER_ASSERTED > RETRIEVED)

## conversations.py integration

- PolicyEnforcer replaces direct ClaimLedger + AbstentionPolicy
- Falls back to legacy ClaimLedger + AbstentionPolicy on schema errors
- PolicyEnforcer.build_system_prompt_additions(query) → (context_block, instruction)
- CaptureStamp.stamp_and_capture() still runs in background thread on detect_factual_assertions

## What is NOT yet built (next phases)

- **P3 Windows inventory adapter**: PowerShell CIM collector → POST /api/pklos/inventory + adapter class + test suite A–G on hardware
- **Output validator wiring**: OutputValidator is built but NOT wired into the chat streaming pipeline (lives in output_validator.py, not called after each response)
- **BPOS schema migration**: writing_architect_pkg/domain/schema.sql → db.py as v62+ migrations

## Critical landmines / gotchas

- "AdapterRAM" lowercased is "adapterram" (double-r: Adapter+RAM). Check for "adapterram" not "adapteram".
- `RequestClass.CHECKABLE_FACT` is a classmethod object — NEVER compare against it. Compare against `RequestClass.DETERMINISTICALLY_VERIFIABLE`.
- DERIVED_FACT must be checked before USER_DECLARED_FACT in classifier (otherwise "can my machine run X" → USER_DECLARED_FACT incorrectly).
- normalize_value: bare large integer (≥1 GiB) → treat as bytes; bare small number → treat as GB (user-stated "128" → 128 GB).
