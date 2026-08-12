"""PROMOTION (E10) — shadow-mode certification for quality instruments.

No new or changed instrument may block the author's book until it has
earned that right.  Candidates run in **shadow**: their findings are
recorded and visibly labeled but never gate; a parity/precision record
accumulates against the author's ratified dispositions (true/false
positives).  Only when a candidate meets its declared precision threshold
— and the author signs — is it promoted to CERTIFIED and gains Tier 1/2
blocking authority.  A certified instrument that degrades can be demoted
back to shadow.  Every transition is one row in the certification ledger.

Doctrine (runtime_shadow_mode_policy, PIPELINE v2 §2.5/§5.2):
* blocking is computed, never stored — Tier 1/2 AND certified;
* shadow findings never block, never mutate, never bypass gates;
* promotion is blocked on insufficient agreement or sample size;
* demotion needs no threshold — the author is sovereign;
* Tier 3 can never be certified (advisory forever).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from orivellum.database.db import (
    ASSAY_DEFAULT_MIN_DISPOSITIONS,
    ASSAY_DEFAULT_MIN_PRECISION,
)

logger = logging.getLogger(__name__)

# Defaults when an instrument's contract does not declare its own bar
# (override per instrument via thresholds["promotion"]).  Single-sourced
# from the database layer, which enforces the bar authoritatively in
# set_assay_certification.
DEFAULT_MIN_PRECISION = ASSAY_DEFAULT_MIN_PRECISION
DEFAULT_MIN_DISPOSITIONS = ASSAY_DEFAULT_MIN_DISPOSITIONS


class PromotionError(ValueError):
    """A promotion/demotion request that must be refused (422 at the API)."""


def promotion_bar(instrument: dict) -> dict:
    """The declared precision bar for one instrument."""
    declared = (instrument.get("thresholds") or {}).get("promotion") or {}
    return {
        "min_precision": float(declared.get("min_precision", DEFAULT_MIN_PRECISION)),
        "min_dispositions": int(declared.get("min_dispositions", DEFAULT_MIN_DISPOSITIONS)),
    }


def precision_report(db: Any, instrument: dict) -> dict:
    """Rolling precision against author dispositions.

    precision = true_positive / (true_positive + false_positive), aggregated
    over the COMPLETE disposition record (count_assay_dispositions — the
    same data definition the DB certification write path enforces against).
    The series is a rendering window over the most recent dispositions,
    cumulative within the window, so the dashboard can show the trend.
    No dispositions → precision is None (never invented).
    """
    epoch = instrument.get("shadow_epoch")
    counts = db.count_assay_dispositions(instrument["id"], since=epoch)
    tp, fp = counts["true_positives"], counts["false_positives"]
    total = tp + fp
    precision = round(tp / total, 4) if total else None
    window = db.list_assay_dispositions(instrument["id"], since=epoch)
    w_tp = w_fp = 0
    series: list[dict] = []
    for d in window:
        if d["disposition"] == "true_positive":
            w_tp += 1
        else:
            w_fp += 1
        series.append(
            {
                "at": d["dispositioned_at"],
                "precision": round(w_tp / (w_tp + w_fp), 4),
                "true_positives": w_tp,
                "false_positives": w_fp,
            }
        )
    bar = promotion_bar(instrument)
    return {
        "true_positives": tp,
        "false_positives": fp,
        "sample_size": total,
        "precision": precision,
        "series": series,
        "bar": bar,
        "meets_bar": (
            total >= bar["min_dispositions"]
            and precision is not None
            and precision >= bar["min_precision"]
        ),
    }


def parity_report(db: Any, instrument: dict) -> dict:
    """Agreement between a shadow candidate and its certified baseline.

    Pairs every companion shadow run (evidence.shadow_companion_of) with the
    baseline run it accompanied and compares the flagged unit sets:
    agreement = |shadow ∩ baseline| / |shadow ∪ baseline| (both-clean pairs
    count as full agreement).  Returns per-pair detail plus the mean.
    """
    baseline_key = instrument.get("shadow_of")
    if not baseline_key:
        return {"baseline": None, "pairs": [], "mean_agreement": None}
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, evidence FROM assay_run
               WHERE instrument_id=? AND status='done'
               ORDER BY started_at DESC LIMIT 50""",
            (instrument["id"],),
        ).fetchall()
    pairs: list[dict] = []
    for r in rows:
        evidence = json.loads(r["evidence"] or "{}")
        primary_run_id = evidence.get("shadow_companion_of")
        if not primary_run_id:
            continue
        shadow_units = _flagged_units(db, r["id"])
        primary_units = _flagged_units(db, primary_run_id)
        union = shadow_units | primary_units
        agreement = round(len(shadow_units & primary_units) / len(union), 4) if union else 1.0
        pairs.append(
            {
                "shadow_run_id": r["id"],
                "baseline_run_id": primary_run_id,
                "agreement": agreement,
                "shadow_only": sorted(shadow_units - primary_units),
                "baseline_only": sorted(primary_units - shadow_units),
            }
        )
    mean = round(sum(p["agreement"] for p in pairs) / len(pairs), 4) if pairs else None
    return {"baseline": baseline_key, "pairs": pairs, "mean_agreement": mean}


def _flagged_units(db: Any, run_id: str) -> set[str]:
    return {f"{f['unit']}|{f['issue_type']}" for f in db.list_assay_findings(run_id)}


def instrument_report(db: Any, instrument: dict) -> dict:
    """One dashboard row: status, precision, parity, eligibility, degradation."""
    prec = precision_report(db, instrument)
    parity = parity_report(db, instrument)
    cert = instrument["certification"]
    degraded = (
        cert == "certified"
        and prec["sample_size"] >= promotion_bar(instrument)["min_dispositions"]
        and prec["precision"] is not None
        and prec["precision"] < promotion_bar(instrument)["min_precision"]
    )
    return {
        "key": instrument["key"],
        "name": instrument["name"],
        "tier": instrument["tier"],
        "certification": cert,
        "shadow_of": instrument.get("shadow_of"),
        "precision": prec,
        "parity": parity,
        "promotable": cert == "shadow" and int(instrument["tier"]) in (1, 2) and prec["meets_bar"],
        "degraded": degraded,
    }


def dashboard(db: Any) -> list[dict]:
    """Reports for every non-retired instrument, shadow candidates first."""
    order = {"shadow": 0, "certified": 1, "advisory": 2}
    instruments = [i for i in db.list_assay_instruments() if i["certification"] != "retired"]
    instruments.sort(key=lambda i: (order.get(i["certification"], 3), i["key"]))
    return [instrument_report(db, i) for i in instruments]


def enter_shadow(db: Any, key: str, *, actor: str, note: str = "") -> dict:
    """advisory → shadow: start accumulating the parity/precision record."""
    try:
        return db.set_assay_certification(
            key, "shadow", actor=actor, note=note or "entered shadow mode"
        )
    except ValueError as exc:
        raise PromotionError(str(exc)) from exc


def promote(db: Any, key: str, *, author: str, note: str = "") -> dict:
    """shadow → certified — refused unless the evidence has been earned.

    Requirements, all checked here (never trusted from the caller):
    * instrument exists and is currently in shadow;
    * Tier 1 or 2 (Tier 3 is advisory forever — also enforced in the DB);
    * the rolling precision meets the instrument's declared bar with the
      declared minimum number of author dispositions.
    The author's signature is the ``author`` actor recorded on the ledger
    row and the audit chain.
    """
    instrument = db.get_assay_instrument(key)
    if instrument is None:
        raise PromotionError(f"instrument {key!r} is not registered")
    if instrument["certification"] != "shadow":
        raise PromotionError(
            f"only shadow instruments can be promoted (current: {instrument['certification']!r})"
        )
    if int(instrument["tier"]) == 3:
        raise PromotionError("Tier 3 instruments are advisory forever")
    prec = precision_report(db, instrument)
    bar = prec["bar"]
    if prec["sample_size"] < bar["min_dispositions"]:
        raise PromotionError(
            f"insufficient dispositions: {prec['sample_size']} < {bar['min_dispositions']} required"
        )
    if not prec["meets_bar"]:
        raise PromotionError(
            f"precision {prec['precision']} below declared bar {bar['min_precision']}"
        )
    try:
        updated = db.set_assay_certification(
            key,
            "certified",
            actor=author,
            note=note or "promoted on precision evidence",
            precision=prec["precision"],
            sample_size=prec["sample_size"],
        )
    except ValueError as exc:
        raise PromotionError(str(exc)) from exc
    logger.info(
        "promotion: %s certified by %s (precision=%s n=%d)",
        key,
        author,
        prec["precision"],
        prec["sample_size"],
    )
    return updated


def demote(db: Any, key: str, *, author: str, note: str = "") -> dict:
    """certified → shadow.  No threshold: the author is sovereign, and a
    degraded instrument must be demotable immediately.  The precision at the
    moment of demotion is recorded on the ledger row as evidence."""
    instrument = db.get_assay_instrument(key)
    if instrument is None:
        raise PromotionError(f"instrument {key!r} is not registered")
    if instrument["certification"] != "certified":
        raise PromotionError(
            f"only certified instruments can be demoted (current: {instrument['certification']!r})"
        )
    prec = precision_report(db, instrument)
    try:
        updated = db.set_assay_certification(
            key,
            "shadow",
            actor=author,
            note=note or "demoted",
            precision=prec["precision"],
            sample_size=prec["sample_size"],
        )
    except ValueError as exc:
        raise PromotionError(str(exc)) from exc
    logger.info("promotion: %s demoted to shadow by %s", key, author)
    return updated
