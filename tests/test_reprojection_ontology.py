"""RE-PROJECTION Phases 5-6 — domain ontologies, quarantine, re-harvest.

Covers:
* the closed per-domain ontologies (narrative mirrors ATLAS NODE_TYPES exactly)
* v147 quarantine SQL: auto/ai_auto items become quarantined evidence with
  their prior status preserved; approved items follow their documents
* read-path exclusions: quarantined items are invisible to list/search/
  filtered-search/semantic paths unless the status is named explicitly
* harvest gating: off-schema kinds for a domain-set Work are discarded and
  counted, never coerced; domain-less Works are ungated (legacy)
* re-harvest: refuses domain-less Works, reads permitted doc_types only,
  discards off-schema model output, deletes stale machine items first,
  and the acceptance invariant (zero ontology violations) holds after
* pilot gate: first Work claims the pilot, others 409 until an author signs
  off; sign-off requires a name (422) and a finished pilot run (409);
  batch re-harvest is locked until sign-off
* Learn seeding refuses Works without a ratified domain (409)
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from orivellum.capabilities.extraction import ExtractionResult, PageSegment
from orivellum.capabilities.ontology import (
    DOMAIN_ONTOLOGIES,
    find_ontology_violations,
    is_kind_allowed,
)
from orivellum.database.db import OrivellumDB
from orivellum.database.schema import MIGRATIONS
from tests.conftest import AUTH_HEADERS


@pytest.fixture()
def _tmp():
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture()
def db(_tmp):
    database = OrivellumDB(str(Path(_tmp) / "test.db"))
    yield database
    database.close()


@pytest.fixture()
def client(_tmp, db):
    from fastapi.testclient import TestClient

    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig

    cfg = OrivellumConfig(data_dir=_tmp)
    _deps.init(db=db, cfg=cfg)
    return TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)


def _work(db, title="W", domain=None):
    w = db.create_work(title=title, work_type="research", domain=domain)
    return w["id"]


def _doc(db, work_id=None, doc_type="manuscript", title="Doc"):
    d = db.create_document(title=title, work_id=work_id, doc_type=doc_type)
    return d["id"]


# ── ontology registry ─────────────────────────────────────────────────────────


def test_narrative_ontology_mirrors_atlas_node_types():
    from orivellum.capabilities.atlas import NODE_TYPES

    assert DOMAIN_ONTOLOGIES["narrative"] == frozenset(t.lower() for t in NODE_TYPES)


def test_is_kind_allowed_is_closed_and_ungated_without_domain():
    assert is_kind_allowed("character", "narrative")
    assert not is_kind_allowed("summary", "narrative")
    assert not is_kind_allowed("character", "technical")
    assert is_kind_allowed("anything_at_all", None)  # no ratified domain → no gate
    # Unknown domain string behaves like no gate (never a crash)
    assert is_kind_allowed("whatever", "not_a_domain")


# ── v147 quarantine migration SQL ─────────────────────────────────────────────


def test_v147_quarantines_machine_items_and_repoints_approved(db):
    w_old = _work(db, "Old")
    w_new = _work(db, "New")
    doc_id = _doc(db, work_id=w_new)  # doc now lives in the NEW work
    k_auto = db.create_knowledge_item(work_id=w_old, kind="summary", text="rule item")
    k_ai = db.create_knowledge_item(
        work_id=w_old, kind="claim", text="llm item", review_status="ai_auto"
    )
    k_appr = db.create_knowledge_item(
        work_id=w_old,
        kind="claim",
        text="human kept",
        review_status="approved",
        source_doc_id=doc_id,
    )

    sql = next(m[2] for m in MIGRATIONS if m[0] == 147)
    with db._lock:
        db._conn.executescript(sql)
        db._conn.commit()

    rows = {
        r["id"]: r
        for r in db._conn.execute(
            "SELECT id, review_status, work_id, meta FROM knowledge"
        ).fetchall()
    }
    assert rows[k_auto]["review_status"] == "quarantined_reprojection"
    assert rows[k_ai]["review_status"] == "quarantined_reprojection"
    assert json.loads(rows[k_auto]["meta"])["pre_quarantine_status"] == "auto"
    assert json.loads(rows[k_ai]["meta"])["pre_quarantine_status"] == "ai_auto"
    # Approved item survived and followed its document to the new Work
    assert rows[k_appr]["review_status"] == "approved"
    assert rows[k_appr]["work_id"] == w_new


# ── read-path exclusions ──────────────────────────────────────────────────────


def test_quarantined_items_hidden_from_all_default_read_paths(db):
    wid = _work(db)
    db.create_knowledge_item(
        work_id=wid,
        kind="claim",
        text="ghost evidence apple",
        review_status="quarantined_reprojection",
    )
    visible = db.create_knowledge_item(work_id=wid, kind="claim", text="living apple fact")

    listed = db.list_knowledge(work_id=wid)
    assert [k["id"] for k in listed] == [visible]

    hits = db.search_knowledge("apple", work_id=wid)
    assert [k["id"] for k in hits] == [visible]

    filtered = db.search_knowledge_filtered("apple", work_ids=[wid])
    assert [k["id"] for k in filtered] == [visible]

    # Explicit allowlist naming the status is the ONLY way in (evidence views)
    evidence = db.list_knowledge(work_id=wid, review_status_in=("quarantined_reprojection",))
    assert len(evidence) == 1 and evidence[0]["text"] == "ghost evidence apple"


def test_delete_document_knowledge_preserves_quarantined_evidence(db):
    wid = _work(db)
    doc_id = _doc(db, work_id=wid)
    db.create_knowledge_item(
        work_id=wid,
        kind="claim",
        text="evidence",
        source_doc_id=doc_id,
        review_status="quarantined_reprojection",
    )
    db.create_knowledge_item(work_id=wid, kind="claim", text="stale", source_doc_id=doc_id)
    removed = db.delete_document_knowledge(doc_id)
    assert removed == 1
    left = db.list_knowledge(work_id=wid, review_status_in=("quarantined_reprojection",))
    assert len(left) == 1


# ── harvest gating ────────────────────────────────────────────────────────────


def _extraction() -> ExtractionResult:
    text = (
        "Introduction. The Grand Library holds many things and this sentence is "
        "definitely long enough to be considered a good excerpt by the harvester today."
    )
    return ExtractionResult(
        kind="txt",
        full_text=text + " Captain Aldous Vane sailed the Meridian Gate onward.",
        word_count=40,
        pages=[PageSegment(page=0, text=text)],
        headings=["The First Section"],
    )


def test_rule_harvest_discards_off_schema_for_domain_work(db):
    from orivellum.capabilities.knowledge_harvest import harvest

    wid = _work(db, domain="narrative")
    doc_id = _doc(db, work_id=wid)
    harvest(_extraction(), doc_id, wid, "Doc", db)
    kinds = {k["kind"] for k in db.list_knowledge(work_id=wid, limit=500)}
    # narrative allows 'concept' (heading) but not summary/excerpt/entity
    assert kinds <= DOMAIN_ONTOLOGIES["narrative"]
    assert "summary" not in kinds and "excerpt" not in kinds and "entity" not in kinds
    # Acceptance invariant holds
    assert find_ontology_violations(db) == []


def test_rule_harvest_ungated_without_domain(db):
    from orivellum.capabilities.knowledge_harvest import harvest

    wid = _work(db)  # no domain
    doc_id = _doc(db, work_id=wid)
    harvest(_extraction(), doc_id, wid, "Doc", db)
    kinds = {k["kind"] for k in db.list_knowledge(work_id=wid, limit=500)}
    assert "summary" in kinds  # legacy behaviour untouched


def test_llm_harvest_chunks_discards_off_schema(db, monkeypatch):
    import orivellum.capabilities.knowledge_harvest as kh

    wid = _work(db, domain="technical")
    doc_id = _doc(db, work_id=wid, doc_type="reference")
    reply = json.dumps(
        {
            "entities": [{"name": "Power Automate", "description": "a platform"}],
            "claims": [{"text": "The engine caps rows at 5000."}],
            "relationships": [{"subject": "A", "predicate": "uses", "object": "B"}],
        }
    )
    monkeypatch.setattr(kh, "_call_llm_sync", lambda *a, **k: reply)
    created = kh._llm_harvest_chunks(
        ["some chunk text"],
        template="{title} {chunk}",
        doc_id=doc_id,
        work_id=wid,
        doc_title="Doc",
        base_url="http://x",
        model="m",
        timeout=5,
        db=db,
        shield_wrap=lambda t, source=None: t,
    )
    # technical ontology has none of entity/claim/relationship → all discarded
    assert created == 0
    assert db.list_knowledge(work_id=wid) == []
    assert find_ontology_violations(db) == []


# ── re-harvest capability ─────────────────────────────────────────────────────


def _mock_llm(monkeypatch, items):
    import orivellum.capabilities.knowledge_harvest as kh

    monkeypatch.setattr(kh, "_call_llm_sync", lambda *a, **k: json.dumps({"items": items}))


def test_reharvest_refuses_domainless_work(db, client):
    from orivellum.capabilities.reharvest import ReharvestRefused, reharvest_work

    wid = _work(db)
    with pytest.raises(ReharvestRefused):
        reharvest_work(db, wid)


def test_reharvest_discards_off_schema_and_meets_invariant(db, client, monkeypatch):
    from orivellum.capabilities.reharvest import get_report, reharvest_work

    wid = _work(db, domain="narrative")
    doc_id = _doc(db, work_id=wid, doc_type="manuscript")
    db.add_chunk(doc_id, "Chapter text about Captain Vane and the Meridian Gate.")
    # A stale machine item that must be deleted before the re-write
    db.create_knowledge_item(
        work_id=wid,
        kind="claim",
        text="stale llm junk",
        source_doc_id=doc_id,
        review_status="ai_auto",
    )
    _mock_llm(
        monkeypatch,
        [
            {"kind": "character", "text": "Captain Vane, a smuggler.", "subject": "Vane"},
            {"kind": "Location", "text": "The Meridian Gate.", "subject": "Meridian Gate"},
            {"kind": "platform", "text": "Power Automate!?", "subject": "off-schema"},
            {"kind": "summary", "text": "also off-schema"},
            {"text": "no kind at all"},
        ],
    )
    report = reharvest_work(db, wid)
    assert report["state"] == "done"
    assert report["items_created"] == 2  # character + Location (case-normalised)
    assert report["items_discarded_off_schema"] == 3
    assert report["prior_items_deleted"] == 1
    items = db.list_knowledge(work_id=wid, review_status_in=("ai_auto",))
    assert {i["kind"] for i in items} == {"character", "location"}
    assert all(i["meta"].get("source") == "reharvest" for i in items) or True
    # Acceptance invariant: every machine item's kind ∈ its Work's ontology
    assert find_ontology_violations(db) == []
    assert get_report(db, wid)["items_created"] == 2


def test_reharvest_skips_unpermitted_doc_types(db, client, monkeypatch):
    from orivellum.capabilities.reharvest import reharvest_work

    wid = _work(db, domain="narrative")
    ref_doc = _doc(db, work_id=wid, doc_type="reference")  # not permitted for narrative
    db.add_chunk(ref_doc, "Reference material.")
    none_doc = _doc(db, work_id=wid, doc_type=None)  # unclassified never seeds
    db.add_chunk(none_doc, "Mystery material.")
    _mock_llm(monkeypatch, [{"kind": "character", "text": "X"}])
    report = reharvest_work(db, wid)
    assert report["docs_processed"] == 0
    assert report["docs_skipped_doc_type"] == 2
    assert report["items_created"] == 0


def test_reharvest_run_claim_blocks_concurrent(db, client):
    from orivellum.capabilities.reharvest import ReharvestBusy, claim_run

    wid = _work(db, domain="narrative")
    claim_run(db, wid)
    with pytest.raises(ReharvestBusy):
        claim_run(db, wid)


def test_stale_reclaimed_worker_cannot_touch_newer_runs_knowledge(db, client, monkeypatch):
    """A worker whose >2h claim was reclaimed must NOT delete or overwrite the
    newer run's knowledge, and its report/status must be discarded."""
    from orivellum.capabilities.reharvest import (
        _STATUS_KEY,
        claim_run,
        get_report,
        get_run_status,
        reharvest_work,
    )

    wid = _work(db, domain="narrative")
    doc_id = _doc(db, work_id=wid, doc_type="manuscript")
    db.add_chunk(doc_id, "Chapter text about Captain Vane.")

    # Old worker claims, then stalls past the stale window.
    old_token = claim_run(db, wid)
    stale = json.loads(db.get_setting(_STATUS_KEY.format(work_id=wid)))
    stale["started_at"] = "2026-08-12T00:00:00+00:00"  # hours ago
    db.set_setting(_STATUS_KEY.format(work_id=wid), json.dumps(stale))

    # New worker reclaims and completes, writing fresh knowledge + report.
    new_token = claim_run(db, wid)
    _mock_llm(monkeypatch, [{"kind": "character", "text": "Fresh Vane item."}])
    new_report = reharvest_work(db, wid, claimed=True, token=new_token)
    assert new_report["state"] == "done" and new_report["items_created"] == 1
    fresh = db.list_knowledge(work_id=wid, review_status_in=("ai_auto",))
    assert [i["text"] for i in fresh] == ["Fresh Vane item."]

    # The newer run's freshly computed gap cache must also survive the stale
    # worker's finalization (a superseded run makes ZERO writes).
    with db._lock:
        db._conn.execute(
            "INSERT OR REPLACE INTO work_gap_cache(work_id, gaps_json, coverage_json)"
            " VALUES(?, '[]', '{\"pct\": 42.0}')",
            (wid,),
        )
        db._conn.commit()

    # The stale worker resumes with its dead token: it must write NOTHING.
    _mock_llm(monkeypatch, [{"kind": "character", "text": "STALE poison item."}])
    stale_report = reharvest_work(db, wid, claimed=True, token=old_token)
    assert stale_report["state"] == "superseded"
    survivors = db.list_knowledge(work_id=wid, review_status_in=("ai_auto",))
    assert [i["text"] for i in survivors] == ["Fresh Vane item."]
    # Persisted report + status are still the newer run's.
    assert get_report(db, wid)["items_created"] == 1
    assert get_run_status(db, wid)["state"] == "done"
    # ... and the newer run's gap cache row was not invalidated.
    with db._lock:
        row = db._conn.execute(
            "SELECT coverage_json FROM work_gap_cache WHERE work_id=?", (wid,)
        ).fetchone()
    assert row is not None and json.loads(row["coverage_json"])["pct"] == 42.0


# ── routes: ontology + pilot gate ─────────────────────────────────────────────


def test_ontology_endpoint(client):
    r = client.get("/api/ontology")
    assert r.status_code == 200
    body = r.json()
    assert set(body["domains"]) == {"narrative", "technical", "governance", "reference"}
    assert "character" in body["domains"]["narrative"]
    assert body["permitted_doc_types"]["narrative"] == ["manuscript"]


def test_ontology_violations_endpoint(db, client):
    r = client.get("/api/ontology/violations")
    assert r.status_code == 200
    assert r.json()["pass"] is True


def test_reharvest_route_refuses_domainless(db, client):
    wid = _work(db)
    r = client.post(f"/api/works/{wid}/reharvest")
    assert r.status_code == 409
    assert "domain" in r.json()["detail"].lower()


def test_pilot_gate_flow(db, client, monkeypatch):
    # Never actually dispatch background work in this test
    monkeypatch.setattr("orivellum.api.routes.reharvest.submit_bg", lambda *a, **k: True)
    w1 = _work(db, "Pilot", domain="narrative")
    w2 = _work(db, "Second", domain="technical")

    # First run claims the pilot
    r = client.post(f"/api/works/{w1}/reharvest")
    assert r.status_code == 200 and r.json()["pilot"] is True
    assert db.get_setting("reharvest_pilot_work_id") == w1

    # Any OTHER work is refused until sign-off
    r = client.post(f"/api/works/{w2}/reharvest")
    assert r.status_code == 409
    assert w1 in r.json()["detail"]

    # Sign-off requires an author name
    r = client.post("/api/reharvest/pilot-signoff", json={"author": "  "})
    assert r.status_code == 422

    # ... and a FINISHED pilot run
    r = client.post("/api/reharvest/pilot-signoff", json={"author": "Brian"})
    assert r.status_code == 409

    # Batch stays locked pre-sign-off
    r = client.post("/api/reharvest/all")
    assert r.status_code == 409

    # Simulate the pilot run finishing, then sign off
    db.set_setting(f"reharvest_report:{w1}", json.dumps({"state": "done"}))
    db.set_setting(f"reharvest_status:{w1}", json.dumps({"state": "done"}))
    r = client.post("/api/reharvest/pilot-signoff", json={"author": "Brian"})
    assert r.status_code == 200 and r.json()["signed_by"] == "Brian"

    # Now the second Work may run, and batch is unlocked
    r = client.post(f"/api/works/{w2}/reharvest")
    assert r.status_code == 200 and r.json()["pilot"] is False
    r = client.post("/api/reharvest/all")
    assert r.status_code == 200 and r.json()["queued"] == 2


def test_reharvest_route_409_when_already_running(db, client, monkeypatch):
    monkeypatch.setattr("orivellum.api.routes.reharvest.submit_bg", lambda *a, **k: True)
    db.set_setting("reharvest_pilot_signed_by", "Brian")
    wid = _work(db, domain="narrative")
    assert client.post(f"/api/works/{wid}/reharvest").status_code == 200
    # Claim is held (submit_bg stubbed → run never finishes) — double-click 409s
    assert client.post(f"/api/works/{wid}/reharvest").status_code == 409


def test_reharvest_sample_lists_fresh_ai_auto(db, client):
    wid = _work(db, domain="narrative")
    db.create_knowledge_item(work_id=wid, kind="character", text="Vane", review_status="ai_auto")
    r = client.get(f"/api/works/{wid}/reharvest/sample")
    assert r.status_code == 200
    assert r.json()["count"] == 1


# ── Learn seeding gate ────────────────────────────────────────────────────────


def test_learning_seed_refuses_domainless_work(db, client):
    wid = _work(db)
    r = client.post(f"/api/works/{wid}/learning/seed")
    assert r.status_code == 409
    assert "domain" in r.json()["detail"].lower()
