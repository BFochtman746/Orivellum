---
name: Collections / series / canon-domain layers
description: The three distinct grouping layers, domain fact visibility, conversion ledger rules, and the canon-binding guards on collection membership.
---

# Three distinct layers (never conflate)

- **book_collection** — reader/production family (branding only). Holds whole series AND standalone works, many-to-many. NOT the provenance `collection` table (import bookkeeping, guarded by `db.assert_not_collection`).
- **series** — strict ordered membership. `series_member.volume` is the ONLY authority order; `chronology_order` / `publication_order` / `relationship_type` are descriptive, freely editable with no canon guard (`SeriesStore.set_member_orders`).
- **canon_domain** — shared universe. Facts scoped to exactly ONE of work/series/domain (exclusive; enforced in `_check_series_scope`). Domain facts bind every served book via the single shared `FACT_VISIBILITY_SQL` (8 positional args via `fact_visibility_args`; paths: direct work, series, or collection membership → `domain_serves_work`).

**Why:** the spec's core law — collections are branding, series order is authority, domains are shared canon. Mixing them silently rebinds facts.

# Canon-binding guards on collection membership

Collection membership is "branding only" ONLY while no fact-bearing domain serves the collection. Guards in `CollectionStore` (structure_store.py):
- `add_member` refuses when a domain with active facts serves the collection unless `confirm_canon_binding=True` (joining would silently bind that canon).
- `remove_member` checks EVERY fact-bearing domain (not just the first) and every remaining path (direct membership, other collections) before allowing removal.

**How to apply:** any new path that adds/removes collection or domain members must re-check reachability for ALL active-fact domains, under the write lock.

# Conversions

- `ConversionService` (structure_store.py): standalone→series, per-item canon promotion (retract + establish at new scope — NEVER a supersede, supersede keeps scope), ledgered in `conversion_ledger`, reversible via `reverse(ledger_id)`.
- Every forward/reverse operation is wrapped in `db.atomic()` so membership + ledger + fact changes commit together (fault-injection tests in tests/test_collections_domains.py prove rollback).
- Overrides never promote — an override is the book's departure, not shared canon.
- `recommend_classification()` is deterministic code, never a model; it recommends, the author decides.

# Gotchas

- `SeriesStore.series_for_work` returns keys `series_id`/`series_title` (NOT `id`/`title`).
- `/works/{id}/scopes` (works.py) feeds the Book tab ScopeStrip; `/works/{id}/collections` is the OLD provenance endpoint — leave it alone.
