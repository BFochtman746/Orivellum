---
name: Series continuity review
description: Evidence-backed whole-series continuity review pipeline — ledgers, comparators, coverage manifests, durable runs.
---

# Series continuity review (capabilities/series_review.py)

- **Scope is snapshotted at run creation** (stored in `review_run.params["scope"]`). Reconcile and the coverage manifest consume ONLY that snapshot — never live series membership.
  **Why:** a membership change while a durable run is paused could otherwise let the manifest claim coverage of books the run never built ledgers for.
  **How to apply:** any new step or report that needs the run's book list must read the snapshot, not `resolve_scope()`.

- **Span verification at ledger build:** any passage-cited item whose quote is not found in the chapter text is flagged `span_unverified`, excluded from all comparators, named in the manifest's unreviewed regions, and forces `partial=true`. Test fixtures must seed quotes that actually appear in the chapter text (helper appends them).

- **Canon-fact evidence is never presented as a passage span** — spans with `chapter_id=None` carry `source:"canon"` + `source_ref` instead of an empty quote.

- **Coverage honesty rule:** ANY exclusion (skipped/failed/stale chapter, missing ledger, unverified span) forces the partial label. There is no code path that upgrades a run to "full" after the fact.

- Findings identity = `dedupe_key` stable across runs; author dispositions (intentional/dismissed/resolved/deferred) are inherited on re-run and never resurrected as open. Ledger rebuilds carry approved/rejected forward by `item_key`; rejected items never feed comparators.

- Runs execute as operations-runner steps (`series_review.ledger` per book + `series_review.reconcile`); route creates the operation directly via `store.create_operation` (per-step `work_id` params are legitimate here). If `start_operation_run` returns False, the run must be marked `failed` — never left `running`.

- `chapter_vs_book` and `change_impact` REQUIRE a `chapter_id` (422 otherwise); UI shows a chapter picker fed by `GET /works/{id}/chapters`.

- Terminology comparator groups by a space-squashed key ("Black-water Keep" vs "Blackwater Keep" is exactly the rename to catch); canon subjects strip years so year-only differences collide in the timeline comparator; HISTORICAL canon raises severity to critical.
