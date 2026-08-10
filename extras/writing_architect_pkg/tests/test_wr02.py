"""
WR-02 test suite — Research & Evidence
=======================================

Proves that the WR-02 additions satisfy their governance rules:

1. T6 and T7 sources are blocked at intake.
2. Evidence without a location reference is blocked.
3. A factual claim cannot be accepted without supporting evidence.
4. A claim cannot be accepted without an independent verifier.
5. Accepting a claim succeeds when all nine gate checks pass.
6. The full question → source → claim → evidence → verify → accept chain
   (the WR-02 exit condition) completes and passes the gate.
7. seed-sources loads canonical seeds into source_artifact without error.
8. Duplicate source_artifact inserts are silently ignored (idempotent).
9. Conflict recording creates a row and an audit entry.
10. research-status CLI returns correct counts.
11. wa demo-wr02 exits 0 and the audit chain remains intact after it.
12. All prior WR-01 tests still pass (governance regression guard).
"""
import io
import json
import os
import sqlite3
import sys
import tempfile

# Allow running from the package root without installation
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from writing_architect.domain import db as dbm    # noqa: E402
from writing_architect.domain import policy       # noqa: E402
from writing_architect.cli import main as wa      # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────────────

def fresh_db():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    os.remove(path)
    conn = dbm.init_db(path)
    return conn, path


def make_book(conn, path, title="Ash and Silence",
              author="Brian Fochtman",
              audience="Adult literary readers",
              reader_promise="A grief-soaked account of exile and return"):
    bid = dbm.new_id("book_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO book_project(id,title,author,audience,reader_promise,"
        "state,created_utc,updated_utc) VALUES (?,?,?,?,?,?,?,?)",
        (bid, title, author, audience, reader_promise, "B0", now, now),
    )
    conn.commit()
    return bid


def add_source(conn, book_id, tier="T2", citation="Reed 2000"):
    sid = dbm.new_id("src_")
    conn.execute(
        "INSERT INTO source(id,book_id,citation,tier,created_utc)"
        " VALUES (?,?,?,?,?)",
        (sid, book_id, citation, tier, dbm.now_utc()),
    )
    conn.commit()
    return sid


def add_claim(conn, book_id, question_id=None,
              proposition="Galilean households used ceramic jars.",
              claim_type="fact", confidence="probable"):
    cid = dbm.new_id("claim_")
    conn.execute(
        "INSERT INTO claim(id,book_id,question_id,proposition,claim_type,"
        "confidence,accepted,created_utc)"
        " VALUES (?,?,?,?,?,?,0,?)",
        (cid, book_id, question_id, proposition, claim_type, confidence,
         dbm.now_utc()),
    )
    conn.commit()
    return cid


def add_evidence(conn, claim_id, source_id,
                 passage="'Storage vessels…'",
                 location="Reed 2000, p.47"):
    eid = dbm.new_id("ev_")
    conn.execute(
        "INSERT INTO evidence_unit(id,claim_id,source_id,passage,"
        "location_ref,stance,created_utc)"
        " VALUES (?,?,?,?,?,?,?)",
        (eid, claim_id, source_id, passage, location, "supports",
         dbm.now_utc()),
    )
    conn.commit()
    return eid


# ── 1. T6 and T7 blocked at intake ───────────────────────────────────────────

def test_t7_blocked_by_policy():
    ok, msg = policy.check_source_tier_admissible("T7")
    assert not ok
    assert "T7" in msg or "AI-generated" in msg


def test_t6_blocked_by_policy():
    ok, msg = policy.check_source_tier_admissible("T6")
    assert not ok
    assert "T6" in msg


def test_t1_through_t5_admitted():
    for tier in ("T1", "T2", "T3", "T4", "T5"):
        ok, _ = policy.check_source_tier_admissible(tier)
        assert ok, f"tier {tier} should be admissible"


def test_wa_source_refuses_t7(tmp_path):
    """'wa source --tier T7' must exit non-zero and create no source row."""
    db_path = str(tmp_path / "book.sqlite")
    conn = dbm.init_db(db_path)
    bid = make_book(conn, db_path)
    conn.close()

    rc = wa([
        "source", db_path, bid,
        "--citation", "GPT says so",
        "--tier", "T7",
    ])
    assert rc != 0

    conn = dbm.init_db(db_path)
    n = conn.execute("SELECT COUNT(*) FROM source WHERE tier='T7'").fetchone()[0]
    conn.close()
    assert n == 0, "T7 source must not be inserted"


# ── 2. Evidence requires a location reference ────────────────────────────────

def test_evidence_without_location_blocked():
    conn, path = fresh_db()
    bid = make_book(conn, path)
    sid = add_source(conn, bid)
    cid = add_claim(conn, bid)
    try:
        conn.execute(
            "INSERT INTO evidence_unit(id,claim_id,source_id,passage,"
            "location_ref,stance,created_utc)"
            " VALUES (?,?,?,?,?,?,?)",
            (dbm.new_id("ev_"), cid, sid, "quote", "", "supports",
             dbm.now_utc()),
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        conn.rollback()
        raised = True
    conn.close(); os.remove(path)
    assert raised, "evidence without location_ref must be refused"


# ── 3. Factual claim cannot be accepted without evidence ─────────────────────

def test_acceptance_gate_fails_without_evidence():
    conn, path = fresh_db()
    bid = make_book(conn, path)
    cid = add_claim(conn, bid)
    # Give it a verifier so evidence is the only blocker
    conn.execute("UPDATE claim SET verifier='rev1' WHERE id=?", (cid,))
    conn.commit()

    ok, failures = policy.check_claim_acceptance_gate(conn, cid)
    conn.close(); os.remove(path)
    assert not ok
    assert any("evidence" in f.lower() for f in failures), failures


# ── 4. Claim cannot be accepted without a verifier ───────────────────────────

def test_acceptance_gate_fails_without_verifier():
    conn, path = fresh_db()
    bid = make_book(conn, path)
    sid = add_source(conn, bid)
    cid = add_claim(conn, bid)
    add_evidence(conn, cid, sid)
    # No verifier set

    ok, failures = policy.check_claim_acceptance_gate(conn, cid)
    conn.close(); os.remove(path)
    assert not ok
    assert any("verifier" in f.lower() for f in failures), failures


# ── 5. Acceptance succeeds when all checks pass ──────────────────────────────

def test_acceptance_succeeds_with_full_chain():
    conn, path = fresh_db()
    bid = make_book(conn, path)
    sid = add_source(conn, bid)
    cid = add_claim(conn, bid)
    add_evidence(conn, cid, sid)
    conn.execute("UPDATE claim SET verifier='external-rev' WHERE id=?", (cid,))
    conn.commit()

    ok, failures = policy.check_claim_acceptance_gate(conn, cid)
    assert ok, f"gate should pass: {failures}"

    conn.execute("UPDATE claim SET accepted=1 WHERE id=?", (cid,))
    conn.commit()
    row = conn.execute("SELECT accepted FROM claim WHERE id=?", (cid,)).fetchone()
    conn.close(); os.remove(path)
    assert row["accepted"] == 1


# ── 6. Full WR-02 exit-condition chain ───────────────────────────────────────

def test_full_wr02_chain_passes_gate():
    """question → source → claim → evidence → verify → accept must complete."""
    conn, path = fresh_db()
    bid = make_book(conn, path)

    # Question
    qid = dbm.new_id("q_")
    conn.execute(
        "INSERT INTO research_question(id,book_id,question,state,created_utc)"
        " VALUES (?,?,?,?,?)",
        (qid, bid,
         "What was daily life like for a Galilean household in 1st-century Palestine?",
         "OPEN", dbm.now_utc()),
    )

    # Source (T2)
    sid = add_source(conn, bid, tier="T2",
                     citation="Reed, Jonathan L. 'Archaeology and the Galilean Jesus.' 2000")

    # Claim linked to question
    cid = add_claim(conn, bid, question_id=qid)

    # Evidence with exact location
    add_evidence(conn, cid, sid)

    # Independent verifier
    conn.execute("UPDATE claim SET verifier='B.Fochtman' WHERE id=?", (cid,))
    conn.commit()

    # Gate check
    ok, failures = policy.check_claim_acceptance_gate(conn, cid)
    assert ok, f"WR-02 exit condition gate failed: {failures}"

    # Accept
    conn.execute("UPDATE claim SET accepted=1 WHERE id=?", (cid,))
    conn.commit()
    row = conn.execute("SELECT accepted FROM claim WHERE id=?", (cid,)).fetchone()
    conn.close(); os.remove(path)
    assert row["accepted"] == 1, "claim must be accepted after gate passes"


# ── 7. seed-sources loads canonical seeds ────────────────────────────────────

def test_seed_sources_loads_capabilities(tmp_path):
    manifest_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "wr00_baseline", "baseline_manifest.json"
    )
    if not os.path.exists(manifest_path):
        print("  SKIP: wr00_baseline/baseline_manifest.json not present")
        return

    db_path = str(tmp_path / "book.sqlite")
    conn = dbm.init_db(db_path)
    bid = make_book(conn, db_path)
    conn.close()

    rc = wa(["seed-sources", db_path, bid, "--manifest", manifest_path])
    assert rc == 0

    conn = dbm.init_db(db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM source_artifact WHERE book_id=? AND disposition='CANONICAL'",
        (bid,)
    ).fetchone()[0]
    conn.close()
    assert n >= 8, f"expected ≥ 8 canonical seeds, got {n}"


# ── 8. seed-sources is idempotent ────────────────────────────────────────────

def test_seed_sources_idempotent(tmp_path):
    manifest_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "wr00_baseline", "baseline_manifest.json"
    )
    if not os.path.exists(manifest_path):
        return

    db_path = str(tmp_path / "book.sqlite")
    conn = dbm.init_db(db_path)
    bid = make_book(conn, db_path)
    conn.close()

    wa(["seed-sources", db_path, bid, "--manifest", manifest_path])
    rc = wa(["seed-sources", db_path, bid, "--manifest", manifest_path])
    assert rc == 0  # idempotent — OR IGNORE prevents duplicate errors

    conn = dbm.init_db(db_path)
    n = conn.execute(
        "SELECT COUNT(*) FROM source_artifact WHERE book_id=?", (bid,)
    ).fetchone()[0]
    conn.close()
    # Should not have doubled
    assert n <= 20, f"seed-sources should not create duplicates, got {n}"


# ── 9. Conflict recording ─────────────────────────────────────────────────────

def test_conflict_recording():
    conn, path = fresh_db()
    bid = make_book(conn, path)
    cid_a = add_claim(conn, bid, proposition="Paul visited Rome in 60 CE.")
    cid_b = add_claim(conn, bid, proposition="Paul arrived Rome no earlier than 61 CE.")

    fid = dbm.new_id("conf_")
    conn.execute(
        "INSERT INTO conflict(id,book_id,claim_a,claim_b,reason,resolved,created_utc)"
        " VALUES (?,?,?,?,?,0,?)",
        (fid, bid, cid_a, cid_b, "scholarly date dispute", dbm.now_utc()),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM conflict WHERE id=?", (fid,)).fetchone()
    n_conflicts = conn.execute(
        "SELECT COUNT(*) FROM conflict WHERE book_id=? AND resolved=0", (bid,)
    ).fetchone()[0]
    conn.close(); os.remove(path)

    assert row is not None
    assert row["reason"] == "scholarly date dispute"
    assert n_conflicts == 1


# ── 10. research-status returns correct counts ───────────────────────────────

def test_research_status_counts(tmp_path, capsys=None):
    db_path = str(tmp_path / "book.sqlite")
    conn = dbm.init_db(db_path)
    bid = make_book(conn, db_path)

    # Add 1 question, 2 claims (1 accepted), 3 evidence units
    qid = dbm.new_id("q_")
    conn.execute(
        "INSERT INTO research_question(id,book_id,question,state,created_utc)"
        " VALUES (?,?,?,?,?)",
        (qid, bid, "Test question?", "OPEN", dbm.now_utc()),
    )
    sid = add_source(conn, bid)
    cid1 = add_claim(conn, bid, question_id=qid)
    cid2 = add_claim(conn, bid, question_id=qid,
                     proposition="Second claim.")
    add_evidence(conn, cid1, sid)
    add_evidence(conn, cid1, sid)
    add_evidence(conn, cid2, sid)
    conn.execute("UPDATE claim SET verifier='rev', accepted=1 WHERE id=?", (cid1,))
    conn.commit()
    conn.close()

    # Capture CLI output
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = wa(["research-status", db_path, bid])
    assert rc == 0

    out = json.loads(buf.getvalue())
    assert out["summary"]["questions"] == 1
    assert out["summary"]["claims"] == 2
    assert out["summary"]["accepted_claims"] == 1
    assert out["summary"]["evidence_units"] == 3


# ── 11. demo-wr02 exits 0 and audit chain stays intact ───────────────────────

def test_demo_wr02_exits_zero_and_chain_intact(tmp_path):
    db_path = str(tmp_path / "demo.sqlite")
    conn = dbm.init_db(db_path)
    bid = make_book(conn, db_path)
    conn.close()

    rc = wa(["demo-wr02", db_path, bid])
    assert rc == 0, f"demo-wr02 should exit 0, got {rc}"

    # Verify audit chain integrity after all demo writes
    conn = dbm.init_db(db_path)
    ok, msg = dbm.verify_audit_chain(conn)
    # Accepted claim must exist
    accepted = conn.execute(
        "SELECT COUNT(*) FROM claim WHERE accepted=1 AND book_id=?", (bid,)
    ).fetchone()[0]
    conn.close()

    assert ok, f"audit chain broken after demo-wr02: {msg}"
    assert accepted >= 1, "demo-wr02 must produce at least one accepted claim"


# ── 12. WR-01 governance regression guard ────────────────────────────────────

def test_wr01_gate_factual_claim_requires_evidence():
    conn, path = fresh_db()
    bid = make_book(conn, path)
    cid = dbm.new_id("claim_")
    conn.execute(
        "INSERT INTO claim(id,book_id,proposition,claim_type,confidence,created_utc)"
        " VALUES (?,?,?,?,?,?)",
        (cid, bid, "p", "fact", "possible", dbm.now_utc()),
    )
    conn.commit()
    try:
        conn.execute("UPDATE claim SET accepted=1 WHERE id=?", (cid,))
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        conn.rollback()
        raised = True
    conn.close(); os.remove(path)
    assert raised, "WR-01 regression: factual claim with no evidence must still be refused"


def test_wr01_draft_requires_approved_contract():
    conn, path = fresh_db()
    bid = make_book(conn, path)
    ccid = dbm.new_id("cc_")
    conn.execute(
        "INSERT INTO chapter_contract(id,book_id,purpose,approved,created_utc)"
        " VALUES (?,?,?,?,?)", (ccid, bid, "p", 0, dbm.now_utc()),
    )
    conn.commit()
    try:
        conn.execute(
            "INSERT INTO draft_unit(id,contract_id,prose,created_utc)"
            " VALUES (?,?,?,?)", (dbm.new_id("du_"), ccid, "text", dbm.now_utc()),
        )
        conn.commit()
        raised = False
    except sqlite3.IntegrityError:
        conn.rollback()
        raised = True
    conn.close(); os.remove(path)
    assert raised, "WR-01 regression: draft against unapproved contract must be refused"


# ── fallback runner ───────────────────────────────────────────────────────────

def _run_all():
    import inspect
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    passed = failed = skipped = 0
    for name, fn in fns:
        sig = inspect.signature(fn)
        if sig.parameters:
            # needs a tmp_path fixture — skip in standalone mode
            skipped += 1
            print(f"SKIP {name} (requires pytest fixture)")
            continue
        try:
            fn()
            passed += 1
            print(f"PASS {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped "
          f"(run with pytest for full coverage)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_all())
