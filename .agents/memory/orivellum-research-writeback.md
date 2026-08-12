---
name: Research writeback & review gate
description: How external research claims enter the corpus, the question-safe review allowlist, plan import, and incremental concept re-seeding.
---

# Research writeback with a review gate

**Rule:** external/web-derived claims always land as knowledge with `review_status='proposed'` and may never ground a learning question or answer key until ratified to `approved` through the normal review CAS flow.

**Why:** unverified web material silently becoming exam material was the audit's core finding — writeback and the review filter must ship together, never separately.

**How to apply:**
- Question/grading grounding goes through `_QUESTION_SAFE_REVIEW = ("auto","ai_auto","approved")` — an allowlist (fails closed for unknown statuses), enforced in SQL (`review_status_in` param on `search_knowledge`/`list_knowledge`) AND re-filtered in Python (defence for DB fakes).
- `proposed` is a valid review status; the review route's claim-first CAS expected set is `("auto","ai_auto","proposed")`.
- Import provenance is mandatory: a claim without a source carrying a real http(s) URL **and** a retrieval date is skipped, never stored. Idempotency = pre-check by (work_id, text) before create; a ratified/rejected claim is never touched by re-import.
- Plan import (`import_training_plan`): concepts reused by subject; verification question stored in `work_concept_items` (UNIQUE(concept_id, question)); concept lookup+insert under ONE lock hold (race-safe).
- Seeding: whole-corpus distinct-subject SQL (oldest first) is the primary source — never a newest-N row snapshot; concept inserts use guarded WHERE NOT EXISTS because the LLM ordering call leaves a race window.
- Every re-seed ends with `validate_prereq_graph` — deterministic iterative DFS that deletes back-edges (removal, not refusal: a cycle would deadlock eligibility).
- Nightly reseed uses a durable per-work cursor setting (`learning.reseed_cursor.<work_id>`) advanced only after a successful seed — comparing newest knowledge vs newest concept makes processed Works re-qualify forever.
- Import surface is inline JSON only (POST /api/works/{id}/learning/import-research) — no server-side path reading, nothing to traverse.
