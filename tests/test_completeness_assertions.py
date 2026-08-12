"""Tests for completeness assertions (review §4.1) — the opposite sign of a gap.

Covers:
  * refusal — an assertion without class/scope/basis/signature is rejected
  * detector guard — create_or_refresh_gap returns None for an asserted
    region and never writes a row; emission resumes after retraction
  * auto-dismiss — asserting closes open gaps in the region, ledgered, with
    the assertion cited in meta
  * retraction — atomic claim (double retract refused), re-opens exactly the
    gaps the assertion dismissed; independent human dismissals stay terminal
  * wildcard — scope='*' closes a whole class for the Work
  * no-value round-trip — the empty-but-complete flag persists
  * API round-trip — assert, list, retract, history over HTTP
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


def _gap(db, work_id, scope="smith 1998", gap_class="citation_closure", **overrides):
    kwargs = dict(
        work_id=work_id,
        gap_class=gap_class,
        scope=scope,
        frame_node_id=f"citation:{scope}",
        frame_source_ref="doc:d1 — Source",
        evidence_absent=f"Library holds nothing matching {scope}",
        unit=f"work:{work_id}",
        force_check="citation_graph_closure",
        issue_type="cited_work_not_held",
    )
    kwargs.update(overrides)
    return db.create_or_refresh_gap(**kwargs)


def _assert(db, work_id, scope="smith 1998", gap_class="citation_closure", **overrides):
    kwargs = dict(
        work_id=work_id,
        gap_class=gap_class,
        scope=scope,
        basis="checked the full bibliography against the Library",
        signed_by="brian",
    )
    kwargs.update(overrides)
    return db.assert_completeness(**kwargs)


# ── refusals ──────────────────────────────────────────────────────────────────


def test_assertion_refused_without_signature(tmp_path):
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    with pytest.raises(ValueError, match="signed_by"):
        _assert(db, w["id"], signed_by="")


def test_assertion_refused_without_basis(tmp_path):
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    with pytest.raises(ValueError, match="basis"):
        _assert(db, w["id"], basis="   ")


def test_assertion_refused_without_region(tmp_path):
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    with pytest.raises(ValueError, match="gap_class and scope"):
        _assert(db, w["id"], gap_class="", scope="")


def test_retraction_requires_reason_and_signature(tmp_path):
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    a = _assert(db, w["id"])
    with pytest.raises(ValueError, match="reason and a signature"):
        db.retract_completeness(a["id"], reason="", signed_by="brian")
    with pytest.raises(ValueError, match="reason and a signature"):
        db.retract_completeness(a["id"], reason="new sources found", signed_by="")


# ── detector guard ────────────────────────────────────────────────────────────


def test_asserted_region_emits_no_gap(tmp_path):
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    _assert(db, w["id"])
    assert _gap(db, w["id"]) is None
    assert db.list_gaps(w["id"]) == []


def test_wildcard_scope_closes_whole_class(tmp_path):
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    _assert(db, w["id"], scope="*")
    assert _gap(db, w["id"], scope="smith 1998") is None
    assert _gap(db, w["id"], scope="jones 2001") is None
    # A different class is untouched.
    g = _gap(
        db,
        w["id"],
        scope="entropy",
        gap_class="mentioned_never_explained",
        frame_node_id="term:entropy",
        force_check="mentioned_never_explained",
        issue_type="term_never_explained",
    )
    assert g is not None


def test_other_works_unaffected(tmp_path):
    db = _make_db(tmp_path)
    w1 = db.create_work(title="W1")
    w2 = db.create_work(title="W2")
    _assert(db, w1["id"])
    assert _gap(db, w1["id"]) is None
    assert _gap(db, w2["id"]) is not None


# ── auto-dismiss on assert ────────────────────────────────────────────────────


def test_assertion_dismisses_open_gaps_with_citation(tmp_path):
    import json

    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    g = _gap(db, w["id"])
    a = _assert(db, w["id"])
    assert a["closed_gap_ids"] == [g["id"]]
    row = db.get_gap(g["id"])
    assert row["status"] == "dismissed"
    assert a["id"] in row["status_reason"]
    assert json.loads(row["meta"])["closed_by_assertion"] == a["id"]
    ledger = db.list_gap_transitions(g["id"])
    assert ledger[-1]["to_status"] == "dismissed"
    assert ledger[-1]["signed_by"] == "brian"


# ── retraction round-trip ─────────────────────────────────────────────────────


def test_retraction_reopens_only_assertion_dismissed_gaps(tmp_path):
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    # A gap dismissed by an independent human decision — must stay terminal.
    human = _gap(db, w["id"], scope="jones 2001")
    db.transition_gap(
        human["id"], "dismissed", reason="not relevant to this book", signed_by="brian"
    )
    # A gap the assertion dismisses.
    g = _gap(db, w["id"])
    a = _assert(db, w["id"])
    assert a["closed_gap_ids"] == [g["id"]]

    r = db.retract_completeness(a["id"], reason="found a missing shelf", signed_by="brian")
    assert r["status"] == "retracted"
    assert r["reopened_gap_ids"] == [g["id"]]
    assert db.get_gap(g["id"])["status"] == "proposed"
    assert db.get_gap(human["id"])["status"] == "dismissed"
    # Emission resumes after retraction.
    assert _gap(db, w["id"]) is not None


def test_retract_exact_with_wildcard_still_active(tmp_path):
    """Exact asserted first, then class-wide '*'.  Retracting the exact one
    must NOT re-open the region — the wildcard keeps it closed, provenance is
    reassigned, and retracting the wildcard later re-opens the gap."""
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    g = _gap(db, w["id"])
    exact = _assert(db, w["id"])  # closes g
    wild = _assert(db, w["id"], scope="*", basis="whole class checked")
    assert exact["closed_gap_ids"] == [g["id"]]

    r = db.retract_completeness(exact["id"], reason="narrowing", signed_by="brian")
    assert r["reopened_gap_ids"] == []
    assert r["still_closed_gap_ids"] == [g["id"]]
    assert db.get_gap(g["id"])["status"] == "dismissed"
    # Region still closed: emission stays refused.
    assert _gap(db, w["id"]) is None

    r2 = db.retract_completeness(wild["id"], reason="fully re-opening", signed_by="brian")
    assert r2["reopened_gap_ids"] == [g["id"]]
    assert db.get_gap(g["id"])["status"] == "proposed"
    assert _gap(db, w["id"]) is not None  # emission resumes only now


def test_retract_wildcard_with_exact_still_active(tmp_path):
    """Class-wide '*' asserted first, then the exact region.  Retracting the
    wildcard re-opens only gaps NOT covered by the surviving exact assertion."""
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    g_smith = _gap(db, w["id"], scope="smith 1998")
    g_jones = _gap(db, w["id"], scope="jones 2001")
    wild = _assert(db, w["id"], scope="*", basis="whole class checked")
    assert set(wild["closed_gap_ids"]) == {g_smith["id"], g_jones["id"]}
    exact = _assert(db, w["id"], scope="smith 1998")

    r = db.retract_completeness(wild["id"], reason="only smith is done", signed_by="brian")
    assert r["reopened_gap_ids"] == [g_jones["id"]]
    assert r["still_closed_gap_ids"] == [g_smith["id"]]
    assert db.get_gap(g_jones["id"])["status"] == "proposed"
    assert db.get_gap(g_smith["id"])["status"] == "dismissed"
    assert _gap(db, w["id"], scope="smith 1998") is None  # still guarded
    assert _gap(db, w["id"], scope="jones 2001") is not None

    # Provenance was reassigned: retracting the exact assertion now re-opens smith.
    r2 = db.retract_completeness(exact["id"], reason="smith too", signed_by="brian")
    assert r2["reopened_gap_ids"] == [g_smith["id"]]
    assert db.get_gap(g_smith["id"])["status"] == "proposed"


def test_double_retraction_refused(tmp_path):
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    a = _assert(db, w["id"])
    db.retract_completeness(a["id"], reason="new sources", signed_by="brian")
    with pytest.raises(ValueError, match="already retracted"):
        db.retract_completeness(a["id"], reason="again", signed_by="brian")


def test_reassert_after_retraction_is_ledgered_not_duplicated(tmp_path):
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    a1 = _assert(db, w["id"])
    db.retract_completeness(a1["id"], reason="doubt", signed_by="brian")
    a2 = _assert(db, w["id"], basis="re-checked; the shelf is complete")
    assert a2["id"] == a1["id"]
    assert a2["status"] == "active"
    ledger = db.list_completeness_transitions(a1["id"])
    assert [t["to_status"] for t in ledger] == ["active", "retracted", "active"]
    assert ledger[-1]["reason"] == "reasserted"
    # Guard is live again.
    assert _gap(db, w["id"]) is None


def test_resolved_gaps_survive_assert_and_retract(tmp_path):
    """covered / mastered gaps are earned lifecycle states — an assertion says
    'nothing more to find', not 'what was found never happened'."""
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    covered = _gap(db, w["id"], scope="smith 1998")
    for step in ("ratified", "assigned", "researched", "covered"):
        db.transition_gap(covered["id"], step, reason="progress", signed_by="brian")
    mastered = _gap(db, w["id"], scope="jones 2001")
    for step in ("ratified", "assigned", "researched", "covered", "mastered"):
        db.transition_gap(mastered["id"], step, reason="progress", signed_by="brian")

    a = _assert(db, w["id"], scope="*")
    assert a["closed_gap_ids"] == []
    assert db.get_gap(covered["id"])["status"] == "covered"
    assert db.get_gap(mastered["id"])["status"] == "mastered"

    db.retract_completeness(a["id"], reason="changed my mind", signed_by="brian")
    assert db.get_gap(covered["id"])["status"] == "covered"
    assert db.get_gap(mastered["id"])["status"] == "mastered"


# ── no-value polarity ─────────────────────────────────────────────────────────


def test_no_value_round_trip(tmp_path):
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    a = _assert(
        db,
        w["id"],
        scope="translations",
        no_value=True,
        basis="this work was never translated — the empty set is the answer",
    )
    row = db.get_completeness_assertion(a["id"])
    assert row["no_value"] == 1
    listed = db.list_completeness_assertions(w["id"], status="active")
    assert [x["id"] for x in listed] == [a["id"]]


# ── detector integration ──────────────────────────────────────────────────────


def test_never_explained_detector_respects_assertion(tmp_path):
    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    from orivellum.capabilities.gap_engine import GAP_CLASS_TERM, detect_never_explained

    doc = db.create_document(
        title="Doc", source="doc.pdf", sha256="sha-doc", kind="pdf", work_id=w["id"]
    )
    db.update_document_extracted(doc["id"], "text", 100, readiness="ready")
    for i in range(6):
        db.add_chunk(doc["id"], f"passage {i} discussing Thermogravimetry in depth")
    # Term universe = knowledge subjects; an entity row is a mention, never an
    # explanation — so the term qualifies as mentioned-but-never-explained.
    db.create_knowledge_item(
        w["id"], "entity", "Thermogravimetry appears in the sources", subject="Thermogravimetry"
    )

    out = detect_never_explained(w["id"], db)
    assert out["candidates"] >= 1
    pair_key = out["gaps"][0]["scope"]
    aid = _assert(
        db,
        w["id"],
        gap_class=GAP_CLASS_TERM,
        scope=pair_key,
        basis="term is fully defined in my notes; nothing more to hold",
    )
    assert aid["closed_gap_ids"]
    out2 = detect_never_explained(w["id"], db)
    assert all(g["scope"] != pair_key for g in out2["gaps"])
    assert db.get_gap(aid["closed_gap_ids"][0])["status"] == "dismissed"


# ── API round-trip ────────────────────────────────────────────────────────────


def test_api_round_trip(tmp_path):
    client, db = _make_app(tmp_path)
    w = db.create_work(title="W")
    g = _gap(db, w["id"])

    r = client.post(
        f"/api/works/{w['id']}/completeness-assertions",
        json={
            "gap_class": "citation_closure",
            "scope": "smith 1998",
            "basis": "bibliography fully cross-checked",
            "signed_by": "brian",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active"
    assert body["closed_gap_ids"] == [g["id"]]

    r = client.get(f"/api/works/{w['id']}/completeness-assertions", params={"status": "active"})
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r = client.get(f"/api/completeness-assertions/{body['id']}/history")
    assert r.status_code == 200
    assert r.json()["transitions"][-1]["to_status"] == "active"

    # Unsigned retraction refused at the boundary.
    r = client.post(
        f"/api/completeness-assertions/{body['id']}/retract",
        json={"reason": "", "signed_by": "x"},
    )
    assert r.status_code == 422

    r = client.post(
        f"/api/completeness-assertions/{body['id']}/retract",
        json={"reason": "found new sources", "signed_by": "brian"},
    )
    assert r.status_code == 200
    assert r.json()["reopened_gap_ids"] == [g["id"]]
    assert db.get_gap(g["id"])["status"] == "proposed"

    # Second retract refused — state conflict, not a validation error.
    r = client.post(
        f"/api/completeness-assertions/{body['id']}/retract",
        json={"reason": "again", "signed_by": "brian"},
    )
    assert r.status_code == 409


def test_api_assert_unknown_work_404(tmp_path):
    client, _ = _make_app(tmp_path)
    r = client.post(
        "/api/works/nope/completeness-assertions",
        json={"gap_class": "c", "scope": "s", "basis": "b", "signed_by": "x"},
    )
    assert r.status_code == 404


# ── runner stopping condition ─────────────────────────────────────────────────


def test_runner_skips_closed_regions(tmp_path, monkeypatch):
    """The research runner's gap intake ends with 'already closed' for
    asserted regions instead of queueing them."""
    import sys

    sys.path.insert(0, "orivellum-runner")
    try:
        from runner.jobs import research
    finally:
        sys.path.pop(0)

    db = _make_db(tmp_path)
    w = db.create_work(title="W")
    # Two gaps on-topic; one region then asserted complete.
    g_smith = _gap(db, w["id"], scope="smith 1998", evidence_absent="quantum entanglement smith")
    g_jones = _gap(db, w["id"], scope="jones 2001", evidence_absent="quantum entanglement jones")
    _assert(db, w["id"], scope="smith 1998")

    gaps, notes = [], []
    toks = research._tokens("quantum entanglement")
    research._gaps_from_gap_table(db._conn, "quantum entanglement", toks, gaps, notes)
    queued_ids = [g.get("gap_id") for g in gaps]
    # jones queued; smith's region never re-queued even though the row was
    # dismissed at assert time (the intake guard is belt-and-braces).
    assert g_jones["id"] in queued_ids
    assert g_smith["id"] not in queued_ids

    # Force the raced-row case: resurrect smith's row to 'proposed' behind the
    # assertion's back — the intake guard alone must stop it, with a note.
    db._conn.execute("UPDATE gap SET status='proposed' WHERE id=?", (g_smith["id"],))
    gaps2, notes2 = [], []
    research._gaps_from_gap_table(db._conn, "quantum entanglement", toks, gaps2, notes2)
    assert g_smith["id"] not in [g.get("gap_id") for g in gaps2]
    assert any("already closed" in n for n in notes2)
