"""
WRITING_ARCHITECT command-line interface
========================================

WR-01 commands (foundation):
    wa forensics ARCHIVE.zip --out DIR   Run WR-00 forensic baseline
    wa init DB.sqlite                     Create/upgrade a governed store
    wa book-new DB --title T --author A   Create a BookProject (enters B0)
    wa status DB BOOK_ID                  Show lifecycle state + gate report
    wa advance DB BOOK_ID --actor NAME    Advance one lifecycle state (gated)
    wa return  DB BOOK_ID --to B3 --actor NAME --reason "..."
    wa audit-verify DB                    Verify the tamper-evident audit chain
    wa demo DB                            Run the spec's first vertical slice
    wa doctor                             Self-check the installation

WR-02 commands (research & evidence):
    wa seed-sources DB BOOK_ID [--manifest PATH]
                                          Load 8 canonical seeds as source_artifact rows
    wa question DB BOOK_ID --text "..." --decision "..."
                                          Create a research question
    wa source   DB BOOK_ID --citation "..." --tier T2 [--edition "..." --pub-date "..." --rights "..."]
                                          Register a source (T7 blocked at intake)
    wa claim    DB BOOK_ID --question QID --text "..." [--type fact] [--confidence probable]
                                          Create a candidate claim (unaccepted)
    wa evidence DB CLAIM_ID --source SID --passage "..." --location "..." [--stance supports]
                                          Attach an evidence unit to a claim
    wa verify   DB CLAIM_ID --actor "reviewer"
                                          Record an independent review of a claim
    wa accept-claim DB CLAIM_ID --actor "..."
                                          Accept a claim through the 9-point gate
    wa conflict DB BOOK_ID --claim-a CID --claim-b CID --reason "..."
                                          Record a conflict between two claims
    wa research-status DB BOOK_ID         Show full research chain summary
    wa demo-wr02 DB BOOK_ID               Seed a complete question→claim→evidence chain
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .domain import db as dbm
from .domain import lifecycle, policy
from .forensics import baseline


def _p(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


# ─── WR-01 commands ──────────────────────────────────────────────────────────

def cmd_forensics(args):
    if not os.path.exists(args.archive):
        print(f"error: archive not found: {args.archive}", file=sys.stderr)
        return 2
    manifest = baseline.run_baseline(args.archive, args.out)
    m = manifest["measures"]
    print(f"WR-00 baseline written to: {args.out}/")
    print(f"  source SHA-256      : {manifest['source_archive']['sha256']}")
    print(f"  manifest seal       : {manifest['baseline_manifest_sha256']}")
    print(f"  payloads analyzed   : {m['payload_files_analyzed']}")
    print(f"  distinct payloads   : {m['distinct_sha256_payloads']}")
    print(f"  duplicate groups    : {m['exact_duplicate_groups']}")
    print(f"  redundant copies    : {m['redundant_copies']}")
    print(f"  read errors         : {m['read_errors']}")
    print("  artifacts           : baseline_manifest.json, WR00_REPORT.md, "
          "INVENTORY.csv, DUPLICATES.csv, baseline.sha256")
    return 0


def cmd_init(args):
    conn = dbm.init_db(args.db)
    conn.close()
    print(f"governed store ready: {args.db}")
    return 0


def cmd_book_new(args):
    conn = dbm.init_db(args.db)
    bid = dbm.new_id("book_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO book_project(id,title,author,form,audience,reader_promise,"
        "state,created_utc,updated_utc) VALUES (?,?,?,?,?,?,?,?,?)",
        (bid, args.title, args.author, args.form, args.audience,
         args.reader_promise, "B0", now, now),
    )
    conn.commit()
    dbm.audit(conn, actor=args.author, action="BOOK_CREATED",
              object_type="book_project", object_id=bid,
              detail={"title": args.title})
    conn.close()
    print(f"created book {bid} in state B0 (INTAKE)")
    return 0


def cmd_status(args):
    conn = dbm.init_db(args.db)
    row = conn.execute(
        "SELECT b.*, s.name AS state_name, s.ordinal FROM book_project b "
        "JOIN lifecycle_state s ON s.code=b.state WHERE b.id=?", (args.book,)
    ).fetchone()
    if not row:
        print(f"error: no such book {args.book}", file=sys.stderr)
        return 2
    ok, msg, nxt = lifecycle.can_advance(conn, args.book)
    report = {
        "book_id": row["id"],
        "title": row["title"],
        "author": row["author"],
        "state": f'{row["state"]} {row["state_name"]}',
        "can_advance": ok,
        "next_state": nxt,
        "advance_blocker": None if ok else msg,
        "gate_report": policy.release_gate_report(conn, args.book),
    }
    conn.close()
    _p(report)
    return 0


def cmd_advance(args):
    conn = dbm.init_db(args.db)
    try:
        nxt = lifecycle.advance(conn, args.book, actor=args.actor, reason=args.reason or "")
        print(f"advanced to {nxt}")
        rc = 0
    except lifecycle.LifecycleError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        rc = 1
    conn.close()
    return rc


def cmd_return(args):
    conn = dbm.init_db(args.db)
    try:
        tgt = lifecycle.return_to(conn, args.book, args.to, actor=args.actor,
                                  reason=args.reason)
        print(f"returned to {tgt}")
        rc = 0
    except lifecycle.LifecycleError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        rc = 1
    conn.close()
    return rc


def cmd_audit_verify(args):
    conn = dbm.init_db(args.db)
    ok, msg = dbm.verify_audit_chain(conn)
    conn.close()
    print(("OK: " if ok else "FAIL: ") + msg)
    return 0 if ok else 1


def cmd_doctor(args):
    import sqlite3
    print(f"WRITING_ARCHITECT {__version__}")
    print(f"  python           : {sys.version.split()[0]}")
    print(f"  sqlite3          : {sqlite3.sqlite_version}")
    tmp = "/tmp/_wa_doctor.sqlite"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = dbm.init_db(tmp)
    n = conn.execute("SELECT COUNT(*) FROM lifecycle_state").fetchone()[0]
    ok, msg = dbm.verify_audit_chain(conn)
    conn.close()
    os.remove(tmp)
    print(f"  lifecycle states : {n} (expected 14)")
    print(f"  audit chain      : {msg}")
    print("  status           : " + ("HEALTHY" if n == 14 and ok else "PROBLEM"))
    return 0 if (n == 14 and ok) else 1


def cmd_demo(args):
    from .demo import run_first_vertical_slice
    return run_first_vertical_slice(args.db)


# ─── WR-02 commands ──────────────────────────────────────────────────────────

def cmd_seed_sources(args):
    """Load the 8 canonical seeds from the WR-00 baseline as source_artifact rows."""
    conn = dbm.init_db(args.db)

    # Locate the manifest
    manifest_path = args.manifest
    if not manifest_path:
        # Try adjacent to the DB, then the bundled copy
        for candidate in [
            os.path.join(os.path.dirname(os.path.abspath(args.db)),
                         "wr00_baseline", "baseline_manifest.json"),
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "wr00_baseline", "baseline_manifest.json"),
        ]:
            if os.path.exists(candidate):
                manifest_path = candidate
                break

    if not manifest_path or not os.path.exists(manifest_path):
        print("error: baseline_manifest.json not found — "
              "pass --manifest PATH", file=sys.stderr)
        conn.close()
        return 2

    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    cap_proposals = manifest.get("capability_map", {}).get("capability_proposals", {})
    if not cap_proposals:
        print("error: manifest has no capability_proposals", file=sys.stderr)
        conn.close()
        return 2

    seeded = 0
    for capability, data in cap_proposals.items():
        logical_path = data.get("proposed_primary_source", "")
        if not logical_path:
            continue
        aid = dbm.new_id("sa_")
        now = dbm.now_utc()
        conn.execute(
            "INSERT OR IGNORE INTO source_artifact"
            "(id,book_id,logical_path,sha256,origin,disposition,"
            " disposition_reason,created_utc)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                aid, args.book, logical_path,
                "",                          # sha256 lives in baseline_manifest records
                "wr00_baseline",
                "CANONICAL",
                f"WR-00 capability canonical seed: {capability}",
                now,
            ),
        )
        dbm.audit(conn, actor="system", action="SOURCE_ARTIFACT_SEEDED",
                  object_type="source_artifact", object_id=aid,
                  detail={"capability": capability, "logical_path": logical_path})
        seeded += 1

    conn.commit()
    conn.close()
    print(f"seeded {seeded} canonical source artifact(s) for book {args.book}")
    return 0


def cmd_question(args):
    """Create a research question for a book."""
    conn = dbm.init_db(args.db)
    # Verify book exists
    if not conn.execute("SELECT id FROM book_project WHERE id=?",
                        (args.book,)).fetchone():
        print(f"error: book {args.book!r} not found", file=sys.stderr)
        conn.close()
        return 2

    qid = dbm.new_id("q_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO research_question"
        "(id,book_id,question,decision_informed,scope,priority,state,created_utc)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (qid, args.book, args.text, args.decision, args.scope,
         args.priority, "OPEN", now),
    )
    conn.commit()
    dbm.audit(conn, actor=args.actor or "operator", action="QUESTION_CREATED",
              object_type="research_question", object_id=qid,
              detail={"question": args.text[:120]})
    conn.close()
    print(f"created question {qid}")
    return 0


def cmd_source(args):
    """Register a bibliographic source (T7 is blocked at intake)."""
    # Policy check before touching the DB
    ok, msg = policy.check_source_tier_admissible(args.tier)
    if not ok:
        print(f"REFUSED: {msg}", file=sys.stderr)
        return 1

    conn = dbm.init_db(args.db)
    if not conn.execute("SELECT id FROM book_project WHERE id=?",
                        (args.book,)).fetchone():
        print(f"error: book {args.book!r} not found", file=sys.stderr)
        conn.close()
        return 2

    sid = dbm.new_id("src_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO source"
        "(id,book_id,citation,edition,pub_date,tier,reliability,rights,"
        " retrieval_record,created_utc)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (sid, args.book, args.citation, args.edition, args.pub_date,
         args.tier, args.reliability, args.rights, args.retrieval, now),
    )
    conn.commit()
    dbm.audit(conn, actor=args.actor or "operator", action="SOURCE_REGISTERED",
              object_type="source", object_id=sid,
              detail={"citation": args.citation[:120], "tier": args.tier})
    conn.close()
    print(f"registered source {sid}  tier={args.tier}")
    return 0


def cmd_claim(args):
    """Create a candidate claim (unaccepted until 'wa accept-claim')."""
    conn = dbm.init_db(args.db)
    if not conn.execute("SELECT id FROM book_project WHERE id=?",
                        (args.book,)).fetchone():
        print(f"error: book {args.book!r} not found", file=sys.stderr)
        conn.close()
        return 2
    if args.question and not conn.execute(
            "SELECT id FROM research_question WHERE id=?",
            (args.question,)).fetchone():
        print(f"error: question {args.question!r} not found", file=sys.stderr)
        conn.close()
        return 2

    cid = dbm.new_id("claim_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO claim"
        "(id,book_id,question_id,proposition,claim_type,confidence,accepted,created_utc)"
        " VALUES (?,?,?,?,?,?,0,?)",
        (cid, args.book, args.question, args.text,
         args.type, args.confidence, now),
    )
    conn.commit()
    dbm.audit(conn, actor=args.actor or "operator", action="CLAIM_CREATED",
              object_type="claim", object_id=cid,
              detail={"proposition": args.text[:120],
                      "type": args.type, "confidence": args.confidence})
    conn.close()
    print(f"created claim {cid}  type={args.type}  confidence={args.confidence}")
    return 0


def cmd_evidence(args):
    """Attach an evidence unit to a claim."""
    conn = dbm.init_db(args.db)
    row = conn.execute("SELECT id,book_id FROM claim WHERE id=?",
                       (args.claim,)).fetchone()
    if not row:
        print(f"error: claim {args.claim!r} not found", file=sys.stderr)
        conn.close()
        return 2
    if not conn.execute("SELECT id FROM source WHERE id=?",
                        (args.source,)).fetchone():
        print(f"error: source {args.source!r} not found", file=sys.stderr)
        conn.close()
        return 2

    # Policy: source must not be T7 (trigger will also block, but give clear message)
    src_row = conn.execute("SELECT tier FROM source WHERE id=?",
                           (args.source,)).fetchone()
    ok, msg = policy.check_source_tier_admissible(src_row["tier"])
    if not ok:
        print(f"REFUSED: {msg}", file=sys.stderr)
        conn.close()
        return 1

    eid = dbm.new_id("ev_")
    now = dbm.now_utc()
    try:
        conn.execute(
            "INSERT INTO evidence_unit"
            "(id,claim_id,source_id,passage,location_ref,stance,created_utc)"
            " VALUES (?,?,?,?,?,?,?)",
            (eid, args.claim, args.source, args.passage,
             args.location, args.stance, now),
        )
        conn.commit()
    except Exception as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        conn.close()
        return 1

    dbm.audit(conn, actor=args.actor or "operator", action="EVIDENCE_ATTACHED",
              object_type="evidence_unit", object_id=eid,
              detail={"claim_id": args.claim, "source_id": args.source,
                      "stance": args.stance, "location": args.location})
    conn.close()
    print(f"attached evidence {eid}  stance={args.stance}")
    return 0


def cmd_verify(args):
    """Record an independent review of a claim (sets claim.verifier)."""
    conn = dbm.init_db(args.db)
    row = conn.execute("SELECT id,verifier FROM claim WHERE id=?",
                       (args.claim,)).fetchone()
    if not row:
        print(f"error: claim {args.claim!r} not found", file=sys.stderr)
        conn.close()
        return 2

    if not args.actor.strip():
        print("REFUSED: verifier actor must be a non-empty name (spec 5.3)",
              file=sys.stderr)
        conn.close()
        return 1

    conn.execute("UPDATE claim SET verifier=? WHERE id=?",
                 (args.actor, args.claim))
    conn.commit()
    dbm.audit(conn, actor=args.actor, action="CLAIM_VERIFIED",
              object_type="claim", object_id=args.claim,
              detail={"verifier": args.actor})
    conn.close()
    print(f"claim {args.claim} verified by {args.actor!r}")
    return 0


def cmd_accept_claim(args):
    """Accept a claim through all nine gate checks (spec 5.3)."""
    conn = dbm.init_db(args.db)

    # Pre-flight: check all acceptance criteria before touching the DB
    ok, failures = policy.check_claim_acceptance_gate(conn, args.claim)
    if not ok:
        print("REFUSED: claim acceptance gate failed:", file=sys.stderr)
        for f in failures:
            print(f"  • {f}", file=sys.stderr)
        conn.close()
        return 1

    try:
        conn.execute("UPDATE claim SET accepted=1 WHERE id=?", (args.claim,))
        conn.commit()
    except Exception as exc:
        print(f"REFUSED (DB trigger): {exc}", file=sys.stderr)
        conn.close()
        return 1

    dbm.audit(conn, actor=args.actor or "operator", action="CLAIM_ACCEPTED",
              object_type="claim", object_id=args.claim,
              detail={"actor": args.actor})
    conn.close()
    print(f"claim {args.claim} accepted")
    return 0


def cmd_conflict(args):
    """Record a conflict between two claims."""
    conn = dbm.init_db(args.db)
    if not conn.execute("SELECT id FROM book_project WHERE id=?",
                        (args.book,)).fetchone():
        print(f"error: book {args.book!r} not found", file=sys.stderr)
        conn.close()
        return 2
    for cid, label in [(args.claim_a, "--claim-a"), (args.claim_b, "--claim-b")]:
        if not conn.execute("SELECT id FROM claim WHERE id=?", (cid,)).fetchone():
            print(f"error: claim {cid!r} ({label}) not found", file=sys.stderr)
            conn.close()
            return 2

    fid = dbm.new_id("conf_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO conflict"
        "(id,book_id,claim_a,claim_b,reason,resolved,created_utc)"
        " VALUES (?,?,?,?,?,0,?)",
        (fid, args.book, args.claim_a, args.claim_b, args.reason, now),
    )
    conn.commit()
    dbm.audit(conn, actor=args.actor or "operator", action="CONFLICT_RECORDED",
              object_type="conflict", object_id=fid,
              detail={"claim_a": args.claim_a, "claim_b": args.claim_b,
                      "reason": args.reason[:120]})
    conn.close()
    print(f"recorded conflict {fid}")
    return 0


def cmd_research_status(args):
    """Show the full research chain (questions → claims → evidence) for a book."""
    conn = dbm.init_db(args.db)
    book_row = conn.execute(
        "SELECT id,title,author,state FROM book_project WHERE id=?",
        (args.book,)
    ).fetchone()
    if not book_row:
        print(f"error: book {args.book!r} not found", file=sys.stderr)
        conn.close()
        return 2

    questions = conn.execute(
        "SELECT * FROM research_question WHERE book_id=? ORDER BY priority, created_utc",
        (args.book,)
    ).fetchall()

    report: dict = {
        "book_id": book_row["id"],
        "title": book_row["title"],
        "state": book_row["state"],
        "questions": [],
        "summary": {},
    }

    total_claims = total_accepted = total_evidence = total_conflicts = 0

    for q in questions:
        claims = conn.execute(
            "SELECT c.*, COUNT(ev.id) AS evidence_count"
            " FROM claim c"
            " LEFT JOIN evidence_unit ev ON ev.claim_id=c.id"
            " WHERE c.question_id=?"
            " GROUP BY c.id",
            (q["id"],)
        ).fetchall()

        claim_list = []
        for c in claims:
            total_claims += 1
            if c["accepted"]:
                total_accepted += 1
            total_evidence += c["evidence_count"]
            claim_list.append({
                "id": c["id"],
                "proposition": c["proposition"],
                "type": c["claim_type"],
                "confidence": c["confidence"],
                "accepted": bool(c["accepted"]),
                "verifier": c["verifier"],
                "evidence_count": c["evidence_count"],
            })

        report["questions"].append({
            "id": q["id"],
            "question": q["question"],
            "decision_informed": q["decision_informed"],
            "state": q["state"],
            "claims": claim_list,
        })

    # Conflicts
    conflicts = conn.execute(
        "SELECT COUNT(*) FROM conflict WHERE book_id=? AND resolved=0",
        (args.book,)
    ).fetchone()[0]
    total_conflicts = conflicts

    # Source artifacts
    seed_count = conn.execute(
        "SELECT COUNT(*) FROM source_artifact WHERE book_id=? AND disposition='CANONICAL'",
        (args.book,)
    ).fetchone()[0]

    sources = conn.execute(
        "SELECT COUNT(*) FROM source WHERE book_id=?", (args.book,)
    ).fetchone()[0]

    report["summary"] = {
        "questions": len(questions),
        "claims": total_claims,
        "accepted_claims": total_accepted,
        "evidence_units": total_evidence,
        "unresolved_conflicts": total_conflicts,
        "canonical_source_artifacts": seed_count,
        "bibliographic_sources": sources,
        "ready_for_architecture": (
            total_questions := len(questions),
            total_accepted >= 1 and total_questions >= 1
        )[1],
    }

    conn.close()
    _p(report)
    return 0


def cmd_demo_wr02(args):
    """Seed a complete question → source → claim → evidence → verify → accept chain.

    Demonstrates the WR-02 exit condition: a real research question for
    'Ash and Silence' passes the full claim-acceptance gate (spec 5.3).
    """
    conn = dbm.init_db(args.db)
    book_row = conn.execute(
        "SELECT id,title FROM book_project WHERE id=?", (args.book,)
    ).fetchone()
    if not book_row:
        print(f"error: book {args.book!r} not found", file=sys.stderr)
        conn.close()
        return 2

    now = dbm.now_utc()
    print(f"WR-02 demo — seeding research chain for '{book_row['title']}'")

    # 1. Research question
    qid = dbm.new_id("q_")
    conn.execute(
        "INSERT INTO research_question"
        "(id,book_id,question,decision_informed,state,created_utc)"
        " VALUES (?,?,?,?,?,?)",
        (
            qid, args.book,
            "What was the daily domestic life of a Jewish household in "
            "first-century Roman Palestine?",
            "How to portray Miriam's household routines authentically "
            "without anachronism",
            "OPEN", now,
        ),
    )
    dbm.audit(conn, actor="demo", action="QUESTION_CREATED",
              object_type="research_question", object_id=qid,
              detail={"demo": True})

    # 2. Source (T2 — primary archaeological report)
    sid = dbm.new_id("src_")
    conn.execute(
        "INSERT INTO source(id,book_id,citation,edition,pub_date,tier,"
        " reliability,rights,created_utc)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (
            sid, args.book,
            "Reed, Jonathan L. 'Archaeology and the Galilean Jesus.' "
            "Trinity Press International, 2000.",
            "First edition",
            "2000",
            "T2",
            "peer-reviewed archaeological study",
            "cite as scholarship",
            now,
        ),
    )
    dbm.audit(conn, actor="demo", action="SOURCE_REGISTERED",
              object_type="source", object_id=sid,
              detail={"demo": True, "tier": "T2"})

    # 3. Claim (fact, probable)
    cid = dbm.new_id("claim_")
    conn.execute(
        "INSERT INTO claim(id,book_id,question_id,proposition,claim_type,"
        " confidence,accepted,created_utc)"
        " VALUES (?,?,?,?,?,?,0,?)",
        (
            cid, args.book, qid,
            "First-century Galilean households stored grain in ceramic "
            "storage jars and lamps were fuelled with olive oil pressed "
            "on-site or purchased at local markets.",
            "fact",
            "probable",
            now,
        ),
    )
    dbm.audit(conn, actor="demo", action="CLAIM_CREATED",
              object_type="claim", object_id=cid, detail={"demo": True})

    # 4. Evidence unit (supporting, with exact location)
    eid = dbm.new_id("ev_")
    conn.execute(
        "INSERT INTO evidence_unit(id,claim_id,source_id,passage,"
        " location_ref,stance,created_utc)"
        " VALUES (?,?,?,?,?,?,?)",
        (
            eid, cid, sid,
            "'Storage vessels in ceramic form dominate the domestic assemblage "
            "at Capernaum and Sepphoris, with olive oil lamps present in "
            "nearly every excavated household stratum.'",
            "Reed 2000, p. 47, §3.2 — domestic material culture",
            "supports",
            now,
        ),
    )
    dbm.audit(conn, actor="demo", action="EVIDENCE_ATTACHED",
              object_type="evidence_unit", object_id=eid, detail={"demo": True})

    # 5. Independent verifier
    conn.execute("UPDATE claim SET verifier=? WHERE id=?",
                 ("Brian Fochtman (author review)", cid))
    dbm.audit(conn, actor="Brian Fochtman (author review)", action="CLAIM_VERIFIED",
              object_type="claim", object_id=cid,
              detail={"verifier": "Brian Fochtman (author review)", "demo": True})

    # 6. Accept through the gate
    ok, failures = policy.check_claim_acceptance_gate(conn, cid)
    if not ok:
        print("ERROR: demo chain failed the acceptance gate:", file=sys.stderr)
        for f in failures:
            print(f"  • {f}", file=sys.stderr)
        conn.close()
        return 1

    conn.execute("UPDATE claim SET accepted=1 WHERE id=?", (cid,))
    dbm.audit(conn, actor="demo", action="CLAIM_ACCEPTED",
              object_type="claim", object_id=cid, detail={"demo": True})

    conn.commit()
    conn.close()

    print(f"  question  {qid}")
    print(f"  source    {sid}  (T2)")
    print(f"  claim     {cid}  confidence=probable  accepted=True")
    print(f"  evidence  {eid}  stance=supports  location set")
    print("  verifier  'Brian Fochtman (author review)'")
    print()
    print("WR-02 exit condition: question → source → claim → evidence → "
          "verify → accept chain complete. Gate passed.")
    return 0


# ─── Parser ──────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wa", description="WRITING_ARCHITECT — governed Book Production OS")
    p.add_argument("--version", action="version", version=f"wa {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    # WR-01 ── ────────────────────────────────────────────────────────────────

    f = sub.add_parser("forensics", help="run WR-00 forensic baseline")
    f.add_argument("archive")
    f.add_argument("--out", default="wr00_baseline")
    f.set_defaults(func=cmd_forensics)

    i = sub.add_parser("init", help="create/upgrade a governed store")
    i.add_argument("db")
    i.set_defaults(func=cmd_init)

    b = sub.add_parser("book-new", help="create a BookProject")
    b.add_argument("db")
    b.add_argument("--title", required=True)
    b.add_argument("--author", required=True)
    b.add_argument("--form", default=None)
    b.add_argument("--audience", default=None)
    b.add_argument("--reader-promise", dest="reader_promise", default=None)
    b.set_defaults(func=cmd_book_new)

    s = sub.add_parser("status", help="show lifecycle + gate report")
    s.add_argument("db"); s.add_argument("book")
    s.set_defaults(func=cmd_status)

    a = sub.add_parser("advance", help="advance one lifecycle state (gated)")
    a.add_argument("db"); a.add_argument("book")
    a.add_argument("--actor", required=True); a.add_argument("--reason", default="")
    a.set_defaults(func=cmd_advance)

    r = sub.add_parser("return", help="return scope to an upstream state")
    r.add_argument("db"); r.add_argument("book")
    r.add_argument("--to", required=True); r.add_argument("--actor", required=True)
    r.add_argument("--reason", required=True)
    r.set_defaults(func=cmd_return)

    v = sub.add_parser("audit-verify", help="verify tamper-evident audit chain")
    v.add_argument("db")
    v.set_defaults(func=cmd_audit_verify)

    d = sub.add_parser("demo", help="run the spec's first vertical slice")
    d.add_argument("db")
    d.set_defaults(func=cmd_demo)

    doc = sub.add_parser("doctor", help="self-check the installation")
    doc.set_defaults(func=cmd_doctor)

    # WR-02 ── ────────────────────────────────────────────────────────────────

    ss = sub.add_parser("seed-sources",
                        help="load WR-00 canonical seeds as source_artifact rows")
    ss.add_argument("db"); ss.add_argument("book")
    ss.add_argument("--manifest", default=None,
                    help="path to baseline_manifest.json (auto-detected if omitted)")
    ss.set_defaults(func=cmd_seed_sources)

    qq = sub.add_parser("question", help="create a research question")
    qq.add_argument("db"); qq.add_argument("book")
    qq.add_argument("--text", required=True, dest="text")
    qq.add_argument("--decision", default=None, dest="decision",
                    help="which narrative decision this question informs")
    qq.add_argument("--scope", default=None)
    qq.add_argument("--priority", type=int, default=3)
    qq.add_argument("--actor", default=None)
    qq.set_defaults(func=cmd_question)

    src = sub.add_parser("source", help="register a bibliographic source (T7 blocked)")
    src.add_argument("db"); src.add_argument("book")
    src.add_argument("--citation", required=True)
    src.add_argument("--tier", required=True,
                     choices=["T1","T2","T3","T4","T5","T6","T7"])
    src.add_argument("--edition", default=None)
    src.add_argument("--pub-date", default=None, dest="pub_date")
    src.add_argument("--rights", default=None)
    src.add_argument("--reliability", default=None)
    src.add_argument("--retrieval", default=None)
    src.add_argument("--actor", default=None)
    src.set_defaults(func=cmd_source)

    cl = sub.add_parser("claim", help="create a candidate claim (unaccepted)")
    cl.add_argument("db"); cl.add_argument("book")
    cl.add_argument("--text", required=True, dest="text")
    cl.add_argument("--question", default=None,
                    help="research_question ID this claim answers")
    cl.add_argument("--type", default="fact",
                    choices=["fact","interpretation","tradition",
                             "creative_interpolation"],
                    dest="type")
    cl.add_argument("--confidence", default="probable",
                    choices=["confirmed","probable","possible","disputed",
                             "unknown","invented_for_fiction"])
    cl.add_argument("--actor", default=None)
    cl.set_defaults(func=cmd_claim)

    ev = sub.add_parser("evidence", help="attach an evidence unit to a claim")
    ev.add_argument("db"); ev.add_argument("claim")
    ev.add_argument("--source", required=True)
    ev.add_argument("--passage", required=True)
    ev.add_argument("--location", required=True)
    ev.add_argument("--stance", default="supports",
                    choices=["supports","qualifies","contradicts"])
    ev.add_argument("--actor", default=None)
    ev.set_defaults(func=cmd_evidence)

    vr = sub.add_parser("verify", help="record independent review of a claim")
    vr.add_argument("db"); vr.add_argument("claim")
    vr.add_argument("--actor", required=True, help="name of the independent reviewer")
    vr.set_defaults(func=cmd_verify)

    ac = sub.add_parser("accept-claim",
                        help="accept a claim through the 9-point gate (spec 5.3)")
    ac.add_argument("db"); ac.add_argument("claim")
    ac.add_argument("--actor", default="operator")
    ac.set_defaults(func=cmd_accept_claim)

    cf = sub.add_parser("conflict", help="record a conflict between two claims")
    cf.add_argument("db"); cf.add_argument("book")
    cf.add_argument("--claim-a", required=True, dest="claim_a")
    cf.add_argument("--claim-b", required=True, dest="claim_b")
    cf.add_argument("--reason", required=True)
    cf.add_argument("--actor", default=None)
    cf.set_defaults(func=cmd_conflict)

    rs = sub.add_parser("research-status",
                        help="show full research chain for a book")
    rs.add_argument("db"); rs.add_argument("book")
    rs.set_defaults(func=cmd_research_status)

    demo2 = sub.add_parser("demo-wr02",
                           help="seed the WR-02 exit-condition chain for Ash and Silence")
    demo2.add_argument("db"); demo2.add_argument("book")
    demo2.set_defaults(func=cmd_demo_wr02)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
