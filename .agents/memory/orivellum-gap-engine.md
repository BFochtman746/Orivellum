---
name: Gap Engine (G-M1 → G-M4)
description: Hygiene/gap split, gap identity + lifecycle, structural detectors, golden oracle, open-world harness, enforced blocking gate.
---

# Gap Engine decisions

- **Hygiene vs gap split**: corpus_hygiene emits findings (defects in what's held); the `gap` table holds absences (what should be held but isn't). Never mix them.
- **Gap identity** = `gap-` + sha256 of `work_id|frame_node_id|gap_class|scope` (40 hex chars). Work-scoped so identical absences in different Works stay separate.
- **Never resurrect**: gaps in `dismissed`/`out_of_scope` are returned untouched by `create_or_refresh_gap`. Hygiene dismissals persist in `hygiene_dismissal`.
- **Severity is derived, never passed**: `create_or_refresh_gap` takes centrality/dependent_count/blocking_active_work and derives severity via lazy import of `gap_engine.compute_severity`.
- **Blocking gate (enforced at insert)**: `blocking_active_work=True` is suppressed (severity recomputed, `meta.blocking_suppressed` recorded) unless `db.has_measured_detector(force_check)` — which requires the latest `gap_detector_measurement` to have `n_labeled >= MIN_ORACLE_LABELED` (20) AND a `labels_fingerprint` matching the CURRENT oracle. Any relabel re-locks the gate.
- **Measurements cannot be injected**: `record_detector_measurement` derives n_labeled/n_unknown and the fingerprint from `gap_oracle_label` itself and refuses strata without both `rare` and `common` bands.
- **Open-world scoring rules**: `unknown` labels stored but excluded from scoring; unlabelled candidates counted but never scored as FP; refuse (ValueError) to evaluate with zero scoreable labels; report Cohen's kappa alongside P/R (no bare MRR); everything stratified rare/common by `RARE_FREQ_MAX = 3`.
- **Detector registry**: `gap_harness.DETECTOR_CANDIDATES` maps detector name → report-only candidates fn. Detector name == `gap.force_check` == oracle label key. Four detectors: citation_graph_closure, mentioned_never_explained, dead_end_citation, failure_clustering.
- **Determinism**: all candidate sorts use `(-frequency, pair_key)`; top-doc/top-citation picks have explicit tie-breakers; SQL feeding candidates is ORDER BY'd. Ties varying across runs breaks harness reproducibility.
- **Dead-end vs citation-closure**: same unheld work cited in chunks AND in a knowledge claim yields two gaps with different classes/identities — intentional (different remediation).
- **Annotation UI**: `/works/:workId/gap-oracle` (linked "oracle" in the Hygiene tab). Must allow authoring pairs the detector never flagged — that's the recall side of the oracle.
- Injected-hole/CWA hold-out evaluation was explicitly rejected by the brutal review; do not reintroduce it.
