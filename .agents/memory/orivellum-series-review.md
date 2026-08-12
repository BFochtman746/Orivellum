---
name: Series continuity review
description: Durable-run and coverage-honesty rules for whole-series continuity reviews.
---

# Series continuity review — durable lessons

- **Durable jobs must snapshot their inputs at creation.** The review scope (book list) is stored on the run; reconciliation and the coverage manifest consume only that snapshot.
  **Why:** series membership can change while a durable run is paused — resolving live membership at reconcile time let a manifest claim coverage of books the run never checked.
  **How to apply:** any new long-running job whose result claims "what was checked" must persist its input set up front and never re-resolve it.

- **Evidence must be verified where it is consumed, not just where it is created.** Even though ATLAS grounds quotes at extraction (LAW 3), text can change afterwards — the ledger re-verifies quote AND exact offset (`text[offset:offset+len(quote)] == quote`) against current chapter text; anything less is excluded from comparators AND forces a partial-coverage label. A quote merely *appearing somewhere* in the text is not verification. Non-passage evidence (canon facts) is explicitly labeled as such, never presented with an empty quote as if it were a manuscript span.

- **Cross-entity comparators can serve within-entity modes via segment keys.** chapter_vs_book splits one book into ordered pseudo-books (before / target chapter / after) with a distinct `seg` key while spans keep the real work_id — the same comparators then detect within-book drift without duplicated logic. Comparator "different book" guards must compare `seg`, never work_id directly.

- **Route-side lifecycle transitions around background submission must be CAS.** Set the "running" state BEFORE handing the operation to the runner (guarded on the prior state); a post-submit unconditional write can overwrite a fast job's "done" and leave the record spinning forever. On admission failure, fail BOTH the domain record and the pending operation (`fail_pending_operation`) — a lingering pending op blocks scheduler admission.

- Coverage honesty rule: ANY exclusion (skipped/failed/stale chapter, missing ledger, unverified span) forces the partial label; there is no path that upgrades a run to "full" after the fact. Scope inputs are validated up front — nonexistent series, empty series, and chapters not belonging to the requested work are refused rather than producing an honest-looking zero-finding "full review".
