"""
First executable vertical slice (spec 14.1)
===========================================

Runs a governed mini-workflow end to end so the enforcement is visible:
create a book, define it, establish research (question -> claim -> evidence),
approve a chapter contract, draft against it, raise and close an editorial
finding, and walk the lifecycle. Along the way it deliberately triggers three
refusals to prove the gates are real, not decorative.
"""
from __future__ import annotations

import sqlite3

from .domain import db as dbm
from .domain import lifecycle, policy


def _step(n, text):
    print(f"[{n:>2}] {text}")


def run_first_vertical_slice(db_path: str) -> int:
    conn = dbm.init_db(db_path)
    author = "Brian Fochtman"

    _step(1, "Create BookProject (B0 INTAKE)")
    bid = dbm.new_id("book_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO book_project(id,title,author,form,state,created_utc,updated_utc)"
        " VALUES (?,?,?,?,?,?,?)",
        (bid, "Ash and Silence", author, "biblical historical fiction",
         "B0", now, now))
    conn.commit()
    dbm.audit(conn, author, "BOOK_CREATED", "book_project", bid)

    _step(2, "Advance B0->B1 (authority resolution) and B1->B2 (definition)")
    lifecycle.advance(conn, bid, actor=author, reason="intake complete")

    _step(3, "PROVE GATE: try to enter B3 before the book is defined -> must REFUSE")
    try:
        # jump ahead: we are at B1, advancing goes to B2 which is ungated;
        # then B2->B3 is gated on reader_promise+audience
        lifecycle.advance(conn, bid, actor=author, reason="to B2")
        lifecycle.advance(conn, bid, actor=author, reason="premature research")
        print("     UNEXPECTED: advance to B3 was allowed")
    except lifecycle.LifecycleError as e:
        print(f"     REFUSED as designed: {e}")

    _step(4, "Define the book (reader_promise + audience), then enter B3")
    conn.execute(
        "UPDATE book_project SET reader_promise=?, audience=?, updated_utc=? WHERE id=?",
        ("A grief-soaked account of exile and return.",
         "Adult literary/historical readers", dbm.now_utc(), bid))
    conn.commit()
    lifecycle.advance(conn, bid, actor=author, reason="book definition approved")
    print(f"     now at {lifecycle.current_state(conn, bid)}")

    _step(5, "Research: create question, source (T1), candidate claim")
    qid = dbm.new_id("q_")
    conn.execute(
        "INSERT INTO research_question(id,book_id,question,decision_informed,"
        "state,created_utc) VALUES (?,?,?,?,?,?)",
        (qid, bid, "What did the Babylonian deportation route look like?",
         "Chapter 3 travel scene geography", "OPEN", dbm.now_utc()))
    sid = dbm.new_id("src_")
    conn.execute(
        "INSERT INTO source(id,book_id,citation,edition,tier,created_utc)"
        " VALUES (?,?,?,?,?,?)",
        (sid, bid, "2 Kings 25 (NRSV)", "NRSV", "T1", dbm.now_utc()))
    cid = dbm.new_id("claim_")
    conn.execute(
        "INSERT INTO claim(id,book_id,question_id,proposition,claim_type,"
        "confidence,created_utc) VALUES (?,?,?,?,?,?,?)",
        (cid, bid, qid, "Judahite captives were led toward Riblah first.",
         "fact", "probable", dbm.now_utc()))
    conn.commit()

    _step(6, "PROVE GATE: accept the factual claim with NO evidence -> must REFUSE")
    try:
        conn.execute("UPDATE claim SET accepted=1 WHERE id=?", (cid,))
        conn.commit()
        print("     UNEXPECTED: claim accepted without evidence")
    except sqlite3.IntegrityError as e:
        conn.rollback()
        print(f"     REFUSED as designed: {e}")

    _step(7, "Attach an evidence unit (with location ref), then accept the claim")
    eid = dbm.new_id("ev_")
    conn.execute(
        "INSERT INTO evidence_unit(id,claim_id,source_id,passage,location_ref,"
        "stance,created_utc) VALUES (?,?,?,?,?,?,?)",
        (eid, cid, sid, "They took the king ... to Riblah", "2 Kings 25:6",
         "supports", dbm.now_utc()))
    conn.commit()
    conn.execute("UPDATE claim SET accepted=1, verifier=? WHERE id=?",
                 ("independent-verifier", cid))
    conn.commit()
    dbm.audit(conn, "independent-verifier", "CLAIM_ACCEPTED", "claim", cid)
    print("     claim accepted (evidence present, independent verifier recorded)")

    _step(8, "Advance to B4 architecture; create + approve a ChapterContract")
    lifecycle.advance(conn, bid, actor=author, reason="research baseline established")
    ccid = dbm.new_id("cc_")
    conn.execute(
        "INSERT INTO chapter_contract(id,book_id,version,purpose,structural_role,"
        "target_min,target_max,approved,created_utc) VALUES (?,?,?,?,?,?,?,?,?)",
        (ccid, bid, 1, "Deliver the deportation march and its dread",
         "escalation", 1800, 2600, 0, dbm.now_utc()))
    conn.execute(
        "INSERT INTO contract_evidence(contract_id,claim_id) VALUES (?,?)", (ccid, cid))
    conn.commit()

    _step(9, "PROVE GATE: draft against the UNAPPROVED contract -> must REFUSE")
    duid = dbm.new_id("du_")
    try:
        conn.execute(
            "INSERT INTO draft_unit(id,contract_id,version,prose,created_utc)"
            " VALUES (?,?,?,?,?)",
            (duid, ccid, 1, "The road bent north under a bruised sky...",
             dbm.now_utc()))
        conn.commit()
        print("     UNEXPECTED: draft written against unapproved contract")
    except sqlite3.IntegrityError as e:
        conn.rollback()
        print(f"     REFUSED as designed: {e}")

    _step(10, "Approve the contract, then draft succeeds")
    conn.execute("UPDATE chapter_contract SET approved=1 WHERE id=?", (ccid,))
    conn.commit()
    dbm.audit(conn, author, "CONTRACT_APPROVED", "chapter_contract", ccid)
    lifecycle.advance(conn, bid, actor=author, reason="architecture approved")
    conn.execute(
        "INSERT INTO draft_unit(id,contract_id,version,prose,created_utc)"
        " VALUES (?,?,?,?,?)",
        (duid, ccid, 1, "The road bent north under a bruised sky...", dbm.now_utc()))
    conn.commit()
    print(f"     drafted unit {duid} (now at {lifecycle.current_state(conn, bid)})")

    _step(11, "Raise a BLOCKER finding; confirm release is refused while it's open")
    fid = dbm.new_id("find_")
    conn.execute(
        "INSERT INTO editorial_finding(id,book_id,draft_unit_id,pass_type,severity,"
        "location,raised_by,state,created_utc) VALUES (?,?,?,?,?,?,?,?,?)",
        (fid, bid, duid, "continuity", "blocker", "para 2",
         "continuity-reviewer", "OPEN", dbm.now_utc()))
    conn.commit()
    ok, msg = policy.check_release_gates(conn, bid)
    print(f"     release gates: {'PASS' if ok else 'BLOCKED'} -> {msg}")

    _step(12, "PROVE GATE: same reviewer tries to close own blocker -> must REFUSE")
    try:
        conn.execute(
            "UPDATE editorial_finding SET state='RESOLVED', resolved_by=? WHERE id=?",
            ("continuity-reviewer", fid))
        conn.commit()
        print("     UNEXPECTED: reviewer closed their own blocker")
    except sqlite3.IntegrityError as e:
        conn.rollback()
        print(f"     REFUSED as designed: {e}")

    _step(13, "A DIFFERENT actor closes the blocker; release gates re-checked")
    conn.execute(
        "UPDATE editorial_finding SET state='RESOLVED', resolved_by=? WHERE id=?",
        ("lead-editor", fid))
    conn.commit()
    dbm.audit(conn, "lead-editor", "FINDING_RESOLVED", "editorial_finding", fid)
    ok, msg = policy.check_release_gates(conn, bid)
    print(f"     release gates: {'PASS' if ok else 'BLOCKED'} -> {msg}")

    _step(14, "Verify the tamper-evident audit chain")
    ok, msg = dbm.verify_audit_chain(conn)
    print(f"     {msg}")

    conn.close()
    print("\nVertical slice complete. Every REFUSED line above is the system "
          "enforcing a spec rule at the database or policy layer.")
    return 0
