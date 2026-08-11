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
- **Registration can never set certification.** `upsert_assay_instrument` refuses contracts carrying a `certification` field and self-referencing `shadow_of`; new instruments always insert as advisory. `set_assay_certification` is the ONLY certification write path — validated transition map (advisory→{shadow,retired}, shadow→{certified,advisory,retired}, certified→{shadow,retired}, retired→{shadow}), Tier 3 refused at certified forever, every transition appends one `assay_certification_event` ledger row inside a governed write.
- **Shadow is observation only.** Shadow runs stamp `authority.shadow=true` + `evidence.shadow=true` on every finding; blocking stays computed so shadow never gates. A shadow candidate resolves its runner via `shadow_of` (baseline's runner family) with its OWN thresholds.
- **Companions co-run with the primary.** After a non-shadow instrument finishes, `list_assay_shadow_companions(key)` candidates run inline, exception-isolated, linked via `evidence.shadow_companion_of` = primary run id — that link is what parity pairing uses. No recursion: only non-shadow primaries co-run.
- **Precision is ground-truthed by author dispositions** (`assay_finding.disposition` true_positive/false_positive, actor = authenticated principal). `promotion.promote` re-checks the declared bar server-side (thresholds["promotion"], default 0.8 over ≥10) and records precision+sample_size on the ledger row; demotion has NO threshold (author sovereign). Never invent precision when sample_size = 0 → None.

**How to apply:** any new instrument is added to the assay contract registry with an honest tier + advisory certification; its runner returns verdict-or-score with evidence and must obey the rules above. To give a detector blocking power: enter shadow → accumulate dispositions → promote via `promotion.py` (dashboard at /assay, routes under /assay/promotion + /assay/instruments/{key}).
