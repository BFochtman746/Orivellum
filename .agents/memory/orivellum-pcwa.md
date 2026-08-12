---
name: PCWA absence detectors
description: Design rules for the four PCWA mechanisms over the world graph, machine-proposed completeness assertions, and measured-demand severity.
---

# PCWA absence detectors (world graph)

- Four deterministic mechanisms in `capabilities/pcwa.py` (zero model calls): functional closure + card_k oracle → machine-PROPOSED completeness assertions; mined max cardinality + peer-group closure → gaps via `create_or_refresh_gap`.
- **Region identity is node-ID keyed** (`{node_id}|{edge_type}`), never name-keyed. **Why:** node names are not unique — a name-keyed scope lets ratifying one entity's closure dismiss a same-named entity's gaps. If re-extraction replaces a node, the old assertion fails OPEN (never matches again), which is safe.
- Cardinality oracles must include **zero-value members** (LEFT-join thinking: subjects come from `graph_node`, not `graph_edge`), gated on class prevalence (≥ the max-cardinality support share) so niche relations don't flag a whole class.
- Completeness lifecycle: `propose_completeness` (status `proposed`, never suppresses gaps, refuses when a signed active/retracted row exists — human decisions are final); `ratify_completeness` is an atomic claim under `db._lock` (status check + promote in one critical section — a route-level read-then-write is a double-sign race). Declining = `retract_completeness` (now also claims `proposed` rows).
- Severity blocking is **measured demand only** (`DEMAND_BLOCKING` in gap_engine): user query traffic + `knowledge_retrievals` injection log via `demand_count`. The old `blocking_active_work` hand flag is gone; unmeasured detectors get demand clamped below threshold with `blocking_suppressed` meta.
- Relation metadata (`graph_relation_meta`, v143) is re-derivable — every mining pass replaces the Work's rows wholesale; never treat it as authored state.
- **How to apply:** any new graph-derived detector should reuse the pair-scoped `graph_pair` gap class so one assertion closes a region against every mechanism, memoize per-entity demand lookups, and stratify reports rare/common by node degree with rare marked low-confidence.
