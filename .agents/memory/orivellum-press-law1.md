---
name: PRESS one-manuscript consolidation
description: PRESS reads real chapters from book_chapters; press_epigraph is the only press-side chapter state
---

# PRESS one-manuscript (LAW 1)

Rule: chapter prose lives only in `book_chapters` (main DB). PRESS opens the main DB read-only (`file:...?mode=ro`) via the press book's `work_id` and computes word counts from actual text. Chapter numbers = stored `seq` normalized to 1-based (`seq - min(seq) + 1`); duplicate/gapped seqs fail the `chapters_contiguous` verify check instead of being papered over.

**Why:** the old `press_chapter` table was a second, hand-typed chapter model (typed word counts, fake contiguity) — audit D-04 flagged it as dishonest verification.

**How to apply:**
- PRESS may only own presentation state (`press_epigraph`, keyed book+number). Never reintroduce press-side prose or typed word counts.
- Slots keyed by number go stale when the manuscript is renumbered/relinked: verify has `epigraph_slots_valid`, `get_book` exposes `orphan_epigraph_slots`, and clearing a slot must always be allowed even when its chapter is gone.
- Legacy table lives on as `press_chapter_legacy` (migration in `cmd_init`, idempotent, ledger-noted).
- Env quirk: plain `uv run` re-syncs WITHOUT dev extras and removes ruff — prefix every gate chain with `uv sync --extra dev -q`.
- API login body is `{"key": $SESSION_SECRET}`, not `{"password": ...}`.
