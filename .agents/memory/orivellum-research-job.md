---
name: Runner research job
description: --job research on the orivellum-runner harness — sourced-claim doctrine, injection containment, resume/artifact reconciliation
---

# Runner `--job research`

Third job module (`runner/jobs/research.py`) beside code/xlsx. Target is a
**topic string**, not a path: modules may set `PATH_TARGET = False` and
`cli.cmd_run` skips `Path.resolve()` for them.

## Rules that must survive future edits
- **The model proposes, code disposes.** `verify_claims()` checks quotes
  against the text of the *cited* source only (per-source blocks from
  `split_context_by_source`), never the combined context — otherwise a claim
  can cite S1 while quoting S2. One unknown cited id rejects the whole claim
  (no silent trimming).
  **Why:** an architect review caught both laundering paths on first build.
- **Injection screening must contain, not just log.** `screen_sources()`
  drops tainted source blocks and their citations *before* the model call and
  records them in `digest.excluded_sources`. Detect-and-log lets a hostile
  passage yield a "verified" claim.
- **Model narrative (summary/not_found) stays in the digest, attributed;**
  curriculum `why` and report text are deterministic — never rendered from
  unverified model prose.
- **Artifacts are crash-reconcilable:** per-gap digests written atomically
  (tmp+rename), and `final_pass` regenerates all `digests/gap-*.json` from
  the checkpoint DB, so artifact/checkpoint can never disagree after a kill.
- **Failed ≠ thin:** planned-but-never-researched gaps (e.g. missing
  TAVILY_API_KEY) get `GAP-UNRESEARCHED` findings and a coverage line;
  missing key fails units, never fakes.

## Reuse seams
- Corpus inventory: read-only sqlite (`file:...?mode=ro`) over the Orivellum
  DB (`ORIVELLUM_DB`), FTS over knowledge_fts/chunks_fts + documents titles;
  every table read guarded — missing tables become notes.
- Websearch: lazy sys.path insert of `ORIVELLUM_SRC`, then
  `orivellum.capabilities.websearch.research_web` (stdlib-only imports at
  module level, verified safe). Context format `[S#] title\ntext\n\n` is the
  contract `split_context_by_source` parses.
- Writeback to the knowledge table is deliberately absent — ships with the
  downstream review-gate task; `research_digests.json` is its input.
