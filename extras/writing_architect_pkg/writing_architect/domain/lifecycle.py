"""
Lifecycle engine (spec 3.2)
===========================

Enforces the governed B0..B13 progression and the spec's hard rule:

    "A downstream pass cannot compensate for an upstream failure ... Any
     blocking upstream defect returns the affected scope to the proper
     lifecycle state."

Rules implemented:
  * Forward moves advance exactly one state (no skipping gates).
  * A move into a state is refused if that state's *entry gate* is not
    satisfied (delegated to policy.gate_for).
  * Backward moves (returns) are always allowed but must carry a reason and
    are recorded — this is how a discovered upstream blocker sends scope back.
  * Every transition writes a lifecycle_transition row AND an audit entry with
    an authorized actor (spec 11.3).
"""
from __future__ import annotations

from . import db, policy

ORDER = [s[0] for s in db.LIFECYCLE]           # ["B0", ..., "B13"]
INDEX = {code: i for i, code in enumerate(ORDER)}


class LifecycleError(Exception):
    pass


def current_state(conn, book_id: str) -> str:
    row = conn.execute(
        "SELECT state FROM book_project WHERE id = ?", (book_id,)
    ).fetchone()
    if not row:
        raise LifecycleError(f"unknown book {book_id}")
    return row["state"]


def can_advance(conn, book_id: str) -> tuple[bool, str, str | None]:
    """Return (ok, message, next_state)."""
    cur = current_state(conn, book_id)
    i = INDEX[cur]
    if i >= len(ORDER) - 1:
        return False, f"{cur} is terminal (RELEASED)", None
    nxt = ORDER[i + 1]
    ok, msg = policy.check_entry_gate(conn, book_id, nxt)
    return ok, msg, nxt


def advance(conn, book_id: str, actor: str, reason: str = "") -> str:
    ok, msg, nxt = can_advance(conn, book_id)
    if not ok:
        raise LifecycleError(f"gate blocks advance: {msg}")
    _transition(conn, book_id, actor, nxt, reason or "advance")
    return nxt


def return_to(conn, book_id: str, target_state: str, actor: str, reason: str) -> str:
    """Send scope back to an earlier state because of an upstream blocker."""
    cur = current_state(conn, book_id)
    if target_state not in INDEX:
        raise LifecycleError(f"unknown state {target_state}")
    if INDEX[target_state] >= INDEX[cur]:
        raise LifecycleError(
            f"return_to must go backward; {target_state} is not upstream of {cur}"
        )
    if not reason:
        raise LifecycleError("a return requires a reason")
    _transition(conn, book_id, actor, target_state, f"RETURN: {reason}")
    return target_state


def _transition(conn, book_id: str, actor: str, to_state: str, reason: str) -> None:
    if not actor:
        raise LifecycleError("a transition requires an authorized actor (spec 11.3)")
    frm = current_state(conn, book_id)
    conn.execute(
        "INSERT INTO lifecycle_transition(id,book_id,from_state,to_state,actor,"
        "reason,ts_utc) VALUES (?,?,?,?,?,?,?)",
        (db.new_id("txn_"), book_id, frm, to_state, actor, reason, db.now_utc()),
    )
    conn.execute(
        "UPDATE book_project SET state = ?, updated_utc = ? WHERE id = ?",
        (to_state, db.now_utc(), book_id),
    )
    conn.commit()
    db.audit(conn, actor=actor, action="LIFECYCLE_TRANSITION",
             object_type="book_project", object_id=book_id,
             detail={"from": frm, "to": to_state, "reason": reason})
