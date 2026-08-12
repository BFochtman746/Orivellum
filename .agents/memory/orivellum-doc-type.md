---
name: Doc-type classification
description: doc_type provenance rules, harvest refusal policy, proposal-only reclassification, and classifier pitfalls.
---

# Doc-type classification

- Every library document gets a doc_type + provenance stamped AT CREATION, on every creation path — including system-persisted outputs (TTS clips, generated files, uploaded templates). A path that skips the stamp leaves generated content harvestable until a manual backfill runs.
  **Why:** the harvest refusal policy (unknown/generated/correspondence never harvested) only protects the corpus if it applies the moment a document exists.
  **How to apply:** any NEW document-creation path must either call the deterministic classifier or explicitly stamp a known type with rule provenance; any new harvest entry point must check harvestability first.
- Provenance vocabulary: `rule:<name>` (deterministic), `model` (proposal only — never applied directly), `author` (human ratification). Ratifying a proposal records the human, not the proposer, as the authority.
- Refused docs still chunk/index and reach readiness — searchable, never harvested. Legacy NULL doc_type passes the gate.
- Reclassification proposals carry provenance PER PROPOSED FIELD; a single shared provenance column gets clobbered when a rule (tier) and the model (doc_type) propose on the same row.
- Review-resolver rule reaffirmed: validation gates run BEFORE the atomic claim — a 422 must leave the item queued, never consume it.
- `\b`-anchored filename rules silently miss snake_case names (underscore is a word char) — match against a separator-normalized name.
- Backfill re-scans rule-classified `unknown` residue each run (skip-if-unchanged keeps it idempotent) so improved rules retroactively catch residue without touching ratified rows.
- Pre-migration backups are named after the highest pending migration version — tests must glob the version wildcard, never a hard-coded number.
