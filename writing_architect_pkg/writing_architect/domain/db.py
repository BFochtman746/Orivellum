"""
Database layer
==============

Opens the SQLite store with governance enforcement ON, applies the schema,
seeds the lifecycle vocabulary, and provides a tamper-evident append-only audit
ledger (each entry hashes the previous entry's hash — a broken chain reveals
tampering even though rows are already UPDATE/DELETE-locked by triggers).
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone

_SCHEMA_PATH     = os.path.join(os.path.dirname(__file__), "schema.sql")
_SCHEMA_WR03_PATH = os.path.join(os.path.dirname(__file__), "schema_wr03.sql")

# Canonical lifecycle (spec 3.2)
LIFECYCLE = [
    ("B0",  0, "INTAKE", "Files received, hashed, scanned, metadata captured"),
    ("B1",  1, "AUTHORITY_RESOLUTION", "Authoritative and historical artifacts designated"),
    ("B2",  2, "BOOK_DEFINITION", "Reader promise, genre, audience, premise, constraints approved"),
    ("B3",  3, "RESEARCH_BASELINE", "Questions, sources, claims, conflicts, gaps established"),
    ("B4",  4, "ARCHITECTURE", "Master structure and chapter contracts approved"),
    ("B5",  5, "DRAFTING", "Sections drafted only from approved contract and evidence packet"),
    ("B6",  6, "DEVELOPMENTAL_EDIT", "Structure, causality, character, argument, pacing repaired"),
    ("B7",  7, "VERIFICATION", "Factual, citation, domain, chronology, continuity gates"),
    ("B8",  8, "LINE_EDIT", "Voice, clarity, rhythm, diction, paragraph movement"),
    ("B9",  9, "COPYEDIT", "Grammar, usage, consistency, style sheet, cross-references"),
    ("B10", 10, "PRODUCTION", "Layout, front/back matter, accessibility, outputs"),
    ("B11", 11, "PROOF", "Rendered proof and final defect closure"),
    ("B12", 12, "RELEASE_CANDIDATE", "All evidence assembled; no open blocker"),
    ("B13", 13, "RELEASED", "Author-approved immutable release with manifest and rollback"),
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def _apply_wr03_migrations(conn: sqlite3.Connection) -> None:
    """Add WR-03 columns to existing tables. Idempotent — guards against
    double-application by catching the OperationalError SQLite raises when
    a column already exists."""
    additions = [
        ("canon_entity",   "birth_date",          "TEXT"),
        ("canon_entity",   "birth_uncertainty",   "TEXT"),
        ("canon_entity",   "death_date",          "TEXT"),
        ("canon_entity",   "destruction_date",    "TEXT"),
        ("canon_fact",     "stated_age_years",    "INTEGER"),
        ("canon_fact",     "at_date",             "TEXT"),
        ("timeline_event", "location",            "TEXT"),
    ]
    for table, column, col_type in additions:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except sqlite3.OperationalError:
            pass  # column already exists — idempotent
    conn.commit()


def init_db(db_path: str) -> sqlite3.Connection:
    """Create/upgrade the store and seed lifecycle states. Idempotent."""
    fresh = not os.path.exists(db_path)
    conn = connect(db_path)
    with open(_SCHEMA_PATH, "r", encoding="utf-8") as fh:
        conn.executescript(fh.read())
    # WR-03: new tables + column additions
    with open(_SCHEMA_WR03_PATH, "r", encoding="utf-8") as fh:
        conn.executescript(fh.read())
    _apply_wr03_migrations(conn)
    # seed lifecycle
    for code, ordinal, name, exit_cond in LIFECYCLE:
        conn.execute(
            "INSERT OR IGNORE INTO lifecycle_state(code,ordinal,name,exit_condition)"
            " VALUES (?,?,?,?)",
            (code, ordinal, name, exit_cond),
        )
    conn.commit()
    if fresh:
        audit(conn, actor="system", action="INIT_DB",
              detail={"schema": os.path.basename(_SCHEMA_PATH)})
    return conn


def _last_hash(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
    ).fetchone()
    return row["entry_hash"] if row and row["entry_hash"] else "GENESIS"


def audit(conn, actor: str, action: str, object_type: str = None,
          object_id: str = None, detail=None) -> str:
    """Append a tamper-evident audit entry and return its hash."""
    ts = now_utc()
    prev = _last_hash(conn)
    payload = json.dumps(
        {"ts": ts, "actor": actor, "action": action,
         "object_type": object_type, "object_id": object_id,
         "detail": detail, "prev": prev},
        sort_keys=True, ensure_ascii=False,
    )
    entry_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    conn.execute(
        "INSERT INTO audit_log(ts_utc,actor,action,object_type,object_id,detail,"
        "prev_hash,entry_hash) VALUES (?,?,?,?,?,?,?,?)",
        (ts, actor, action, object_type, object_id,
         json.dumps(detail, ensure_ascii=False) if detail is not None else None,
         prev, entry_hash),
    )
    conn.commit()
    return entry_hash


def verify_audit_chain(conn) -> tuple[bool, str]:
    """Recompute the hash chain; return (ok, message)."""
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY seq ASC"
    ).fetchall()
    prev = "GENESIS"
    for r in rows:
        detail = json.loads(r["detail"]) if r["detail"] else None
        payload = json.dumps(
            {"ts": r["ts_utc"], "actor": r["actor"], "action": r["action"],
             "object_type": r["object_type"], "object_id": r["object_id"],
             "detail": detail, "prev": prev},
            sort_keys=True, ensure_ascii=False,
        )
        expect = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        if r["prev_hash"] != prev:
            return False, f"chain break at seq {r['seq']}: prev_hash mismatch"
        if r["entry_hash"] != expect:
            return False, f"tamper at seq {r['seq']}: entry_hash mismatch"
        prev = r["entry_hash"]
    return True, f"audit chain intact ({len(rows)} entries)"
