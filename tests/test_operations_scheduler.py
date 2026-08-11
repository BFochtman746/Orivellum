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


def test_run_now_starts_through_runner_with_schedule_link(db, quiet):
    sched = _make_schedule(db)
    result = scheduler.run_schedule_now(db, None, sched["id"])
    assert result["ok"] is True
    assert quiet["started"] == [result["operation_id"]]
    op = store.get_operation(db, result["operation_id"])
    assert op["schedule_id"] == sched["id"]
    # Manual runs never touch the regular occurrence.
    after = scheduler.get_schedule(db, sched["id"])
    assert after["next_run_at"] == sched["next_run_at"]
    # It lands in the automation's run history.
    runs = scheduler.list_schedule_runs(db, sched["id"])
    assert [r["id"] for r in runs] == [result["operation_id"]]


def test_run_now_works_on_a_paused_schedule(db, quiet):
    sched = _make_schedule(db)
    scheduler.update_schedule(db, sched["id"], enabled=False)
    result = scheduler.run_schedule_now(db, None, sched["id"])
    assert result["ok"] is True


def test_run_now_refuses_when_busy(db, quiet):
    sched = _make_schedule(db)
    store.create_operation(db, title="Busy", steps=[])
    with db._lock:
        db._conn.execute("UPDATE operations SET state='running'")
        db._conn.commit()
    result = scheduler.run_schedule_now(db, None, sched["id"])
    assert result["ok"] is False
    assert result["code"] == 409
    assert "already running" in result["reason"]
    assert quiet["started"] == []


def test_run_now_refuses_a_pending_run_of_the_same_schedule(db, quiet):
    sched = _make_schedule(db)
    store.create_operation(db, title="Pending", steps=[], schedule_id=sched["id"])
    result = scheduler.run_schedule_now(db, None, sched["id"])
    assert result["ok"] is False
    assert result["code"] == 409
    assert "already running" in result["reason"]
    assert quiet["started"] == []


def test_run_now_refuses_an_ordinary_pending_operation(db, quiet):
    """The create→start gap of the manual /start endpoint: its op sits in
    'pending' for a moment before the runner claims it. Admission treats any
    pending op as live, so Run now refuses instead of double-starting."""
    sched = _make_schedule(db)
    store.create_operation(db, title="Manual builder op", steps=[])  # no schedule_id
    result = scheduler.run_schedule_now(db, None, sched["id"])
    assert result["ok"] is False
    assert result["code"] == 409
    assert quiet["started"] == []
    # And the failed-submission path releases the block (never wedged forever).
    ops = store.list_operations(db)
    assert store.fail_pending_operation(db, ops[0]["id"], "server busy") is True
    assert scheduler.run_schedule_now(db, None, sched["id"])["ok"] is True


def test_run_now_refuses_a_pending_run_of_another_schedule(db, quiet):
    """Quiet rule is global: a schedule-linked run that's about to start
    blocks other manual runs too."""
    sched_a = _make_schedule(db)
    sched_b = _make_schedule(db, time_of_day="03:00")
    store.create_operation(db, title="Pending", steps=[], schedule_id=sched_a["id"])
    result = scheduler.run_schedule_now(db, None, sched_b["id"])
    assert result["ok"] is False
    assert result["code"] == 409
    assert quiet["started"] == []


def test_run_now_unknown_schedule_and_deleted_playbook(db, quiet):
    assert scheduler.run_schedule_now(db, None, "sched_nope")["code"] == 404

    sched = _make_schedule(db)
    with db._lock:
        db._conn.execute("DELETE FROM custom_playbooks")
        db._conn.commit()
    result = scheduler.run_schedule_now(db, None, sched["id"])
    assert result["ok"] is False
    assert result["code"] == 422
    assert quiet["started"] == []


def test_run_now_rejected_executor_fails_the_op(db, quiet, monkeypatch):
    sched = _make_schedule(db)
    monkeypatch.setattr(
        "orivellum.capabilities.operations.runner.start_operation_run",
        lambda db, cfg, op_id: False,
    )
    result = scheduler.run_schedule_now(db, None, sched["id"])
    assert result["ok"] is False
    assert result["code"] == 503
    # The created op is failed, not left pending forever.
    runs = scheduler.list_schedule_runs(db, sched["id"])
    assert runs and runs[0]["state"] == "failed"


def test_run_now_concurrent_requests_have_a_single_winner(db, quiet):
    """Truly concurrent Run-now clicks: admission+create share one DB lock,
    so exactly one request wins no matter the interleaving."""
    import threading

    sched = _make_schedule(db)
    barrier = threading.Barrier(2)
    results: list[dict] = []

    def _go() -> None:
        barrier.wait()
        results.append(scheduler.run_schedule_now(db, None, sched["id"]))

    threads = [threading.Thread(target=_go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(r["ok"] for r in results) == [False, True]
    assert len(quiet["started"]) == 1  # exactly one run reached the runner
    loser = next(r for r in results if not r["ok"])
    assert loser["code"] == 409


def test_tick_defers_when_a_manual_run_won_admission(db, quiet):
    """A manual run admitted between the tick's busy check and its dispatch
    must not double-start — and the occurrence is deferred, not lost."""
    sched = _make_schedule(db)
    due = scheduler.get_schedule(db, sched["id"])

    # Manual run wins admission first (its op is pending — runner mocked).
    manual = scheduler.run_schedule_now(db, None, sched["id"])
    assert manual["ok"] is True
    with db._lock:  # keep it live-but-not-running: admission must still block
        db._conn.execute(
            "UPDATE operations SET state='pending' WHERE id=?", (manual["operation_id"],)
        )
        db._conn.commit()

    result = scheduler.tick(db, None, now=datetime.fromisoformat(due["next_run_at"]))
    assert result["started"] == []
    assert sched["id"] in result["skipped_busy"]
    # Occurrence given back: still due at the original time.
    after = scheduler.get_schedule(db, sched["id"])
    assert after["next_run_at"] == due["next_run_at"]
    # Exactly one operation exists for this schedule — the manual one.
    runs = scheduler.list_schedule_runs(db, sched["id"])
    assert [r["id"] for r in runs] == [manual["operation_id"]]


def _seed_terminal_run(db, schedule_id, state, days_old, alerted=0, steps=1):
    """Insert a finished scheduled run *days_old* days in the past."""
    from datetime import UTC, timedelta

    op_id = store.create_operation(
        db, title="Old run", steps=[{"action_id": "notify", "label": "x"}] * steps
    )
    ts = (datetime.now(UTC) - timedelta(days=days_old)).isoformat()
    with db._lock:
        db._conn.execute(
            "UPDATE operations SET state=?, schedule_id=?, failure_alerted=?, "
            "created_at=?, updated_at=? WHERE id=?",
            (state, schedule_id, alerted, ts, ts, op_id),
        )
        db._conn.commit()
    return op_id


def _op_count(db, table="operations"):
    with db._lock:
        return db._conn.execute(f"SELECT COUNT(*) c FROM {table}").fetchone()["c"]


def test_prune_keeps_recent_runs_and_caps_per_schedule(db, quiet):
    sched = _make_schedule(db)
    for i in range(60):
        _seed_terminal_run(db, sched["id"], "done", days_old=i * 0.01)
    deleted = store.prune_finished_schedule_runs(db, keep_per_schedule=50)
    assert deleted == 10
    runs = scheduler.list_schedule_runs(db, sched["id"], limit=100)
    assert len(runs) == 50
    # Step rows of pruned ops are gone too — no orphans.
    with db._lock:
        orphan_steps = db._conn.execute(
            "SELECT COUNT(*) c FROM operation_steps "
            "WHERE operation_id NOT IN (SELECT id FROM operations)"
        ).fetchone()["c"]
    assert orphan_steps == 0
    # Idempotent: nothing left to delete.
    assert store.prune_finished_schedule_runs(db, keep_per_schedule=50) == 0


def test_prune_drops_ancient_runs_but_keeps_a_little_history(db, quiet):
    sched = _make_schedule(db)
    for i in range(20):
        _seed_terminal_run(db, sched["id"], "done", days_old=100 + i)
    deleted = store.prune_finished_schedule_runs(db, max_age_days=90, always_keep=5)
    assert deleted == 15  # dormant schedule never goes fully blank
    assert len(scheduler.list_schedule_runs(db, sched["id"], limit=100)) == 5


def test_prune_never_touches_active_manual_or_unalerted_runs(db, quiet):
    sched = _make_schedule(db)
    keep_ids = {
        _seed_terminal_run(db, sched["id"], "running", days_old=200),  # active
        _seed_terminal_run(db, sched["id"], "pending", days_old=200),  # active
        _seed_terminal_run(db, None, "done", days_old=200),  # manual op
        # failed but its exactly-once alert has not been claimed yet
        _seed_terminal_run(db, sched["id"], "failed", days_old=200, alerted=0),
    }
    doomed = _seed_terminal_run(db, sched["id"], "failed", days_old=200, alerted=1)
    for _ in range(6):  # push the alerted failure past always_keep
        _seed_terminal_run(db, sched["id"], "done", days_old=95)
    deleted = store.prune_finished_schedule_runs(
        db, keep_per_schedule=50, max_age_days=90, always_keep=5
    )
    assert deleted >= 1
    with db._lock:
        remaining = {r["id"] for r in db._conn.execute("SELECT id FROM operations").fetchall()}
    assert keep_ids <= remaining
    assert doomed not in remaining


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
