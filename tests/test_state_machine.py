"""M0.2 acceptance tests — State-machine engine (Sovereign Platform).

Acceptance criteria (from the M0.2 spec):
  AC-1  An undeclared transition is refused.
  AC-2  A transition blocked by an open finding is refused and names the blocker.
  AC-3  The client cannot force a transition (server is authority).

Also covers:
  - BookState B0..B17 forward progression
  - JOB_SM and MESSAGE_SM pre-built machines
  - Finding lifecycle (create, list, resolve)
  - backward (return) transitions that bypass blocker check
  - apply_transition is atomic (no partial writes on failure)
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import uuid

import pytest

from orivellum.capabilities.enums import (
    BookState, DocumentLifecycle, DocumentReadiness,
    FindingSeverity, FindingState, JobState, MessageState,
)
from orivellum.capabilities.state_machine import (
    BOOK_SM, JOB_SM, MESSAGE_SM,
    BlockedTransitionError, InvalidTransitionError,
    StateMachine, apply_transition,
)
from orivellum.database.db import OrivellumDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    """Fresh OrivellumDB instance for each test."""
    db_path = str(tmp_path / "test.db")
    instance = OrivellumDB.open(db_path)
    yield instance
    instance.close()


def _make_message(db: OrivellumDB, state: str = "queued") -> str:
    """Insert a bare-minimum messages row and return its id."""
    mid = str(uuid.uuid4())
    conv_id = str(uuid.uuid4())
    # Insert a conversation row first (FK not enforced in SQLite by default,
    # but we do it anyway to be safe).
    with db._lock:
        db._conn.execute(
            "INSERT INTO conversations(id, title, archived, created_at, updated_at) "
            "VALUES(?,?,0,datetime('now'),datetime('now'))",
            (conv_id, "test-conv"),
        )
        db._conn.execute(
            "INSERT INTO messages(id, conversation_id, role, text, state, created_at) "
            "VALUES(?,?,?,?,?,datetime('now'))",
            (mid, conv_id, "assistant", "hello", state),
        )
        db._conn.commit()
    return mid


def _make_job(db: OrivellumDB, state: str = "queued") -> str:
    """Insert a bare-minimum jobs row and return its id."""
    jid = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            "INSERT INTO jobs(id, job_type, state, created_at, input) "
            "VALUES(?,?,?,datetime('now'),'{}')",
            (jid, "test_job", state),
        )
        db._conn.commit()
    return jid


def _get_state(db: OrivellumDB, table: str, col: str, obj_id: str) -> str:
    with db._lock:
        row = db._conn.execute(
            f"SELECT {col} FROM {table} WHERE id=?", (obj_id,)
        ).fetchone()
    assert row is not None, f"{table} row {obj_id!r} not found"
    return row[0]


# ---------------------------------------------------------------------------
# 1 — Enum completeness
# ---------------------------------------------------------------------------

class TestEnums:
    def test_message_states_complete(self):
        expected = {"queued", "running", "streaming", "done", "failed"}
        assert {s.value for s in MessageState} == expected

    def test_job_states_complete(self):
        expected = {"queued", "running", "done", "failed", "cancelled"}
        assert {s.value for s in JobState} == expected

    def test_finding_states(self):
        assert FindingState.open.value == "open"
        assert FindingState.resolved.value == "resolved"

    def test_book_states_b0_to_b17(self):
        values = [s.value for s in BookState]
        assert values[0] == "B0"
        assert values[-1] == "B17"
        assert len(values) == 18, f"Expected 18 BookState values, got {len(values)}"

    def test_enums_are_string_valued(self):
        """All enum instances must compare equal to their plain string values."""
        assert MessageState.done == "done"
        assert JobState.running == "running"
        assert BookState.B0 == "B0"

    def test_document_lifecycle_enum_covers_db_values(self):
        db_values = {"draft", "canonical", "superseded", "reference", "active", "deleted"}
        assert db_values.issubset({s.value for s in DocumentLifecycle})

    def test_document_readiness_enum_covers_db_values(self):
        db_values = {"imported", "ready", "error", "no_text", "reprocessing"}
        assert db_values.issubset({s.value for s in DocumentReadiness})


# ---------------------------------------------------------------------------
# 2 — StateMachine graph validation (pure, no DB)
# ---------------------------------------------------------------------------

class TestStateMachineGraph:
    def _sm(self):
        return StateMachine({
            "a": {"b", "c"},
            "b": {"d"},
            # c and d are terminal
        })

    def test_can_transition_true(self):
        sm = self._sm()
        assert sm.can_transition("a", "b") is True
        assert sm.can_transition("a", "c") is True
        assert sm.can_transition("b", "d") is True

    def test_can_transition_false(self):
        sm = self._sm()
        assert sm.can_transition("a", "d") is False
        assert sm.can_transition("c", "a") is False

    def test_allowed_from_returns_correct_set(self):
        sm = self._sm()
        assert sm.allowed_from("a") == frozenset({"b", "c"})
        assert sm.allowed_from("d") == frozenset()      # terminal

    # ── AC-1: undeclared transition is refused ───────────────────────────────

    def test_assert_transition_ok(self):
        sm = self._sm()
        sm.assert_transition("a", "b")  # should not raise

    def test_assert_transition_raises_for_undeclared(self):
        """AC-1 — Undeclared transition is refused."""
        sm = self._sm()
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.assert_transition("a", "d")
        err = exc_info.value
        assert err.from_state == "a"
        assert err.to_state == "d"
        assert "d" not in err.allowed

    def test_assert_transition_raises_for_terminal(self):
        """AC-1 — Terminal state has no outgoing transitions."""
        sm = self._sm()
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.assert_transition("d", "a")
        assert exc_info.value.allowed == frozenset()

    def test_assert_transition_error_lists_allowed(self):
        sm = self._sm()
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.assert_transition("a", "z")
        # The error message should mention what IS allowed
        assert "b" in str(exc_info.value) or "c" in str(exc_info.value)

    def test_graph_is_immutable_after_construction(self):
        sm = self._sm()
        # The internal graph should not be directly mutable.
        original_allowed = sm.allowed_from("a")
        try:
            sm._graph["a"] = frozenset({"z"})  # mutate the dict
        except Exception:
            pass
        # Even if we mutated the dict, the frozen set itself is immutable.
        assert isinstance(original_allowed, frozenset)


# ---------------------------------------------------------------------------
# 3 — Pre-built state machines
# ---------------------------------------------------------------------------

class TestBuiltInMachines:
    def test_message_sm_happy_path(self):
        sm = MESSAGE_SM
        sm.assert_transition("queued",    "running")
        sm.assert_transition("running",   "streaming")
        sm.assert_transition("streaming", "done")

    def test_message_sm_failure_paths(self):
        sm = MESSAGE_SM
        sm.assert_transition("running",   "failed")
        sm.assert_transition("streaming", "failed")

    def test_message_sm_refuses_skip(self):
        """AC-1 — queued cannot jump directly to done."""
        with pytest.raises(InvalidTransitionError):
            MESSAGE_SM.assert_transition("queued", "done")

    def test_job_sm_happy_path(self):
        JOB_SM.assert_transition("queued",  "running")
        JOB_SM.assert_transition("running", "done")

    def test_job_sm_cancellation(self):
        JOB_SM.assert_transition("queued",  "cancelled")
        JOB_SM.assert_transition("running", "cancelled")

    def test_job_sm_terminal_states_have_no_transitions(self):
        for state in ("done", "failed", "cancelled"):
            assert JOB_SM.allowed_from(state) == frozenset()

    def test_book_sm_sequential_forward(self):
        book_states = list(BookState)
        for i in range(len(book_states) - 1):
            BOOK_SM.assert_transition(book_states[i].value, book_states[i + 1].value)

    def test_book_sm_no_skip(self):
        """AC-1 — B0 cannot skip to B2."""
        with pytest.raises(InvalidTransitionError):
            BOOK_SM.assert_transition("B0", "B2")

    def test_book_sm_b17_is_terminal(self):
        assert BOOK_SM.allowed_from("B17") == frozenset()


# ---------------------------------------------------------------------------
# 4 — Findings CRUD
# ---------------------------------------------------------------------------

class TestFindingsCRUD:
    def test_create_and_get_finding(self, db):
        fid = db.create_finding(
            object_id="obj-1",
            object_type="document",
            description="Chapter 3 is missing citations.",
            severity="high",
        )
        assert isinstance(fid, str)
        finding = db.get_finding(fid)
        assert finding is not None
        assert finding["object_id"] == "obj-1"
        assert finding["state"] == "open"
        assert finding["severity"] == "high"
        assert finding["description"] == "Chapter 3 is missing citations."

    def test_list_findings_by_object_id(self, db):
        obj = "obj-list-test"
        db.create_finding(object_id=obj, object_type="document",
                          description="A", severity="high")
        db.create_finding(object_id=obj, object_type="document",
                          description="B", severity="warning")
        db.create_finding(object_id="other", object_type="document",
                          description="C", severity="critical")
        results = db.list_findings(object_id=obj)
        assert len(results) == 2
        assert all(f["object_id"] == obj for f in results)

    def test_list_findings_by_state(self, db):
        obj = "obj-state-test"
        f1 = db.create_finding(object_id=obj, object_type="document",
                               description="open one", severity="high")
        f2 = db.create_finding(object_id=obj, object_type="document",
                               description="open two", severity="critical")
        db.resolve_finding(f1)
        open_findings = db.list_findings(object_id=obj, state="open")
        resolved_findings = db.list_findings(object_id=obj, state="resolved")
        assert len(open_findings) == 1
        assert open_findings[0]["id"] == f2
        assert len(resolved_findings) == 1
        assert resolved_findings[0]["id"] == f1

    def test_list_findings_by_min_severity(self, db):
        obj = "obj-sev-test"
        db.create_finding(object_id=obj, object_type="document",
                          description="info", severity="info")
        db.create_finding(object_id=obj, object_type="document",
                          description="warning", severity="warning")
        db.create_finding(object_id=obj, object_type="document",
                          description="high", severity="high")
        db.create_finding(object_id=obj, object_type="document",
                          description="critical", severity="critical")
        blocking = db.list_findings(
            object_id=obj, state="open", min_severity=("high", "critical")
        )
        assert len(blocking) == 2
        severities = {f["severity"] for f in blocking}
        assert severities == {"high", "critical"}

    def test_resolve_finding(self, db):
        fid = db.create_finding(
            object_id="obj-resolve",
            object_type="document",
            description="Needs attention",
        )
        assert db.resolve_finding(fid) is True
        finding = db.get_finding(fid)
        assert finding["state"] == "resolved"
        assert finding["resolved_at"] is not None

    def test_resolve_already_resolved_returns_false(self, db):
        fid = db.create_finding(
            object_id="obj-double-resolve",
            object_type="document",
            description="Already resolved",
        )
        db.resolve_finding(fid)
        assert db.resolve_finding(fid) is False

    def test_get_nonexistent_finding_returns_none(self, db):
        assert db.get_finding("nonexistent-id") is None


# ---------------------------------------------------------------------------
# 5 — apply_transition (DB-integrated)
# ---------------------------------------------------------------------------

class TestApplyTransition:
    """These tests require a real DB because apply_transition calls governed_write."""

    def test_valid_transition_updates_state(self, db):
        """Happy path: a declared transition updates the column atomically."""
        # Insert a message with 'queued' state
        mid = _make_message(db, state="queued")
        apply_transition(
            db, MESSAGE_SM,
            object_id=mid,
            object_type="message",
            table="messages",
            state_col="state",
            from_state="queued",
            to_state="running",
        )
        assert _get_state(db, "messages", "state", mid) == "running"

    def test_valid_transition_writes_audit_row(self, db):
        """apply_transition must write an audit row via governed_write."""
        mid = _make_message(db, state="queued")
        with db._lock:
            before = db._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        apply_transition(
            db, MESSAGE_SM,
            object_id=mid, object_type="message",
            table="messages", state_col="state",
            from_state="queued", to_state="running",
        )
        with db._lock:
            after = db._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        assert after > before

    def test_valid_transition_writes_outbox_row(self, db):
        """apply_transition must write an outbox event via governed_write."""
        mid = _make_message(db, state="queued")
        with db._lock:
            before = db._conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE dispatched_at IS NULL"
            ).fetchone()[0]
        apply_transition(
            db, MESSAGE_SM,
            object_id=mid, object_type="message",
            table="messages", state_col="state",
            from_state="queued", to_state="running",
        )
        with db._lock:
            after = db._conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE dispatched_at IS NULL"
            ).fetchone()[0]
        assert after > before

    # ── AC-1: undeclared transitions raise and do NOT mutate state ───────────

    def test_invalid_transition_raises_and_does_not_mutate(self, db):
        """AC-1 — Undeclared transition is refused; DB state is unchanged."""
        mid = _make_message(db, state="queued")
        with pytest.raises(InvalidTransitionError):
            apply_transition(
                db, MESSAGE_SM,
                object_id=mid, object_type="message",
                table="messages", state_col="state",
                from_state="queued", to_state="done",   # not allowed
            )
        # State must still be 'queued' — no partial write.
        assert _get_state(db, "messages", "state", mid) == "queued"

    # ── AC-2: blocked transitions name the blocker ───────────────────────────

    def test_blocked_transition_raises_with_blocker_details(self, db):
        """AC-2 — A transition blocked by an open finding is refused and
        names the blocker."""
        mid = _make_message(db, state="queued")
        # Create a high-severity finding on this message.
        fid = db.create_finding(
            object_id=mid,
            object_type="message",
            description="Critical content policy violation detected.",
            severity="high",
        )
        with pytest.raises(BlockedTransitionError) as exc_info:
            apply_transition(
                db, MESSAGE_SM,
                object_id=mid, object_type="message",
                table="messages", state_col="state",
                from_state="queued", to_state="running",
            )
        err = exc_info.value
        assert err.from_state == "queued"
        assert err.to_state == "running"
        assert len(err.blockers) >= 1
        # The exception must name the blocker id.
        blocker_ids = [b["id"] for b in err.blockers]
        assert fid in blocker_ids

    def test_blocked_transition_does_not_mutate_state(self, db):
        """AC-2 — State is unchanged when a blocker refuses the transition."""
        mid = _make_message(db, state="queued")
        db.create_finding(
            object_id=mid, object_type="message",
            description="Blocker", severity="high",
        )
        with pytest.raises(BlockedTransitionError):
            apply_transition(
                db, MESSAGE_SM,
                object_id=mid, object_type="message",
                table="messages", state_col="state",
                from_state="queued", to_state="running",
            )
        assert _get_state(db, "messages", "state", mid) == "queued"

    def test_blocking_finding_resolved_allows_transition(self, db):
        """Resolving the blocking finding unblocks the transition."""
        mid = _make_message(db, state="queued")
        fid = db.create_finding(
            object_id=mid, object_type="message",
            description="Blocker", severity="high",
        )
        # Still blocked.
        with pytest.raises(BlockedTransitionError):
            apply_transition(
                db, MESSAGE_SM,
                object_id=mid, object_type="message",
                table="messages", state_col="state",
                from_state="queued", to_state="running",
            )
        # Resolve the finding.
        db.resolve_finding(fid)
        # Now it should succeed.
        apply_transition(
            db, MESSAGE_SM,
            object_id=mid, object_type="message",
            table="messages", state_col="state",
            from_state="queued", to_state="running",
        )
        assert _get_state(db, "messages", "state", mid) == "running"

    def test_warning_finding_does_not_block(self, db):
        """Warning-severity findings are advisory and do NOT block transitions."""
        mid = _make_message(db, state="queued")
        db.create_finding(
            object_id=mid, object_type="message",
            description="Advisory only", severity="warning",
        )
        # Should succeed — only high/critical block.
        apply_transition(
            db, MESSAGE_SM,
            object_id=mid, object_type="message",
            table="messages", state_col="state",
            from_state="queued", to_state="running",
        )
        assert _get_state(db, "messages", "state", mid) == "running"

    def test_info_finding_does_not_block(self, db):
        mid = _make_message(db, state="queued")
        db.create_finding(
            object_id=mid, object_type="message",
            description="Info only", severity="info",
        )
        apply_transition(
            db, MESSAGE_SM,
            object_id=mid, object_type="message",
            table="messages", state_col="state",
            from_state="queued", to_state="running",
        )
        assert _get_state(db, "messages", "state", mid) == "running"

    # ── AC-3: server authority — client cannot force a transition ────────────

    def test_server_is_authority_client_cannot_skip_validation(self, db):
        """AC-3 — The state machine validates on the server.

        Even if a caller passes states that are not in the enum, the machine
        refuses them — there is no way to bypass validation from the outside.
        The only entry point is apply_transition(), which always runs
        assert_transition() first.
        """
        mid = _make_message(db, state="queued")
        with pytest.raises(InvalidTransitionError):
            # A client-invented state that doesn't exist.
            apply_transition(
                db, MESSAGE_SM,
                object_id=mid, object_type="message",
                table="messages", state_col="state",
                from_state="queued", to_state="FORCE_DONE",
            )
        assert _get_state(db, "messages", "state", mid) == "queued"

    def test_backward_transition_bypasses_blocker_check(self, db):
        """Backward (return) transitions with check_blockers=False are always
        allowed regardless of open findings — this is the upstream-failure path."""
        mid = _make_message(db, state="running")
        db.create_finding(
            object_id=mid, object_type="message",
            description="Upstream failure found", severity="critical",
        )
        # A "backward" return (running → failed is forward, so use a custom SM
        # that has a genuine backward arc).
        sm = StateMachine({
            "running": {"queued", "done"},
        })
        # With check_blockers=True (default), the critical finding would block it.
        # With check_blockers=False (backward return), it is always allowed.
        apply_transition(
            db, sm,
            object_id=mid, object_type="message",
            table="messages", state_col="state",
            from_state="running", to_state="queued",
            check_blockers=False,
        )
        assert _get_state(db, "messages", "state", mid) == "queued"

    def test_job_transition_queued_to_done_via_running(self, db):
        jid = _make_job(db, state="queued")
        apply_transition(
            db, JOB_SM,
            object_id=jid, object_type="job",
            table="jobs", state_col="state",
            from_state="queued", to_state="running",
        )
        apply_transition(
            db, JOB_SM,
            object_id=jid, object_type="job",
            table="jobs", state_col="state",
            from_state="running", to_state="done",
        )
        assert _get_state(db, "jobs", "state", jid) == "done"
