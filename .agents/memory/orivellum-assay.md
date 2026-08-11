---
name: ASSAY quality-instrument registry
description: Governed instrument registry — engine contracts, three-tier authority, runs, standard findings, gate signatures.
---

# ASSAY quality-instrument registry

## Durable rules
- **The run row IS the claim.** run creation refuses (RuntimeError → 409) while a run for the same instrument+work is `running`. Background instruments (gate.d14–d17, judge.hierarchical) create the run row BEFORE `submit_bg` dispatch; a rejected dispatch finishes the run as error + 503.
  **Why:** claim-before-dispatch prevents duplicate gateway-heavy runs; no separate claim table to leak.
- **Blocking is computed, never stored.** `is_blocking(inst)` = tier ≤ 2 AND certification == 'certified'. Every run stamps `evidence.authority {tier, certification, blocking}` at execution time. Reseeding contracts preserves certification. Tier 3 never blocks, even if marked certified.
- **The machine never renders a gate go/no-go.** D15–D17 stay `locked` (zero model calls) until the latest author signature is open/go. D17 structural checks run unsigned but the verdict is `structural_violations` (a measurement), never `fail`. D14 verdicts are `confirmed_drift`/`clean`. Judge verdict is ALWAYS `advisory`.
- **Judge is never the drafter.** `judge_model()` raises JudgeModelError when it resolves to the workhorse/drafting model (checks DB overrides too).
- **Untrusted model output is validated before storage.** D14 confirmation requires a strict JSON boolean (`"false"` string → unconfirmed, never confirmed); gate/judge annotations must be list/dict shapes or the run fails loud; pairwise scores must be numeric 0–100 maps. Gateway-down on D14 confirm → unconfirmed advisory (never fails a chapter); gateway-down on judge → run marked error and raises.
- **Pairwise regression is score-based**: preference "A" OR any rubric category below its predecessor surfaces a `pairwise_regression` finding; snapshot then advances (previous-revision snapshot stored per chapter, full text by design).
- **No invented baselines.** voice.envelope without a stored baseline → verdict `no_baseline`, zero findings. `build_voice_baseline` raises on empty text.
- **Post-claim failures always finish the claim.** Any failure after a run row is claimed (including retired-instrument validation on a pre-claimed row) marks the run `error` — a leaked `running` row would 409 the instrument+work forever.
- **The signer is the authenticated principal, never request JSON.** Gate signatures stamp the authenticated single-user identity; caller-supplied author names are not accepted (impersonation).
- Chapter ownership enforced at the claim boundary: run creation rejects a chapter_id not belonging to the work (ValueError → 422).

## PROMOTION (E10) — shadow-mode certification
- **The DB transition path is the authority, never the caller.** Certification changes go through one write path with a validated transition map; the shadow→certified transition aggregates the COMPLETE disposition record (uncapped SQL, inside the same transaction as the status CAS — caller-supplied numbers ignored) and refuses below the declared bar, so no code path can certify an untested detector. Lost CAS race → RuntimeError → 409.
- **A changed contract must re-earn its authority.** Re-seeding a certified instrument with different tier/thresholds/scope/shadow_of auto-demotes it to shadow via a governed write + ledger row.
- **Shadow is observation only.** Shadow runs/findings are visibly labeled; blocking stays computed so they never gate. Companions co-run inline after their non-shadow baseline, exception-isolated, linked to the primary run id for parity pairing; demotion has no threshold (author sovereign); never invent precision when there are zero dispositions.

**How to apply:** any new instrument is added to the assay contract registry with an honest tier + advisory certification; its runner returns verdict-or-score with evidence and must obey the rules above. To give a detector blocking power: enter shadow → accumulate dispositions → promote via `promotion.py` (dashboard at /assay, routes under /assay/promotion + /assay/instruments/{key}).
