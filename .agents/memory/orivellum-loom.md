---
name: LOOM chapter drafting engine
description: Durable invariants for the B5 drafting loop — character agents, critic gate, narrator, beat compliance, entropy gate, world state, provenance.
---

# LOOM drafting invariants

- **Beat controller escalates, never rewrites.** The only goal is the chapter contract's beat; stall, word-band misses, and beat drift become high-severity findings for the author. The controller must never change story text or goals.
  **Why:** MAGNET's emergent goal generator was deliberately replaced by the blueprint contract; the author stays the sole story authority.
- **Critic never skipped; drafter never judges itself.** Critic and drafter must be distinct models — equal models are a refusal, not a warning. Malformed agent output consumes a bounded attempt without reaching the critic (it is not an action).
- **Grounding must reach the judges, not just the assembler.** Critic and narrator prompts must carry the horizon-restricted persona, canon facts, world state, and previous closing passage — assembling context without feeding it to a judge silently voids the consistency guarantee, so prompt-content assertions belong in any judge's tests.
- **Narrator selection is strict.** Selection must be exact JSON integers in range; any float, bool, string, negative, or out-of-range item rejects the whole response (never coerce — 0.9 must not commit action 0). Missing/malformed/empty → refuse the run; never default to "all accepted actions".
- **Approved chapters never overwritten — atomically.** The approval re-check, revision insert, and text update happen in one transaction, and the store runs BEFORE the world-state commit so a refused draft leaves the world untouched.
- **World state uses overwrite semantics** and is replayed from the world graph whenever the table is empty or polluted with entries at/after the current chapter's seq (re-drafting must never see its own or future state). Between sequential drafts, trust the accumulated table — an unconditional replay would discard critic-emitted updates that aren't graph-backed.
- **Entropy NULL = not measured, never certain.** Absent logprobs are reported `available:false`; hot spans get temp-0 verification against canon+state before storing; failures become findings.
- **The run row is the claim** and startup recovery must release orphaned running rows, or one crash permanently blocks drafting for that work.
- **Personas are review-gated authority**: drafting refuses unapproved cast personas; approval needs an author signature via an atomic conditional update.

- Drafting cockpit routes: chapter readiness must MIRROR the engine refusals (contract fields, approved/inherited personas, approved-chapter guard) — never invent looser rules; meta read-merge-write must happen under one db._lock transaction or concurrent pipeline meta writes get clobbered.
