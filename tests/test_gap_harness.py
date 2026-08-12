"""Tests for the golden oracle + open-world harness (G-M4).

Covers:
  * oracle labels — three-way, signed, upsert on revision; blank signature or
    invalid label refused
  * open-world scoring — unknown labels excluded (never false positives),
    unlabelled candidates never scored, precision/recall/kappa math on a
    hand-built fixture, frequency stratification
  * honest refusal — evaluating with zero scoreable labels raises
  * blocking-status gate — an unmeasured detector cannot produce
    blocking-severity gaps; a measured one (over the label floor) can
  * routes — label upsert, candidates surface, evaluate, measurements
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_app(tmp_path):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=str(tmp_path))
    db = OrivellumDB(str(tmp_path / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS), db


def _make_db(tmp_path):
    from orivellum.database.db import OrivellumDB

    return OrivellumDB(str(tmp_path / "test.db"))


def _ready_doc(db, work_id, title, chunks=()):
    doc = db.create_document(
        title=title,
        source=f"{title}.pdf",
        sha256=f"sha-{title}-{work_id}",
        kind="pdf",
        work_id=work_id,
    )
    db.update_document_extracted(doc["id"], f"text of {title}", 100, readiness="ready")
    for text in chunks:
        db.add_chunk(doc["id"], text)
    return doc["id"]


DET = "citation_graph_closure"


# ── oracle labels ─────────────────────────────────────────────────────────────


def test_oracle_label_requires_signature_and_valid_label(tmp_path):
    db = _make_db(tmp_path)
    work = db.create_work("W")
    with pytest.raises(ValueError, match="signed_by"):
        db.upsert_oracle_label(work["id"], DET, "smith 1998", "is_gap", signed_by="  ")
    with pytest.raises(ValueError, match="invalid label"):
        db.upsert_oracle_label(work["id"], DET, "smith 1998", "maybe", signed_by="ben")
    with pytest.raises(ValueError, match="pair_key"):
        db.upsert_oracle_label(work["id"], DET, "  ", "is_gap", signed_by="ben")
    assert db.list_oracle_labels(detector=DET) == []


def test_oracle_label_upserts_on_revision(tmp_path):
    db = _make_db(tmp_path)
    work = db.create_work("W")
    a = db.upsert_oracle_label(
        work["id"], DET, "smith 1998", "unknown", signed_by="ben", frequency=2
    )
    b = db.upsert_oracle_label(
        work["id"], DET, "smith 1998", "is_gap", signed_by="ben", frequency=5, note="verified"
    )
    assert a["id"] == b["id"]
    labels = db.list_oracle_labels(detector=DET, work_id=work["id"])
    assert len(labels) == 1
    assert labels[0]["label"] == "is_gap"
    assert labels[0]["frequency"] == 5


# ── open-world scoring on a hand-built fixture ────────────────────────────────


def _citation_fixture(db):
    """A Work whose citation detector predictions are fully known.

    Chunks cite four works; 'held 2000' is in the Library, so the detector
    flags exactly: gapa 1990 (×1), gapb 1991 (×5), noise 1992 (×1).
    """
    work = db.create_work("W")
    _ready_doc(db, work["id"], "Held - Collected Papers")  # makes 'Held (2000)' held
    _ready_doc(
        db,
        work["id"],
        "Survey",
        chunks=[
            "See Gapa (1990). " + "Gapb (1991) said so. " * 5,
            "Also Noise (1992) and Held (2000).",
        ],
    )
    return work


def test_harness_open_world_scoring_and_stratification(tmp_path):
    from orivellum.capabilities.gap_harness import evaluate_detector

    db = _make_db(tmp_path)
    work = _citation_fixture(db)
    wid = work["id"]
    # Golden oracle: rare TP, common TP, a miss (FN), a correct rejection (TN),
    # an unknown that the detector flags — MUST be excluded, not a FP.
    db.upsert_oracle_label(wid, DET, "gapa 1990", "is_gap", signed_by="ben", frequency=1)
    db.upsert_oracle_label(wid, DET, "gapb 1991", "is_gap", signed_by="ben", frequency=5)
    db.upsert_oracle_label(wid, DET, "missed 1900", "is_gap", signed_by="ben", frequency=1)
    db.upsert_oracle_label(wid, DET, "held 2000", "is_not_gap", signed_by="ben", frequency=1)
    db.upsert_oracle_label(wid, DET, "noise 1992", "unknown", signed_by="ben", frequency=1)

    result = evaluate_detector(db, DET)
    assert result["n_labeled"] == 4
    assert result["n_unknown_excluded"] == 1
    # TP: gapa, gapb.  FN: missed 1900.  TN: held 2000.  FP: none —
    # 'noise 1992' is flagged but labelled unknown, so it never counts.
    assert (result["tp"], result["fp"], result["fn"], result["tn"]) == (2, 0, 1, 1)
    assert result["precision"] == 1.0
    assert result["recall"] == pytest.approx(2 / 3)
    assert result["kappa"] == pytest.approx(0.5)
    # stratification: rare band holds gapa/missed/held; common holds gapb
    assert result["strata"]["rare"]["n"] == 3
    assert result["strata"]["common"]["n"] == 1
    assert result["strata"]["common"]["recall"] == 1.0
    assert result["strata"]["rare"]["recall"] == pytest.approx(0.5)
    # persisted measurement
    m = db.latest_detector_measurement(DET)
    assert m is not None and m["n_labeled"] == 4
    assert m["labels_fingerprint"] == db.oracle_fingerprint(DET)
    # 4 labels is below the blocking floor
    assert result["meets_blocking_floor"] is False
    assert db.has_measured_detector(DET) is False


def test_harness_refuses_without_scoreable_labels(tmp_path):
    from orivellum.capabilities.gap_harness import evaluate_detector

    db = _make_db(tmp_path)
    work = db.create_work("W")
    with pytest.raises(ValueError, match="no scoreable"):
        evaluate_detector(db, DET)
    db.upsert_oracle_label(work["id"], DET, "x 1990", "unknown", signed_by="ben")
    with pytest.raises(ValueError, match="no scoreable"):
        evaluate_detector(db, DET)
    with pytest.raises(ValueError, match="unknown detector"):
        evaluate_detector(db, "made_up")


# ── blocking-status gate ──────────────────────────────────────────────────────


def _blocking_gap(db, work_id, detector):
    return db.create_or_refresh_gap(
        work_id=work_id,
        gap_class="citation_closure",
        scope="big 1990",
        frame_node_id="citation:big 1990",
        frame_source_ref="doc:d — Survey",
        evidence_absent="not held",
        centrality=9,
        dependent_count=3,
        blocking_active_work=True,
        force_check=detector,
    )


def test_unmeasured_detector_cannot_reach_blocking_severity(tmp_path):
    import json

    db = _make_db(tmp_path)
    work = db.create_work("W")
    gap = _blocking_gap(db, work["id"], DET)
    assert gap["severity"] != "critical"  # blocking weight suppressed
    meta = json.loads(gap["meta"])
    assert "blocking_suppressed" in meta
    assert DET in meta["blocking_suppressed"]


def _label_floor(db, wid, n, *, prefix="pair"):
    """Author n scoreable labels (arbitrary pairs — misses are labels too)."""
    for i in range(n):
        db.upsert_oracle_label(
            wid, DET, f"{prefix}{i} 1990", "is_not_gap", signed_by="ben", frequency=1
        )


def test_measured_detector_over_floor_can_reach_blocking_severity(tmp_path):
    import json

    from orivellum.capabilities.gap_harness import evaluate_detector

    db = _make_db(tmp_path)
    work = db.create_work("W")
    # a real harness run over >= floor CURRENT labels unlocks blocking
    _label_floor(db, work["id"], db.MIN_ORACLE_LABELED)
    evaluate_detector(db, DET)
    assert db.has_measured_detector(DET) is True
    gap = _blocking_gap(db, work["id"], DET)
    assert gap["severity"] == "critical"
    assert "blocking_suppressed" not in json.loads(gap["meta"])


def test_measurement_below_floor_does_not_unlock_blocking(tmp_path):
    from orivellum.capabilities.gap_harness import evaluate_detector

    db = _make_db(tmp_path)
    work = db.create_work("W")
    _label_floor(db, work["id"], db.MIN_ORACLE_LABELED - 1)
    evaluate_detector(db, DET)
    gap = _blocking_gap(db, work["id"], DET)
    assert gap["severity"] != "critical"


def test_measurement_counts_cannot_be_injected(tmp_path):
    """record_detector_measurement derives counts from the oracle table —
    a caller cannot inflate n_labeled to unlock blocking, and malformed
    (unstratified) figures are refused."""
    db = _make_db(tmp_path)
    db.create_work("W")
    with pytest.raises(ValueError, match="strata"):
        db.record_detector_measurement(DET, precision=1.0, recall=1.0, f1=1.0, kappa=1.0, strata={})
    row = db.record_detector_measurement(
        DET,
        precision=1.0,
        recall=1.0,
        f1=1.0,
        kappa=1.0,
        strata={"rare": {"n": 50}, "common": {"n": 50}},
    )
    assert row["n_labeled"] == 0  # derived: no labels exist
    assert db.has_measured_detector(DET) is False


def test_relabeling_relocks_the_blocking_gate(tmp_path):
    """A measurement is bound to the oracle it was computed over: any label
    change after measuring makes it stale, and blocking re-locks until the
    detector is re-evaluated."""
    from orivellum.capabilities.gap_harness import evaluate_detector

    db = _make_db(tmp_path)
    work = db.create_work("W")
    _label_floor(db, work["id"], db.MIN_ORACLE_LABELED)
    evaluate_detector(db, DET)
    assert db.has_measured_detector(DET) is True
    # the author revises one label — the measured figure no longer describes
    # this oracle
    db.upsert_oracle_label(work["id"], DET, "pair0 1990", "is_gap", signed_by="ben", frequency=1)
    assert db.has_measured_detector(DET) is False
    gap = _blocking_gap(db, work["id"], DET)
    assert gap["severity"] != "critical"
    # re-evaluating over the revised oracle unlocks it again
    evaluate_detector(db, DET)
    assert db.has_measured_detector(DET) is True


# ── routes ────────────────────────────────────────────────────────────────────


def test_oracle_routes_label_candidates_evaluate_measurements(tmp_path):
    client, db = _make_app(tmp_path)
    work = _citation_fixture(db)
    wid = work["id"]

    # candidates surface (report-only, includes any existing label)
    r = client.get(f"/api/works/{wid}/gap-oracle/candidates", params={"detector": DET})
    assert r.status_code == 200
    cands = {c["pair_key"]: c for c in r.json()["candidates"]}
    assert "gapa 1990" in cands and cands["gapa 1990"]["label"] is None
    assert "held 2000" not in cands  # held — not flagged
    assert db.list_gaps(wid) == []  # report-only: nothing was written

    # label via route; blank signature refused
    r = client.post(
        f"/api/works/{wid}/gap-oracle/labels",
        json={
            "detector": DET,
            "pair_key": "gapa 1990",
            "label": "is_gap",
            "signed_by": "ben",
            "frequency": 1,
        },
    )
    assert r.status_code == 200
    r = client.post(
        f"/api/works/{wid}/gap-oracle/labels",
        json={"detector": DET, "pair_key": "x 1990", "label": "is_gap", "signed_by": " "},
    )
    assert r.status_code in (422,)

    # a labelled pair the detector no longer flags shows up as unflagged
    client.post(
        f"/api/works/{wid}/gap-oracle/labels",
        json={
            "detector": DET,
            "pair_key": "missed 1900",
            "label": "is_gap",
            "signed_by": "ben",
        },
    )
    r = client.get(f"/api/works/{wid}/gap-oracle/candidates", params={"detector": DET})
    unflagged = {u["pair_key"] for u in r.json()["unflagged_labels"]}
    assert "missed 1900" in unflagged

    # evaluate + measurements
    r = client.post("/api/gap-oracle/evaluate", json={"detector": DET})
    assert r.status_code == 200
    body = r.json()
    assert body["n_labeled"] == 2
    assert "rare" in body["strata"] and "common" in body["strata"]
    r = client.get("/api/gap-oracle/measurements")
    assert r.status_code == 200
    ms = r.json()
    assert ms["min_labeled_for_blocking"] == db.MIN_ORACLE_LABELED
    assert len(ms["measurements"]) == 1
    assert ms["measurements"][0]["meets_blocking_floor"] is False

    # evaluating an unknown detector via the route → 422
    r = client.post("/api/gap-oracle/evaluate", json={"detector": "made_up"})
    assert r.status_code == 422
