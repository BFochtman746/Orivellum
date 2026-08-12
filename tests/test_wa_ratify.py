"""WA proposal → canon ratification bridge (task: one-click ratify).

Covers:
* single ratify: approved proposal → signed canon fact, status 'ratified',
  ratified_fact_id forward link, provenance source_ref from the proposal
* double-ratify refused (409); re-deciding a ratified proposal refused (409)
* batch ratify-approved: ratifies every approved row, reports refusals
  per proposal (non-series scope without a Work), leaves them 'approved'
* signature is mandatory (422)
* decomposer re-runs (INSERT OR IGNORE) never clobber a ratified row
* /architect list surfaces ratified status; /canon list shows the fact
"""

from __future__ import annotations

import sys
import tempfile
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

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


def _insert_proposal(
    db,
    *,
    status: str = "approved",
    scope: str = "series:The Harp in the Dark",
    classification: str = "HISTORICAL",
    title: str = "Uz location",
    text: str = "Job lived in the land of Uz",
) -> str:
    pid = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            """INSERT INTO wa_canon_proposals
               (id, fact_title, fact_text, classification, scope, source_path,
                source_location, status, created_at)
               VALUES (?,?,?,?,?,?,?,?,datetime('now'))""",
            (pid, title, text, classification, scope, "bible_data/job.md", "1:1", status),
        )
        db._conn.commit()
    return pid


def _prop_row(db, pid: str) -> dict:
    with db._lock:
        row = db._conn.execute("SELECT * FROM wa_canon_proposals WHERE id=?", (pid,)).fetchone()
    return dict(row)


# ── single ratify ─────────────────────────────────────────────────────────────


def test_ratify_approved_proposal_creates_fact(client, db):
    pid = _insert_proposal(db, status="approved")
    r = client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "Brian"})
    assert r.status_code == 200
    fact = r.json()["fact"]
    assert fact["signed_by"] == "Brian"
    assert fact["origin"] == "wa_archive"
    assert fact["proposal_id"] == pid
    assert fact["source_ref"] == "bible_data/job.md#1:1"
    row = _prop_row(db, pid)
    assert row["status"] == "ratified"
    assert row["ratified_fact_id"] == fact["id"]


def test_ratify_from_proposed_also_allowed(client, db):
    pid = _insert_proposal(db, status="proposed")
    r = client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "Brian"})
    assert r.status_code == 200
    assert _prop_row(db, pid)["status"] == "ratified"


def test_double_ratify_conflicts(client, db):
    pid = _insert_proposal(db, status="approved")
    first = client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "Brian"})
    assert first.status_code == 200
    r = client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "Brian"})
    assert r.status_code == 409
    # still exactly one fact for this proposal
    with db._lock:
        n = db._conn.execute(
            "SELECT COUNT(*) c FROM canon_fact WHERE proposal_id=?", (pid,)
        ).fetchone()["c"]
    assert n == 1


def test_ratify_rejected_proposal_conflicts(client, db):
    pid = _insert_proposal(db, status="rejected")
    r = client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "Brian"})
    assert r.status_code == 409


def test_ratify_requires_signature(client, db):
    pid = _insert_proposal(db, status="approved")
    r = client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "  "})
    assert r.status_code == 422
    assert _prop_row(db, pid)["status"] == "approved"


def test_ratify_unknown_proposal_404(client):
    r = client.post("/api/canon/proposals/missing/ratify", json={"author": "Brian"})
    assert r.status_code == 404


def test_non_series_scope_refused_without_work(client, db):
    pid = _insert_proposal(db, status="approved", scope="book:1")
    r = client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "Brian"})
    assert r.status_code == 422
    # refusal releases the claim — still ratifiable once a Work is given
    assert _prop_row(db, pid)["status"] == "approved"
    wid = db.create_work(title="Book One")["id"]
    r = client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "Brian", "work_id": wid})
    assert r.status_code == 200
    assert r.json()["fact"]["work_id"] == wid


# ── ratified proposals are final ──────────────────────────────────────────────


def test_reopen_ratified_proposal_refused(client, db):
    pid = _insert_proposal(db, status="approved")
    client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "Brian"})
    for status in ("proposed", "approved", "rejected"):
        r = client.patch(f"/api/wa/canon-proposals/{pid}", json={"status": status})
        assert r.status_code == 409, status
    assert _prop_row(db, pid)["status"] == "ratified"


def test_decomposer_rerun_preserves_ratified(client, db):
    """INSERT OR IGNORE on the content-hash id never clobbers a ratification."""
    pid = _insert_proposal(db, status="approved")
    client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "Brian"})
    with db._lock:
        db._conn.execute(
            """INSERT OR IGNORE INTO wa_canon_proposals
               (id, fact_title, fact_text, classification, scope, source_path,
                source_location, status, created_at)
               VALUES (?,?,?,?,?,?,?,'proposed',datetime('now'))""",
            (
                pid,
                "Uz location",
                "Job lived in the land of Uz",
                "HISTORICAL",
                "series:The Harp in the Dark",
                "bible_data/job.md",
                "1:1",
            ),
        )
        db._conn.commit()
    row = _prop_row(db, pid)
    assert row["status"] == "ratified"
    assert row["ratified_fact_id"]


# ── batch ratify ──────────────────────────────────────────────────────────────


def test_batch_ratifies_all_approved(client, db):
    a = _insert_proposal(db, status="approved", title="A", text="Fact A")
    b = _insert_proposal(db, status="approved", title="B", text="Fact B")
    untouched = _insert_proposal(db, status="proposed", title="C", text="Fact C")
    rejected = _insert_proposal(db, status="rejected", title="D", text="Fact D")
    r = client.post("/api/canon/proposals/ratify-approved", json={"author": "Brian"})
    assert r.status_code == 200
    j = r.json()
    assert sorted(j["ratified"]) == sorted([a, b])
    assert j["counts"] == {"ratified": 2, "refused": 0, "skipped": 0}
    assert _prop_row(db, untouched)["status"] == "proposed"
    assert _prop_row(db, rejected)["status"] == "rejected"


def test_batch_reports_refusals_individually(client, db):
    ok = _insert_proposal(db, status="approved", title="OK", text="Series fact")
    bad = _insert_proposal(
        db, status="approved", scope="book:2", title="Needs work", text="Book fact"
    )
    r = client.post("/api/canon/proposals/ratify-approved", json={"author": "Brian"})
    assert r.status_code == 200
    j = r.json()
    assert j["ratified"] == [ok] or ok in j["ratified"]
    assert len(j["refused"]) == 1 and j["refused"][0]["id"] == bad
    # refused row stays approved — author can ratify it with an explicit Work
    assert _prop_row(db, bad)["status"] == "approved"


def test_batch_requires_signature(client, db):
    _insert_proposal(db, status="approved")
    r = client.post("/api/canon/proposals/ratify-approved", json={"author": ""})
    assert r.status_code == 422


# ── surfacing ────────────────────────────────────────────────────────────────


def test_lists_reflect_ratified_state(client, db):
    pid = _insert_proposal(db, status="approved")
    fact_id = client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "Brian"}).json()[
        "fact"
    ]["id"]
    # /architect proposals list: status filter + forward link
    items = client.get("/api/wa/canon-proposals?status=ratified").json()["items"]
    assert [i["id"] for i in items] == [pid]
    assert items[0]["ratified_fact_id"] == fact_id
    # /canon facts list: the fact is there with provenance
    facts = client.get("/api/canon/facts").json()["facts"]
    match = [f for f in facts if f["id"] == fact_id]
    assert match and match[0]["origin"] == "wa_archive" and match[0]["proposal_id"] == pid


def _ratify_audit_count(db, pid: str) -> int:
    with db._lock:
        return db._conn.execute(
            "SELECT COUNT(*) c FROM audit_log WHERE operation='canon.proposal_ratified' "
            "AND object_id=?",
            (pid,),
        ).fetchone()["c"]


def test_governed_write_audited(client, db):
    pid = _insert_proposal(db, status="approved")
    client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "Brian"})
    assert _ratify_audit_count(db, pid) == 1


def test_no_audit_event_on_conflict_or_not_found(client, db):
    """Failed/no-op ratifications must emit NO successful audit event."""
    pid = _insert_proposal(db, status="rejected")
    assert (
        client.post(f"/api/canon/proposals/{pid}/ratify", json={"author": "Brian"}).status_code
        == 409
    )
    assert _ratify_audit_count(db, pid) == 0
    # double-ratify: exactly one event (the successful one), never two
    pid2 = _insert_proposal(db, status="approved")
    client.post(f"/api/canon/proposals/{pid2}/ratify", json={"author": "Brian"})
    client.post(f"/api/canon/proposals/{pid2}/ratify", json={"author": "Brian"})
    assert _ratify_audit_count(db, pid2) == 1
    # unknown id: no event at all
    client.post("/api/canon/proposals/ghost/ratify", json={"author": "Brian"})
    assert _ratify_audit_count(db, "ghost") == 0


def test_concurrent_double_ratify_single_fact(db):
    """Two simultaneous ratifications: exactly one wins, one fact exists."""
    import threading

    from orivellum.database.canon_store import CanonStore

    pid = _insert_proposal(db, status="approved")
    store = CanonStore(db)
    results: list[str] = []
    barrier = threading.Barrier(2)

    def go():
        barrier.wait()
        r = store.ratify_proposal(pid, decision="approve", author="Brian")
        results.append(r["result"])

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict", "ok"]
    with db._lock:
        n = db._conn.execute(
            "SELECT COUNT(*) c FROM canon_fact WHERE proposal_id=?", (pid,)
        ).fetchone()["c"]
    assert n == 1
    assert _prop_row(db, pid)["ratified_fact_id"]
    assert _ratify_audit_count(db, pid) == 1


def test_migration_backfill_marks_legacy_ratified(db):
    """The v150 backfill flips proposals already linked via canon_fact.proposal_id."""
    from orivellum.database.schema import MIGRATIONS

    backfill_sql = next(sql for version, _label, sql in MIGRATIONS if version == 150)
    # simulate the pre-v150 state: an 'approved' proposal whose fact already
    # exists (review-inbox ratification before the bridge shipped)
    pid = _insert_proposal(db, status="approved")
    fact_id = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            """INSERT INTO canon_fact
               (id, statement, classification, source_ref, signed_by, status,
                origin, proposal_id, created_at)
               VALUES (?,?,?,?,?,'active','wa_archive',?,datetime('now'))""",
            (fact_id, "Job lived in the land of Uz", "HISTORICAL", "job.md#1:1", "Brian", pid),
        )
        db._conn.execute("UPDATE wa_canon_proposals SET ratified_fact_id=NULL WHERE id=?", (pid,))
        db._conn.commit()
        # re-run only the backfill UPDATE (the ALTER already ran at db init)
        for stmt in backfill_sql.split(";"):
            if stmt.strip().upper().startswith("UPDATE"):
                db._conn.execute(stmt)
        db._conn.commit()
    row = _prop_row(db, pid)
    assert row["status"] == "ratified"
    assert row["ratified_fact_id"] == fact_id
