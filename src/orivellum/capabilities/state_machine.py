"""State-machine engine — M0.2 (Sovereign Platform).

Provides a declarative, server-enforced lifecycle engine that:

1. Refuses undeclared transitions.
2. Refuses transitions when an open BLOCKING finding exists on the object.
3. Records every transition via ``governed_write`` (audit + outbox, one TX).
4. Supplies pre-built machines for the seed enums defined in ``enums.py``.

The client NEVER decides whether a transition is legal.  The server calls
``apply_transition()`` and that function either succeeds atomically or raises
a typed exception.  The caller surfaces the exception as an appropriate HTTP
response (409 for conflicts, 422 for illegal transitions).

Usage::

    from orivellum.capabilities.state_machine import (
        MESSAGE_SM, apply_transition
    )

    apply_transition(
        db, MESSAGE_SM,
        object_id=msg_id,
        object_type="message",
        table="messages",
        state_col="state",
        from_state="queued",
        to_state="running",
        actor="system",
    )
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

from .enums import BookState, JobState, MessageState

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InvalidTransitionError(Exception):
    """Raised when a requested transition is not in the declared graph.

    Attributes:
        from_state: the state the object is currently in.
        to_state:   the state that was requested.
        allowed:    the set of states that ARE reachable from ``from_state``.
    """

    def __init__(self, from_state: str, to_state: str,
                 allowed: frozenset[str]) -> None:
        super().__init__(
            f"Transition {from_state!r} → {to_state!r} is not declared. "
            f"Allowed from {from_state!r}: {sorted(allowed) or '(terminal)'}"
        )
        self.from_state = from_state
        self.to_state = to_state
        self.allowed = allowed


class TransitionConflictError(Exception):
    """Raised when the row is no longer in the claimed ``from_state``.

    The UPDATE is compare-and-set (``WHERE id=? AND state=?``); a zero
    rowcount means another actor transitioned the object concurrently or the
    caller forged/staled its ``from_state``. Surfaces as HTTP 409.
    """

    def __init__(self, object_id: str, from_state: str, to_state: str) -> None:
        super().__init__(
            f"Object {object_id!r} is not in state {from_state!r} anymore — "
            f"transition to {to_state!r} not applied (concurrent update?)"
        )
        self.object_id = object_id
        self.from_state = from_state
        self.to_state = to_state


class BlockedTransitionError(Exception):
    """Raised when open findings block a forward transition.

    Attributes:
        from_state: current state.
        to_state:   requested state.
        blockers:   list of finding dicts that are blocking.
    """

    def __init__(self, from_state: str, to_state: str,
                 blockers: list[dict]) -> None:
        ids = [b["id"][:12] for b in blockers[:3]]
        extra = f" + {len(blockers) - 3} more" if len(blockers) > 3 else ""
        super().__init__(
            f"Transition {from_state!r} → {to_state!r} is blocked by "
            f"{len(blockers)} open finding(s): {ids}{extra}"
        )
        self.from_state = from_state
        self.to_state = to_state
        self.blockers = blockers


# ---------------------------------------------------------------------------
# StateMachine
# ---------------------------------------------------------------------------

class StateMachine:
    """Declarative state machine built from a transition adjacency dict.

    ``transitions`` maps each state to the set of states reachable from it.
    States with no outgoing transitions are terminal (map to empty set or
    are absent from the dict).

    Example::

        sm = StateMachine({
            "queued":    {"running"},
            "running":   {"done", "failed"},
        })
        sm.assert_transition("queued", "running")   # OK
        sm.assert_transition("queued", "done")      # raises InvalidTransitionError
    """

    def __init__(self, transitions: dict[str, set[str]]) -> None:
        # Freeze sets so the graph is immutable after construction.
        self._graph: dict[str, frozenset[str]] = {
            k: frozenset(v) for k, v in transitions.items()
        }

    # ── Inspection ──────────────────────────────────────────────────────────

    def allowed_from(self, state: str) -> frozenset[str]:
        """Return the set of states reachable from *state*."""
        return self._graph.get(state, frozenset())

    def can_transition(self, from_state: str, to_state: str) -> bool:
        """Return True if the transition is declared in the graph."""
        return to_state in self.allowed_from(from_state)

    def assert_transition(self, from_state: str, to_state: str) -> None:
        """Raise :exc:`InvalidTransitionError` if the transition is undeclared."""
        if not self.can_transition(from_state, to_state):
            raise InvalidTransitionError(
                from_state, to_state, self.allowed_from(from_state)
            )


# ---------------------------------------------------------------------------
# Core transition helper
# ---------------------------------------------------------------------------

def apply_transition(
    db: OrivellumDB,
    sm: StateMachine,
    *,
    object_id: str,
    object_type: str,
    table: str,
    state_col: str,
    from_state: str,
    to_state: str,
    actor: str = "system",
    detail: str | None = None,
    check_blockers: bool = True,
) -> None:
    """Validate and atomically apply a state-machine transition.

    Steps (all must succeed or nothing is committed):
    1. Assert the transition is declared in *sm*.
    2. If *check_blockers* is True, query open findings with severity
       ``high`` or ``critical`` on *object_id*.  Raise
       :exc:`BlockedTransitionError` if any exist.
    3. Execute the UPDATE and write audit + outbox via ``governed_write``.

    Args:
        db:            OrivellumDB instance.
        sm:            StateMachine that governs *object_type*.
        object_id:     Primary key of the object being transitioned.
        object_type:   Human-readable type label (e.g. ``"message"``).
        table:         SQLite table name (e.g. ``"messages"``).
        state_col:     Column name that holds the state (e.g. ``"state"``).
        from_state:    Current state (used for logging and validation).
        to_state:      Desired new state.
        actor:         Who is requesting the transition.
        detail:        Optional free-text reason logged in the audit row.
        check_blockers: When False, skip the findings check (use only for
                        backward / return transitions that are always allowed).

    Raises:
        InvalidTransitionError: if the transition is not in *sm*.
        BlockedTransitionError: if open high/critical findings exist and
                                *check_blockers* is True.
        TransitionConflictError: if the row is not in *from_state* at write
                                 time (compare-and-set failed) — the caller's
                                 snapshot was stale or forged.
    """
    # 1 — Structural validation (pure, no DB access)
    sm.assert_transition(from_state, to_state)

    # 2 — Blocker check
    if check_blockers:
        blockers = db.list_findings(
            object_id=object_id,
            state="open",
            min_severity=("high", "critical"),
        )
        if blockers:
            raise BlockedTransitionError(from_state, to_state, blockers)

    # 3 — Atomic domain write + audit + outbox. Compare-and-set: the UPDATE
    # only applies while the row is still in the *claimed* from_state, so a
    # forged/stale from_state (or two concurrent callers racing from the same
    # snapshot) cannot authorize a different graph edge (FA-04).
    with db.governed_write(
        operation=f"{object_type}.transition",
        event_type=f"{object_type}.transition",
        object_id=object_id,
        object_type=object_type,
        payload={
            "from_state": from_state,
            "to_state": to_state,
            "table": table,
            "state_col": state_col,
        },
        actor=actor,
        detail=detail or f"{from_state}→{to_state}",
    ):
        cur = db._conn.execute(
            f"UPDATE {table} SET {state_col}=? WHERE id=? AND {state_col}=?",  # noqa: S608
            (to_state, object_id, from_state),
        )
        if cur.rowcount == 0:
            # Raising inside governed_write rolls the audit/outbox rows back
            # together with the (empty) domain write.
            raise TransitionConflictError(object_id, from_state, to_state)


# ---------------------------------------------------------------------------
# Pre-built state machines for seed enums
# ---------------------------------------------------------------------------

MESSAGE_SM: StateMachine = StateMachine({
    # queued → failed: pre-stream disconnect before any token arrives
    MessageState.queued:    {MessageState.running, MessageState.failed},
    MessageState.running:   {MessageState.streaming, MessageState.failed},
    MessageState.streaming: {MessageState.done, MessageState.failed},
    # done and failed are terminal
})
"""State machine for assistant chat messages."""

JOB_SM: StateMachine = StateMachine({
    JobState.queued:  {JobState.running, JobState.cancelled},
    JobState.running: {JobState.done, JobState.failed, JobState.cancelled},
    # done / failed / cancelled are terminal
})
"""State machine for background jobs."""

# BookState: sequential forward, single-step; backward returns allowed without
# the blocker check (return transitions are flagged via check_blockers=False).
_BOOK_ORDER = list(BookState)
BOOK_SM: StateMachine = StateMachine({
    state: {_BOOK_ORDER[i + 1]}
    for i, state in enumerate(_BOOK_ORDER[:-1])
})
"""State machine for book/chapter production (B0 → B17).

Forward: exactly one step at a time.
Backward: use apply_transition(..., check_blockers=False) to return scope.
"""

# Human-readable labels for each B-stage (matches web book-tab.tsx)
BOOK_STAGE_LABELS: dict[str, str] = {
    "B0":  "Intake",
    "B1":  "Outline",
    "B2":  "Research",
    "B3":  "Architecture",
    "B4":  "Chapter Extraction",
    "B5":  "Chapter Drafting",
    "B6":  "Continuity Review",
    "B7":  "Fact Check",
    "B8":  "Style Pass",
    "B9":  "Editorial Review",
    "B10": "Beta Read",
    "B11": "Revision",
    "B12": "Final Polish",
    "B13": "Proof",
    "B14": "Layout",
    "B15": "Index & TOC",
    "B16": "Quality Gate",
    "B17": "Published",
}
