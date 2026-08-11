---
name: LOOM chapter drafting engine
description: B5 drafting loop — character agents, critic gate, narrator, beat compliance controller, entropy gate, world state, provenance.
---

# LOOM (capabilities/loom.py, routes/loom.py, schema v128)

## Core rules
- **Beat controller escalates, never rewrites.** No emergent goals: the goal is always the chapter contract's beat (`book_chapters.meta['contract']`). Stall (no critic-accepted action), word-band misses, and beat drift become `create_finding(kind='loom_escalation', severity='high')` on the book pipeline (or the chapter if none) — the story text is never changed by the controller.
  **Why:** MAGNET's goal generator replaced by the blueprint per spec; author stays the authority.
- **Critic never skipped; drafter never judges itself.** Drafter = workhorse, critic = reasoner (DB overrides respected); equal models → refusal. Malformed agent output is not an action — it consumes a bounded attempt without reaching the critic (documented deliberately).
- **Narrator selection is strict.** `selected` must be a list with ≥1 valid index; missing/malformed/empty → LoomError, run 'error', nothing persisted. NEVER default to "all accepted actions" — only selected actions' world updates commit.
- **Approved chapters never overwritten — atomically.** `_store_draft` re-checks status inside ONE transaction (status check + revision insert + text update); approval mid-run discards everything. Store runs BEFORE world-state commit so a refused draft leaves the world untouched.
- **World state = overwrite semantics** (`loom_world_state` PK(work_id,key)). Replay from `graph_node` (joined to chapter seq, folded in order) whenever state is empty OR polluted with entries `source_chapter_seq >= current seq` (re-drafting must not see its own/future state). Sequential drafting trusts the accumulated table — replay would discard critic-emitted updates that aren't graph-backed.
- **Entropy gate:** llm.py now returns `call_id` (llm_calls rowid) + `logprobs` (request via `extra={"logprobs": True}`). No logprobs → `{"available": false}`, never fabricated. Hot spans (sliding-window mean NLL > 2.5) get temp-0 verification vs canon+state before storing; failures → findings.
- **Provenance:** `artifact_provenance` PK(artifact_id, artifact_kind); `record_provenance` MERGES llm_call_ids (audit trail only grows). Revision AND chapter both recorded as `ai_generated` by 'loom'.
- **Run row is the claim** (like position_audit): one 'running' per work; route has release-on-error dispatch guard; **startup recovery** (`recover_orphaned_loom_runs` in app lifespan) flips orphaned running rows to error — without it a restart permanently blocks drafting for that work.
- **Personas review-gated:** `loom_persona` proposed→approved via review queue namespace `loom_persona` (author signature mandatory, atomic conditional UPDATE). Drafting REFUSES missing/unapproved cast personas. Knowledge horizon = `{act: [canon_fact_ids]}`, union of acts ≤ contract act.
