---
name: POSITION audit (E5)
description: Derived-stage audit for inherited manuscripts — ten tests, review-gated reconstruction, fail-closed stage ladder.
---

# POSITION audit design (Masterpiece Pipeline E5, Track B)

- Schema v127: `position_audit` (derived vs claimed stage + evidence/blocking JSON; row is the claim — created 'running' under write lock, EVERY exit path incl. route-side import/dispatch failures finishes it) and `position_proposal` (persona / blueprint / voice_spec, deterministic ids via sha256(work|kind|key) + INSERT OR IGNORE so re-runs never clobber resolved rows).
- Canon extraction proposals reuse `wa_canon_proposals` (status='proposed') so the existing `canon_fact` review resolver + `CanonStore.ratify_proposal` handle signatures — no new canon write path. HISTORICAL claims with unlocatable quotes are downgraded to INFERRED with a "[source gap…]" suffix and `source_location='unlocated'`.
- **Rule:** existing prose is evidence, not authority. The de-facto voice spec computes A4 metrics WITHOUT calling `assay.build_voice_baseline` (which writes the baseline); the baseline is installed only in the review resolver AFTER the atomic claim. If the install fails, `db.reopen_position_proposal(id, expected_resolved_by=author)` rolls the approval back (guarded by the resolver's identity) so the author retries — never approved-with-no-baseline.
- **Stage derivation:** ordered ladder A1(canon)→A2(personas)→A3(blueprint)→A4(voice)→A5(standard)→B4…B15; derived stage = FIRST failing rung ("no gaps below" — 40 chapters with no canon ⇒ A1-with-prose, never B5). Battery rungs (B6/B8/B9) **fail closed**: a missing or errored instrument run can never count as clean; require presence + status='done' + non-failing verdict.
- Battery skips gate.d15/d16/d17 (signature gates open on the author's decision, not an audit). LLM reconstruction stops on first gateway failure and records the error in evidence; deterministic proposals (blueprint, voice spec) always produced.
- Repair list weighting: chapter fraction in [0.15, 0.30] gets weight 3.0 and sorts by (-weight, severity, seq) — early-book errors propagate forward.
- Cast/persona character list derived from non-sentence-initial proper-noun frequency (no entity-table dependency; entities have no work scoping).
- Review gate namespace "position" added to review.py (_VALID_TYPES, _PENDING_SQL, queue section, resolvers dict); signature (author) mandatory like canon_fact.
