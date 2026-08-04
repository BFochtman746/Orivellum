"""
WR-04 tests — plan tree + chapter contracts (spec §6)
======================================================

Covers:
  * plan_node CRUD and state machine
  * Top-down approval chain enforcement (parent must be APPROVED first)
  * CHANGE_REQUESTED cascade to descendants
  * chapter_contract evidence-packet gate
  * chapter_contract plan_node gate
  * check_chapter_contract_gate (draft readiness)
  * DB trigger: draft blocked without approved contract
  * Full demo-wr04 CLI command
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import pytest

from writing_architect.domain import db as dbm, policy
from writing_architect.cli import main as wa


# ── helpers ──────────────────────────────────────────────────────────────────

def _db() -> str:
    """Return a fresh temp DB path (file is created by init_db)."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    os.unlink(path)          # let init_db create it clean
    return path


def _book(db_path: str) -> str:
    """Create a minimal BookProject and return its ID."""
    rc = wa(["book-new", db_path, "--title", "Ash and Silence",
             "--author", "Test Author",
             "--audience", "adult literary fiction readers",
             "--reader-promise", "A woman reclaims her prophetic voice"])
    assert rc == 0
    conn = dbm.init_db(db_path)
    bid = conn.execute("SELECT id FROM book_project LIMIT 1").fetchone()["id"]
    conn.close()
    return bid


def _accepted_claim(db_path: str, book_id: str) -> str:
    """Seed a research question → source → claim → evidence → verify → accept.
    Returns the accepted claim ID."""
    rc = wa(["demo-wr02", db_path, book_id])
    assert rc == 0
    conn = dbm.init_db(db_path)
    cid = conn.execute(
        "SELECT id FROM claim WHERE book_id=? AND accepted=1 LIMIT 1", (book_id,)
    ).fetchone()["id"]
    conn.close()
    return cid


# ── plan_node creation ────────────────────────────────────────────────────────

def test_plan_add_creates_proposed_node():
    db = _db()
    bid = _book(db)
    rc = wa(["plan-add", db, bid, "--type", "promise",
             "--purpose", "A woman finds her voice"])
    assert rc == 0
    conn = dbm.init_db(db)
    row = conn.execute("SELECT * FROM plan_node WHERE book_id=?", (bid,)).fetchone()
    conn.close()
    assert row is not None
    assert row["state"] == "PROPOSED"
    assert row["node_type"] == "promise"
    assert row["parent_id"] is None


def test_plan_add_rejects_unknown_book():
    db = _db()
    dbm.init_db(db).close()
    rc = wa(["plan-add", db, "book_NOTEXIST", "--type", "promise"])
    assert rc == 2


def test_plan_add_child_node():
    db = _db()
    bid = _book(db)
    wa(["plan-add", db, bid, "--type", "promise", "--purpose", "Root promise"])
    conn = dbm.init_db(db)
    parent_id = conn.execute("SELECT id FROM plan_node WHERE book_id=?", (bid,)).fetchone()["id"]
    conn.close()
    rc = wa(["plan-add", db, bid, "--type", "part",
             "--purpose", "Part I", "--parent", parent_id])
    assert rc == 0
    conn = dbm.init_db(db)
    child = conn.execute(
        "SELECT * FROM plan_node WHERE parent_id=?", (parent_id,)
    ).fetchone()
    conn.close()
    assert child is not None
    assert child["node_type"] == "part"


# ── plan_node approval chain ──────────────────────────────────────────────────

def test_plan_approve_root_node_works():
    db = _db()
    bid = _book(db)
    wa(["plan-add", db, bid, "--type", "promise", "--purpose", "Root"])
    conn = dbm.init_db(db)
    nid = conn.execute("SELECT id FROM plan_node LIMIT 1").fetchone()["id"]
    conn.close()
    rc = wa(["plan-approve", db, nid, "--actor", "author"])
    assert rc == 0
    conn = dbm.init_db(db)
    state = conn.execute("SELECT state FROM plan_node WHERE id=?", (nid,)).fetchone()["state"]
    conn.close()
    assert state == "APPROVED"


def test_plan_approve_child_blocked_when_parent_proposed():
    db = _db()
    bid = _book(db)
    wa(["plan-add", db, bid, "--type", "promise", "--purpose", "Root"])
    conn = dbm.init_db(db)
    parent_id = conn.execute("SELECT id FROM plan_node LIMIT 1").fetchone()["id"]
    conn.close()
    wa(["plan-add", db, bid, "--type", "part", "--purpose", "Part I", "--parent", parent_id])
    conn = dbm.init_db(db)
    child_id = conn.execute(
        "SELECT id FROM plan_node WHERE parent_id=?", (parent_id,)
    ).fetchone()["id"]
    conn.close()
    # parent is still PROPOSED — child approval must be refused
    rc = wa(["plan-approve", db, child_id, "--actor", "author"])
    assert rc == 1  # REFUSED


def test_plan_approve_child_succeeds_after_parent_approved():
    db = _db()
    bid = _book(db)
    wa(["plan-add", db, bid, "--type", "promise", "--purpose", "Root"])
    conn = dbm.init_db(db)
    parent_id = conn.execute("SELECT id FROM plan_node LIMIT 1").fetchone()["id"]
    conn.close()
    wa(["plan-add", db, bid, "--type", "part", "--purpose", "Part I", "--parent", parent_id])
    conn = dbm.init_db(db)
    child_id = conn.execute(
        "SELECT id FROM plan_node WHERE parent_id=?", (parent_id,)
    ).fetchone()["id"]
    conn.close()
    # approve parent first
    assert wa(["plan-approve", db, parent_id, "--actor", "author"]) == 0
    # now child should be approvable
    assert wa(["plan-approve", db, child_id, "--actor", "author"]) == 0
    conn = dbm.init_db(db)
    state = conn.execute("SELECT state FROM plan_node WHERE id=?", (child_id,)).fetchone()["state"]
    conn.close()
    assert state == "APPROVED"


def test_plan_approve_nonexistent_node():
    db = _db()
    _book(db)
    rc = wa(["plan-approve", db, "pn_NOTEXIST", "--actor", "author"])
    assert rc == 2


def test_plan_approve_idempotent():
    """Approving an already-APPROVED node returns 0 without error."""
    db = _db()
    bid = _book(db)
    wa(["plan-add", db, bid, "--type", "promise", "--purpose", "Root"])
    conn = dbm.init_db(db)
    nid = conn.execute("SELECT id FROM plan_node LIMIT 1").fetchone()["id"]
    conn.close()
    assert wa(["plan-approve", db, nid, "--actor", "author"]) == 0
    assert wa(["plan-approve", db, nid, "--actor", "author"]) == 0  # second call is no-op


# ── plan-status ───────────────────────────────────────────────────────────────

def test_plan_status_returns_tree(capsys):
    db = _db()
    bid = _book(db)
    wa(["plan-add", db, bid, "--type", "promise", "--purpose", "A woman finds her voice"])
    rc = wa(["plan-status", db, bid])
    assert rc == 0
    out = capsys.readouterr().out
    assert "promise" in out.lower()


# ── plan-change-request ───────────────────────────────────────────────────────

def test_plan_change_request_requires_approved_node():
    db = _db()
    bid = _book(db)
    wa(["plan-add", db, bid, "--type", "promise", "--purpose", "Root"])
    conn = dbm.init_db(db)
    nid = conn.execute("SELECT id FROM plan_node LIMIT 1").fetchone()["id"]
    conn.close()
    # node is PROPOSED, not APPROVED — must be refused
    rc = wa(["plan-change-request", db, nid, "--actor", "author", "--reason", "needs rework"])
    assert rc == 1


def test_plan_change_request_sets_node_and_cascades():
    db = _db()
    bid = _book(db)
    # promise → part → chapter (all approved)
    wa(["plan-add", db, bid, "--type", "promise", "--purpose", "Root"])
    conn = dbm.init_db(db)
    promise_id = conn.execute("SELECT id FROM plan_node LIMIT 1").fetchone()["id"]
    conn.close()
    wa(["plan-add", db, bid, "--type", "part", "--purpose", "Part I", "--parent", promise_id])
    conn = dbm.init_db(db)
    part_id = conn.execute("SELECT id FROM plan_node WHERE parent_id=?", (promise_id,)).fetchone()["id"]
    conn.close()
    wa(["plan-add", db, bid, "--type", "chapter", "--purpose", "Ch1", "--parent", part_id])
    conn = dbm.init_db(db)
    chap_id = conn.execute("SELECT id FROM plan_node WHERE parent_id=?", (part_id,)).fetchone()["id"]
    conn.close()

    # approve all three
    wa(["plan-approve", db, promise_id, "--actor", "author"])
    wa(["plan-approve", db, part_id, "--actor", "author"])
    wa(["plan-approve", db, chap_id, "--actor", "author"])

    # change request on the part — chapter should also become CHANGE_REQUESTED
    rc = wa(["plan-change-request", db, part_id, "--actor", "author",
             "--reason", "structural rethink needed"])
    assert rc == 0

    conn = dbm.init_db(db)
    states = {
        r["id"]: r["state"]
        for r in conn.execute("SELECT id, state FROM plan_node WHERE book_id=?", (bid,)).fetchall()
    }
    conn.close()
    assert states[promise_id] == "APPROVED"       # parent unchanged
    assert states[part_id] == "CHANGE_REQUESTED"
    assert states[chap_id] == "CHANGE_REQUESTED"  # cascaded


def test_plan_change_request_blocks_child_approval():
    """A node in CHANGE_REQUESTED state cannot be approved.
    Also verifies that plan-add refuses to create children of CHANGE_REQUESTED nodes.
    """
    db = _db()
    bid = _book(db)
    # Create promise → part both PROPOSED
    wa(["plan-add", db, bid, "--type", "promise", "--purpose", "Root"])
    conn = dbm.init_db(db)
    promise_id = conn.execute("SELECT id FROM plan_node LIMIT 1").fetchone()["id"]
    conn.close()
    wa(["plan-add", db, bid, "--type", "part", "--purpose", "Part I", "--parent", promise_id])
    conn = dbm.init_db(db)
    part_id = conn.execute(
        "SELECT id FROM plan_node WHERE parent_id=?", (promise_id,)
    ).fetchone()["id"]
    conn.close()
    # Approve both
    wa(["plan-approve", db, promise_id, "--actor", "author"])
    wa(["plan-approve", db, part_id, "--actor", "author"])
    # Change-request promise → cascades to part
    wa(["plan-change-request", db, promise_id, "--actor", "author", "--reason", "needs work"])
    # Trying to approve part (now CHANGE_REQUESTED) must be refused
    rc = wa(["plan-approve", db, part_id, "--actor", "author"])
    assert rc == 1  # part is CHANGE_REQUESTED
    # Trying to add a new child under a CHANGE_REQUESTED node must also be refused
    rc2 = wa(["plan-add", db, bid, "--type", "chapter", "--purpose", "Ch1", "--parent", promise_id])
    assert rc2 == 1


# ── policy helpers ────────────────────────────────────────────────────────────

def test_check_plan_node_gate_no_parent():
    db = _db()
    bid = _book(db)
    conn = dbm.init_db(db)
    nid = dbm.new_id("pn_")
    conn.execute(
        "INSERT INTO plan_node(id,book_id,parent_id,node_type,purpose,state,created_utc)"
        " VALUES (?,?,NULL,'promise',?,?,?)",
        (nid, bid, "Root", "PROPOSED", dbm.now_utc()),
    )
    conn.commit()
    ok, msg = policy.check_plan_node_gate(conn, nid)
    conn.close()
    assert ok, msg


def test_check_plan_node_gate_change_requested_is_blocked():
    db = _db()
    bid = _book(db)
    conn = dbm.init_db(db)
    nid = dbm.new_id("pn_")
    conn.execute(
        "INSERT INTO plan_node(id,book_id,parent_id,node_type,state,created_utc)"
        " VALUES (?,?,NULL,'promise','CHANGE_REQUESTED',?)",
        (nid, bid, dbm.now_utc()),
    )
    conn.commit()
    ok, msg = policy.check_plan_node_gate(conn, nid)
    conn.close()
    assert not ok
    assert "CHANGE_REQUESTED" in msg


# ── chapter_contract ──────────────────────────────────────────────────────────

def test_contract_new_creates_unapproved_contract():
    db = _db()
    bid = _book(db)
    rc = wa(["contract-new", db, bid, "--purpose", "Establish the world"])
    assert rc == 0
    conn = dbm.init_db(db)
    row = conn.execute("SELECT * FROM chapter_contract WHERE book_id=?", (bid,)).fetchone()
    conn.close()
    assert row is not None
    assert row["approved"] == 0


def test_contract_approve_requires_evidence():
    db = _db()
    bid = _book(db)
    wa(["contract-new", db, bid, "--purpose", "Establish the world"])
    conn = dbm.init_db(db)
    cid = conn.execute("SELECT id FROM chapter_contract LIMIT 1").fetchone()["id"]
    conn.close()
    # no evidence added — must be refused
    rc = wa(["contract-approve", db, cid, "--actor", "author"])
    assert rc == 1


def test_contract_approve_requires_accepted_claim_not_just_any():
    """contract-evidence rejects an unaccepted claim."""
    db = _db()
    bid = _book(db)
    wa(["contract-new", db, bid, "--purpose", "Test"])
    # create an unaccepted claim
    wa(["question", db, bid, "--text", "Test question", "--actor", "author"])
    conn = dbm.init_db(db)
    qid = conn.execute("SELECT id FROM research_question LIMIT 1").fetchone()["id"]
    cid = conn.execute("SELECT id FROM chapter_contract LIMIT 1").fetchone()["id"]
    conn.close()
    # source
    wa(["source", db, bid, "--citation", "Test source", "--tier", "T2"])
    conn = dbm.init_db(db)
    sid = conn.execute("SELECT id FROM source LIMIT 1").fetchone()["id"]
    conn.close()
    # unaccepted claim
    wa(["claim", db, bid, "--text", "A test claim", "--question", qid])
    conn = dbm.init_db(db)
    claim_id = conn.execute("SELECT id FROM claim LIMIT 1").fetchone()["id"]
    conn.close()
    # try to add unaccepted claim to evidence packet — must be refused
    rc = wa(["contract-evidence", db, cid, "--claim", claim_id])
    assert rc == 1


def test_contract_approve_succeeds_with_evidence_and_no_plan_node():
    db = _db()
    bid = _book(db)
    accepted_claim = _accepted_claim(db, bid)
    wa(["contract-new", db, bid, "--purpose", "Establish the world"])
    conn = dbm.init_db(db)
    cid = conn.execute("SELECT id FROM chapter_contract LIMIT 1").fetchone()["id"]
    conn.close()
    assert wa(["contract-evidence", db, cid, "--claim", accepted_claim]) == 0
    assert wa(["contract-approve", db, cid, "--actor", "author"]) == 0
    conn = dbm.init_db(db)
    row = conn.execute("SELECT approved FROM chapter_contract WHERE id=?", (cid,)).fetchone()
    conn.close()
    assert row["approved"] == 1


def test_contract_approve_requires_plan_node_approved():
    db = _db()
    bid = _book(db)
    accepted_claim = _accepted_claim(db, bid)
    # create a PROPOSED plan node and link contract to it
    wa(["plan-add", db, bid, "--type", "chapter", "--purpose", "Ch1"])
    conn = dbm.init_db(db)
    pn_id = conn.execute("SELECT id FROM plan_node LIMIT 1").fetchone()["id"]
    conn.close()
    wa(["contract-new", db, bid, "--purpose", "Ch1 contract", "--plan-node", pn_id])
    conn = dbm.init_db(db)
    cid = conn.execute("SELECT id FROM chapter_contract WHERE plan_node_id=?", (pn_id,)).fetchone()["id"]
    conn.close()
    wa(["contract-evidence", db, cid, "--claim", accepted_claim])
    # plan_node is PROPOSED — contract approval must be refused
    rc = wa(["contract-approve", db, cid, "--actor", "author"])
    assert rc == 1


def test_contract_approve_succeeds_with_approved_plan_node():
    db = _db()
    bid = _book(db)
    accepted_claim = _accepted_claim(db, bid)
    wa(["plan-add", db, bid, "--type", "chapter", "--purpose", "Ch1"])
    conn = dbm.init_db(db)
    pn_id = conn.execute("SELECT id FROM plan_node LIMIT 1").fetchone()["id"]
    conn.close()
    wa(["plan-approve", db, pn_id, "--actor", "author"])
    wa(["contract-new", db, bid, "--purpose", "Ch1 contract", "--plan-node", pn_id])
    conn = dbm.init_db(db)
    cid = conn.execute("SELECT id FROM chapter_contract WHERE plan_node_id=?", (pn_id,)).fetchone()["id"]
    conn.close()
    wa(["contract-evidence", db, cid, "--claim", accepted_claim])
    rc = wa(["contract-approve", db, cid, "--actor", "author"])
    assert rc == 0


def test_contract_approve_idempotent():
    db = _db()
    bid = _book(db)
    accepted_claim = _accepted_claim(db, bid)
    wa(["contract-new", db, bid, "--purpose", "Test"])
    conn = dbm.init_db(db)
    cid = conn.execute("SELECT id FROM chapter_contract LIMIT 1").fetchone()["id"]
    conn.close()
    wa(["contract-evidence", db, cid, "--claim", accepted_claim])
    assert wa(["contract-approve", db, cid, "--actor", "author"]) == 0
    assert wa(["contract-approve", db, cid, "--actor", "author"]) == 0  # no-op


# ── check_chapter_contract_gate ───────────────────────────────────────────────

def test_contract_gate_refuses_unapproved_contract():
    db = _db()
    bid = _book(db)
    wa(["contract-new", db, bid, "--purpose", "Test"])
    conn = dbm.init_db(db)
    cid = conn.execute("SELECT id FROM chapter_contract LIMIT 1").fetchone()["id"]
    ok, msg = policy.check_chapter_contract_gate(conn, cid)
    conn.close()
    assert not ok
    assert "FM-09" in msg or "not yet approved" in msg


def test_contract_gate_passes_after_approval():
    db = _db()
    bid = _book(db)
    accepted_claim = _accepted_claim(db, bid)
    wa(["contract-new", db, bid, "--purpose", "Test"])
    conn = dbm.init_db(db)
    cid = conn.execute("SELECT id FROM chapter_contract LIMIT 1").fetchone()["id"]
    conn.close()
    wa(["contract-evidence", db, cid, "--claim", accepted_claim])
    wa(["contract-approve", db, cid, "--actor", "author"])
    conn = dbm.init_db(db)
    ok, msg = policy.check_chapter_contract_gate(conn, cid)
    conn.close()
    assert ok, msg


def test_contract_gate_refuses_when_plan_node_change_requested():
    db = _db()
    bid = _book(db)
    accepted_claim = _accepted_claim(db, bid)
    wa(["plan-add", db, bid, "--type", "chapter", "--purpose", "Ch1"])
    conn = dbm.init_db(db)
    pn_id = conn.execute("SELECT id FROM plan_node LIMIT 1").fetchone()["id"]
    conn.close()
    wa(["plan-approve", db, pn_id, "--actor", "author"])
    wa(["contract-new", db, bid, "--purpose", "Ch1 contract", "--plan-node", pn_id])
    conn = dbm.init_db(db)
    cid = conn.execute("SELECT id FROM chapter_contract WHERE plan_node_id=?", (pn_id,)).fetchone()["id"]
    conn.close()
    wa(["contract-evidence", db, cid, "--claim", accepted_claim])
    wa(["contract-approve", db, cid, "--actor", "author"])
    # Now file a change request on the plan node
    wa(["plan-change-request", db, pn_id, "--actor", "author", "--reason", "needs rework"])
    conn = dbm.init_db(db)
    ok, msg = policy.check_chapter_contract_gate(conn, cid)
    conn.close()
    # plan node is now CHANGE_REQUESTED — gate should refuse
    assert not ok
    assert "CHANGE_REQUESTED" in msg or "plan-approval chain" in msg


# ── DB trigger: draft blocked without approved contract ───────────────────────

def test_db_trigger_blocks_draft_without_approved_contract():
    """The trg_draft_requires_approved_contract trigger fires on INSERT."""
    db = _db()
    bid = _book(db)
    wa(["contract-new", db, bid, "--purpose", "Test"])
    conn = dbm.init_db(db)
    cid = conn.execute("SELECT id FROM chapter_contract LIMIT 1").fetchone()["id"]
    # contract is not approved — insert must raise
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO draft_unit(id,contract_id,prose,created_utc) VALUES (?,?,?,?)",
            (dbm.new_id("du_"), cid, "Some prose", dbm.now_utc()),
        )
    conn.close()


def test_db_trigger_allows_draft_with_approved_contract():
    db = _db()
    bid = _book(db)
    accepted_claim = _accepted_claim(db, bid)
    wa(["contract-new", db, bid, "--purpose", "Test"])
    conn = dbm.init_db(db)
    cid = conn.execute("SELECT id FROM chapter_contract LIMIT 1").fetchone()["id"]
    conn.close()
    wa(["contract-evidence", db, cid, "--claim", accepted_claim])
    wa(["contract-approve", db, cid, "--actor", "author"])
    conn = dbm.init_db(db)
    conn.execute(
        "INSERT INTO draft_unit(id,contract_id,prose,created_utc) VALUES (?,?,?,?)",
        (dbm.new_id("du_"), cid, "First draft prose.", dbm.now_utc()),
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM draft_unit WHERE contract_id=?", (cid,)).fetchone()[0]
    conn.close()
    assert n == 1


# ── contract_evidence_gate ────────────────────────────────────────────────────

def test_check_contract_evidence_gate_empty():
    db = _db()
    bid = _book(db)
    wa(["contract-new", db, bid, "--purpose", "Test"])
    conn = dbm.init_db(db)
    cid = conn.execute("SELECT id FROM chapter_contract LIMIT 1").fetchone()["id"]
    ok, msg = policy.check_contract_evidence_gate(conn, cid)
    conn.close()
    assert not ok
    assert "FM-CE" in msg


def test_check_contract_evidence_gate_with_accepted_claim():
    db = _db()
    bid = _book(db)
    accepted_claim = _accepted_claim(db, bid)
    wa(["contract-new", db, bid, "--purpose", "Test"])
    conn = dbm.init_db(db)
    cid = conn.execute("SELECT id FROM chapter_contract LIMIT 1").fetchone()["id"]
    conn.execute(
        "INSERT INTO contract_evidence(contract_id,claim_id) VALUES (?,?)",
        (cid, accepted_claim),
    )
    conn.commit()
    ok, msg = policy.check_contract_evidence_gate(conn, cid)
    conn.close()
    assert ok, msg


# ── full demo ─────────────────────────────────────────────────────────────────

def test_demo_wr04_exits_clean():
    """demo-wr04 must return 0 and leave an approved plan tree + contract."""
    db = _db()
    bid = _book(db)
    # demo-wr04 requires an accepted claim — seed one via demo-wr02
    assert wa(["demo-wr02", db, bid]) == 0
    rc = wa(["demo-wr04", db, bid])
    assert rc == 0
    conn = dbm.init_db(db)
    # Plan tree: all three nodes should be APPROVED
    nodes = conn.execute(
        "SELECT state FROM plan_node WHERE book_id=?", (bid,)
    ).fetchall()
    assert all(r["state"] == "APPROVED" for r in nodes)
    assert len(nodes) == 3
    # Contract: should be approved with an evidence packet
    contract = conn.execute(
        "SELECT id, approved FROM chapter_contract WHERE book_id=?", (bid,)
    ).fetchone()
    assert contract is not None
    assert contract["approved"] == 1
    ev_count = conn.execute(
        "SELECT COUNT(*) FROM contract_evidence WHERE contract_id=?",
        (contract["id"],),
    ).fetchone()[0]
    assert ev_count >= 1
    # Audit chain must be intact
    chain_ok, chain_msg = dbm.verify_audit_chain(conn)
    conn.close()
    assert chain_ok, chain_msg


def test_demo_wr04_requires_book():
    db = _db()
    dbm.init_db(db).close()
    rc = wa(["demo-wr04", db, "book_NOTEXIST"])
    assert rc == 2


def test_demo_wr04_requires_existing_accepted_claim():
    """demo-wr04 must fail if no accepted claim exists (no demo-wr02 run)."""
    db = _db()
    bid = _book(db)
    # do NOT run demo-wr02
    rc = wa(["demo-wr04", db, bid])
    assert rc == 2   # "no accepted claim found"


# ── plan-status smoke ─────────────────────────────────────────────────────────

def test_plan_status_after_demo(capsys):
    db = _db()
    bid = _book(db)
    wa(["demo-wr02", db, bid])
    wa(["demo-wr04", db, bid])
    rc = wa(["plan-status", db, bid])
    assert rc == 0
    out = capsys.readouterr().out
    assert "APPROVED" in out
    assert "chapter" in out.lower()


# ── audit chain survives all WR-04 operations ────────────────────────────────

def test_audit_chain_intact_after_wr04_operations():
    db = _db()
    bid = _book(db)
    wa(["demo-wr02", db, bid])
    wa(["demo-wr04", db, bid])
    # additional operations
    wa(["plan-add", db, bid, "--type", "scene", "--purpose", "A new scene"])
    conn = dbm.init_db(db)
    scene_id = conn.execute(
        "SELECT id FROM plan_node WHERE node_type='scene' LIMIT 1"
    ).fetchone()["id"]
    conn.close()
    ok, msg = dbm.verify_audit_chain(dbm.init_db(db))
    assert ok, msg
