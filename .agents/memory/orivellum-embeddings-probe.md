
## Probe & reindex failure surfacing (Aug 2026)
- The probe must call embed_texts(bypass_cooldown=True): resetting the breaker first is racy (a concurrent failure can reopen it between reset and call), and without bypass a probe during the 60s cooldown short-circuits to "no vectors" even after recovery.
- run_full_reindex DELETEs all vectors up front, so any early stop (endpoint dies mid-run OR worker exception) must persist a `reindex_error` DB setting; /system/reindex/status returns it as `error`, web SemanticSearchCard shows a rust banner. trigger_reindex clears it at start.
- count_embeddable_items "done" counts must be eligibility-matched DISTINCT joins to the source tables — raw COUNT(*) over vectors counts orphans/dupes/rejected-knowledge vectors and masks incomplete reindexes.
- Batch embed calls must scale timeout with batch size and split adaptively: a 16-text batch on the 8B GGUF embedder exceeds the flat 30s timeout, which killed full reindexes at 0. _embed_batch_resilient: size-scaled timeout, fixed-string canary (never batch content), bisection, 1500-char truncation retry, per-call failure budget.
