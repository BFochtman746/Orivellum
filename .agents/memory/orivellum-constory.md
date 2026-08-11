---
name: ConStory contradiction checker
description: Story-contradiction detection (19 subtypes) — computed severity, storage-boundary grounding, disposition-preserving re-runs, CED metric.
---

# ConStory (narrative_finding)

Checker at `capabilities/constory.py`; findings in `narrative_finding` (schema v125); routes on works.py (`/constory/run`, `/constory/status`, `/findings*`); UI section on the Book Intelligence page.

## Durable rules

- **Severity is computed, never chosen** — by the model OR the caller. `db.create_narrative_finding` has no severity param; it computes `compute_severity(subtype, canon_class)` itself (HISTORICAL canon → critical; INFERRED floors high; INVENTED floors medium; else subtype base). **Why:** spec forbids model-picked severity; enforcing it only in the pipeline left the ledger unguaranteed (code-review finding).
- **LAW 3 lives at the write path** — the DB insert verifies both quotes verbatim at their claimed offsets against real `book_chapters` text, and refuses out-of-schema subtypes. A fact side with `fact_chapter=0` must be canon-backed (`canon_class` required). Ungrounded findings are unstorable, not merely unproduced.
- **Re-runs swap findings in ONE transaction** — `db.replace_open_narrative_findings` (delete-open + inserts inside `db.atomic()`), so a disposition PATCH can never land mid-swap. Stable sha1 dedupe key (subtype+chapters+offsets), UNIQUE per work: dispositioned findings never resurrect as fresh 'open' rows.
- **Claim-before-dispatch** — `try_claim_run(work_id)` marks the in-memory run status 'running' synchronously in the route, before `submit_bg`. **Why:** otherwise a second POST double-starts and the UI's first status poll sees null, disables polling, and misses the whole run. Release the claim if the executor refuses.
- **Window long chapters** — reuse atlas `_windows` for extract AND pair passes; ground against the FULL chapter text. A single `_fence` truncates at 16k and silently blinds the checker to late scenes.
- **Fail loud on canon load** — a run that silently skipped canon checking would report a clean bill of health it never earned.
- **CED** (contradiction error density) = findings per 10k words; excludes `intentional`/`wontfix` (author-declared non-errors), includes `fixed`.
- Dispositions use the conditional-UPDATE claim pattern; deliberately NOT wired into the generic `/api/review` inbox (approve/reject doesn't map to the 4 dispositions).
