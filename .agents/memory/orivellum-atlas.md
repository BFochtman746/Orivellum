---
name: ATLAS-O world graph
description: Durable constraints for the typed story world graph — evidence grounding, verification, rebuild hygiene.
---

# ATLAS-O world graph — durable constraints

- **Never strip chapter text before grounding.** Offsets must index into the chapter text exactly as stored; strip only for emptiness checks. **Why:** a leading-newline manuscript shifted every offset. **How to apply:** any new grounding consumer takes raw `book_chapters.text`.
- **Stored evidence must be the verbatim original span.** When grounding falls back to whitespace/case-normalized matching, persist the exact original-text substring at the found offset — never the model's version of the quote. Otherwise stored quotes don't occur at their recorded offsets and the audit guarantee breaks.
- **Discard, never coerce.** Out-of-schema types, ungroundable quotes, and unverified inconsistency proposals are dropped; closed node/edge sets are enforced in Python AND SQL CHECKs (nonblank quotes, nonnegative offsets) so raw-SQL bypasses fail too.
- **Cross-chapter findings are only stored after two-stage verification:** deterministic grounding of BOTH quotes plus a temp-0 verifier confirm.
- **Stage, then commit.** Never delete a chapter's graph rows before the LLM work succeeds — stage all extraction/verification results in memory and replace stored rows only after every call succeeded. A gateway failure must raise (AtlasLLMError), never return empty results, or an outage silently erases good data.
- **Rebuild hygiene:** builds serialize per work (in-process lock); a partial (per-doc) rebuild must re-verify all downstream chapters (their prior world state changed) or stale findings survive; emptied/blank chapters must still be enumerated so their graph rows get purged — filtering them out of the rebuild query silently preserves stale data.
- **Graph payload contract:** the legacy work-graph view merges ATLAS rows on every return path with a reserved share of the node budget, and every returned edge endpoint must exist in the returned node set (filter edges after the final node slice).
- Fiction chapter harvest feeds the ATLAS graph and no longer writes characters/relationships to the legacy entities store; global graph + memory recall still read legacy entities (follow-up task exists).
