---
name: RE-PROJECTION quarantine & domain re-harvest
description: quarantined_reprojection status semantics, closed-ontology discard rule, and token-fenced re-harvest coordination invariants.
---

# Quarantine status (`quarantined_reprojection`)
- Machine-extracted (auto/ai_auto) knowledge from before domain ratification is quarantined evidence: kept, never re-served. Approved items survive and follow their source document's current Work.
- **Why:** legacy harvests coerced content into a wrong universal schema; deleting would destroy evidence, serving would keep polluting AI output.
- **How to apply:** every read path that grounds AI output, search, graphs, or stats must exclude the status. Knowledge-eligibility predicates are scattered across many inline SQL sites, not centralized — when adding a new excluded review_status, sweep ALL raw SQL, not just the DB helper methods. Explicit `review_status_in` allowlists are the only way to read quarantined evidence.

# Closed ontologies
- Narrative kinds mirror ATLAS node types, kept in lockstep by a test. No-domain Works are ungated (legacy behavior preserved).
- Off-schema model output is discarded AND counted, never coerced — in every harvest path. Acceptance invariant: zero ontology violations (checkable via the violations endpoint).

# Re-harvest run coordination
- Run claims carry a UUID **fencing token** with a stale-reclaim window. Fencing at finalization alone is NOT enough: a reclaimed stale worker would still delete/overwrite the newer run's knowledge mid-run. The rule: collect all LLM output per document first (read-only), then do delete+insert as ONE atomic transaction conditional on current token ownership; report/status finalization is fenced the same way. A superseded run must end with zero writes.
- Pilot-gate CAS and the run claim must sit in one atomic transaction, with rollback of the pilot claim if background dispatch fails — otherwise two concurrent first-runs both claim the pilot.
- `db.atomic()` is re-entrant (nested blocks join the outer transaction), which is what makes route-level wrapping of capability-level claims safe.

# Learning grounding
- Only human-approved knowledge grounds questions/answers/seeding; seeding requires a ratified domain. Tests exercising ladder levels above recall must seed approved knowledge or the rubric fails closed to recall.
