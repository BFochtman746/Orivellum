---
name: PDF→Excel transcription protocol
description: Design rules for the PDF→Excel Protocol v2.1 capability (dual-channel extraction, fail-closed gates, auto workbook review on upload)
---

# PDF→Excel Protocol v2.1 capability

Transcription publishes as v1 of a Workbench project (verdict "transcribed"), so versioning/review/download/analysis all reuse Workbench machinery — never build a parallel storage path.

**Fail-closed rules (all learned via an architect review that failed the first cut):**
- **Never truncate silently.** Oversized tables/narrative must raise, and the completeness gate certifies exact per-page row counts and narrative character lengths against extraction totals recorded in the manifest. A partial workbook must never publish as "verified".
- **Channel comparison must be occurrence-aware and bidirectional.** Set-membership checks have false negatives (a wrong value passes if the same number appears elsewhere on the page). Use per-page Counters of numeric tokens; A-only surplus → disagreement exceptions, B-only surplus → possible-omission exceptions. Duplicate occurrences need duplicate corroboration.
- **Cap parser work up front.** 30 MB upload cap does not bound PDF work: enforce page-count / total-text / total-table-cell ceilings incrementally during extraction, and an exception-count ceiling (flood = extraction untrustworthy → reject).

**Why:** the protocol's core guarantee is "verified or refused" — any silent-partial path breaks it.

**Other decisions:**
- Exception row always beats a guessed value (O-3); disagreements never averaged.
- Checks sheet uses real cross-sheet formulas so the independent-recalc gate (`formulas` engine, zero error cells) has teeth against renamed/missing sheets.
- Auto workbook review: every Workbench upload gets a claim+analysis dispatched after import (skipped silently if busy); Library .xlsx uploads spawn a Workbench review project, gated by DB setting `workbench_auto_review` (default on).
- run_transcription chains run_analysis while holding the build claim; run_analysis releases it in its finally. Failure paths must release the claim and record last_error themselves.
- Tests that import via the route must patch `orivellum.api.executor.submit_bg` (return False to drop the auto-review) or the project stays "building".
