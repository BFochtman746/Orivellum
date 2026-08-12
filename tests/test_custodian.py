"""Tests for the proactive custodian — an unattended nightshift pass.

Characterisation (Class 4 audit follow-up): custodian is governance-adjacent
in the "unattended job" sense — it runs nightly with no human watching and
writes user-facing nudges.  Under the floor rule (no module imported by an
unattended job without tests), these pin:

  * nudge upsert policy — at most ONE unresolved nudge per (work, kind);
    re-running refreshes in place, never duplicates
  * user dismissal suppresses re-nudging (SUPPRESS_DAYS window)
  * auto-resolution clears without triggering the suppression window
  * run_custodian isolates per-Work failures and never raises
"""

from __future__ import annotations

from datetime import timedelta

from orivellum.capabilities import custodian


def _make_db(tmp_path):
    from orivellum.database.db import OrivellumDB

    return OrivellumDB(str(tmp_path / "test.db"))


def _nudge_rows(db, work_id):
    with db._lock:
        rows = db._conn.execute("SELECT * FROM work_nudges WHERE work_id=?", (work_id,)).fetchall()
    return [dict(r) for r in rows]


# ── Upsert policy ─────────────────────────────────────────────────────────────


def test_upsert_creates_once_then_refreshes_in_place(tmp_path):
    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]

    nid1, created1 = custodian._upsert_nudge(db, wid, "stalled", "msg 1", None, 15, 2)
    assert created1
    nid2, created2 = custodian._upsert_nudge(db, wid, "stalled", "msg 2", None, 16, 2)
    assert not created2
    assert nid2 == nid1

    rows = _nudge_rows(db, wid)
    assert len(rows) == 1, "re-running the pass must never duplicate a nudge"
    assert rows[0]["message"] == "msg 2"
    assert rows[0]["days_stalled"] == 16


def test_different_kinds_are_independent(tmp_path):
    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    custodian._upsert_nudge(db, wid, "stalled", "m", None, 15, 2)
    custodian._upsert_nudge(db, wid, "no_docs", "m", None, None, 1)
    assert len(_nudge_rows(db, wid)) == 2


# ── Dismissal & suppression ───────────────────────────────────────────────────


def test_user_dismissal_suppresses_renudge(tmp_path):
    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    nid, _ = custodian._upsert_nudge(db, wid, "stalled", "m", None, 15, 2)
    assert custodian.resolve_nudge(db, nid)
    assert not custodian.resolve_nudge(db, nid)  # already resolved

    sentinel, created = custodian._upsert_nudge(db, wid, "stalled", "again", None, 16, 2)
    assert sentinel == "" and not created, "dismissed nudge must not reappear in the window"
    assert len(_nudge_rows(db, wid)) == 1


def test_auto_resolve_does_not_suppress(tmp_path):
    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    custodian._upsert_nudge(db, wid, "stalled", "m", None, 15, 2)
    assert custodian._auto_resolve_nudge(db, wid, "stalled")

    # Condition returned — auto-resolution must not block a fresh nudge.
    nid, created = custodian._upsert_nudge(db, wid, "stalled", "back", None, 20, 2)
    assert created and nid


def test_prune_removes_only_old_resolved(tmp_path):
    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    nid, _ = custodian._upsert_nudge(db, wid, "stalled", "m", None, 15, 2)
    custodian.resolve_nudge(db, nid)
    old = (custodian._now_utc() - timedelta(days=custodian.PRUNE_DAYS + 5)).isoformat()
    with db._lock:
        db._conn.execute("UPDATE work_nudges SET resolved_at=? WHERE id=?", (old, nid))
        db._conn.commit()
    assert custodian._prune_old_nudges(db) == 1
    assert _nudge_rows(db, wid) == []


# ── Retrieval & pass isolation ────────────────────────────────────────────────


def test_get_top_nudges_orders_by_priority(tmp_path):
    db = _make_db(tmp_path)
    w1 = db.create_work("Low")["id"]
    w2 = db.create_work("High")["id"]
    custodian._upsert_nudge(db, w1, "no_docs", "low", None, None, 1)
    custodian._upsert_nudge(db, w2, "pipeline_stuck", "high", "B3", 12, 3)
    top = custodian.get_top_nudges(db, limit=5)
    assert top[0]["work_id"] == w2
    assert top[0]["work_title"] == "High"


def test_run_custodian_isolates_per_work_errors(tmp_path, monkeypatch):
    db = _make_db(tmp_path)
    db.create_work("A")
    db.create_work("B")

    calls = {"n": 0}

    def _flaky(db_, work):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bad work")
        return (1, 0)

    monkeypatch.setattr(custodian, "_check_one_work", _flaky)
    summary = custodian.run_custodian(db)
    assert summary["status"] == "ok"
    assert calls["n"] == 2, "one bad work must not abort the pass"
    assert summary["nudges_written"] == 1
    assert summary["nudges_skipped"] == 1


def test_run_custodian_survives_list_works_failure(tmp_path, monkeypatch):
    db = _make_db(tmp_path)

    def _boom(**kw):
        raise RuntimeError("db gone")

    monkeypatch.setattr(db, "list_works", _boom)
    summary = custodian.run_custodian(db)
    assert summary["status"] == "error"
