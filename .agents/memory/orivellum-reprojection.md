---
name: RE-PROJECTION quarantine & domain re-harvest
description: Per-domain closed ontologies, quarantined_reprojection status semantics, and the pilot-gated re-harvest design.
---

# Quarantine status (`quarantined_reprojection`)
- Migration quarantined ALL auto/ai_auto knowledge (prior status kept in `meta.$.pre_quarantine_status`); approved items survive and follow their source doc's current work_id.
- **Why:** legacy harvests coerced content into a wrong universal schema; the items are kept as evidence, never re-served.
- **How to apply:** any read path that grounds AI output, search, graphs, or stats must exclude it. Eligibility predicates are SCATTERED across many raw-SQL sites (context compiler, system search FTS, suggestions, MCOS sampling, evidence, gap engine, coverage, completeness, book intelligence, trailer, nightshift, graph fallbacks, embeddings). When adding a new excluded review_status, `grep -rn "review_status"` across src and patch every inline predicate — the DB helper methods alone are NOT sufficient.
- Explicit `review_status_in` allowlists remain the only way to read quarantined evidence.

# Per-domain closed ontologies (capabilities/ontology.py)
- narrative kinds mirror `atlas.NODE_TYPES` lowercased — keep them in lockstep (test enforces it).
- `is_kind_allowed(kind, None)` is True: domain-less (legacy) Works stay ungated by design.
- Off-schema model output is discarded AND counted, never coerced — everywhere (rules harvest, LLM harvest, chapter harvest, re-harvest).
- Acceptance invariant: `find_ontology_violations(db) == []` (also `GET /api/ontology/violations`).

# Re-harvest run coordination (capabilities/reharvest.py)
- Run claim = `reharvest_status:<work_id>` setting with a UUID **fencing token**; 2 h stale reclaim. Finalization (report write + release) happens in one `db.atomic()` and only if the token still owns the claim — a reclaimed stale worker's results are discarded, never clobbering the newer run.
- Pilot gate: pilot-claim CAS + run claim must sit in ONE `db.atomic()` block in the route, with rollback of the pilot claim if background dispatch fails. Two concurrent first-runs otherwise both claim the pilot.
- `db.atomic()` is re-entrant (nested blocks join the outer txn), so capability-level `claim_run` can be safely wrapped by route-level atomic blocks.

# Learning grounding
- Only `approved` knowledge grounds questions/answers/seeding; Learn seeding 409s for domain-less Works. Tests seeding knowledge for ladder levels above recall must use `review_status="approved"` or the rubric fails closed to recall.
