"""
WR-03 Continuity Validators
============================

Five deterministic checks that query the canon/continuity tables and emit
editorial_finding rows (pass_type='continuity', severity='blocker') when a
violation is detected.  No LLM calls — pure SQL + Python logic.

Validators
----------
1. check_age_date_conflict  — stated age inconsistent with entity's birth_date
2. check_impossible_travel  — entity appears at two locations on the same date
3. check_knowledge_leak     — POV character accesses knowledge before they can know it
4. check_name_drift         — entity referenced by a name not in entity_alias
5. check_object_resurrection — destroyed object (or dead character) reappears later

Each validator returns a list of finding IDs (strings).  An empty list means
no violations found (clean for that check).

Usage::

    conn = db.init_db("book.sqlite")
    findings = run_all_validators(conn, book_id)
    # findings["total_findings"] == 0  →  continuity is clean
"""
from __future__ import annotations

import re
import sqlite3
from typing import Optional

from .db import audit, new_id, now_utc

_WORKER = "continuity_validator_wr03"


# ─── internal helpers ────────────────────────────────────────────────────────

def _raise_finding(
    conn: sqlite3.Connection,
    book_id: str,
    validator_name: str,
    description: str,
    evidence: str = "",
    draft_unit_id: Optional[str] = None,
) -> str:
    """Insert one editorial_finding row and audit-log it. Returns the finding id."""
    fid = new_id("find_")
    now = now_utc()
    conn.execute(
        "INSERT INTO editorial_finding"
        "(id,book_id,draft_unit_id,pass_type,severity,location,evidence,"
        " proposed_resolution,state,raised_by,created_utc)"
        " VALUES (?,?,?,'continuity','blocker',?,?,?,?,?,?)",
        (
            fid, book_id, draft_unit_id,
            validator_name,
            description[:512],
            evidence[:512],
            "OPEN",
            _WORKER,
            now,
        ),
    )
    audit(
        conn, actor=_WORKER, action="CONTINUITY_FINDING_RAISED",
        object_type="editorial_finding", object_id=fid,
        detail={"validator": validator_name, "desc": description[:120]},
    )
    return fid


def _parse_year(date_str: str) -> int:
    """Parse a year from a date string for numeric comparison.

    Handles:
    - Pure integers: "70", "-587", "2026"
    - BCE/BC notation: "1200 BCE", "587 BC", "c.1200 BCE"
    - CE/AD notation: "70 CE", "33 AD"
    - Approximate prefixes: "c.", "circa", "~", "approx."
    - Ranges: uses the start year ("1200-1150 BCE" → -1200)

    Returns a signed integer where BCE years are negative.
    Raises ValueError if the string cannot be parsed.
    """
    s = date_str.strip()
    # Strip approximate / circa prefixes
    s = re.sub(r"^(?:c\.|circa|~|approx\.?)\s*", "", s, flags=re.IGNORECASE).strip()
    # Strip uncertainty suffixes like "±25yr" or "+/-25yr".
    # Use ± only (not bare -) so negative years like "-587" survive this pass.
    s = re.sub(r"\s*(?:±|\+/-)\s*\d+\s*(?:yr[s]?|years?)?\s*$", "", s, flags=re.IGNORECASE).strip()
    # Use first year of a range  ("1200-1150 BCE" → "1200 BCE")
    range_bce = re.match(r"(\d+)\s*[-–]\s*\d+\s*(BCE|BC)\b", s, re.IGNORECASE)
    if range_bce:
        return -int(range_bce.group(1))
    range_ce = re.match(r"(\d+)\s*[-–]\s*\d+\s*(CE|AD)\b", s, re.IGNORECASE)
    if range_ce:
        return int(range_ce.group(1))
    # BCE / BC
    bce = re.search(r"(\d+)\s*(?:BCE|BC)\b", s, re.IGNORECASE)
    if bce:
        return -int(bce.group(1))
    # CE / AD
    ce = re.search(r"(\d+)\s*(?:CE|AD)\b", s, re.IGNORECASE)
    if ce:
        return int(ce.group(1))
    # Negative ISO year
    neg = re.fullmatch(r"-(\d+)", s)
    if neg:
        return -int(neg.group(1))
    # Plain integer
    plain = re.fullmatch(r"\d{1,4}", s)
    if plain:
        return int(plain.group(0))
    raise ValueError(f"Cannot parse year from: {date_str!r}")


# ─── Validator 1: age / date conflict ────────────────────────────────────────

def check_age_date_conflict(
    conn: sqlite3.Connection, book_id: str, tolerance_years: int = 5
) -> list[str]:
    """For each canon_fact with stated_age_years + at_date, verify that the
    implied age is consistent with the entity's birth_date (within
    *tolerance_years*).  Biblical / ancient dates carry inherent uncertainty,
    so a tolerance of 5 years is applied by default.

    Returns a list of finding IDs created (empty = clean).
    """
    findings: list[str] = []
    rows = conn.execute(
        """
        SELECT cf.id  AS fact_id,
               ce.id  AS entity_id,
               ce.name,
               ce.birth_date,
               cf.stated_age_years,
               cf.at_date,
               cf.fact
        FROM   canon_fact   cf
        JOIN   canon_entity ce ON ce.id = cf.entity_id
        WHERE  cf.stated_age_years IS NOT NULL
          AND  cf.at_date          IS NOT NULL
          AND  ce.birth_date       IS NOT NULL
          AND  ce.book_id = ?
        """,
        (book_id,),
    ).fetchall()

    for row in rows:
        try:
            birth_year = _parse_year(row["birth_date"])
            at_year    = _parse_year(row["at_date"])
            expected   = at_year - birth_year
            stated     = row["stated_age_years"]
            if abs(expected - stated) > tolerance_years:
                desc = (
                    f"Age conflict for {row['name']!r}: "
                    f"stated age {stated} at {row['at_date']!r} but "
                    f"birth_date {row['birth_date']!r} implies ~{expected}. "
                    f"Discrepancy: {abs(expected - stated)} years."
                )
                fid = _raise_finding(
                    conn, book_id, "age_date_conflict", desc,
                    evidence=f"canon_fact:{row['fact_id']},canon_entity:{row['entity_id']}",
                )
                findings.append(fid)
        except (ValueError, TypeError):
            pass  # unparseable date — skip rather than false-positive

    conn.commit()
    return findings


# ─── Validator 2: impossible travel ──────────────────────────────────────────

def check_impossible_travel(
    conn: sqlite3.Connection, book_id: str
) -> list[str]:
    """Flag any entity that appears in two *different* locations on the
    *same date_ref* string.  Date strings are compared for exact equality;
    use consistent date formats when populating entity_location.

    Returns a list of finding IDs created (empty = clean).
    """
    findings: list[str] = []
    rows = conn.execute(
        """
        SELECT a.entity_id,
               ce.name,
               a.date_ref,
               a.location  AS loc_a,
               b.location  AS loc_b,
               a.id        AS loc_a_id,
               b.id        AS loc_b_id
        FROM   entity_location a
        JOIN   entity_location b
               ON  a.entity_id = b.entity_id
               AND a.date_ref  = b.date_ref
               AND a.location <> b.location
               AND a.id        < b.id   -- deduplicate pairs
        JOIN   canon_entity ce ON ce.id = a.entity_id
        WHERE  ce.book_id = ?
        """,
        (book_id,),
    ).fetchall()

    for row in rows:
        desc = (
            f"Impossible travel for {row['name']!r}: "
            f"appears in both {row['loc_a']!r} and {row['loc_b']!r} "
            f"on {row['date_ref']!r}."
        )
        fid = _raise_finding(
            conn, book_id, "impossible_travel", desc,
            evidence=f"entity_location:{row['loc_a_id']},{row['loc_b_id']}",
        )
        findings.append(fid)

    conn.commit()
    return findings


# ─── Validator 3: knowledge leak ─────────────────────────────────────────────

def check_knowledge_leak(
    conn: sqlite3.Connection, book_id: str
) -> list[str]:
    """Flag any contract_knowledge_access row whose *scene_sequence* is
    earlier than the referenced knowledge_state's *scene_sequence* (the
    earliest scene from which the character can know the fact).

    Returns a list of finding IDs created (empty = clean).
    """
    findings: list[str] = []
    rows = conn.execute(
        """
        SELECT ck.contract_id,
               ck.knowledge_state_id,
               ck.scene_sequence        AS access_seq,
               ks.scene_sequence        AS available_seq,
               ks.fact_description,
               ks.can_know_from_scene,
               ce.name                  AS entity_name
        FROM   contract_knowledge_access ck
        JOIN   knowledge_state           ks ON ks.id = ck.knowledge_state_id
        JOIN   canon_entity              ce ON ce.id = ks.entity_id
        WHERE  ce.book_id   = ?
          AND  ck.scene_sequence < ks.scene_sequence
        """,
        (book_id,),
    ).fetchall()

    for row in rows:
        desc = (
            f"Knowledge leak for {row['entity_name']!r}: "
            f"fact '{row['fact_description'][:80]}' is accessed in "
            f"contract {row['contract_id']!r} at scene_sequence "
            f"{row['access_seq']}, but cannot be known before scene "
            f"'{row['can_know_from_scene']!r}' (sequence {row['available_seq']})."
        )
        fid = _raise_finding(
            conn, book_id, "knowledge_leak", desc,
            evidence=(
                f"contract:{row['contract_id']},"
                f"knowledge_state:{row['knowledge_state_id']}"
            ),
        )
        findings.append(fid)

    conn.commit()
    return findings


# ─── Validator 4: name drift ─────────────────────────────────────────────────

def check_name_drift(
    conn: sqlite3.Connection, book_id: str
) -> list[str]:
    """Flag any chapter_contract_entity_ref row where the *name_used* differs
    from the entity's canonical name AND is not listed in entity_alias.

    Returns a list of finding IDs created (empty = clean).
    """
    findings: list[str] = []
    rows = conn.execute(
        """
        SELECT cer.contract_id,
               cer.entity_id,
               cer.name_used,
               ce.name  AS canonical_name
        FROM   chapter_contract_entity_ref cer
        JOIN   canon_entity                ce ON ce.id = cer.entity_id
        WHERE  ce.book_id   = ?
          AND  cer.name_used <> ce.name
          AND  NOT EXISTS (
                   SELECT 1 FROM entity_alias ea
                   WHERE  ea.entity_id = cer.entity_id
                     AND  ea.alias     = cer.name_used
               )
        """,
        (book_id,),
    ).fetchall()

    for row in rows:
        desc = (
            f"Name drift: entity {row['canonical_name']!r} is referenced as "
            f"{row['name_used']!r} in contract {row['contract_id']!r}, "
            f"but {row['name_used']!r} is not in the registered alias list."
        )
        fid = _raise_finding(
            conn, book_id, "name_drift", desc,
            evidence=(
                f"contract:{row['contract_id']},"
                f"entity:{row['entity_id']}"
            ),
        )
        findings.append(fid)

    conn.commit()
    return findings


# ─── Validator 5: object resurrection ────────────────────────────────────────

def check_object_resurrection(
    conn: sqlite3.Connection, book_id: str
) -> list[str]:
    """Flag objects (kind='object') whose *destruction_date* is set and which
    have canon_facts or entity_locations dated *after* that destruction.
    Also flags persons (kind='person') with a *death_date* that appear in
    entity_locations after their death.

    Returns a list of finding IDs created (empty = clean).
    """
    findings: list[str] = []

    # --- objects with a destruction_date ---
    objects = conn.execute(
        """
        SELECT id, name, destruction_date
        FROM   canon_entity
        WHERE  book_id          = ?
          AND  kind             = 'object'
          AND  destruction_date IS NOT NULL
        """,
        (book_id,),
    ).fetchall()

    for obj in objects:
        try:
            dest_year = _parse_year(obj["destruction_date"])
        except (ValueError, TypeError):
            continue

        # canon_facts after destruction
        for cf in conn.execute(
            "SELECT id, fact, time_start FROM canon_fact"
            " WHERE entity_id=? AND time_start IS NOT NULL",
            (obj["id"],),
        ).fetchall():
            try:
                if _parse_year(cf["time_start"]) > dest_year:
                    desc = (
                        f"Object resurrection: {obj['name']!r} was destroyed "
                        f"at {obj['destruction_date']!r} but canon_fact "
                        f"{cf['id']!r} ({cf['fact'][:80]!r}) "
                        f"is dated {cf['time_start']!r}."
                    )
                    findings.append(_raise_finding(
                        conn, book_id, "object_resurrection", desc,
                        evidence=f"canon_entity:{obj['id']},canon_fact:{cf['id']}",
                    ))
            except (ValueError, TypeError):
                pass

        # entity_locations after destruction
        for el in conn.execute(
            "SELECT id, date_ref, location FROM entity_location"
            " WHERE entity_id=? AND date_ref IS NOT NULL",
            (obj["id"],),
        ).fetchall():
            try:
                if _parse_year(el["date_ref"]) > dest_year:
                    desc = (
                        f"Object resurrection: {obj['name']!r} was destroyed "
                        f"at {obj['destruction_date']!r} but appears at "
                        f"{el['location']!r} on {el['date_ref']!r}."
                    )
                    findings.append(_raise_finding(
                        conn, book_id, "object_resurrection", desc,
                        evidence=f"canon_entity:{obj['id']},entity_location:{el['id']}",
                    ))
            except (ValueError, TypeError):
                pass

    # --- persons with a death_date in entity_location ---
    persons = conn.execute(
        """
        SELECT id, name, death_date
        FROM   canon_entity
        WHERE  book_id     = ?
          AND  kind        = 'person'
          AND  death_date  IS NOT NULL
        """,
        (book_id,),
    ).fetchall()

    for person in persons:
        try:
            death_year = _parse_year(person["death_date"])
        except (ValueError, TypeError):
            continue

        for el in conn.execute(
            "SELECT id, date_ref, location FROM entity_location"
            " WHERE entity_id=? AND date_ref IS NOT NULL",
            (person["id"],),
        ).fetchall():
            try:
                if _parse_year(el["date_ref"]) > death_year:
                    desc = (
                        f"Object resurrection (person): {person['name']!r} "
                        f"died at {person['death_date']!r} but appears at "
                        f"{el['location']!r} on {el['date_ref']!r}."
                    )
                    findings.append(_raise_finding(
                        conn, book_id, "object_resurrection", desc,
                        evidence=f"canon_entity:{person['id']},entity_location:{el['id']}",
                    ))
            except (ValueError, TypeError):
                pass

    conn.commit()
    return findings


# ─── Aggregate runner ────────────────────────────────────────────────────────

def run_all_validators(
    conn: sqlite3.Connection, book_id: str
) -> dict:
    """Run all five continuity validators and return a summary dict.

    Return format::

        {
            "age_date_conflict":    [list of finding IDs],
            "impossible_travel":    [...],
            "knowledge_leak":       [...],
            "name_drift":           [...],
            "object_resurrection":  [...],
            "total_findings":       N,
            "clean":                True/False,
        }
    """
    results: dict = {}
    validators = [
        ("age_date_conflict",   check_age_date_conflict),
        ("impossible_travel",   check_impossible_travel),
        ("knowledge_leak",      check_knowledge_leak),
        ("name_drift",          check_name_drift),
        ("object_resurrection", check_object_resurrection),
    ]
    total = 0
    for name, fn in validators:
        ids = fn(conn, book_id)
        results[name] = ids
        total += len(ids)
    results["total_findings"] = total
    results["clean"] = total == 0
    return results
