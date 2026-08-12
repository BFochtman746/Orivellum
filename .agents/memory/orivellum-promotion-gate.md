---
name: Promotion gate & lifecycle provenance
description: Promote-to-Book readiness predicates, documents.lifecycle_by provenance, and honest completeness rules
---

# Promotion gate & lifecycle provenance

**Rules:**
- Promote-to-Book (new pipeline) requires all three predicates from `capabilities/readiness.py`: ≥1 manuscript doc, GENESIS G8 PASSED, and a canonical manuscript with `lifecycle_by='author'`. Refusals are 422 with `detail={message, reasons, checks}` — never bare.
- The existing-check + eligibility check + insert all run inside `create_book_pipeline(..., require_ready=True)` under the writer lock (raises `PromotionRefused` carrying the eligibility dict). Never re-implement the gate at the route level — that reintroduces the duplicate-pipeline race.
- `update_document_lifecycle` requires an explicit `actor` (no default). Canonical + manuscript + actor≠'author' raises. `lifecycle_by` NULL (legacy) intentionally does NOT count as author provenance.
- auto_dedup pair guards (`_pair_refusal`: cross-doc_type, manuscript, book-pipeline Work, lookup-failure=refuse) must be evaluated inside the same `db.atomic()` block as the resolution — check-then-act outside the lock is a TOCTOU bug.
- Completeness report has NO overall score, NO readiness label, NO default denominators. Targets come only from author-set `works.meta.completeness_targets`; coverage is the Chao1 upper bound.

**Why:** RE-PROJECTION Phases 7–8 — the system must refuse to lie about readiness; an assumed denominator or system-signed canonical is a rejected outcome. Architect review specifically flagged the promotion race, the implicit author default, and the dedup TOCTOU.

**How to apply:** any new caller designating lifecycle must pass an intentional actor; anything creating pipelines goes through `create_book_pipeline(require_ready=True)`; any new automated mutation of docs must re-check its refusal predicates inside the write transaction.
