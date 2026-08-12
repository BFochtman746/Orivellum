"""RE-PROJECTION Phase 4 — content-derived Work proposals + signed ratification.

Covers:
* clustering derives proposals from document embeddings (never filenames)
* eligibility: ARTIFACT/SYSTEM tier + generated docs excluded, assigned docs excluded
* proposals are idempotent across re-runs (deterministic fingerprint)
* ratification requires an author signature AND a valid domain (422 pre-claim)
* a 422 leaves the proposal queued (never consumed)
* approve creates a Work, re-points members, records collection provenance
* reject leaves everything untouched
* the substrate invariant: document/chunk/vector counts never change
"""

from __future__ import annotations

import struct
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from orivellum.capabilities.work_proposals import generate_work_proposals
from orivellum.database.db import OrivellumDB
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


# ── helpers ───────────────────────────────────────────────────────────────────


def _vec(*floats: float) -> bytes:
    return struct.pack(f"<{len(floats)}f", *floats)


def _add_doc(
    db: OrivellumDB,
    title: str,
    emb: tuple[float, ...],
    text: str,
    *,
    collection_id: str | None = None,
    tier: str = "source",
    doc_type: str | None = None,
    work_id: str | None = None,
) -> str:
    doc = db.create_document(
        title=title,
        collection_id=collection_id,
        tier=tier,
        doc_type=doc_type,
        work_id=work_id,
    )
    did = doc["id"]
    cid = db.add_chunk(did, text)
    with db._lock:
        db._conn.execute(
            "INSERT INTO vectors(id,object_id,object_type,embedding,dim,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (str(uuid.uuid4()), cid, "chunk", _vec(*emb), len(emb), "2026-01-01"),
        )
        db._conn.commit()
    return did


def _counts(db: OrivellumDB) -> dict:
    with db._lock:
        return {
            "documents": db._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "chunks": db._conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "vectors": db._conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0],
        }


def _seed_two_subjects(db: OrivellumDB) -> tuple[list[str], list[str]]:
    """Two well-separated subjects, each spanning two collections (so the
    cross-collection merge fires and both become genuine proposals)."""
    c1 = db.create_collection("Import A", "zip")["id"]
    c2 = db.create_collection("Import B", "zip")["id"]
    quantum = []
    baking = []
    # 3 of each subject in each collection so both subjects form a within-
    # collection cluster and then merge across the two collections.
    for coll in (c1, c2):
        for i in range(3):
            quantum.append(
                _add_doc(
                    db,
                    f"q{coll[:4]}{i}",
                    (1.0, 0.02 * i, 0.0),
                    "quantum entanglement superposition qubit measurement quantum state",
                    collection_id=coll,
                )
            )
            baking.append(
                _add_doc(
                    db,
                    f"b{coll[:4]}{i}",
                    (0.0, 0.02 * i, 1.0),
                    "sourdough fermentation starter hydration crumb baking bread loaf",
                    collection_id=coll,
                )
            )
    return quantum, baking


# ── clustering ────────────────────────────────────────────────────────────────


def test_generate_proposals_derives_subjects(db):
    _seed_two_subjects(db)
    out = generate_work_proposals(db)
    assert out["status"] == "ok"
    props = db.list_work_proposals(status="proposed")
    assert len(props) == 2
    # Names are content-derived, not filenames (q0/b0…).
    for p in props:
        assert p["name_source"] in ("tfidf", "model")
        assert "q" not in p["suggested_name"].lower() or "quantum" in p["suggested_name"].lower()
        assert p["size"] == 6
        assert len(p["collection_spread"]) == 2  # cross-collection spread


def test_generate_proposals_idempotent(db):
    _seed_two_subjects(db)
    generate_work_proposals(db)
    first = {p["fingerprint"] for p in db.list_work_proposals()}
    generate_work_proposals(db)
    second = {p["fingerprint"] for p in db.list_work_proposals()}
    assert first == second
    assert len(db.list_work_proposals()) == 2  # no duplicates stacked


def test_excluded_tiers_and_generated_not_clustered(db):
    c = db.create_collection("Import", "zip")["id"]
    for i in range(3):
        _add_doc(
            db,
            f"a{i}",
            (1.0, 0.01 * i, 0.0),
            "artifact zip payload data",
            collection_id=c,
            tier="artifact",
        )
    for i in range(3):
        _add_doc(
            db,
            f"g{i}",
            (0.0, 0.01 * i, 1.0),
            "generated output rendered text",
            collection_id=c,
            doc_type="generated",
        )
    out = generate_work_proposals(db)
    assert out["eligible_docs"] == 0
    assert db.list_work_proposals() == []


def test_assigned_docs_not_reclustered(db):
    w = db.create_work("Existing")
    c = db.create_collection("Import", "zip")["id"]
    for i in range(3):
        _add_doc(
            db,
            f"x{i}",
            (1.0, 0.01 * i, 0.0),
            "already assigned subject text",
            collection_id=c,
            work_id=w["id"],
        )
    out = generate_work_proposals(db)
    assert out["eligible_docs"] == 0


# ── ratification gates (via the review resolver) ───────────────────────────────


def _resolve(client, proposal_id, **body):
    return client.post(f"/api/review/work_proposal:{proposal_id}/resolve", json=body)


def test_ratify_requires_signature(db, client):
    _seed_two_subjects(db)
    generate_work_proposals(db)
    pid = db.list_work_proposals()[0]["id"]
    r = _resolve(client, pid, decision="approve", domain="technical")
    assert r.status_code == 422
    # proposal still queued — a 422 must not consume it
    assert db.get_work_proposal(pid)["status"] == "proposed"


def test_ratify_requires_valid_domain(db, client):
    _seed_two_subjects(db)
    generate_work_proposals(db)
    pid = db.list_work_proposals()[0]["id"]
    r = _resolve(client, pid, decision="approve", author="Brian")
    assert r.status_code == 422
    r2 = _resolve(client, pid, decision="approve", author="Brian", domain="nonsense")
    assert r2.status_code == 422
    assert db.get_work_proposal(pid)["status"] == "proposed"


def test_ratify_creates_work_and_repoints_docs(db, client):
    quantum, _baking = _seed_two_subjects(db)
    generate_work_proposals(db)
    before = _counts(db)
    # find the quantum proposal
    props = db.list_work_proposals()
    target = next(p for p in props if set(p["member_doc_ids"]) == set(sorted(quantum)))
    r = _resolve(client, target["id"], decision="approve", author="Brian", domain="technical")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["linked"] == 6
    work_id = data["work_id"]
    work = db.get_work(work_id)
    assert work["domain"] == "technical"
    # docs re-pointed
    for did in quantum:
        assert db.get_document(did)["work_id"] == work_id
    # collection provenance recorded (2 collections contributed)
    provs = db.get_work_collections(work_id)
    assert sum(p["doc_count"] for p in provs) == 6
    assert len(provs) == 2
    # substrate invariant
    assert _counts(db) == before
    # proposal marked ratified with the created work id
    assert db.get_work_proposal(target["id"])["status"] == "ratified"
    assert db.get_work_proposal(target["id"])["work_id"] == work_id


def test_reject_leaves_everything_untouched(db, client):
    quantum, _b = _seed_two_subjects(db)
    generate_work_proposals(db)
    before = _counts(db)
    pid = db.list_work_proposals()[0]["id"]
    r = _resolve(client, pid, decision="reject", author="Brian")
    assert r.status_code == 200
    assert db.get_work_proposal(pid)["status"] == "rejected"
    # no docs assigned, no work created, counts unchanged
    for did in quantum:
        assert db.get_document(did)["work_id"] is None
    assert db.list_works() == []
    assert _counts(db) == before


def test_no_work_without_signed_ratification(db):
    """A Work only ever comes into existence through a signed proposal
    ratification (or the explicit manual create path) — generating proposals
    alone must never create a Work."""
    _seed_two_subjects(db)
    generate_work_proposals(db)
    assert db.list_works() == []


def test_reclassify_decision_rejected_and_proposal_stays_queued(db, client):
    _seed_two_subjects(db)
    generate_work_proposals(db)
    pid = db.list_work_proposals()[0]["id"]
    r = _resolve(client, pid, decision="reclassify", author="Brian", domain="technical")
    assert r.status_code == 400
    assert db.get_work_proposal(pid)["status"] == "proposed"
    assert db.list_works() == []


def test_mid_apply_failure_rolls_back_everything(db, client, monkeypatch):
    """If any approval side effect fails, the ENTIRE transaction — claim,
    Work, re-points, provenance — rolls back; the proposal stays queued."""
    quantum, _b = _seed_two_subjects(db)
    generate_work_proposals(db)
    before = _counts(db)
    pid = db.list_work_proposals()[0]["id"]

    def _boom(*a, **k):
        raise RuntimeError("injected failure")

    monkeypatch.setattr(db, "finalize_work_proposal", _boom)
    r = _resolve(client, pid, decision="approve", author="Brian", domain="technical")
    assert r.status_code == 500
    # proposal back to proposed — retryable
    assert db.get_work_proposal(pid)["status"] == "proposed"
    # no orphaned Work, no re-pointed docs, substrate unchanged
    assert db.list_works() == []
    for did in quantum:
        assert db.get_document(did)["work_id"] is None
    assert _counts(db) == before
    # and the retry succeeds once the failure is gone
    monkeypatch.undo()
    r2 = _resolve(client, pid, decision="approve", author="Brian", domain="technical")
    assert r2.status_code == 200


def test_ratify_skips_docs_no_longer_eligible(db, client):
    """A member doc that gained a work_id after the proposal was generated is
    skipped at ratification — never stolen from its owner."""
    quantum, _b = _seed_two_subjects(db)
    generate_work_proposals(db)
    props = db.list_work_proposals()
    target = next(p for p in props if set(p["member_doc_ids"]) == set(sorted(quantum)))
    other = db.create_work("Claimed elsewhere")
    stolen = quantum[0]
    assert db.update_document_work(stolen, other["id"])
    r = _resolve(client, target["id"], decision="approve", author="Brian", domain="technical")
    assert r.status_code == 200
    data = r.json()
    assert data["linked"] == 5
    assert data["skipped"] == 1
    # the previously assigned doc keeps its original owner
    assert db.get_document(stolen)["work_id"] == other["id"]


def test_ratify_uses_claimed_row_not_stale_snapshot(db, client, monkeypatch):
    """A concurrent generation refresh landing just before the claim must not
    make the created Work disagree with the ratified proposal row — the Work
    is built from the row read INSIDE the claim transaction."""
    import json

    quantum, _b = _seed_two_subjects(db)
    generate_work_proposals(db)
    props = db.list_work_proposals()
    target = next(p for p in props if set(p["member_doc_ids"]) == set(sorted(quantum)))
    pid = target["id"]
    new_members = sorted(quantum)[:3]  # the refresh shrank the cluster
    real_claim = db.claim_work_proposal

    def racing_claim(proposal_id, status, author):
        # Simulate a concurrent generation pass refreshing the still-proposed
        # row in the last instant before the claim lands.
        with db._lock:
            db._conn.execute(
                """UPDATE work_proposals
                   SET suggested_name=?, member_doc_ids=?, size=?
                   WHERE id=? AND status='proposed'""",
                ("Refreshed Subject", json.dumps(new_members), len(new_members), proposal_id),
            )
            db._maybe_commit()
        return real_claim(proposal_id, status, author)

    monkeypatch.setattr(db, "claim_work_proposal", racing_claim)
    r = _resolve(client, pid, decision="approve", author="Brian", domain="technical")
    assert r.status_code == 200
    data = r.json()
    # the Work reflects the refreshed (claimed) row, not the stale snapshot
    assert data["linked"] == 3
    assert db.get_work(data["work_id"])["title"] == "Refreshed Subject"
    for did in set(quantum) - set(new_members):
        assert db.get_document(did)["work_id"] is None


def test_ratify_invalidates_chunk_vector_cache(db, client, monkeypatch):
    """The chunk vector cache carries d.work_id from its JOIN on documents.
    If ratification re-points members without bumping that cache, work-scoped
    semantic search keeps seeing them as unassigned and returns nothing for
    the new Work.  Warm the cache first, ratify, then search scoped to the
    created Work — its member content must be retrievable immediately."""
    from orivellum.capabilities import embeddings

    quantum, _b = _seed_two_subjects(db)
    generate_work_proposals(db)
    props = db.list_work_proposals()
    target = next(p for p in props if set(p["member_doc_ids"]) == set(sorted(quantum)))

    embeddings.invalidate_vector_cache()
    # Query embedding aligned with the quantum docs' vectors (endpoint is down
    # in this container, so stub the query-embedding call).
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts, **kw: [[1.0, 0.0, 0.0]])

    # Warm the cache BEFORE ratification — entries carry work_id=None.
    warm = embeddings.semantic_search("quantum", db, "chunk")
    assert warm, "cache should be warm with unscoped hits"

    r = _resolve(client, target["id"], decision="approve", author="Brian", domain="technical")
    assert r.status_code == 200
    work_id = r.json()["work_id"]

    scoped = embeddings.semantic_search("quantum", db, "chunk", work_id=work_id)
    assert scoped, "newly ratified Work must retrieve its member content immediately"
    assert all(h["work_id"] == work_id for h in scoped)

    embeddings.invalidate_vector_cache()


def test_rerun_after_ratification_does_not_clobber(db, client):
    quantum, _b = _seed_two_subjects(db)
    generate_work_proposals(db)
    props = db.list_work_proposals()
    target = next(p for p in props if set(p["member_doc_ids"]) == set(sorted(quantum)))
    _resolve(client, target["id"], decision="approve", author="Brian", domain="technical")
    # Re-running the pass must not resurrect or duplicate the ratified proposal.
    out = generate_work_proposals(db)
    still = db.get_work_proposal(target["id"])
    assert still["status"] == "ratified"
    # the ratified members now have a work_id, so they're no longer eligible —
    # only the baking cluster remains proposable
    assert out["proposals_upserted"] <= 1
