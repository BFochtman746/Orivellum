---
name: Doc-type classification (RE-PROJECTION Phase 3)
description: doc_type/doc_type_by on documents, harvest refusal gates, proposal-only backfill, per-field reclassify provenance.
---

# Doc-type classification

- Every document carries `doc_type` + `doc_type_by` provenance: `rule:<name>` (deterministic), `model` (LLM proposal, never applied directly), `author` (human ratification). Ratifying a proposal always stamps `doc_type_by='author'` — the human's decision, not the proposer, is the authority.
  **Why:** pipelines must know who claimed a classification before trusting it; model output only ever PROPOSES via the review queue.
- `HARVEST_REFUSED_DOC_TYPES` = unknown/generated/correspondence. Refused docs still chunk/index and reach readiness `ready` — they are searchable, never harvested. NULL doc_type (legacy) passes the gate.
- `pending_reclassify` proposals carry PER-FIELD provenance (`proposed_tier_by`, `proposed_doc_type_by`); a shared `proposal_by` column got clobbered when tier (rule) and doc_type (model) proposals landed on one row. Upserts must never overwrite the other field's reason/provenance.
- Review resolver rule reaffirmed: validation gates (e.g. tier may-become-work) run BEFORE the atomic claim — a 422 must leave the item queued, never consume it. Architect review caught this.
- Name-based classification regexes must match a separator-normalized name (`_`/`-` → space): underscores are word chars, so `\b`-anchored rules silently miss `style_policy.md`.
- Backfill re-scans rule-classified `unknown` residue on every run (skip-if-unchanged keeps it idempotent), so improved rules retroactively pick up residue without touching author/model-ratified rows.
- The pre-migration backup file is named after the HIGHEST pending migration version — tests must glob `pre-migration-v*.db`, never a hard-coded version.

**How to apply:** any new import path must stamp doc_type at create; any new harvest entry point must call `assert_doc_type_harvestable`; classification changes always go through proposals, never direct mutation.
