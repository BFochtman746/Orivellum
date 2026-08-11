---
name: ATLAS-O world graph
description: Typed story world graph (LAW 2) — closed schema, evidence grounding, cross-chapter verification, harvest integration.
---

# ATLAS-O world graph

- Tables: `graph_node` (7 closed types), `graph_edge` (20 closed edge types in 5 groups, edge_group derived from edge_type in `db.create_graph_edge` — callers never pass it), `graph_inconsistency` (verified cross-chapter contradictions, quote+offset on BOTH sides). Closed sets enforced in Python AND SQL CHECKs, plus CHECKs for nonblank quotes / nonnegative offsets.
- **Never strip chapter text before grounding.** Offsets must index into `book_chapters.text` exactly as stored; use `.strip()` only for emptiness checks. **Why:** a leading-newline manuscript shifted every offset in review.
- `ground_quote(quote, text)` — exact match, then whitespace-normalized case-insensitive fallback mapped to a real offset. Ungroundable or out-of-schema extractor output is DISCARDED, never coerced (LAW 3).
- Extraction = 3 passes (events → entities → relations) + attribute pass, all temperature 0.0 via the `llm_call` gateway (`atlas.*` purposes). Long chapters run in overlapping 16k windows (`_windows`); duplicates across windows are skipped silently — only schema/grounding rejections count as "discarded".
- Cross-chapter verification is two-stage: deterministic grounding of BOTH quotes, then a temp-0 verifier call must return `confirmed`. Unverified proposals are never stored.
- Builds are serialized per work by an in-process lock (`_work_build_lock`) — concurrent builds would interleave the per-chapter delete+rebuild.
- The chapter harvest (`llm_harvest_by_chapters`) triggers `build_work_graph` (gated by `atlas_enabled` setting, default true) and NO LONGER writes fiction characters/relationships to the legacy entities store. `db.get_work_graph` merges ATLAS rows via `_merge_atlas_graph` on every return path so the graph UI keeps showing them. **How to apply:** any new graph consumer should read graph_node/graph_edge for fiction; global graph + memory recall still read legacy entities (follow-up).
- `delete_graph_for_chapter` drops rows raised BY that chapter but keeps inconsistencies where it is only the prior side.
- Canon linkage: node name (≥4 chars) whole-word match against active canon fact statements → `canon_fact_id`.
