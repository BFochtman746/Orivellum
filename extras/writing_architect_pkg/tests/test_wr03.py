"""
WR-03 tests: Canon & Continuity validators
==========================================

Run with:   uv run --with pytest pytest tests/test_wr03.py -v
All 28 prior tests (test_system.py + test_wr02.py) must remain green.

Test plan (17 tests)
--------------------
Validator fixtures (catch-defect + no-false-positive pairs):
  T01  age_date_conflict   — defect fires
  T02  age_date_conflict   — clean data → silent
  T03  impossible_travel   — defect fires
  T04  impossible_travel   — clean data → silent
  T05  knowledge_leak      — defect fires
  T06  knowledge_leak      — clean data → silent
  T07  name_drift          — defect fires
  T08  name_drift          — clean data (registered alias) → silent
  T09  object_resurrection — defect fires (canon_fact after destruction)
  T10  object_resurrection — clean data → silent
  T11  object_resurrection — person death_date variant fires

CLI commands:
  T12  wa entity     — creates a canon_entity row
  T13  wa alias      — registers an alias; duplicate is rejected
  T14  wa fact       — creates a canon_fact with stated_age + at_date
  T15  wa entity-location — registers two locations; impossible-travel finds them
  T16  wa knowledge-state + contract-knowledge — seeds a knowledge_leak via CLI
  T17  wa continuity-check — runs all validators and prints JSON summary

Integration:
  T18  run_all_validators clean book → total_findings == 0
  T19  run_all_validators dirty book → total_findings >= 5 (one defect per check)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile

import pytest

# Ensure the package under test is importable from this directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from writing_architect.domain import db as dbm
from writing_architect.domain.continuity import (
    check_age_date_conflict,
    check_impossible_travel,
    check_knowledge_leak,
    check_name_drift,
    check_object_resurrection,
    run_all_validators,
    _parse_year,
)
from writing_architect.cli import build_parser, cmd_entity, cmd_alias, cmd_fact
from writing_architect.cli import (
    cmd_entity_location,
    cmd_knowledge_state,
    cmd_contract_knowledge,
    cmd_continuity_check,
)


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    p = str(tmp_path / "test_wr03.sqlite")
    conn = dbm.init_db(p)
    conn.close()
    return p


@pytest.fixture
def book(db_path):
    """Return (db_path, book_id) for a fresh book in B0."""
    conn = dbm.init_db(db_path)
    bid = dbm.new_id("book_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO book_project(id,title,author,state,created_utc,updated_utc)"
        " VALUES (?,?,?,'B0',?,?)",
        (bid, "Ash and Silence", "Brian Fochtman", now, now),
    )
    conn.commit()
    conn.close()
    return db_path, bid


def _add_entity(conn, book_id, name, kind="person", **kwargs):
    eid = dbm.new_id("ce_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO canon_entity"
        "(id,book_id,kind,name,birth_date,birth_uncertainty,"
        " death_date,destruction_date,created_utc)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (
            eid, book_id, kind, name,
            kwargs.get("birth_date"),
            kwargs.get("birth_uncertainty"),
            kwargs.get("death_date"),
            kwargs.get("destruction_date"),
            now,
        ),
    )
    return eid


def _add_fact(conn, entity_id, fact_text, **kwargs):
    fid = dbm.new_id("cf_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO canon_fact"
        "(id,entity_id,fact,time_start,time_end,stated_age_years,at_date,created_utc)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            fid, entity_id, fact_text,
            kwargs.get("time_start"),
            kwargs.get("time_end"),
            kwargs.get("stated_age_years"),
            kwargs.get("at_date"),
            now,
        ),
    )
    return fid


def _add_location(conn, entity_id, date_ref, location, scene_ref=None):
    lid = dbm.new_id("el_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO entity_location(id,entity_id,date_ref,location,scene_ref,created_utc)"
        " VALUES (?,?,?,?,?,?)",
        (lid, entity_id, date_ref, location, scene_ref, now),
    )
    return lid


def _add_alias(conn, entity_id, alias, alias_type="name"):
    aid = dbm.new_id("ea_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO entity_alias(id,entity_id,alias,alias_type,created_utc)"
        " VALUES (?,?,?,?,?)",
        (aid, entity_id, alias, alias_type, now),
    )
    return aid


def _add_knowledge_state(conn, entity_id, fact_desc, from_scene, scene_seq):
    kid = dbm.new_id("ks_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO knowledge_state"
        "(id,entity_id,fact_description,can_know_from_scene,scene_sequence,created_utc)"
        " VALUES (?,?,?,?,?,?)",
        (kid, entity_id, fact_desc, from_scene, scene_seq, now),
    )
    return kid


def _add_contract(conn, book_id):
    cid = dbm.new_id("cc_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO chapter_contract"
        "(id,book_id,purpose,approved,created_utc)"
        " VALUES (?,?,?,0,?)",
        (cid, book_id, "test contract", now),
    )
    return cid


def _add_contract_knowledge(conn, contract_id, knowledge_state_id, scene_seq):
    kid = dbm.new_id("cka_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO contract_knowledge_access"
        "(id,contract_id,knowledge_state_id,scene_sequence,created_utc)"
        " VALUES (?,?,?,?,?)",
        (kid, contract_id, knowledge_state_id, scene_seq, now),
    )
    return kid


def _add_entity_ref(conn, contract_id, entity_id, name_used):
    rid = dbm.new_id("cer_")
    now = dbm.now_utc()
    conn.execute(
        "INSERT INTO chapter_contract_entity_ref"
        "(id,contract_id,entity_id,name_used,created_utc)"
        " VALUES (?,?,?,?,?)",
        (rid, contract_id, entity_id, name_used, now),
    )
    return rid


# ─── T01: age_date_conflict — defect fires ───────────────────────────────────

def test_age_date_conflict_fires(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    # Deborah born 1200 BCE; fact claims she was 25 at the battle (1125 BCE).
    # Expected age = -1125 − (-1200) = 75.  Stated = 25.  Δ = 50 > 5 → fires.
    eid = _add_entity(conn, bid, "Deborah", birth_date="1200 BCE")
    _add_fact(conn, eid, "Deborah led Israel at the battle",
              stated_age_years=25, at_date="1125 BCE")
    conn.commit()

    findings = check_age_date_conflict(conn, bid)
    conn.close()

    assert len(findings) == 1, f"expected 1 finding, got {len(findings)}"


# ─── T02: age_date_conflict — clean data is silent ───────────────────────────

def test_age_date_conflict_no_false_positive(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    # Deborah born 1200 BCE; states she was 75 at 1125 BCE → expected 75, exact match.
    eid = _add_entity(conn, bid, "Deborah", birth_date="1200 BCE")
    _add_fact(conn, eid, "Deborah led Israel",
              stated_age_years=75, at_date="1125 BCE")
    conn.commit()

    findings = check_age_date_conflict(conn, bid)
    conn.close()

    assert findings == [], f"false positive: {findings}"


# ─── T03: impossible_travel — defect fires ───────────────────────────────────

def test_impossible_travel_fires(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    eid = _add_entity(conn, bid, "Barak")
    _add_location(conn, eid, "1125 BCE", "Mount Tabor")
    _add_location(conn, eid, "1125 BCE", "Jericho")   # same date, different place
    conn.commit()

    findings = check_impossible_travel(conn, bid)
    conn.close()

    assert len(findings) == 1, f"expected 1 finding, got {len(findings)}"


# ─── T04: impossible_travel — clean data is silent ───────────────────────────

def test_impossible_travel_no_false_positive(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    eid = _add_entity(conn, bid, "Barak")
    _add_location(conn, eid, "1125 BCE", "Mount Tabor")
    _add_location(conn, eid, "1124 BCE", "Jericho")   # different dates — fine
    conn.commit()

    findings = check_impossible_travel(conn, bid)
    conn.close()

    assert findings == [], f"false positive: {findings}"


# ─── T05: knowledge_leak — defect fires ──────────────────────────────────────

def test_knowledge_leak_fires(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    eid = _add_entity(conn, bid, "Miriam")
    # Knowledge becomes available at Chapter 5 (seq 5).
    kid = _add_knowledge_state(conn, eid, "Miriam learns of Sisera's defeat",
                               "Chapter 5", scene_seq=5)
    # A chapter contract in Chapter 3 (seq 3) accesses that knowledge → leak.
    contract_id = _add_contract(conn, bid)
    _add_contract_knowledge(conn, contract_id, kid, scene_seq=3)
    conn.commit()

    findings = check_knowledge_leak(conn, bid)
    conn.close()

    assert len(findings) == 1, f"expected 1 finding, got {len(findings)}"


# ─── T06: knowledge_leak — clean data is silent ──────────────────────────────

def test_knowledge_leak_no_false_positive(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    eid = _add_entity(conn, bid, "Miriam")
    kid = _add_knowledge_state(conn, eid, "Miriam learns of Sisera's defeat",
                               "Chapter 5", scene_seq=5)
    contract_id = _add_contract(conn, bid)
    # Scene seq 7 > available seq 5 → no leak
    _add_contract_knowledge(conn, contract_id, kid, scene_seq=7)
    conn.commit()

    findings = check_knowledge_leak(conn, bid)
    conn.close()

    assert findings == [], f"false positive: {findings}"


# ─── T07: name_drift — defect fires ──────────────────────────────────────────

def test_name_drift_fires(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    eid = _add_entity(conn, bid, "Deborah")
    _add_alias(conn, eid, "the Judge", alias_type="title")
    # Register contract that calls her "the Prophetess" — not in aliases
    contract_id = _add_contract(conn, bid)
    _add_entity_ref(conn, contract_id, eid, "the Prophetess")
    conn.commit()

    findings = check_name_drift(conn, bid)
    conn.close()

    assert len(findings) == 1, f"expected 1 finding, got {len(findings)}"


# ─── T08: name_drift — registered alias is silent ────────────────────────────

def test_name_drift_no_false_positive(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    eid = _add_entity(conn, bid, "Deborah")
    _add_alias(conn, eid, "the Judge", alias_type="title")
    _add_alias(conn, eid, "the Prophetess", alias_type="title")
    contract_id = _add_contract(conn, bid)
    _add_entity_ref(conn, contract_id, eid, "the Prophetess")  # now registered
    conn.commit()

    findings = check_name_drift(conn, bid)
    conn.close()

    assert findings == [], f"false positive: {findings}"


# ─── T09: object_resurrection — canon_fact after destruction ─────────────────

def test_object_resurrection_fires_on_fact(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    # The Ark is destroyed in 587 BCE; a later fact places it in 500 BCE.
    eid = _add_entity(conn, bid, "Ark of the Covenant", kind="object",
                      destruction_date="587 BCE")
    _add_fact(conn, eid, "The Ark is carried in procession",
              time_start="500 BCE")   # 500 BCE is AFTER 587 BCE
    conn.commit()

    findings = check_object_resurrection(conn, bid)
    conn.close()

    assert len(findings) >= 1, f"expected ≥1 finding, got {len(findings)}"


# ─── T10: object_resurrection — clean data is silent ─────────────────────────

def test_object_resurrection_no_false_positive(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    eid = _add_entity(conn, bid, "Ark of the Covenant", kind="object",
                      destruction_date="587 BCE")
    # Fact dated before destruction → clean
    _add_fact(conn, eid, "The Ark rests in the Temple",
              time_start="950 BCE")   # 950 BCE is well before 587 BCE
    conn.commit()

    findings = check_object_resurrection(conn, bid)
    conn.close()

    assert findings == [], f"false positive: {findings}"


# ─── T11: object_resurrection — person death_date variant ────────────────────

def test_object_resurrection_fires_on_person_death(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    eid = _add_entity(conn, bid, "Sisera", kind="person", death_date="1125 BCE")
    # location AFTER death → resurrection
    _add_location(conn, eid, "1120 BCE", "Hazor")
    conn.commit()

    findings = check_object_resurrection(conn, bid)
    conn.close()

    assert len(findings) >= 1, f"expected ≥1 finding, got {len(findings)}"


# ─── T12: wa entity CLI command ──────────────────────────────────────────────

def test_cli_entity(book):
    db_path, bid = book
    parser = build_parser()
    args = parser.parse_args([
        "entity", db_path, bid,
        "--name", "Barak",
        "--kind", "person",
        "--birth-date", "1150 BCE",
        "--actor", "test",
    ])
    rc = args.func(args)
    assert rc == 0

    conn = dbm.init_db(db_path)
    row = conn.execute(
        "SELECT * FROM canon_entity WHERE name='Barak' AND book_id=?", (bid,)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["birth_date"] == "1150 BCE"


# ─── T13: wa alias CLI command ───────────────────────────────────────────────

def test_cli_alias(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    eid = _add_entity(conn, bid, "Deborah")
    conn.commit(); conn.close()

    parser = build_parser()
    args = parser.parse_args([
        "alias", db_path, eid,
        "--alias", "the Judge",
        "--alias-type", "title",
    ])
    rc = args.func(args)
    assert rc == 0

    conn = dbm.init_db(db_path)
    row = conn.execute(
        "SELECT * FROM entity_alias WHERE entity_id=? AND alias='the Judge'", (eid,)
    ).fetchone()
    conn.close()
    assert row is not None


# ─── T14: wa fact CLI command ────────────────────────────────────────────────

def test_cli_fact(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    eid = _add_entity(conn, bid, "Deborah", birth_date="1200 BCE")
    conn.commit(); conn.close()

    parser = build_parser()
    args = parser.parse_args([
        "fact", db_path, eid,
        "--fact", "Deborah leads Israel",
        "--time-start", "1125 BCE",
        "--stated-age", "25",
        "--at-date", "1125 BCE",
    ])
    rc = args.func(args)
    assert rc == 0

    conn = dbm.init_db(db_path)
    row = conn.execute(
        "SELECT * FROM canon_fact WHERE entity_id=?", (eid,)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["stated_age_years"] == 25


# ─── T15: wa entity-location CLI + impossible-travel integration ─────────────

def test_cli_entity_location_and_validator(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    eid = _add_entity(conn, bid, "Barak")
    conn.commit(); conn.close()

    parser = build_parser()
    for loc in [("Mount Tabor", "Chapter 1"), ("Jericho", "Chapter 1")]:
        args = parser.parse_args([
            "entity-location", db_path, eid,
            "--date", "1125 BCE",
            "--location", loc[0],
            "--scene", loc[1],
        ])
        assert args.func(args) == 0

    conn = dbm.init_db(db_path)
    findings = check_impossible_travel(conn, bid)
    conn.close()
    assert len(findings) == 1


# ─── T16: wa knowledge-state + contract-knowledge CLI ────────────────────────

def test_cli_knowledge_state_and_contract(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    eid = _add_entity(conn, bid, "Miriam")
    contract_id = _add_contract(conn, bid)
    conn.commit(); conn.close()

    parser = build_parser()

    # Register knowledge state (available from seq 5)
    args = parser.parse_args([
        "knowledge-state", db_path, eid,
        "--fact", "Miriam learns of Sisera's defeat",
        "--from-scene", "Chapter 5",
        "--scene-seq", "5",
    ])
    rc = args.func(args)
    assert rc == 0

    conn = dbm.init_db(db_path)
    kid = conn.execute(
        "SELECT id FROM knowledge_state WHERE entity_id=?", (eid,)
    ).fetchone()["id"]
    conn.close()

    # Access at seq 3 (before it's available → leak)
    args = parser.parse_args([
        "contract-knowledge", db_path, contract_id,
        "--knowledge-state", kid,
        "--scene-seq", "3",
    ])
    rc = args.func(args)
    assert rc == 0

    conn = dbm.init_db(db_path)
    findings = check_knowledge_leak(conn, bid)
    conn.close()
    assert len(findings) == 1


# ─── T17: wa continuity-check CLI ────────────────────────────────────────────

def test_cli_continuity_check(book, capsys):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    # Seed one impossible-travel defect
    eid = _add_entity(conn, bid, "Barak")
    _add_location(conn, eid, "1125 BCE", "Mount Tabor")
    _add_location(conn, eid, "1125 BCE", "Jericho")
    conn.commit(); conn.close()

    parser = build_parser()
    args = parser.parse_args(["continuity-check", db_path, bid])
    rc = args.func(args)
    assert rc in (0, 1)   # 0 = clean, 1 = findings present; both are valid exits

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["total_findings"] >= 1
    assert len(data["impossible_travel"]) == 1


# ─── T18: run_all_validators — fully clean book ──────────────────────────────

def test_run_all_validators_clean(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)
    # No defects seeded
    result = run_all_validators(conn, bid)
    conn.close()

    assert result["total_findings"] == 0
    assert result["clean"] is True


# ─── T19: run_all_validators — one defect per validator ──────────────────────

def test_run_all_validators_all_fire(book):
    db_path, bid = book
    conn = dbm.init_db(db_path)

    # 1. Age/date conflict: Deborah born 1200 BCE, stated 25 at 1125 BCE
    d_eid = _add_entity(conn, bid, "Deborah", birth_date="1200 BCE")
    _add_fact(conn, d_eid, "Deborah judges Israel",
              stated_age_years=25, at_date="1125 BCE")

    # 2. Impossible travel: Barak in two places same date
    b_eid = _add_entity(conn, bid, "Barak")
    _add_location(conn, b_eid, "1125 BCE", "Mount Tabor")
    _add_location(conn, b_eid, "1125 BCE", "Jericho")

    # 3. Knowledge leak: Miriam accesses knowledge before it's available
    m_eid = _add_entity(conn, bid, "Miriam")
    kid = _add_knowledge_state(conn, m_eid, "Miriam learns of Sisera's defeat",
                               "Chapter 5", scene_seq=5)
    contract_id = _add_contract(conn, bid)
    _add_contract_knowledge(conn, contract_id, kid, scene_seq=2)

    # 4. Name drift: unknown alias used for Deborah
    contract2 = _add_contract(conn, bid)
    _add_entity_ref(conn, contract2, d_eid, "the Prophetess")  # not in aliases

    # 5. Object resurrection: Ark destroyed then appears later
    ark_eid = _add_entity(conn, bid, "Ark of the Covenant", kind="object",
                          destruction_date="587 BCE")
    _add_fact(conn, ark_eid, "The Ark is carried north",
              time_start="500 BCE")

    conn.commit()
    result = run_all_validators(conn, bid)
    conn.close()

    assert result["age_date_conflict"],   "age_date_conflict should fire"
    assert result["impossible_travel"],   "impossible_travel should fire"
    assert result["knowledge_leak"],      "knowledge_leak should fire"
    assert result["name_drift"],          "name_drift should fire"
    assert result["object_resurrection"], "object_resurrection should fire"
    assert result["total_findings"] >= 5
    assert result["clean"] is False


# ─── _parse_year unit tests ───────────────────────────────────────────────────

@pytest.mark.parametrize("s,expected", [
    ("1200 BCE",  -1200),
    ("c.1200 BCE", -1200),
    ("587 BC",    -587),
    ("70 CE",      70),
    ("33 AD",      33),
    ("-587",      -587),
    ("2026",       2026),
    ("1200-1150 BCE", -1200),
    ("~1125 BCE", -1125),
])
def test_parse_year(s, expected):
    assert _parse_year(s) == expected
