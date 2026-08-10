"""
GENESIS gate logic — stage ordering, ledger chain, status helpers.
All DB calls use a raw sqlite3 Connection (the caller holds the lock).
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

STAGES: list[tuple[str, str, str, str]] = [
    ("G0", "Spark Slate",
     "G0_spark_slate",
     "One spark chosen; every LIBRARY spark has a source_pointer or is relabelled IDEA-ONLY."),
    ("G1", "Premise Forge",
     "G1_premise",
     "Protagonist/desire/opposition/stakes/change all present; logline ≤60 words; dramatic question whole-book."),
    ("G2", "Viability Gate",
     "G2_viability",
     "Verdict GO; conflict engine sized to length; risk watchlist recorded."),
    ("G3", "Canon Seed",
     "G3_canon_seed",
     "Zero HISTORICAL/INFERRED facts without a source_pointer; research backlog set."),
    ("G4", "Character Web",
     "G4_character_web",
     "Protagonist + antagonist have full arc spine; every principal has a function; cast-economy pass done."),
    ("G5", "Structure & Beats",
     "G5_structure_beats",
     "Structure named; no filler beats; four acts have pacing shapes; tentpoles placed."),
    ("G6", "Voice Spec",
     "G6_voice_spec",
     "All voice invariants fixed (no TBD); three golden samples signed on-voice."),
    ("G7", "Standard Binding",
     "G7_standard_binding",
     "Every act/chapter-range mapped to an instrument + pass condition; concordance recorded."),
    ("G8", "Chapter Blueprint",
     "G8_chapter_blueprint",
     "Every chapter row complete; each chapter has a value-shift; promises paid; risks defused."),
    ("G9", "Ready-to-Write Seal",
     "G9_ready_to_write",
     "All gates green; manifest computed; author sign-off present → READY_FOR_B0."),
]

STAGE_CODES: list[str] = [s[0] for s in STAGES]
STAGE_BY_CODE: dict[str, tuple[str, str, str, str]] = {s[0]: s for s in STAGES}

GENESIS_HASH = "0" * 64  # chain anchor


# ── Helpers ────────────────────────────────────────────────────────────────────

def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


# ── Ledger ─────────────────────────────────────────────────────────────────────

def ledger_append(conn, book_id: str, kind: str, payload: dict) -> str:
    """Append one entry to the tamper-evident ledger and return the new hash."""
    import uuid as _uuid
    row = conn.execute(
        "SELECT seq, hash FROM genesis_ledger WHERE book_id=? ORDER BY seq DESC LIMIT 1",
        (book_id,),
    ).fetchone()
    seq = (row[0] + 1) if row else 0
    prev_hash = row[1] if row else GENESIS_HASH
    body = canonical({"seq": seq, "kind": kind, "payload": payload})
    h = sha256_text(prev_hash + body)
    entry_id = str(_uuid.uuid4())
    conn.execute(
        "INSERT INTO genesis_ledger (id, book_id, seq, kind, payload, prev_hash, hash, at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (entry_id, book_id, seq, kind, canonical(payload), prev_hash, h, now_iso()),
    )
    return h


# ── Stage status helpers ───────────────────────────────────────────────────────

def get_stage_status(conn, book_id: str) -> dict[str, str]:
    """Return {stage_code: 'PENDING'|'PASSED'|'FAILED'} for all ten stages."""
    rows = conn.execute(
        "SELECT stage_code, status FROM genesis_stages WHERE book_id=?", (book_id,)
    ).fetchall()
    result: dict[str, str] = {code: "PENDING" for code in STAGE_CODES}
    for code, status in rows:
        result[code] = status
    return result


def next_open_stage(status_map: dict[str, str]) -> str | None:
    """Return the first stage that is not PASSED, or None if all are PASSED."""
    for code in STAGE_CODES:
        if status_map.get(code) != "PASSED":
            return code
    return None
