---
name: FORCE detectors & RIPPLE simulation
description: Story Force engines 11-17 as ASSAY instruments and the world-graph blast-radius simulation (M16/M17).
---

# FORCE (engines 11–17)

- All seven detectors are **deterministic** (lexicon/metric based, no LLM), Tier 2, registered as ASSAY Engine Contracts.
- **Shadow-on-first-seed-only**: seeding moves them advisory→shadow ONLY when the instrument has zero certification events. An author's deliberate demotion is never overridden by a re-seed.
- **Why:** "starting in shadow" is a guarantee; but the author's certification authority outranks the system default.
- If shadow entry fails for any reason other than a genuinely lost CAS race (verified by re-reading the instrument), the seed must raise — never leave a FORCE instrument silently advisory.
- **Story-level findings must carry grounded quoted evidence** (a verbatim quote from a representative chapter + evidence_chapter ref). The contract forbids evidence-free detections — an empty quote on a story finding is a review-blocking violation.
- Chapter-scoped runs always compute full-book context but report findings only for the requested chapter; story-level findings appear only on book runs (never duplicated per chapter).
- Motif/candidate ranking needs frequency as a tiebreaker when presence counts tie, or a dominant signal can lose its slot under a cap.

# RIPPLE (world-graph blast radius)

- Read-only, deterministic simulation: seed by node / canon fact / name (exactly one selector), bidirectional BFS over ATLAS edges, shortest evidence path retained per node.
- **Determinism rule:** sort seeds and edges before walking — set/hash iteration order must never decide which shortest path is retained or the output ordering. Covered by a run-it-thrice equality test.
- **Honest truncation:** loader ceilings (node/edge caps) and over-cap seed sets must mark the report `truncated` — a capped load must never present as a complete blast radius.
- Every reported affected chapter and downstream fact carries the evidence path of its shallowest carrier node, so consumers can trace each impact to the seed.
- Refusals are loud (RippleError → 422): missing graph, unknown seed, ambiguous/empty seed spec — never a silently empty report.
- Seed facts are never reported as their own blast radius; a chapter ripple never reports the edited chapter as affected by itself.
- Surfaces: BAND edit dialog shows blast radius before an edit; canon page has per-fact "Preview ripple" (series facts need an explicit book scope).
