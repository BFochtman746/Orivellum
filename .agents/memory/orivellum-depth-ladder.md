---
name: Depth ladder & reverse research loop
description: Training-system integrity invariants — graduation gating, fail-closed grading, issued-question binding, research-request lifecycle
---

# Training integrity invariants

- **Graduation = streak AND ladder.** A pass streak alone never graduates a concept; every required depth level must also be passed. Any place that asks "was this ever learned?" (including decay diagnosis) must use the same two-axis definition — a historical streak without the ladder is *never_learned*, not decay.
  **Why:** the spec forbids graduating on recall alone; streak and depth are independent axes, and a completion review caught the decay diagnosis using streak-only.

- **Grading fails closed.** Scores that advance learner state must be computed in code from enforced rubric criteria (extractive quotes verified as substrings of the answer, minimum criterion count). Above the recall level, a bare model float can never grant credit — cap it at neutral.

- **Assessments bind to server-issued questions.** The server records the exact question and level at issue time; assessment claims it single-use and atomically, with a bounded TTL. Client-authored, mismatched, replayed, or expired submissions are refused. Tests that POST an assessment directly must issue first; the UI must submit the fetched question verbatim.
  **Why:** re-deriving the *level* server-side is not enough — without binding *content*, a client can grade itself against a trivial self-written question.

- **Reverse research loop.** Only a corpus-insufficient diagnosis emits a research request (at most one open per concept). The research runner consumes open requests first (a blocked learner outranks other gap sources), and writeback resolves a request only when its digest landed new material, scoped to the owning Work — cross-Work digests must never resolve another Work's request.

- **Teach-back is graduated-only** at the API boundary, with the same issued-prompt claim; a failed teach-back can revoke graduation.
