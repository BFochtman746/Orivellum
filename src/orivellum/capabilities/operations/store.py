"""SQLite persistence for operations — CAS-style state transitions.

Every transition is a conditional UPDATE (``WHERE state IN (...)``) so two
racing callers can never both claim the same operation, and a restart can
never resurrect a cancelled run. Follows the raw-SQL precedent set by the
one-shot actions framework (avoids growing db.py).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

# Operation states
OP_ACTIVE = ("pending", "running", "paused")
OP_TERMINAL = ("done", "failed", "cancelled")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


# ── Create ─────────────────────────────────────────────────────────────────────


def create_operation(
    db: OrivellumDB,
    title: str,
    steps: list[dict],
    work_id: str | None = None,
    playbook_id: str | None = None,
    params: dict | None = None,
    schedule_id: str | None = None,
) -> str:
    """Insert an operation (state=pending) plus its step rows; return op id.

    Each step dict: {action_id, label, params?}. ``schedule_id`` links a run
    started by an automation back to its schedule for run history/alerting.
    """
    op_id = _uid()
    now = _now()
    with db._lock:
        db._conn.execute(
            """INSERT INTO operations
               (id, title, playbook_id, work_id, params, state, schedule_id,
                created_at, updated_at)
               VALUES (?,?,?,?,?,'pending',?,?,?)""",
            (
                op_id,
                title,
                playbook_id,
                work_id,
                json.dumps(params or {}),
                schedule_id,
                now,
                now,
            ),
        )
        for i, s in enumerate(steps):
            db._conn.execute(
                """INSERT INTO operation_steps
                   (id, operation_id, step_index, action_id, label, params, state)
                   VALUES (?,?,?,?,?,?,'pending')""",
                (
                    _uid(),
                    op_id,
                    i,
                    s["action_id"],
                    s.get("label") or s["action_id"],
                    json.dumps(s.get("params") or {}),
                ),
            )
        db._conn.commit()
    return op_id


# ── Read ───────────────────────────────────────────────────────────────────────


def get_operation(db: OrivellumDB, op_id: str) -> dict | None:
    with db._lock:
        row = db._conn.execute("SELECT * FROM operations WHERE id=?", (op_id,)).fetchone()
    return dict(row) if row else None


def get_operation_state(db: OrivellumDB, op_id: str) -> str | None:
    with db._lock:
        row = db._conn.execute("SELECT state FROM operations WHERE id=?", (op_id,)).fetchone()
    return row["state"] if row else None


def get_operation_claim(db: OrivellumDB, op_id: str) -> tuple[str | None, str | None]:
    """Return (state, run_token) in one read — for stale-runner checks."""
    with db._lock:
        row = db._conn.execute(
            "SELECT state, run_token FROM operations WHERE id=?", (op_id,)
        ).fetchone()
    return (row["state"], row["run_token"]) if row else (None, None)


def list_operations(db: OrivellumDB, limit: int = 30, work_id: str | None = None) -> list[dict]:
    with db._lock:
        if work_id:
            rows = db._conn.execute(
                "SELECT * FROM operations WHERE work_id=? ORDER BY created_at DESC LIMIT ?",
                (work_id, limit),
            ).fetchall()
        else:
            rows = db._conn.execute(
                "SELECT * FROM operations ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def list_steps(db: OrivellumDB, op_id: str) -> list[dict]:
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM operation_steps WHERE operation_id=? ORDER BY step_index",
            (op_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Operation transitions (all CAS) ────────────────────────────────────────────


def claim_operation(db: OrivellumDB, op_id: str) -> str | None:
    """pending/paused/failed → running; returns a fresh run token iff won.

    The whole read → (reset failed steps) → CAS happens under db._lock, so two
    racing resumes cannot interleave: exactly one caller gets a token, and the
    token fences every later transition against stale runners.
    """
    token = _uid()
    with db._lock:
        row = db._conn.execute("SELECT state FROM operations WHERE id=?", (op_id,)).fetchone()
        if not row or row["state"] not in ("pending", "paused", "failed"):
            return None
        # Reset non-done steps so the new claim can redo them. This also covers
        # resuming BEFORE a paused worker reached its checkpoint: its step is
        # still 'running', and once the token rotates that worker can neither
        # revert nor finish it — without this reset the run would strand.
        db._conn.execute(
            """UPDATE operation_steps
               SET state='pending', error=NULL, started_at=NULL, finished_at=NULL
               WHERE operation_id=? AND state IN ('failed','cancelled','running')""",
            (op_id,),
        )
        cur = db._conn.execute(
            """UPDATE operations
               SET state='running', run_token=?, error=NULL,
                   started_at=COALESCE(started_at, ?), updated_at=?
               WHERE id=? AND state=?""",
            (token, _now(), _now(), op_id, row["state"]),
        )
        db._conn.commit()
    return token if cur.rowcount == 1 else None


def release_claim(db: OrivellumDB, op_id: str, run_token: str, error: str | None = None) -> None:
    """running → pending (used when the executor rejects the job).

    Fenced on the caller's own token: a slow-failing submit must never
    release a NEWER claim obtained by a pause/resume in the meantime.
    """
    with db._lock:
        db._conn.execute(
            "UPDATE operations SET state='pending', error=?, updated_at=? "
            "WHERE id=? AND state='running' AND run_token=?",
            (error, _now(), op_id, run_token),
        )
        db._conn.commit()


def request_pause(db: OrivellumDB, op_id: str) -> bool:
    """pending/running → paused. The runner honours it at the next checkpoint."""
    with db._lock:
        cur = db._conn.execute(
            "UPDATE operations SET state='paused', updated_at=? "
            "WHERE id=? AND state IN ('pending','running')",
            (_now(), op_id),
        )
        db._conn.commit()
    return cur.rowcount == 1


def request_cancel(db: OrivellumDB, op_id: str) -> bool:
    """Any active state → cancelled."""
    now = _now()
    with db._lock:
        cur = db._conn.execute(
            "UPDATE operations SET state='cancelled', finished_at=?, updated_at=? "
            "WHERE id=? AND state IN ('pending','running','paused')",
            (now, now, op_id),
        )
        if cur.rowcount == 1:
            db._conn.execute(
                "UPDATE operation_steps SET state='cancelled' "
                "WHERE operation_id=? AND state IN ('pending','running')",
                (op_id,),
            )
        db._conn.commit()
    return cur.rowcount == 1


def mark_operation_done(db: OrivellumDB, op_id: str, run_token: str) -> bool:
    now = _now()
    with db._lock:
        cur = db._conn.execute(
            "UPDATE operations SET state='done', finished_at=?, updated_at=? "
            "WHERE id=? AND state='running' AND run_token=?",
            (now, now, op_id, run_token),
        )
        db._conn.commit()
    return cur.rowcount == 1


def mark_operation_failed(db: OrivellumDB, op_id: str, error: str, run_token: str) -> bool:
    now = _now()
    with db._lock:
        cur = db._conn.execute(
            "UPDATE operations SET state='failed', error=?, finished_at=?, updated_at=? "
            "WHERE id=? AND state='running' AND run_token=?",
            (error[:500], now, now, op_id, run_token),
        )
        db._conn.commit()
    return cur.rowcount == 1


# ── Step transitions (all fenced by the claiming run's token) ─────────────────

# A stale runner — one that lost its claim to a newer resume — must never
# mutate step state. Forward transitions additionally require the operation
# to still be 'running': a pause that lands between the runner's boundary
# check and the transition must win (the pause endpoint already reported
# 'paused' to the user). Interrupted work is redone from scratch on resume,
# so refusing the late transition is always safe.
_ACTIVE_FENCE = (
    "EXISTS (SELECT 1 FROM operations o "
    "WHERE o.id = operation_steps.operation_id AND o.run_token = ? AND o.state = 'running')"
)
# revert (running step → pending) must still work when the op just became
# paused — that IS the normal interrupted path — so it fences on token only.
_TOKEN_FENCE = (
    "EXISTS (SELECT 1 FROM operations o "
    "WHERE o.id = operation_steps.operation_id AND o.run_token = ?)"
)


def mark_step_running(db: OrivellumDB, step_id: str, run_token: str) -> bool:
    with db._lock:
        cur = db._conn.execute(
            "UPDATE operation_steps SET state='running', started_at=?, error=NULL "
            f"WHERE id=? AND state='pending' AND {_ACTIVE_FENCE}",
            (_now(), step_id, run_token),
        )
        db._conn.commit()
    return cur.rowcount == 1


def mark_step_done(db: OrivellumDB, step_id: str, result: dict | None, run_token: str) -> bool:
    with db._lock:
        cur = db._conn.execute(
            "UPDATE operation_steps SET state='done', result=?, finished_at=? "
            f"WHERE id=? AND state='running' AND {_ACTIVE_FENCE}",
            (json.dumps(result or {}, default=str)[:20000], _now(), step_id, run_token),
        )
        db._conn.commit()
    return cur.rowcount == 1


def mark_step_failed(db: OrivellumDB, step_id: str, error: str, run_token: str) -> bool:
    with db._lock:
        cur = db._conn.execute(
            "UPDATE operation_steps SET state='failed', error=?, finished_at=? "
            f"WHERE id=? AND state='running' AND {_ACTIVE_FENCE}",
            (error[:500], _now(), step_id, run_token),
        )
        db._conn.commit()
    return cur.rowcount == 1


def revert_step(db: OrivellumDB, step_id: str, run_token: str) -> None:
    """running → pending (interrupted by pause/restart; resume redoes it)."""
    with db._lock:
        db._conn.execute(
            "UPDATE operation_steps SET state='pending', started_at=NULL "
            f"WHERE id=? AND state='running' AND {_TOKEN_FENCE}",
            (step_id, run_token),
        )
        db._conn.commit()


# ── Startup reconciliation ─────────────────────────────────────────────────────


def reconcile_interrupted_operations(db: OrivellumDB) -> int:
    """Flip operations orphaned by a restart from running → paused.

    Their in-flight steps go back to pending so Resume redoes exactly the
    interrupted step and skips everything already done.
    """
    now = _now()
    with db._lock:
        db._conn.execute(
            """UPDATE operation_steps SET state='pending', started_at=NULL
               WHERE state='running' AND operation_id IN
                     (SELECT id FROM operations WHERE state='running')""",
        )
        cur = db._conn.execute(
            "UPDATE operations SET state='paused', "
            "error='Interrupted by a restart — press Resume to continue.', updated_at=? "
            "WHERE state='running'",
            (now,),
        )
        db._conn.commit()
    return cur.rowcount
