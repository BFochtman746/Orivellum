---
name: Content-derived Work proposals (RE-PROJECTION Phase 4)
description: Clustering pipeline that proposes Works from document embeddings; signed ratification flow in the review queue.
---

# Content-derived Work proposals

**Rule:** A Work only ever comes into existence through signed ratification (author signature + ontology domain from `VALID_DOMAINS` in `capabilities/work_proposals.py`) or the explicit manual create path. Proposal generation never creates Works and never mutates the substrate (documents/chunks/vectors).

**Why:** The subject taxonomy must be author-governed; automated clustering is evidence, not authority.

**How it works:**
- Cluster within each collection first (cosine k-means, k≈round(√n)), then merge cross-collection ONLY across different origin collections (centroid cosine ≥ 0.80). Same-collection pairs are never merged directly — k-means just split them. `MIN_CLUSTER_SIZE` gate applies AFTER the merge so per-collection fragments (even singletons) can combine.
- Names are content-derived (LLM best-effort → TF-IDF fallback); filenames are never naming inputs.
- Proposals upsert by deterministic fingerprint (sha over sorted member doc_ids); refresh only rows still `status='proposed'` — ratified/rejected are never clobbered.

**Ratification (review.py `_resolve_work_proposal`):**
- All 4xx gates (decision ∈ {approve, reject}, signature, domain) run BEFORE any claim, so a rejected validation leaves the proposal queued.
- Approve runs claim + create_work + member re-points + provenance + finalize inside ONE `db.atomic()` block — any failure rolls back everything including the claim (no compensating revert; the author retries).
- The proposal row is snapshotted INSIDE the transaction AFTER the claim (a concurrent generation refresh can update a proposed row right up to the claim; the Work must be built from exactly the claimed row).
- Member re-point uses `db.assign_document_to_work_if_eligible()` — one conditional UPDATE with all eligibility predicates (work_id IS NULL, quarantine, tier, generated, lifecycle) so docs are never stolen (no read-then-write TOCTOU).
- After commit, bump the chunk vector cache ONCE for the batch — cached chunk entries carry `d.work_id` from the JOIN, so work-scoped semantic search stays stale otherwise.

**Provenance:** `work_collections` table records which collections contributed how many docs; shown on the Work detail documents tab.

**Lesson (lint ratchet):** `scripts/check_lint_ratchet.py` re-lints with `--isolated`, so the default mccabe C901 threshold (10) applies even when project config is looser — a `ruff check` pass does not guarantee the ratchet passes.
