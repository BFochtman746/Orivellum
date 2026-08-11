"""Scheduled automations — due-time math, overlap skip, and failure alerting.

Time is passed in explicitly and the runner/notify/studio engines are mocked:

- compute_next_run: daily before/after today's time; weekly day arithmetic
- tick starts a due schedule through the durable runner and advances
  next_run_at (never re-fires the same occurrence)
- quiet resource rule: a running operation or live audiobook render leaves
  due schedules untouched (they run next free tick, no pile-up)
- a failed scheduled run alerts exactly once (notification + review-inbox
  suggestion), atomically claimed so a racing tick cannot double-alert
- a schedule whose playbook was deleted alerts instead of running, and still
  advances so it never spins every tick
- disable/delete take effect immediately
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from orivellum.capabilities.operations import hooks, scheduler, store
from orivellum.capabilities.operations.playbooks import save_custom_playbook
from orivellum.capabilities.operations.scheduler import compute_next_run
from orivellum.database.db import OrivellumDB

NOW = datetime(2026, 8, 12, 10, 0, 0)  # a Wednesday (weekday 2)


@pytest.fixture()
def db(tmp_path):
    d = OrivellumDB(str(Path(tmp_path) / "test.db"))
    yield d
    d.close()


@pytest.fixture()
def quiet(monkeypatch):
    """No heavy jobs, runner accepts everything, notifications recorded."""
    started: list[str] = []
    notifications: list[dict] = []
    monkeypatch.setattr(
        "orivellum.capabilities.operations.runner.start_operation_run",
        lambda db, cfg, op_id: started.append(op_id) or True,
    )
    saved_notify, saved_studio = hooks.HOOKS.notify, hooks.HOOKS.studio
    hooks.HOOKS.notify = lambda kind, title, body="", url="": notifications.append(
        {"kind": kind, "title": title, "body": body}
    )
    hooks.HOOKS.studio = None
    yield {"started": started, "notifications": notifications}
    hooks.HOOKS.notify, hooks.HOOKS.studio = saved_notify, saved_studio


def _make_schedule(db, cadence="daily", time_of_day="02:00", **kw):
    pb = save_custom_playbook(
        db, "Nightly notify", [{"action_id": "notify", "label": "Hi", "params": {"title": "Hi"}}]
    )
    return scheduler.create_schedule(
        db, pb["id"], cadence, time_of_day, now=kw.pop("now", NOW), **kw
    )


# ── Due-time computation ─────────────────────────────────────────────────────


def test_daily_next_run_today_and_tomorrow():
    # 14:00 is still ahead of 10:00 → today.
    nxt = compute_next_run("daily", "14:00", None, NOW)
    assert nxt == datetime(2026, 8, 12, 14, 0)
    # 02:00 already passed → tomorrow.
    nxt = compute_next_run("daily", "02:00", None, NOW)
    assert nxt == datetime(2026, 8, 13, 2, 0)
    # Exactly now → strictly after, so tomorrow.
    nxt = compute_next_run("daily", "10:00", None, NOW)
    assert nxt == datetime(2026, 8, 13, 10, 0)


def test_weekly_next_run_day_math():
    # Sunday (6) from Wednesday → this Sunday.
    assert compute_next_run("weekly", "09:00", 6, NOW) == datetime(2026, 8, 16, 9, 0)
    # Same weekday, time passed → next week.
    assert compute_next_run("weekly", "02:00", 2, NOW) == datetime(2026, 8, 19, 2, 0)
    # Same weekday, time ahead → today.
    assert compute_next_run("weekly", "23:30", 2, NOW) == datetime(2026, 8, 12, 23, 30)


def test_bad_inputs_rejected(db):
    with pytest.raises(ValueError, match="HH:MM"):
        compute_next_run("daily", "25:99", None, NOW)
    with pytest.raises(ValueError, match="cadence"):
        _make_schedule(db, cadence="hourly")
    with pytest.raises(ValueError, match="day_of_week"):
        _make_schedule(db, cadence="weekly")
    with pytest.raises(ValueError, match="Unknown playbook"):
        scheduler.create_schedule(db, "nope", "daily", "02:00", now=NOW)


# ── Tick: dispatch & advancement ─────────────────────────────────────────────


def test_tick_starts_due_schedule_and_advances(db, quiet):
    sched = _make_schedule(db, time_of_day="02:00")  # next run tomorrow 02:00
    due_time = datetime(2026, 8, 13, 2, 0, 30)

    result = scheduler.tick(db, None, now=due_time)
    assert len(result["started"]) == 1
    ops = scheduler.list_schedule_runs(db, sched["id"])
    assert len(ops) == 1

    fresh = scheduler.get_schedule(db, sched["id"])
    assert fresh["last_run_at"] == due_time.isoformat()
    assert fresh["next_run_at"] == datetime(2026, 8, 14, 2, 0).isoformat()

    # Same occurrence never re-fires.
    result = scheduler.tick(db, None, now=datetime(2026, 8, 13, 2, 1, 30))
    assert result["started"] == []


def test_tick_ignores_disabled_and_future(db, quiet):
    sched = _make_schedule(db, time_of_day="02:00")
    scheduler.update_schedule(db, sched["id"], enabled=False)
    result = scheduler.tick(db, None, now=datetime(2026, 8, 13, 2, 0, 30))
    assert result["started"] == [] and result["failed_dispatch"] == []
    # Not yet due either way.
    result = scheduler.tick(db, None, now=datetime(2026, 8, 13, 1, 0))
    assert result["started"] == []


def test_reenable_recomputes_next_run(db, quiet):
    sched = _make_schedule(db, time_of_day="02:00")
    scheduler.update_schedule(db, sched["id"], enabled=False)
    # Re-enable long after the stored next_run passed — must NOT fire
    # immediately for a stale occurrence.
    later = datetime(2026, 9, 1, 10, 0)
    fresh = scheduler.update_schedule(db, sched["id"], enabled=True, now=later)
    assert fresh["next_run_at"] == datetime(2026, 9, 2, 2, 0).isoformat()


def test_delete_takes_effect_immediately(db, quiet):
    sched = _make_schedule(db)
    assert scheduler.delete_schedule(db, sched["id"]) is True
    assert scheduler.get_schedule(db, sched["id"]) is None
    result = scheduler.tick(db, None, now=datetime(2026, 8, 13, 2, 0, 30))
    assert result["started"] == []


# ── Quiet resource rule ──────────────────────────────────────────────────────


def test_busy_operation_defers_without_losing_the_run(db, quiet):
    sched = _make_schedule(db, time_of_day="02:00")
    # A heavy run is in flight.
    blocker = store.create_operation(db, "Heavy", [{"action_id": "notify", "label": "x"}])
    store.claim_operation(db, blocker)  # → running

    due_time = datetime(2026, 8, 13, 2, 0, 30)
    result = scheduler.tick(db, None, now=due_time)
    assert result["skipped_busy"] == [sched["id"]]
    assert result["started"] == []
    # Still due — next_run_at untouched.
    assert scheduler.get_schedule(db, sched["id"])["next_run_at"] <= due_time.isoformat()

    # System frees up → next tick runs it.
    with db._lock:
        db._conn.execute("UPDATE operations SET state='done' WHERE id=?", (blocker,))
        db._conn.commit()
    result = scheduler.tick(db, None, now=datetime(2026, 8, 13, 2, 1, 30))
    assert len(result["started"]) == 1


def test_busy_audiobook_render_defers(db, quiet):
    class FakeStudio:
        @staticmethod
        def list_active_work_tts_jobs():
            return [{"job_id": "render-1"}]

    hooks.HOOKS.studio = FakeStudio
    sched = _make_schedule(db, time_of_day="02:00")
    result = scheduler.tick(db, None, now=datetime(2026, 8, 13, 2, 0, 30))
    assert result["skipped_busy"] == [sched["id"]]


# ── Failure alerting ─────────────────────────────────────────────────────────


def test_failed_scheduled_run_alerts_exactly_once(db, quiet):
    sched = _make_schedule(db)
    op_id = store.create_operation(
        db,
        "Nightly notify",
        [{"action_id": "notify", "label": "x"}],
        schedule_id=sched["id"],
    )
    with db._lock:
        db._conn.execute(
            "UPDATE operations SET state='failed', error='engine down' WHERE id=?", (op_id,)
        )
        db._conn.commit()

    result = scheduler.tick(db, None, now=NOW)
    assert result["alerted"] == 1
    assert len(quiet["notifications"]) == 1
    assert "Automation failed" in quiet["notifications"][0]["title"]
    with db._lock:
        sugg = db._conn.execute(
            "SELECT * FROM suggestions WHERE kind='automation_failure'"
        ).fetchall()
    assert len(sugg) == 1
    assert json.loads(sugg[0]["meta"])["op_id"] == op_id

    # Second tick: no duplicate alert.
    result = scheduler.tick(db, None, now=NOW)
    assert result["alerted"] == 0
    assert len(quiet["notifications"]) == 1


def test_cancelled_run_does_not_alert(db, quiet):
    sched = _make_schedule(db)
    op_id = store.create_operation(
        db, "n", [{"action_id": "notify", "label": "x"}], schedule_id=sched["id"]
    )
    with db._lock:
        db._conn.execute("UPDATE operations SET state='cancelled' WHERE id=?", (op_id,))
        db._conn.commit()
    assert scheduler.tick(db, None, now=NOW)["alerted"] == 0
    assert quiet["notifications"] == []


def test_deleted_playbook_alerts_and_advances(db, quiet):
    from orivellum.capabilities.operations.playbooks import delete_custom_playbook

    sched = _make_schedule(db, time_of_day="02:00")
    delete_custom_playbook(db, sched["playbook_id"])

    due_time = datetime(2026, 8, 13, 2, 0, 30)
    result = scheduler.tick(db, None, now=due_time)
    assert result["started"] == []
    assert result["failed_dispatch"] == [sched["id"]]
    assert result["alerted"] == 1
    assert len(quiet["notifications"]) == 1
    # A failed run lands in history — nothing silent.
    runs = scheduler.list_schedule_runs(db, sched["id"])
    assert len(runs) == 1 and runs[0]["state"] == "failed"
    # Advanced — it will not spin every tick.
    assert scheduler.get_schedule(db, sched["id"])["next_run_at"] > due_time.isoformat()
    # Re-tick: no duplicate alert, no second run.
    result = scheduler.tick(db, None, now=datetime(2026, 8, 13, 2, 1, 30))
    assert result["alerted"] == 0 and len(quiet["notifications"]) == 1


def test_rejected_executor_alerts_immediately(db, quiet, monkeypatch):
    """Runner refusal (busy executor) marks the op failed and alerts at once."""
    monkeypatch.setattr(
        "orivellum.capabilities.operations.runner.start_operation_run",
        lambda db, cfg, op_id: False,
    )
    sched = _make_schedule(db, time_of_day="02:00")
    result = scheduler.tick(db, None, now=datetime(2026, 8, 13, 2, 0, 30))
    assert result["failed_dispatch"] == [sched["id"]]
    assert result["alerted"] == 1
    assert len(quiet["notifications"]) == 1
    # Next tick: nothing new — same occurrence never re-fires or re-alerts.
    result = scheduler.tick(db, None, now=datetime(2026, 8, 13, 2, 1, 30))
    assert result["alerted"] == 0 and result["failed_dispatch"] == []


# ── Race safety ──────────────────────────────────────────────────────────────


def test_occurrence_claim_is_single_winner(db, quiet):
    """Two racing ticks reading the same due row: exactly one claims it."""
    sched = _make_schedule(db, time_of_day="02:00")
    fresh = scheduler.get_schedule(db, sched["id"])
    now = datetime(2026, 8, 13, 2, 0, 30)
    assert scheduler._claim_occurrence(db, fresh, now) is True
    # Second claimant read the SAME stale row — fenced out.
    assert scheduler._claim_occurrence(db, fresh, now) is False


def test_disable_between_read_and_claim_blocks_dispatch(db, quiet):
    """A disable that lands after the due-row read still prevents the run."""
    sched = _make_schedule(db, time_of_day="02:00")
    fresh = scheduler.get_schedule(db, sched["id"])
    scheduler.update_schedule(db, sched["id"], enabled=False)
    assert scheduler._claim_occurrence(db, fresh, datetime(2026, 8, 13, 2, 0, 30)) is False


def test_alert_claim_and_inbox_row_are_transactional(db, quiet):
    """The alert flag and the review-inbox row commit together."""
    sched = _make_schedule(db)
    op_id = store.create_operation(
        db, "n", [{"action_id": "notify", "label": "x"}], schedule_id=sched["id"]
    )
    with db._lock:
        db._conn.execute("UPDATE operations SET state='failed' WHERE id=?", (op_id,))
        db._conn.commit()
    assert scheduler._claim_and_alert(db, store.get_operation(db, op_id)) is True
    with db._lock:
        flag = db._conn.execute(
            "SELECT failure_alerted FROM operations WHERE id=?", (op_id,)
        ).fetchone()[0]
        n_sugg = db._conn.execute(
            "SELECT COUNT(*) FROM suggestions WHERE kind='automation_failure'"
        ).fetchone()[0]
    assert flag == 1 and n_sugg == 1
    # Re-claim is a no-op.
    assert scheduler._claim_and_alert(db, store.get_operation(db, op_id)) is False
