"""Open-world evaluation harness for gap detectors (G-M4).

The field's converged protocol, not injected-hole/CWA hold-out evaluation:

  * The **golden oracle** is a hand-annotated set of (work, pair) fixtures
    with THREE labels — is_gap / is_not_gap / unknown.  The labels are the
    author's, signed at annotation time.
  * A detector is formalised as a **completeness oracle**: run report-only,
    it answers "gap" (candidate emitted) or "not flagged" for each pair.
  * ``unknown`` items are stored but EXCLUDED from scoring — never counted
    as false positives.  Penalising a detector for finding a real gap that
    the annotator has not ruled on would tune it to stop finding real gaps.
  * Every metric is **stratified by entity frequency** (rare vs common) —
    frequency-based detectors have known popularity bias, and a single
    number hides it.
  * Reporting is **chance-adjusted** (Cohen's kappa) alongside precision and
    recall.  No bare mean-reciprocal-rank figures.

A detector reaches blocking status only after a measurement over at least
``OrivellumDB.MIN_ORACLE_LABELED`` scoreable labels exists — enforced at the
gap insert path (``has_measured_detector``), not aspirational.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from orivellum.capabilities.domain_model import (
    DETECTOR_DOMAIN_COVERAGE,
    candidates_domain_coverage,
)
from orivellum.capabilities.gap_engine import (
    DETECTOR_CITATION,
    DETECTOR_DEADEND,
    DETECTOR_FAILURE,
    DETECTOR_TERM,
    RARE_FREQ_MAX,
    candidates_citation_closure,
    candidates_dead_end,
    candidates_failure_clusters,
    candidates_never_explained,
)

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# Detector registry: name -> report-only candidates function (no writes).
DETECTOR_CANDIDATES = {
    DETECTOR_CITATION: candidates_citation_closure,
    DETECTOR_TERM: candidates_never_explained,
    DETECTOR_DEADEND: candidates_dead_end,
    DETECTOR_FAILURE: candidates_failure_clusters,
    DETECTOR_DOMAIN_COVERAGE: candidates_domain_coverage,
}


def _prf(tp: int, fp: int, fn: int) -> tuple[float | None, float | None, float | None]:
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    f1 = None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1 = 2 * precision * recall / (precision + recall)
    return precision, recall, f1


def _kappa(tp: int, fp: int, fn: int, tn: int) -> float | None:
    """Cohen's kappa — chance-adjusted agreement between detector and oracle."""
    n = tp + fp + fn + tn
    if n == 0:
        return None
    po = (tp + tn) / n
    pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)
    if pe >= 1.0:
        return None  # degenerate: all labels on one side — chance undefined
    return (po - pe) / (1 - pe)


def _collect_predictions(
    db: OrivellumDB, detector: str, work_ids: list[str], labels: list[dict]
) -> tuple[set[tuple[str, str]], int]:
    """Run the detector report-only over the labelled Works.

    Returns the predicted (work_id, pair_key) set plus the count of candidates
    for pairs nobody has labelled — reported, never scored (open world).
    """
    candidates_fn = DETECTOR_CANDIDATES[detector]
    labelled_keys = {(label["work_id"], label["pair_key"]) for label in labels}
    predicted: set[tuple[str, str]] = set()
    unlabelled = 0
    for wid in work_ids:
        for cand in candidates_fn(wid, db):
            key = (wid, cand["pair_key"])
            predicted.add(key)
            if key not in labelled_keys:
                unlabelled += 1
    return predicted, unlabelled


def evaluate_detector(db: OrivellumDB, detector: str, *, persist: bool = True) -> dict:
    """Score one detector against the golden oracle, open-world style.

    Runs the detector report-only over every Work that carries labels, joins
    predictions with labels on pair_key, and scores ONLY the labelled pairs
    whose label is not ``unknown``.  Candidates for unlabelled pairs are
    reported as a count but never scored — under the open-world assumption
    an unruled absence is not an error.

    Raises ValueError when the detector is unknown or no scoreable labels
    exist (an honest refusal beats a fabricated zero).
    """
    if detector not in DETECTOR_CANDIDATES:
        raise ValueError(
            f"unknown detector {detector!r}: expected one of {sorted(DETECTOR_CANDIDATES)}"
        )
    labels = db.list_oracle_labels(detector=detector)
    scoreable = [label for label in labels if label["label"] != "unknown"]
    n_unknown = len(labels) - len(scoreable)
    if not scoreable:
        raise ValueError(
            f"detector {detector!r} has no scoreable oracle labels — annotate "
            "pairs (is_gap / is_not_gap) before evaluating"
        )

    work_ids = sorted({label["work_id"] for label in scoreable})
    predicted, unlabelled_candidates = _collect_predictions(db, detector, work_ids, labels)

    def _confusion(rows: list[dict]) -> tuple[int, int, int, int]:
        tp = fp = fn = tn = 0
        for label in rows:
            hit = (label["work_id"], label["pair_key"]) in predicted
            if label["label"] == "is_gap":
                tp, fn = tp + int(hit), fn + int(not hit)
            else:  # is_not_gap
                fp, tn = fp + int(hit), tn + int(not hit)
        return tp, fp, fn, tn

    tp, fp, fn, tn = _confusion(scoreable)
    precision, recall, f1 = _prf(tp, fp, fn)
    kappa = _kappa(tp, fp, fn, tn)

    strata: dict[str, dict] = {}
    for band, rows in (
        ("rare", [r for r in scoreable if r["frequency"] <= RARE_FREQ_MAX]),
        ("common", [r for r in scoreable if r["frequency"] > RARE_FREQ_MAX]),
    ):
        btp, bfp, bfn, btn = _confusion(rows)
        bp, br, bf1 = _prf(btp, bfp, bfn)
        strata[band] = {
            "n": len(rows),
            "tp": btp,
            "fp": bfp,
            "fn": bfn,
            "tn": btn,
            "precision": bp,
            "recall": br,
            "f1": bf1,
            "kappa": _kappa(btp, bfp, bfn, btn),
        }

    result = {
        "detector": detector,
        "n_labeled": len(scoreable),
        "n_unknown_excluded": n_unknown,
        "unlabelled_candidates": unlabelled_candidates,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "kappa": kappa,
        "strata": strata,
        "works_evaluated": work_ids,
        "meets_blocking_floor": len(scoreable) >= db.MIN_ORACLE_LABELED,
    }
    if persist:
        row = db.record_detector_measurement(
            detector,
            precision=precision,
            recall=recall,
            f1=f1,
            kappa=kappa,
            strata=strata,
        )
        result["measurement_id"] = row["id"]
    return result
