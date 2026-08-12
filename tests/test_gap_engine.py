"""Tests for the Gap Engine (G-M1 / G-M2).

Covers:
  * insert refusal — a gap without a frame citation is rejected
  * content-hash identity — the same absence maps to one row
  * lifecycle — forward transitions, ledgering, illegal jumps refused
  * dismissal — requires reason + signature; persists forever; re-detection
    never resurrects a dismissed gap
  * citation-graph closure detector — cited-but-not-held works become ranked
    gaps citing the in-corpus source that demands them; held works do not
  * hygiene dismissal — a dismissed hygiene finding never reappears
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


def _valid_gap(db, work_id, **overrides):
    kwargs = dict(
        work_id=work_id,
        gap_class="citation_closure",
        scope="smith 1998",
        frame_node_id="citation:smith 1998",
        frame_source_ref="doc:abc — Some Held Doc",
        evidence_absent="Library holds no document matching Smith (1998)",
        centrality=3,
        dependent_count=1,
    )
    kwargs.update(overrides)
    return db.create_or_refresh_gap(**kwargs)


# ── insert refusal ────────────────────────────────────────────────────────────


def test_gap_insert_refuses_missing_frame_citation(tmp_path):
    db = _make_db(tmp_path)
    work = db.create_work("W")
    for missing in ("frame_node_id", "frame_source_ref", "evidence_absent"):
        with pytest.raises(ValueError, match=missing):
            _valid_gap(db, work["id"], **{missing: "   "})
    assert db.list_gaps(work["id"]) == []


def test_gap_content_hash_identity(tmp_path):
    db = _make_db(tmp_path)
    work = db.create_work("W")
    a = _valid_gap(db, work["id"], centrality=1, dependent_count=1)
    b = _valid_gap(
        db, work["id"], centrality=9, dependent_count=3, evidence_absent="now cited 9× more"
    )
    assert a["id"] == b["id"]
    rows = db.list_gaps(work["id"])
    assert len(rows) == 1
    assert rows[0]["severity"] == "high"  # evidence + derived severity refreshed in place
    assert rows[0]["status"] == "proposed"  # lifecycle untouched
    # Different scope → different identity
    c = _valid_gap(db, work["id"], scope="jones 2001", frame_node_id="citation:jones 2001")
    assert c["id"] != a["id"]


# ── lifecycle ─────────────────────────────────────────────────────────────────


def test_gap_lifecycle_forward_and_ledgered(tmp_path):
    db = _make_db(tmp_path)
    work = db.create_work("W")
    gap = _valid_gap(db, work["id"])
    for status in ("ratified", "assigned", "researched", "covered", "mastered"):
        row = db.transition_gap(gap["id"], status)
        assert row["status"] == status
    ledger = db.list_gap_transitions(gap["id"])
    # creation ('' → proposed) + 5 transitions, all recorded
    assert [t["to_status"] for t in ledger] == [
        "proposed",
        "ratified",
        "assigned",
        "researched",
        "covered",
        "mastered",
    ]


def test_gap_illegal_transition_refused(tmp_path):
    db = _make_db(tmp_path)
    work = db.create_work("W")
    gap = _valid_gap(db, work["id"])
    with pytest.raises(ValueError, match="illegal"):
        db.transition_gap(gap["id"], "mastered")  # proposed → mastered skips steps
    with pytest.raises(ValueError, match="illegal"):
        db.transition_gap(gap["id"], "proposed")  # no self/backward from proposed


def test_gap_dismissal_requires_reason_and_signature(tmp_path):
    db = _make_db(tmp_path)
    work = db.create_work("W")
    gap = _valid_gap(db, work["id"])
    with pytest.raises(ValueError, match="reason and a signature"):
        db.transition_gap(gap["id"], "dismissed")
    with pytest.raises(ValueError, match="reason and a signature"):
        db.transition_gap(gap["id"], "out_of_scope", reason="not relevant")
    row = db.transition_gap(gap["id"], "dismissed", reason="duplicate", signed_by="ben")
    assert row["status"] == "dismissed"
    assert row["status_reason"] == "duplicate"
    assert row["signed_by"] == "ben"


def test_dismissed_gap_is_terminal_and_never_resurrected(tmp_path):
    db = _make_db(tmp_path)
    work = db.create_work("W")
    gap = _valid_gap(db, work["id"])
    db.transition_gap(gap["id"], "dismissed", reason="noise", signed_by="ben")
    # terminal — no transitions out
    with pytest.raises(ValueError, match="illegal"):
        db.transition_gap(gap["id"], "ratified", reason="x", signed_by="y")
    # re-detection maps to the same identity and must NOT resurrect
    again = _valid_gap(db, work["id"], centrality=12, dependent_count=4)
    assert again["id"] == gap["id"]
    assert again["status"] == "dismissed"
    assert db.get_gap(gap["id"])["status"] == "dismissed"


def test_gap_identity_is_work_scoped(tmp_path):
    """Two Works citing the same absent source keep independent gaps and
    independent dismissals — one Work's dismissal never suppresses another's."""
    db = _make_db(tmp_path)
    w1 = db.create_work("W1")
    w2 = db.create_work("W2")
    g1 = _valid_gap(db, w1["id"])
    g2 = _valid_gap(db, w2["id"])
    assert g1["id"] != g2["id"]
    assert len(db.list_gaps(w1["id"])) == 1
    assert len(db.list_gaps(w2["id"])) == 1
    db.transition_gap(g1["id"], "dismissed", reason="noise", signed_by="ben")
    assert db.get_gap(g2["id"])["status"] == "proposed"
    # re-detection in W2 still refreshes normally
    again = _valid_gap(db, w2["id"], centrality=9, dependent_count=3)
    assert again["id"] == g2["id"] and again["status"] == "proposed"


def test_gap_severity_is_derived_not_caller_assigned(tmp_path):
    """The insert path derives severity from evidence counts — callers cannot
    hand it an arbitrary severity."""
    db = _make_db(tmp_path)
    work = db.create_work("W")
    with pytest.raises(TypeError):
        _valid_gap(db, work["id"], severity="critical")
    row = _valid_gap(db, work["id"], centrality=9, dependent_count=3)
    from orivellum.capabilities.gap_engine import compute_severity

    assert row["severity"] == compute_severity("citation_closure", centrality=9, dependent_count=3)


# ── severity is computed, never model-asked ──────────────────────────────────


def test_compute_severity_is_deterministic():
    from orivellum.capabilities.gap_engine import compute_severity

    assert compute_severity("citation_closure", centrality=1, dependent_count=1) == "low"
    assert compute_severity("citation_closure", centrality=3, dependent_count=1) == "medium"
    assert compute_severity("citation_closure", centrality=8, dependent_count=2) == "high"
    assert (
        compute_severity(
            "citation_closure", centrality=8, dependent_count=2, blocking_active_work=True
        )
        == "critical"
    )


# ── citation-graph closure detector ───────────────────────────────────────────


def test_citation_closure_detector(tmp_path):
    from orivellum.capabilities.gap_engine import detect_citation_gaps

    db = _make_db(tmp_path)
    work = db.create_work("Bronze Age")
    _ready_doc(
        db,
        work["id"],
        "Held Study A",
        chunks=[
            "As Kramer (1963) showed, the archives agree. Kramer (1963) is definitive. "
            "Later work (Postgate, 1992, p. 44) refined the chronology.",
            "Kramer (1963) remains the touchstone for Sumerian sources.",
        ],
    )
    _ready_doc(
        db,
        work["id"],
        "Held Study B",
        chunks=["The tablets were catalogued by Kramer (1963) and revised (Postgate 1992)."],
    )
    # Postgate IS held in the Library (different work is fine — Library-wide check)
    other = db.create_work("Other")
    _ready_doc(db, other["id"], "Postgate - Early Mesopotamia")

    result = detect_citation_gaps(work["id"], db)
    assert result["scanned_docs"] == 2
    assert result["distinct_citations"] == 2
    assert result["held"] == 1  # Postgate held → no gap
    gaps = result["gaps"]
    assert len(gaps) == 1
    g = gaps[0]
    assert g["gap_class"] == "citation_closure"
    assert g["scope"] == "kramer 1963"
    assert g["frame_node_id"] == "citation:kramer 1963"
    assert g["frame_source_ref"].startswith("doc:")  # cites the demanding in-corpus doc
    assert "Kramer" in g["evidence_absent"]
    assert g["status"] == "proposed"

    # Re-run is idempotent — same row, no duplicates
    again = detect_citation_gaps(work["id"], db)
    assert len(again["gaps"]) == 1
    assert again["gaps"][0]["id"] == g["id"]
    assert len(db.list_gaps(work["id"])) == 1

    # Dismiss, then re-scan — the gap stays dismissed forever
    db.transition_gap(g["id"], "dismissed", reason="not a real source", signed_by="ben")
    rescan = detect_citation_gaps(work["id"], db)
    assert all(r["status"] == "dismissed" for r in rescan["gaps"] if r["id"] == g["id"])
    assert db.get_gap(g["id"])["status"] == "dismissed"


def test_citation_ranking_by_frequency(tmp_path):
    from orivellum.capabilities.gap_engine import detect_citation_gaps

    db = _make_db(tmp_path)
    work = db.create_work("W")
    _ready_doc(
        db,
        work["id"],
        "Doc",
        chunks=[
            "Rare (2010) appears once. Common (2005) again and again: Common (2005), "
            "Common (2005), Common (2005)."
        ],
    )
    result = detect_citation_gaps(work["id"], db)
    scopes = [g["scope"] for g in result["gaps"]]
    assert scopes[0] == "common 2005"  # most-cited absence ranks first
    assert "rare 2010" in scopes


def test_citation_extraction_skips_non_authors():
    from orivellum.capabilities.gap_engine import extract_citations

    text = "See Figure (2001) and Table (1999). But Smith (1998) is a real citation."
    assert extract_citations(text) == [("Smith", "1998")]


def test_citation_extraction_et_al_and_held_word_boundary(tmp_path):
    from orivellum.capabilities.gap_engine import _is_held, extract_citations

    assert ("Roux et al.", "2014") in extract_citations("As Roux et al. (2014) found.")
    assert ("Roux et al.", "2014") in extract_citations("It was shown (Roux et al., 2014).")
    # word-boundary held matching: "Smith" must not match "blacksmith"
    assert not _is_held("Smith", "1998", ["the blacksmith guild records 1998"])
    assert _is_held("Smith", "1998", ["smith - collected papers"])
    assert _is_held("Roux et al.", "2014", ["roux, ceramics and society"])


# ── API routes ────────────────────────────────────────────────────────────────


def test_research_gap_routes(tmp_path):
    client, db = _make_app(tmp_path)
    work = db.create_work("W")
    _ready_doc(db, work["id"], "Doc", chunks=["As Nobody (1970) claimed."])

    r = client.post(f"/api/works/{work['id']}/research-gaps/citation-scan")
    assert r.status_code == 200
    assert len(r.json()["gaps"]) == 1
    gap_id = r.json()["gaps"][0]["id"]

    r = client.get(f"/api/works/{work['id']}/research-gaps")
    assert r.status_code == 200
    assert r.json()["total"] == 1

    # dismissal without reason/signature → 422
    r = client.post(f"/api/research-gaps/{gap_id}/transition", json={"to_status": "dismissed"})
    assert r.status_code == 422

    r = client.post(
        f"/api/research-gaps/{gap_id}/transition",
        json={"to_status": "dismissed", "reason": "noise", "signed_by": "ben"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "dismissed"

    r = client.get(f"/api/research-gaps/{gap_id}/history")
    assert r.status_code == 200
    assert [t["to_status"] for t in r.json()["transitions"]] == ["proposed", "dismissed"]


# ── hygiene dismissal persistence ────────────────────────────────────────────


def test_hygiene_dismissal_never_reappears(tmp_path):
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    client, db = _make_app(tmp_path)
    work = db.create_work("W")
    # A ready doc with no chapters → 'undocumented_doc' hygiene finding
    _ready_doc(db, work["id"], "Structureless")

    report = detect_hygiene(work["id"], db)
    target = next(f for f in report.findings if f.kind == "undocumented_doc")
    assert target.finding_key.startswith("hyg-")

    r = client.post(
        f"/api/works/{work['id']}/hygiene/dismiss",
        json={"finding_key": target.finding_key, "reason": "known, fine"},
    )
    assert r.status_code == 200

    after = detect_hygiene(work["id"], db)
    assert all(f.finding_key != target.finding_key for f in after.findings)
    # and the endpoint (fresh detection) no longer serves it either
    r = client.get(f"/api/works/{work['id']}/gaps?refresh=true")
    assert all(g["finding_key"] != target.finding_key for g in r.json()["gaps"])
    # dismissal is idempotent
    db.dismiss_hygiene_finding(work["id"], target.finding_key, reason="again")
    assert target.finding_key in db.list_hygiene_dismissal_keys(work["id"])


def test_hygiene_sibling_findings_have_distinct_keys(tmp_path):
    """Two weak/uncovered chapters in the SAME document get distinct keys —
    dismissing one never hides the other."""
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    db = _make_db(tmp_path)
    work = db.create_work("W")
    doc_id = _ready_doc(db, work["id"], "TwoChapters")
    db.upsert_book_chapters(
        doc_id,
        work["id"],
        [
            {"seq": 1, "level": 1, "title": "Chapter One", "text": ""},
            {"seq": 2, "level": 1, "title": "Chapter Two", "text": ""},
        ],
    )
    report = detect_hygiene(work["id"], db)
    uncovered = [f for f in report.findings if f.kind == "uncovered_chapter"]
    assert len(uncovered) == 2
    keys = {f.finding_key for f in uncovered}
    assert len(keys) == 2
    # dismiss chapter one's finding — chapter two's must survive
    victim = next(f for f in uncovered if f.metadata.get("chapter_title") == "Chapter One")
    db.dismiss_hygiene_finding(work["id"], victim.finding_key, reason="fine")
    after = [f for f in detect_hygiene(work["id"], db).findings if f.kind == "uncovered_chapter"]
    assert len(after) == 1
    assert after[0].metadata.get("chapter_title") == "Chapter Two"


def test_hygiene_finding_keys_are_stable(tmp_path):
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    db = _make_db(tmp_path)
    work = db.create_work("W")
    _ready_doc(db, work["id"], "Structureless")
    k1 = {f.finding_key for f in detect_hygiene(work["id"], db).findings}
    k2 = {f.finding_key for f in detect_hygiene(work["id"], db).findings}
    assert k1 == k2 and k1
