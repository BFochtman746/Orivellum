---
name: PCWA absence detectors
description: Durable design rules for graph-based absence detection, machine-proposed completeness, and measured-demand severity.
---

# PCWA absence detectors (world graph)

- Region identity for (entity, relation) pairs is **node-ID keyed, never name-keyed**. **Why:** entity names are not unique — a name-keyed completeness scope lets ratifying one entity's closure dismiss a same-named entity's gaps. If re-extraction replaces a node, an id-keyed assertion fails OPEN (detection resumes), which is the safe direction.
- Cardinality-style oracles must consider **zero-value members** (subjects come from the node table, not the edge table), gated on class prevalence so a niche relation doesn't flag the whole class.
- Machine-inferred completeness is **proposal-only**: proposals never suppress gaps and never auto-ratify; a signed active OR retracted row is a human decision the machine may not touch. Promotion to active must be an atomic claim (status check + promote in one critical section) — a route-level read-then-write is a double-sign race.
- Mined relation statistics are **re-derivable snapshots**: every detection pass must re-mine before judging, or scans run after new extraction emit from stale statistics. Never treat the stored snapshot as authored state.
- Blocking severity comes **only from measured demand** (query + retrieval traffic); the old hand-written blocking flag is gone. Unmeasured detectors get demand clamped below the blocking threshold with suppression recorded in meta.
- **How to apply:** new graph-derived detectors should reuse the shared pair-scoped gap class so one assertion closes a region against every mechanism, memoize per-entity demand lookups, and stratify reports rare/common by node degree with rare marked low-confidence.
