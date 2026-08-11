---
name: AUTONOMY unattended runs
description: M12 draft-check-revise runner — claims, fail-closed blockers, never-sign rule, halt queue
---

# AUTONOMY (Masterpiece M12) — unattended draft→check→revise

Capability `capabilities/autonomy.py`, routes `api/routes/autonomy.py`, schema v133 `autonomy_run`.

## Rules that must never regress
- **Signatures stay human.** The runner never writes `assay_signature`, never
  passes an author to BAND (`author=""`, `accept_regression=False`). Unsigned
  D15–D17 gates halt + queue. Gate *relevance* is range-based: a gate blocks
  once drafting reaches `gates.GATE_RANGES[key][0]`, and all unsigned gates
  block at completion.
- **Fail-closed blocker evaluation is an ALLOWLIST.** A blocking instrument
  only passes with `status=="done"` and verdict in `("clean","pass")` —
  no_baseline/locked/unknown all block. An errored ConStory or a *crashed*
  battery is a blocker, never an error-only exit.
- **Revision can never erase a blocker.** After bounded BAND edits the FULL
  battery re-runs (`_check_chapter` again); hard blockers skip revision
  entirely. Never trust a partial re-check.
- **BAND edits are quote-grounded.** Stored finding offsets go stale after
  the first edit in a run — locate `contradiction_quote` via `text.find()`
  in the CURRENT text; not found verbatim → refuse the edit (finding stays
  open → halt). Never edit on stale offsets.
- **Run row is the claim** (per-work, `create_autonomy_run` refuses a
  second); every exit finishes it, startup recovery marks orphans. A crash
  still queues a review item + terminal report (best-effort).
- **Halt queue = one `suggestions` row** kind `autonomy_halt` with meta
  {run_id, chapter, reasons, finding_ids}; unified review queue surfaces it
  as `suggestion:<id>` with zero queue-side changes.

## Other decisions
- Budgets: max_chapters/minutes/tokens (tokens = llm_calls rows past a
  baseline id); kill switch `autonomy_enabled` re-read before every chapter.
- `halt_policy` continue must add halted chapter ids to an `exclude` set for
  `_next_chapter`, or the loop respins the same chapter forever (test-caught).
- Nightshift pass 14a dispatches each opted-in work via `submit_bg` (never
  synchronously — a run can burn its whole minute budget); refused dispatch
  releases the claim row as error. Double-gated: `autonomy_enabled` AND
  `autonomy_nightshift_enabled` AND per-work `works.meta.autonomy_optin`.
