---
name: Series continuity (trilogy-wide canon)
description: How series scope makes canon/voice/continuity span books — visibility rule, override lifecycle, membership guards.
---

# Series continuity

- **One visibility rule, used everywhere.** `FACT_VISIBILITY_SQL` in canon_store is the single clause deciding which canon facts a book sees: its own, legacy globals, its series' facts, and facts from strictly EARLIER volumes — minus facts it actively overrides. ConStory, LOOM, and list_facts all consume it, so cross-book behavior stays consistent by construction. **Why:** duplicating the direction logic per consumer is how backward leaks happen.
- **Forward-only, never backward.** Book 1 never sees book 2's canon. Replay folds prior volumes in volume order before the current book's chapters; precedence is fold order.
- **Override lifecycle:** superseding an override inherits its target (a revision stays a departure); retargeting or superseding from another book is refused; `retract` is the ONLY way to restore the series fact for that book. **Why:** a bare supersede used to flip the override to 'superseded' and silently resurrect the series fact.
- **Membership mutations are guarded:** removing a member is refused while its canon binds later volumes or it holds an active override into the series; reordering is refused once any member canon exists ("order is authority"); delete returns `has_continuity` (409) — dismantle latest-volume-first.
- **Cross-book finding labels must be validated:** same series AND source volume strictly earlier — `cf.work_id != nf.work_id` alone counts stale facts from removed members as false drift.
- Voice/persona inheritance: nearest earlier volume wins when the book has none of its own; local approved always beats inherited; unapproved never inherits. `resolve_assay_baseline` in series_store returns provenance (`inherited`, `source_work_id`).
- UI: /series list + /series/:id overview pages in the Writing app; command palette picks them up automatically from the APPS registry.
