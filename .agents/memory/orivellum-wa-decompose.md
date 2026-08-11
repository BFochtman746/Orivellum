---
name: Writing Architect decomposition (Pipeline M0)
description: How the WRITING_ARCHITECT archive is turned into machine-readable records; invariants future milestones must keep
---

# Writing Architect decomposition (M0)

- Capability `capabilities/wa_decompose.py`, store `database/wa_store.py` (MailStore pattern), routes `/api/wa/*` (require_auth), schema v122 (wa_archive_docs / wa_records / wa_canon_proposals).
- **Invariant 1 — proposal-only canon:** BIBLE_DATA, Story Bible, and Book Bible content NEVER writes canon authority. Everything lands in `wa_canon_proposals` at status `proposed` (HISTORICAL/INFERRED/INVENTED; research frontmatter → HISTORICAL default). Only PATCH /api/wa/canon-proposals/{id} changes status.
- **Invariant 2 — no silent loss:** every archive file gets an explicit disposition: extracted, deduped (byte-identical, duplicate_of set), or deferred with a written reason. Coverage report at data/wa/coverage_report.{md,json}.
- **Invariant 3 — re-run safety:** inventory + records are wipe-and-rebuild per run; proposals use deterministic content-hash ids + INSERT OR IGNORE so author ratifications survive re-runs.
- Duplicate pairs (`NAME__1.docx`, `NAME 2`): canonical = largest file; differing variants are deferred "needs manual reconciliation" (task queued).
- ENGINE_INDEX operator table (File/Purpose/When to Call/Runtime Status) backfills engine contract metadata; certification is runtime status like "On-Demand"/"Runtime Core", not PRESERVED-style keywords.
- Real archive: 207 files → 143 extracted, 64 deferred, 131 records, 99 proposals, ~4s.

**Why:** downstream milestones (canon authority table, context compiler, ASSAY) consume these tables; breaking invariant 1 or 3 would grant unratified authority or destroy author decisions.
**How to apply:** any consumer of wa_canon_proposals must filter status='approved'; any decomposer change must keep dispositions exhaustive and proposal ids deterministic.
