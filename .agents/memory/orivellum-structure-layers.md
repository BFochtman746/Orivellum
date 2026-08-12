---
name: Collections / series / canon-domain layers
description: The three distinct grouping layers, domain fact visibility, conversion ledger rules, and the canon-binding guards on collection membership.
---

# Three distinct layers (never conflate)

- **book_collection** — reader/production family (branding only). Holds whole series AND standalone works, many-to-many. NOT the provenance `collection` table (import bookkeeping, guarded by `db.assert_not_collection`).
- **series** — strict ordered membership. `series_member.volume` is the ONLY authority order; `chronology_order` / `publication_order` / `relationship_type` are descriptive, freely editable with no canon guard (`SeriesStore.set_member_orders`).
- **canon_domain** — shared universe. Facts scoped to exactly ONE of work/series/domain (exclusive; enforced in `_check_series_scope`). Domain facts bind every served book via the single shared `FACT_VISIBILITY_SQL` (8 positional args via `fact_visibility_args`; paths: direct work, series, or collection membership → `domain_serves_work`).

**Why:** the spec's core law — collections are branding, series order is authority, domains are shared canon. Mixing them silently rebinds facts.

# No-silent-canon-binding rule (applies to EVERY membership path)

Any mutation that changes whether a fact-bearing domain reaches a book is a canon event, not a filing change. That includes indirect paths: adding a work to a series whose series sits in a domain-served collection binds the domain's facts just as surely as joining the domain directly.

**Rule:** every membership mutation (collection add/remove, series add/remove/delete, domain edits) must compute reachability for ALL active-fact domains and all remaining paths, then refuse newly-binding changes without explicit confirmation (`confirm_canon_binding`) and refuse unbinding changes unless an independent path exists.

**Why:** a completion review caught series-level mutations bypassing the collection-level guards — guards on one entry point are worthless if a sibling entry point reaches the same state.

# Conversions

- Per-item canon promotion = retract + establish at the new scope, NEVER a supersede (supersede keeps scope). Overrides never promote — an override is the book's departure, not shared canon.
- Every path that turns a standalone into a series member must land in the same reversible `conversion_ledger` (plain series add ledgered too; ConversionService writes its own richer entry, so composed paths pass `ledger=False` to avoid doubles).
- Forward and reverse operations are single transactions — membership + ledger + fact changes commit together; classification recommendation is deterministic code, never a model.

# Gotchas

- `SeriesStore.series_for_work` returns keys `series_id`/`series_title` (NOT `id`/`title`).
- `/works/{id}/scopes` feeds the Book tab ScopeStrip; `/works/{id}/collections` is the OLD provenance endpoint — leave it alone.
