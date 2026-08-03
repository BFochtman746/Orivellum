"""M0.1 acceptance tests — Sovereign Platform governed data foundation.

Covers three acceptance criteria verbatim from MILESTONES.md:
  1. A stale update (wrong version) is rejected with VERSION_CONFLICT.
  2. Every write produces exactly one audit row and one outbox row, atomically.
  3. Editing any audit row breaks the chain and is detected by verify().

Additional tests exercise the governed_write context manager, outbox helpers,
and verify_audit_chain() on multi-entry chains.
"""
from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path

import pytest

from orivellum.database.db import OrivellumDB, VersionConflictError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> OrivellumDB:
    """Return an in-memory OrivellumDB with all migrations applied."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    db = OrivellumDB(path)
    return db


def _make_work(db: OrivellumDB, title: str = "Test Work") -> str:
    """Create a work and return its id."""
    return db.create_work(title=title, work_type="research")["id"]


def _make_conv(db: OrivellumDB, title: str = "Test Conv") -> str:
    """Create a conversation and return its id."""
    return db.create_conversation(title=title)["id"]


# ---------------------------------------------------------------------------
# 1. Optimistic concurrency — VERSION_CONFLICT
# ---------------------------------------------------------------------------

class TestVersionConflict:
    """Acceptance criterion 1: a stale update is rejected with VERSION_CONFLICT."""

    def test_update_work_wrong_version_raises(self):
        db = _make_db()
        wid = _make_work(db)
        # Version starts at 1 in objects.
        with pytest.raises(VersionConflictError) as exc_info:
            db.update_work(wid, expected_version=99, title="Should Fail")
        err = exc_info.value
        assert err.object_id == wid
        assert err.expected == 99
        assert err.actual == 1

    def test_update_work_correct_version_succeeds(self):
        db = _make_db()
        wid = _make_work(db, "Original")
        # Version is 1 after creation.
        result = db.update_work(wid, expected_version=1, title="Updated")
        assert result["title"] == "Updated"

    def test_update_work_bumps_version_on_success(self):
        db = _make_db()
        wid = _make_work(db)
        db.update_work(wid, expected_version=1, title="v2")
        row = db._conn.execute(
            "SELECT version FROM objects WHERE id=?", (wid,)
        ).fetchone()
        assert row["version"] == 2

    def test_update_work_stale_after_first_update(self):
        """Two concurrent clients both start with version=1; the second must fail."""
        db = _make_db()
        wid = _make_work(db)
        db.update_work(wid, expected_version=1, title="First writer wins")
        with pytest.raises(VersionConflictError):
            db.update_work(wid, expected_version=1, title="Second writer should lose")

    def test_update_work_no_version_arg_always_succeeds(self):
        """Passing no expected_version is the permissive (last-write-wins) path."""
        db = _make_db()
        wid = _make_work(db)
        db.update_work(wid, title="Permissive first")
        db.update_work(wid, title="Permissive second")  # must not raise
        work = db.get_work(wid)
        assert work["title"] == "Permissive second"

    def test_update_conversation_wrong_version_raises(self):
        db = _make_db()
        cid = _make_conv(db)
        with pytest.raises(VersionConflictError) as exc_info:
            db.update_conversation(cid, title="Fail", expected_version=999)
        assert exc_info.value.object_id == cid
        assert exc_info.value.expected == 999

    def test_update_conversation_correct_version_succeeds(self):
        db = _make_db()
        cid = _make_conv(db)
        row = db._conn.execute(
            "SELECT version FROM conversations WHERE id=?", (cid,)
        ).fetchone()
        v = row["version"]
        result = db.update_conversation(cid, title="Renamed", expected_version=v)
        assert result["title"] == "Renamed"

    def test_update_conversation_bumps_version(self):
        db = _make_db()
        cid = _make_conv(db)
        before = db._conn.execute(
            "SELECT version FROM conversations WHERE id=?", (cid,)
        ).fetchone()["version"]
        db.update_conversation(cid, title="New Title", expected_version=before)
        after = db._conn.execute(
            "SELECT version FROM conversations WHERE id=?", (cid,)
        ).fetchone()["version"]
        assert after == before + 1

    def test_version_conflict_rolls_back_domain_change(self):
        """When VersionConflictError is raised, no domain change must persist."""
        db = _make_db()
        wid = _make_work(db, "Original Title")
        with pytest.raises(VersionConflictError):
            db.update_work(wid, expected_version=999, title="Should Not Persist")
        work = db.get_work(wid)
        assert work["title"] == "Original Title"

    def test_version_conflict_error_attributes(self):
        err = VersionConflictError("obj-1", expected=3, actual=5)
        assert err.object_id == "obj-1"
        assert err.expected == 3
        assert err.actual == 5
        assert "obj-1" in str(err)
        assert "3" in str(err)
        assert "5" in str(err)


# ---------------------------------------------------------------------------
# 2. Atomic write — exactly ONE audit row + ONE outbox event per governed_write
# ---------------------------------------------------------------------------

class TestAtomicWrite:
    """Acceptance criterion 2: every governed write emits exactly one audit row
    and one outbox event in the same transaction."""

    def _count_audit(self, db: OrivellumDB, object_id: str) -> int:
        return db._conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE object_id=?", (object_id,)
        ).fetchone()[0]

    def _count_outbox(self, db: OrivellumDB, object_id: str) -> int:
        return db._conn.execute(
            "SELECT COUNT(*) FROM outbox WHERE object_id=?", (object_id,)
        ).fetchone()[0]

    def test_update_work_emits_one_audit_and_one_outbox(self):
        db = _make_db()
        wid = _make_work(db)
        audit_before = self._count_audit(db, wid)
        outbox_before = self._count_outbox(db, wid)
        db.update_work(wid, title="Audited Update")
        assert self._count_audit(db, wid) == audit_before + 1
        assert self._count_outbox(db, wid) == outbox_before + 1

    def test_update_conversation_emits_one_audit_and_one_outbox(self):
        db = _make_db()
        cid = _make_conv(db)
        audit_before = self._count_audit(db, cid)
        outbox_before = self._count_outbox(db, cid)
        db.update_conversation(cid, title="Renamed")
        assert self._count_audit(db, cid) == audit_before + 1
        assert self._count_outbox(db, cid) == outbox_before + 1

    def test_governed_write_direct_usage(self):
        """Direct use of governed_write via a synthetic operation."""
        db = _make_db()
        wid = _make_work(db)
        audit_before = self._count_audit(db, wid)
        outbox_before = self._count_outbox(db, wid)
        with db.governed_write(
            operation="work.custom",
            event_type="work.custom",
            object_id=wid,
            object_type="work",
            payload={"custom": True},
            detail="direct test",
        ):
            db._conn.execute(
                "UPDATE works SET description=? WHERE id=?",
                ("governed", wid),
            )
        assert self._count_audit(db, wid) == audit_before + 1
        assert self._count_outbox(db, wid) == outbox_before + 1

    def test_exception_inside_governed_write_produces_no_audit_no_outbox(self):
        """If the domain SQL raises, the transaction rolls back — no audit or outbox rows."""
        db = _make_db()
        wid = _make_work(db)
        audit_before = self._count_audit(db, wid)
        outbox_before = self._count_outbox(db, wid)
        with pytest.raises(RuntimeError):
            with db.governed_write(
                operation="work.boom",
                event_type="work.boom",
                object_id=wid,
                object_type="work",
            ):
                db._conn.execute("UPDATE works SET title=? WHERE id=?",
                                 ("mid-flight", wid))
                raise RuntimeError("simulated domain failure")
        # Nothing committed.
        assert self._count_audit(db, wid) == audit_before
        assert self._count_outbox(db, wid) == outbox_before
        work = db.get_work(wid)
        assert work["title"] != "mid-flight"

    def test_version_conflict_produces_no_audit_no_outbox(self):
        """VersionConflictError also rolls back — no audit or outbox rows emitted."""
        db = _make_db()
        wid = _make_work(db)
        audit_before = self._count_audit(db, wid)
        outbox_before = self._count_outbox(db, wid)
        with pytest.raises(VersionConflictError):
            db.update_work(wid, expected_version=999, title="Stale")
        assert self._count_audit(db, wid) == audit_before
        assert self._count_outbox(db, wid) == outbox_before

    def test_outbox_event_payload_stored_correctly(self):
        db = _make_db()
        wid = _make_work(db)
        db.update_work(wid, title="Payload Test")
        row = db._conn.execute(
            "SELECT payload FROM outbox WHERE object_id=? ORDER BY created_at DESC LIMIT 1",
            (wid,),
        ).fetchone()
        assert row is not None
        payload = json.loads(row["payload"])
        assert "fields" in payload
        assert "title" in payload["fields"]

    def test_audit_operation_stored_correctly(self):
        db = _make_db()
        wid = _make_work(db)
        db.update_work(wid, title="Op Test")
        row = db._conn.execute(
            "SELECT operation FROM audit_log WHERE object_id=? ORDER BY timestamp DESC LIMIT 1",
            (wid,),
        ).fetchone()
        assert row["operation"] == "work.updated"

    def test_outbox_dispatch(self):
        db = _make_db()
        wid = _make_work(db)
        db.update_work(wid, title="Dispatch Me")
        pending = db.list_outbox(pending_only=True)
        assert len(pending) >= 1
        event_id = pending[0]["id"]
        ok = db.dispatch_outbox_event(event_id)
        assert ok is True
        # Now it should no longer be in the pending list.
        still_pending = [e for e in db.list_outbox(pending_only=True)
                         if e["id"] == event_id]
        assert len(still_pending) == 0

    def test_outbox_dispatch_idempotent(self):
        db = _make_db()
        wid = _make_work(db)
        db.update_work(wid, title="Idempotent")
        pending = db.list_outbox(pending_only=True)
        eid = pending[0]["id"]
        db.dispatch_outbox_event(eid)
        result = db.dispatch_outbox_event(eid)  # second call
        assert result is False  # already dispatched


# ---------------------------------------------------------------------------
# 3. Hash-chained audit ledger — tampering is detectable
# ---------------------------------------------------------------------------

class TestAuditChain:
    """Acceptance criterion 3: editing an audit row breaks the chain and
    verify_audit_chain() detects it."""

    def test_clean_chain_passes(self):
        db = _make_db()
        wid = _make_work(db)
        db.update_work(wid, title="Step 1")
        db.update_work(wid, title="Step 2")
        db.update_work(wid, title="Step 3")
        ok, reason = db.verify_audit_chain()
        assert ok is True, f"Chain should be valid but got: {reason}"
        assert reason == ""

    def test_tamper_row_hash_detected(self):
        """Directly overwriting row_hash of a chained row breaks the chain."""
        db = _make_db()
        wid = _make_work(db)
        db.update_work(wid, title="Before Tamper")
        db.update_work(wid, title="After Tamper row")
        # Grab the most recent chained audit row and overwrite its row_hash.
        row = db._conn.execute(
            "SELECT id FROM audit_log WHERE row_hash IS NOT NULL "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        db._conn.execute(
            "UPDATE audit_log SET row_hash=? WHERE id=?",
            ("deadbeef" * 8, row["id"]),  # 64 hex chars
        )
        db._conn.commit()
        ok, reason = db.verify_audit_chain()
        assert ok is False
        assert reason != ""

    def test_tamper_prev_hash_detected(self):
        """Overwriting prev_hash severs the link from the previous row."""
        db = _make_db()
        wid = _make_work(db)
        db.update_work(wid, title="Row A")
        db.update_work(wid, title="Row B")
        # Get the second chained row.
        rows = db._conn.execute(
            "SELECT id FROM audit_log WHERE row_hash IS NOT NULL "
            "ORDER BY timestamp ASC, id ASC"
        ).fetchall()
        assert len(rows) >= 2
        second_id = rows[-1]["id"]
        db._conn.execute(
            "UPDATE audit_log SET prev_hash=? WHERE id=?",
            ("0" * 64, second_id),
        )
        db._conn.commit()
        ok, reason = db.verify_audit_chain()
        assert ok is False

    def test_tamper_operation_field_detected(self):
        """Changing the operation field alters the inputs to row_hash, breaking it."""
        db = _make_db()
        wid = _make_work(db)
        db.update_work(wid, title="Modify Operation")
        row = db._conn.execute(
            "SELECT id FROM audit_log WHERE row_hash IS NOT NULL "
            "ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        db._conn.execute(
            "UPDATE audit_log SET operation=? WHERE id=?",
            ("work.secretly_deleted", row["id"]),
        )
        db._conn.commit()
        ok, reason = db.verify_audit_chain()
        assert ok is False

    def test_empty_chain_passes(self):
        """A freshly created DB with no governed writes has an empty chain — valid."""
        db = _make_db()
        ok, reason = db.verify_audit_chain()
        assert ok is True
        assert reason == ""

    def test_chain_grows_correctly_across_multiple_objects(self):
        """Writes to different objects all participate in one global chain."""
        db = _make_db()
        w1 = _make_work(db, "Work Alpha")
        w2 = _make_work(db, "Work Beta")
        db.update_work(w1, title="Alpha v2")
        db.update_work(w2, title="Beta v2")
        db.update_work(w1, title="Alpha v3")
        ok, reason = db.verify_audit_chain()
        assert ok is True, f"Multi-object chain should be valid: {reason}"
        # All five ops should have chained rows.
        count = db._conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE row_hash IS NOT NULL"
        ).fetchone()[0]
        assert count >= 3  # at minimum the three update_work calls

    def test_pre_v55_null_rows_are_skipped_by_verify(self):
        """Rows with NULL row_hash (legacy or standalone audit()) do not falsify
        the verify result — the chain ignores them."""
        db = _make_db()
        wid = _make_work(db)
        # Inject a row without row_hash to simulate a pre-v55 row.
        db._conn.execute(
            """INSERT INTO audit_log(id, timestamp, actor, operation,
               object_id, result, app_version)
               VALUES('legacy-row', datetime('now'), 'system',
                      'legacy.op', ?, 'ok', '0.0.1')""",
            (wid,),
        )
        db._conn.commit()
        db.update_work(wid, title="Post-legacy")
        ok, reason = db.verify_audit_chain()
        assert ok is True, f"Legacy null-hash rows should be skipped: {reason}"


# ---------------------------------------------------------------------------
# 4. Schema — version columns and outbox table exist
# ---------------------------------------------------------------------------

class TestSchemaV55ToV57:
    """Verify that schema migrations v55–v57 applied correctly."""

    def test_audit_log_has_hash_chain_columns(self):
        db = _make_db()
        cols = {r[1] for r in db._conn.execute(
            "PRAGMA table_info(audit_log)"
        ).fetchall()}
        assert "prev_hash" in cols
        assert "row_hash" in cols

    def test_outbox_table_exists_with_expected_columns(self):
        db = _make_db()
        cols = {r[1] for r in db._conn.execute(
            "PRAGMA table_info(outbox)"
        ).fetchall()}
        assert {"id", "event_type", "object_id", "object_type",
                "payload", "created_at", "dispatched_at"}.issubset(cols)

    def test_conversations_has_version_column(self):
        db = _make_db()
        cols = {r[1] for r in db._conn.execute(
            "PRAGMA table_info(conversations)"
        ).fetchall()}
        assert "version" in cols

    def test_messages_has_version_column(self):
        db = _make_db()
        cols = {r[1] for r in db._conn.execute(
            "PRAGMA table_info(messages)"
        ).fetchall()}
        assert "version" in cols

    def test_documents_has_version_column(self):
        db = _make_db()
        cols = {r[1] for r in db._conn.execute(
            "PRAGMA table_info(documents)"
        ).fetchall()}
        assert "version" in cols

    def test_knowledge_has_version_column(self):
        db = _make_db()
        cols = {r[1] for r in db._conn.execute(
            "PRAGMA table_info(knowledge)"
        ).fetchall()}
        assert "version" in cols

    def test_schema_version_is_57_or_higher(self):
        db = _make_db()
        version = int(db._conn.execute(
            "SELECT value FROM settings WHERE key='schema_version'"
        ).fetchone()["value"])
        assert version >= 57
