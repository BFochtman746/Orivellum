"""Scheduled automations — playbooks that run themselves.

A schedule attaches a recurrence (nightly / weekly at a local time) to any
playbook, built-in or custom. A daemon thread ticks once a minute; each tick:

1. Alerts exactly once for any failed scheduled run (browser notification via
   the notify hook + a review-inbox item via the suggestions table).
2. Finds due, enabled schedules. If the system is busy with a heavy job (any
   operation currently running, or a live audiobook render), due schedules
   are left due and retried next tick — quiet resource rule, never a pile-up.
3. Starts each due schedule through the SAME durable runner as manual runs
   (checkpoints, claim fencing, resume) and advances next_run_at.

Times are LOCAL server time (this is a local-first personal server), matching
the nightshift daemon's precedent. next_run_at is a naive local ISO string —
lexically comparable, human-readable in the DB.

Layering: capabilities never import orivellum.api — notifications and the
studio module arrive via the operations hooks.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from orivellum.capabilities.operations import hooks, store
from orivellum.capabilities.operations.planner import validate_steps
from orivellum.capabilities.operations.playbooks import get_playbook
from orivellum.capabilities.operations.registry import get_op_registry

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.operations.scheduler")

_TICK_SECONDS = 60
CADENCES = ("daily", "weekly")


def _now_local() -> datetime:
    return datetime.now()


# ── Due-time computation ─────────────────────────────────────────────────────


def compute_next_run(
    cadence: str,
    time_of_day: str,
    day_of_week: int | None,
    now: datetime,
) -> datetime:
    """Next occurrence strictly AFTER *now* (local time).

    cadence: "daily" | "weekly"; time_of_day: "HH:MM";
    day_of_week: 0=Monday … 6=Sunday (weekly only).
    """
    hour, minute = _parse_time(time_of_day)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if cadence == "daily":
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate
    if cadence == "weekly":
        dow = int(day_of_week or 0)
        ahead = (dow - candidate.weekday()) % 7
        candidate += timedelta(days=ahead)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate
    raise ValueError(f"Unknown cadence {cadence!r}")


def _parse_time(time_of_day: str) -> tuple[int, int]:
    try:
        hour_s, minute_s = time_of_day.strip().split(":")
        hour, minute = int(hour_s), int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour, minute
    except Exception:
        raise ValueError(f"time_of_day must be HH:MM, got {time_of_day!r}") from None


# ── Schedule CRUD ────────────────────────────────────────────────────────────


def _row_to_schedule(row: Any) -> dict:
    return {
        "id": row["id"],
        "playbook_id": row["playbook_id"],
        "title": row["title"],
        "work_id": row["work_id"],
        "cadence": row["cadence"],
        "time_of_day": row["time_of_day"],
        "day_of_week": row["day_of_week"],
        "enabled": bool(row["enabled"]),
        "last_run_at": row["last_run_at"],
        "next_run_at": row["next_run_at"],
        "created_at": row["created_at"],
    }


def list_schedules(db: OrivellumDB) -> list[dict]:
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM playbook_schedules ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_schedule(r) for r in rows]


def get_schedule(db: OrivellumDB, schedule_id: str) -> dict | None:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM playbook_schedules WHERE id=?", (schedule_id,)
        ).fetchone()
    return _row_to_schedule(row) if row else None


def create_schedule(
    db: OrivellumDB,
    playbook_id: str,
    cadence: str,
    time_of_day: str,
    *,
    day_of_week: int | None = None,
    work_id: str | None = None,
    title: str = "",
    now: datetime | None = None,
) -> dict:
    """Validate and persist a schedule. Raises ValueError on bad input."""
    if cadence not in CADENCES:
        raise ValueError(f"cadence must be one of: {', '.join(CADENCES)}")
    _parse_time(time_of_day)  # raises on bad format
    if cadence == "weekly" and (day_of_week is None or not (0 <= int(day_of_week) <= 6)):
        raise ValueError("weekly schedules need day_of_week 0 (Mon) … 6 (Sun)")
    pb = get_playbook(playbook_id, db)
    if pb is None:
        raise ValueError(f"Unknown playbook {playbook_id!r}")
    if not pb.get("custom") and not work_id:
        # Every built-in playbook operates on a single Work.
        raise ValueError("This playbook works on one Work — include work_id.")
    if work_id and db.get_work(work_id) is None:
        raise ValueError(f"Unknown Work {work_id!r}")

    now = now or _now_local()
    sched_id = f"sched_{uuid.uuid4().hex[:10]}"
    next_run = compute_next_run(cadence, time_of_day, day_of_week, now)
    with db._lock:
        db._conn.execute(
            """INSERT INTO playbook_schedules
               (id, playbook_id, title, work_id, cadence, time_of_day, day_of_week,
                enabled, last_run_at, next_run_at, created_at)
               VALUES (?,?,?,?,?,?,?,1,NULL,?,?)""",
            (
                sched_id,
                playbook_id,
                (title or pb["title"])[:120],
                work_id,
                cadence,
                time_of_day.strip(),
                int(day_of_week) if day_of_week is not None else None,
                next_run.isoformat(),
                now.isoformat(),
            ),
        )
        db._conn.commit()
    return get_schedule(db, sched_id)  # type: ignore[return-value]


def update_schedule(
    db: OrivellumDB,
    schedule_id: str,
    *,
    enabled: bool | None = None,
    cadence: str | None = None,
    time_of_day: str | None = None,
    day_of_week: int | None = None,
    now: datetime | None = None,
) -> dict | None:
    """Update fields; recompute next_run_at when timing changes or on re-enable.

    Takes effect immediately — the tick reads the table fresh every pass.
    """
    sched = get_schedule(db, schedule_id)
    if sched is None:
        return None
    new_cadence = cadence or sched["cadence"]
    new_time = time_of_day or sched["time_of_day"]
    new_dow = day_of_week if day_of_week is not None else sched["day_of_week"]
    if new_cadence not in CADENCES:
        raise ValueError(f"cadence must be one of: {', '.join(CADENCES)}")
    _parse_time(new_time)
    if new_cadence == "weekly" and (new_dow is None or not (0 <= int(new_dow) <= 6)):
        raise ValueError("weekly schedules need day_of_week 0 (Mon) … 6 (Sun)")

    timing_changed = (
        new_cadence != sched["cadence"]
        or new_time != sched["time_of_day"]
        or new_dow != sched["day_of_week"]
    )
    re_enabled = enabled is True and not sched["enabled"]
    next_run = sched["next_run_at"]
    if timing_changed or re_enabled:
        next_run = compute_next_run(new_cadence, new_time, new_dow, now or _now_local()).isoformat()

    with db._lock:
        db._conn.execute(
            """UPDATE playbook_schedules
               SET enabled=?, cadence=?, time_of_day=?, day_of_week=?, next_run_at=?
               WHERE id=?""",
            (
                int(enabled if enabled is not None else sched["enabled"]),
                new_cadence,
                new_time,
                int(new_dow) if new_dow is not None else None,
                next_run,
                schedule_id,
            ),
        )
        db._conn.commit()
    return get_schedule(db, schedule_id)


def delete_schedule(db: OrivellumDB, schedule_id: str) -> bool:
    with db._lock:
        cur = db._conn.execute("DELETE FROM playbook_schedules WHERE id=?", (schedule_id,))
        db._conn.commit()
    return cur.rowcount > 0


def list_schedule_runs(db: OrivellumDB, schedule_id: str, limit: int = 20) -> list[dict]:
    ops = []
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, title, state, error, created_at, started_at, finished_at
               FROM operations WHERE schedule_id=? ORDER BY created_at DESC LIMIT ?""",
            (schedule_id, limit),
        ).fetchall()
    for r in rows:
        duration = None
        if r["started_at"] and r["finished_at"]:
            try:
                duration = (
                    datetime.fromisoformat(r["finished_at"])
                    - datetime.fromisoformat(r["started_at"])
                ).total_seconds()
            except Exception:
                duration = None
        ops.append(
            {
                "id": r["id"],
                "title": r["title"],
                "state": r["state"],
                "error": r["error"],
                "created_at": r["created_at"],
                "started_at": r["started_at"],
                "finished_at": r["finished_at"],
                "duration_seconds": duration,
            }
        )
    return ops


# ── Tick ─────────────────────────────────────────────────────────────────────


def system_busy(db: OrivellumDB) -> str | None:
    """Reason the system is too busy for a scheduled run, or None.

    Heavy = any operation already running, or a live audiobook render.
    """
    with db._lock:
        row = db._conn.execute("SELECT id FROM operations WHERE state='running' LIMIT 1").fetchone()
    if row:
        return "an operation is already running"
    studio = hooks.HOOKS.studio
    try:
        if studio is not None and studio.list_active_work_tts_jobs():
            return "an audiobook render is in progress"
    except Exception:  # busy-probe must never break the tick
        logger.debug("Studio busy probe failed", exc_info=True)
    return None


def _claim_and_alert(db: OrivellumDB, op: dict) -> bool:
    """Alert exactly once for one failed scheduled run.

    The failure_alerted flag AND the review-inbox row (suggestions table) are
    written in a single transaction, so a crash can never claim the alert
    without persisting the inbox item. The browser notification is delivered
    best-effort afterwards — its ring buffer is in-memory anyway.
    """
    from datetime import UTC

    title = f"Automation failed: {op['title']}"
    body = (op.get("error") or "The scheduled run failed.")[:200]
    meta = {"op_id": op["id"], "schedule_id": op["schedule_id"], "work_id": op.get("work_id")}
    with db._lock:
        cur = db._conn.execute(
            "UPDATE operations SET failure_alerted=1 "
            "WHERE id=? AND failure_alerted=0 AND state='failed'",
            (op["id"],),
        )
        claimed = cur.rowcount == 1
        if claimed:
            db._conn.execute(
                """INSERT INTO suggestions (id, work_id, kind, text, meta, created_at, expires_at)
                   VALUES (?,?,?,?,?,?,NULL)""",
                (
                    str(uuid.uuid4()),
                    meta.get("work_id"),
                    "automation_failure",
                    f"{title} — {body}"[:500],
                    json.dumps(meta),
                    datetime.now(UTC).isoformat(),
                ),
            )
        db._conn.commit()
    if not claimed:
        return False
    if hooks.HOOKS.notify is not None:
        try:
            hooks.HOOKS.notify("automation_failed", title, body=body, url="/operations")
        except Exception:
            logger.exception("Automation failure notification failed (non-fatal)")
    return True


def _alert_failed_runs(db: OrivellumDB) -> int:
    """Alert exactly once per failed scheduled run (atomic claim on the flag).

    Cancelled runs are user decisions — no alert.
    """
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, title, error, schedule_id, work_id FROM operations
               WHERE schedule_id IS NOT NULL AND state='failed' AND failure_alerted=0
               LIMIT 20""",
        ).fetchall()
    return sum(1 for r in rows if _claim_and_alert(db, dict(r)))


def tick(db: OrivellumDB, cfg: OrivellumConfig | None, now: datetime | None = None) -> dict:
    """One scheduler pass. Testable in isolation; the daemon just loops this."""
    now = now or _now_local()
    result: dict = {"started": [], "skipped_busy": [], "failed_dispatch": [], "alerted": 0}
    result["alerted"] = _alert_failed_runs(db)

    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM playbook_schedules WHERE enabled=1 AND next_run_at <= ? "
            "ORDER BY next_run_at ASC LIMIT 10",
            (now.isoformat(),),
        ).fetchall()
    due = [_row_to_schedule(r) for r in rows]
    for i, sched in enumerate(due):
        # Recheck busy before EVERY dispatch: the previous iteration's run is
        # already 'running', so at most one heavy automation starts per tick
        # and nothing runs in parallel with a heavy job. Due schedules keep
        # next_run_at untouched — they fire as soon as the system frees up.
        busy = system_busy(db)
        if busy:
            result["skipped_busy"].extend(s["id"] for s in due[i:])
            logger.info("Scheduler: %d due automation(s) waiting — %s", len(due) - i, busy)
            break
        if not _claim_occurrence(db, sched, now):
            continue  # another tick claimed it, or it was disabled/deleted
        _dispatch(db, cfg, sched, now, result)
    return result


def _claim_occurrence(db: OrivellumDB, sched: dict, now: datetime) -> bool:
    """Atomically claim ONE due occurrence of a schedule.

    Fenced on the exact next_run_at we read AND enabled=1, so a racing tick,
    a just-flipped disable toggle, or a delete can never double-fire or
    resurrect a run. next_run_at advances no matter what happens afterwards —
    a broken automation alerts once per occurrence, never spins every tick.
    """
    next_run = compute_next_run(
        sched["cadence"], sched["time_of_day"], sched["day_of_week"], now
    ).isoformat()
    with db._lock:
        cur = db._conn.execute(
            "UPDATE playbook_schedules SET last_run_at=?, next_run_at=? "
            "WHERE id=? AND enabled=1 AND next_run_at=?",
            (now.isoformat(), next_run, sched["id"], sched["next_run_at"]),
        )
        db._conn.commit()
    return cur.rowcount == 1


def _fail_op(db: OrivellumDB, op_id: str, error: str) -> None:
    """CAS a pending op to failed so it lands in run history and alerts."""
    with db._lock:
        db._conn.execute(
            "UPDATE operations SET state='failed', error=?, updated_at=? "
            "WHERE id=? AND state='pending'",
            (error, datetime.now().isoformat(), op_id),
        )
        db._conn.commit()


def _dispatch(
    db: OrivellumDB, cfg: OrivellumConfig | None, sched: dict, now: datetime, result: dict
) -> None:
    """Start one claimed occurrence through the durable runner.

    Every failure path leaves a failed operation row (run history) and alerts
    through the exactly-once claim — nothing silent.
    """
    pb = get_playbook(sched["playbook_id"], db)
    problems = (
        [f"Playbook {sched['playbook_id']!r} no longer exists."]
        if pb is None
        else validate_steps(pb["steps"], get_op_registry())
    )
    params: dict = {}
    if sched["work_id"]:
        params["work_id"] = sched["work_id"]
    op_id = store.create_operation(
        db,
        title=sched["title"] or (pb["title"] if pb else sched["playbook_id"]),
        steps=pb["steps"] if pb and not problems else [],
        work_id=sched["work_id"],
        playbook_id=sched["playbook_id"],
        params=params,
        schedule_id=sched["id"],
    )
    if problems:
        _fail_op(db, op_id, f"Automation can't run: {problems[0][:300]}")
        result["failed_dispatch"].append(sched["id"])
        result["alerted"] += 1 if _claim_and_alert(db, store.get_operation(db, op_id)) else 0
        return

    from orivellum.capabilities.operations.runner import start_operation_run

    if start_operation_run(db, cfg, op_id):
        result["started"].append(op_id)
        logger.info("Scheduler started automation %s → operation %s", sched["id"], op_id)
    else:
        # A rejected dispatch leaves the op pending — mark it failed and
        # alert right away. Nothing silent, nothing waiting a tick.
        _fail_op(db, op_id, "The scheduled run could not start — the server was too busy.")
        result["failed_dispatch"].append(sched["id"])
        result["alerted"] += 1 if _claim_and_alert(db, store.get_operation(db, op_id)) else 0


# ── Daemon ───────────────────────────────────────────────────────────────────


def start_schedule_daemon(db: OrivellumDB, cfg: OrivellumConfig | None) -> threading.Thread:
    """Minute-tick daemon thread (daemon=True, survives crashes, never blocks
    shutdown). Mirrors the nightshift daemon pattern."""

    def _loop() -> None:
        logger.info("Automation scheduler ready (ticks every %ds)", _TICK_SECONDS)
        while True:
            time.sleep(_TICK_SECONDS)
            try:
                tick(db, cfg)
            except Exception:
                logger.error("Scheduler tick crashed", exc_info=True)

    t = threading.Thread(target=_loop, name="orivellum-automations", daemon=True)
    t.start()
    return t
