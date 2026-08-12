---
name: Content-derived Work proposals & signed ratification
description: How subject clusters become Works — governance rules, atomicity, and cache invalidation lessons.
---

# Content-derived Work proposals

**Rule:** A Work only ever comes into existence through signed ratification (author signature + ontology domain) or the explicit manual create path. Automated clustering only *proposes*; it never creates Works and never mutates the document/chunk/vector substrate.
**Why:** The subject taxonomy must be author-governed — clustering is evidence, not authority.
**How to apply:** Any new pathway that would create a Work from derived data must route through the signed review-queue ratification, never create directly.

## Durable design decisions

- **Cluster within each collection first, then merge only ACROSS different collections.** Same-collection cluster pairs are never merged directly — the within-collection split just separated them, and re-fusing would undo it. Cross-collection agreement is the genuineness signal; single-collection proposals are allowed but carry weaker evidence. Minimum-size gates apply AFTER the merge so small per-collection fragments can still combine.
- **Names come from content only** (model best-effort, deterministic TF-IDF fallback). Filenames are never naming inputs.
- **Idempotency via deterministic fingerprint** over sorted member ids; upserts refresh only still-proposed rows — resolved rows are never clobbered.

## Ratification correctness lessons (each caught by review)

- All 4xx validation gates run BEFORE any claim so a rejected validation leaves the proposal queued.
- Claim + Work creation + member re-points + provenance + finalize belong in ONE transaction — a compensating revert after separate commits leaves orphaned Works behind on partial failure.
- Snapshot the proposal row INSIDE the transaction AFTER the claim: a concurrent generation refresh can update a still-proposed row right up to the claim, and building from a pre-claim read makes the ratified row disagree with the Work it produced.
- Re-point members with a single conditional UPDATE embedding all eligibility predicates — a read-then-write re-check can steal a doc assigned in between.
- After commit, bump the chunk vector cache once for the batch: cached chunk entries carry the joined work_id, so work-scoped semantic search silently misses the new Work's content until invalidated.
- A derived pipeline is incomplete until it runs somewhere operational (nightshift pass + a visible UI trigger) — an endpoint nobody calls does not ship the feature.
