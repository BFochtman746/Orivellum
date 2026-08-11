---
name: Extraction reservation
description: Why every extraction pipeline run must go through the shared per-document reservation, and the ownership rules that keep it safe.
---

# Extraction reservation

- Every extraction run on a document must claim the shared per-document
  reservation before touching any document state; a run that cannot claim it
  no-ops without mutating anything. **Why:** two concurrent pipelines both
  delete/rewrite chunks, knowledge and readiness — last writer wins. An
  in-process registry is sufficient (all pipelines are threads of the single
  API process) and can never strand a stale claim across a restart.
- **How to apply:** never add an entry point that bypasses the shared
  reservation, and never mutate document state (readiness resets, warning
  wipes) before ownership is acquired. Routes wanting a deterministic 409
  reserve up front and transfer the token to the pipeline; a transferred
  token is only ownership if it is still the currently registered claim —
  never assume ownership from a nonempty token. Release is token-matched so a
  double release is safe and can never free a later run's claim (plain
  boolean/set registries were rejected for that double-free hazard).
- The older readiness-based guards stay as complementary UX: they cover
  queued-but-not-yet-started runs, which the reservation (taken when the
  pipeline begins) does not.
