---
name: PKLOS Layer 0 — Full verification stack
description: What was built, what's correct, what's not yet wired in the PKLOS Layer 0 enforcement foundation.
---

## What is built and passing (83 tests: 52 unit + 31 acceptance A–G)

All in `src/orivellum/capabilities/pklos/`:
- `authority.py` — A0–A8 tiers (spec §3.1 exact labels), ClaimStatus states (USER_ASSERTED/RETRIEVED/PARTIALLY_VERIFIED/VERIFIED/CONFLICTED/STALE/INVALIDATED/UNAVAILABLE/ABSTAINED), ConflictType (7 types §3.5), TTLClass, ALLOWED_TRANSITIONS, SUBJECT_DEVICE_A01
- `authority_resolver.py` — per-predicate policy (preferred_sources, minimum_authority, minimum_corroboration, conflict_policy); INV-REQ-001: "adapterram" substring match blocks AdapterRAM from VRAM predicates
- `claim_verifier.py` — normalize_value (byte unit inference: ≥1 GiB bare int → bytes), corroborate, conflict engine (7 types), verify_single_assertion always returns USER_ASSERTED for A7
- `policy_enforcer.py` — PolicyEnforcer.enforce() + build_system_prompt_additions(); FTS FALLBACK: when FTS finds only RETRIEVED claims (no usable VERIFIED/USER_ASSERTED), loads all device claims by subject. Must compare against RequestClass.DETERMINISTICALLY_VERIFIABLE
- `fact_router.py` — 7-class classifier (spec §5.1); DERIVED_FACT checked BEFORE USER_DECLARED_FACT; "can my machine run X" → DERIVED_FACT
- `output_validator.py` — OutputValidator; pattern-based atomic claim checker; hedged specific-value guess with NO evidence = HARD violation (hedging doesn't save a guess); ROUTE-REQ-002 recall measurement is future (R4)
- `adapters/base.py` — AdapterBase, Evidence, Recipe, AdapterRegistry
- `adapters/recollection.py` — Adapter 4: A7 user assertion → USER_ASSERTED
- `adapters/library.py` — Adapter 2: A4 library/vault → wraps hybrid_search_knowledge + hybrid_search_chunks
- `adapters/windows_inventory.py` — Adapter 1: WindowsInventoryAdapter; ingest_inventory() parses PowerShell JSON; INV-REQ-001 enforced (AdapterRAM logged as violation if present); VRAM only from Lemonade API

## API routes (all under /api/pklos/)

- `POST /api/pklos/inventory` — ingest PowerShell collector JSON; runs through adapter + verifier; stores in claims table
- `GET /api/pklos/inventory` — return all device:a01 claims (no A8)
- `GET /api/pklos/status` — claim counts by status/authority
- `POST /api/pklos/enforcement/check` — run enforcement on a query without calling the AI

All in `src/orivellum/api/routes/pklos.py`; registered in `app.py`.

## PowerShell collector

`scripts/inventory_collector.ps1` — collects CPU/RAM/GPU/VRAM/OS/BIOS/Storage/Models via Get-CimInstance (NOT Get-WmiObject).
Usage: `.\inventory_collector.ps1 -ApiUrl http://localhost:8000 -ApiKey YOUR_KEY`
AdapterRAM is deliberately not collected (INV-REQ-001).
VRAM probes Lemonade on ports 13305, 11434, 8080, 1234.

## Acceptance tests (spec §9.1 A–G)

`tests/test_pklos_acceptance.py` — 31 tests across all 7 spec test cases.
All 31 pass. Test cases:
- A: Known-fact (correct answer from verified inventory)
- B: Missing-data (controlled abstention)
- C: Contradiction (CONFLICTED detection, higher authority wins)
- D: Adapter-failure (graceful degradation, UNAVAILABLE)
- E: Alias (canonical CPU identity, no false merge)
- F: Derived-fact (vram as input for model-fit calculation)
- G: Adversarial injection (A8/A7 injection blocked, INV-REQ-001)

## Output validator wiring

`src/orivellum/api/routes/conversations.py`:
- Non-streaming: runs after `_call_ai()` completes; replaces reply if must_regenerate
- Streaming: runs after `db.finalize_message()`, before `[DONE]`; emits `pklos_correction` SSE event if regeneration needed
- Both paths gated by `is_checkable_fact(query)` and wrapped in try/except (best-effort; never blocks response)

## Key schema/db facts

- Schema v61: added normalized_display_value, confidence_basis, observed_at, valid_from, valid_until, verification_rule, supersedes, contract_version, producer columns to `claims` table
- db.upsert_claim: A7/A8 → `USER_ASSERTED`; A0–A6 → `RETRIEVED`; higher authority wins, lower never downgrades
- db.search_claims_for_context: searches VERIFIED, PARTIALLY_VERIFIED, USER_ASSERTED, RETRIEVED, CURRENT; A8 always excluded
- db.get_claim_by_predicate: returns highest-priority status (VERIFIED > PARTIALLY_VERIFIED > USER_ASSERTED > RETRIEVED)
- db.list_claims takes keyword-only args: `list_claims(*, subject=None, status=None, limit=50)`

## conversations.py integration

- PolicyEnforcer replaces direct ClaimLedger + AbstentionPolicy
- Falls back to legacy ClaimLedger + AbstentionPolicy on schema errors
- OutputValidator imported at top level (always available)

## Old test renames (now fixed)

- `status="CURRENT"` → `status="USER_ASSERTED"` in list_claims calls
- `RequestClass.CHECKABLE_FACT` → `RequestClass.DETERMINISTICALLY_VERIFIABLE`
- Transition log: initial insert logs USER_ASSERTED (not CURRENT); existing transition tests updated

## Critical landmines / gotchas

- "AdapterRAM" lowercased is "adapterram" (double-r: Adapter+RAM). Check for "adapterram" not "adapteram".
- `RequestClass.CHECKABLE_FACT` is a classmethod object — NEVER compare against it. Compare against `RequestClass.DETERMINISTICALLY_VERIFIABLE`.
- DERIVED_FACT must be checked before USER_DECLARED_FACT in classifier (otherwise "can my machine run X" → USER_DECLARED_FACT incorrectly).
- normalize_value: bare large integer (≥1 GiB) → treat as bytes; bare small number → treat as GB (user-stated "128" → 128 GB).
- PolicyEnforcer FTS fallback: FTS alone doesn't find "ram_gb" from "how much RAM" — fallback to list_claims(subject=SUBJECT_DEVICE_A01) is REQUIRED when no usable claims found by FTS.
- Hedged specific-value guess with no evidence = HARD violation (not soft).

## What is NOT yet built

- **Output validator Phase 2**: Replace pattern-based claim detection with LLM-based atomic decomposition (spec R4, ROUTE-REQ-002).
- **BPOS schema migration**: writing_architect_pkg/domain/schema.sql → db.py as v62+ migrations.
- **Frontend PKLOS UI**: no dashboard for claim status, inventory trigger, or verification history yet.
