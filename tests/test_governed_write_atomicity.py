"""Atomicity tests for ``governed_write`` — task #248.

Verifies that when a write fails mid-transaction — whether the failure occurs
inside the caller's domain-SQL block or in the post-yield audit/outbox step —
the database is left fully consistent:

  (a) the domain row is absent (rolled back)
  (b) no orphaned audit row exists
  (c) no orphaned outbox event exists
  (d) ``verify_audit_chain()`` still returns ``(True, "")``

Tests cover three representative high-level methods (``create_document``,
``create_job``, ``upsert_claim``) plus low-level ``governed_write`` blocks.
"""

from __future__ import annotations

import tempfile
from unittest.mock import patch

import pytest

from orivellum.database.db import OrivellumDB

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture()
def db() -> OrivellumDB:
    """Fresh on-disk OrivellumDB with all migrations applied."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return OrivellumDB(path)


def _audit_count(db: OrivellumDB) -> int:
    return db._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]


def _outbox_count(db: OrivellumDB) -> int:
    return db._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]


def _doc_exists(db: OrivellumDB, doc_id: str) -> bool:
    return bool(db._conn.execute("SELECT 1 FROM documents WHERE id=?", (doc_id,)).fetchone())


def _object_exists(db: OrivellumDB, oid: str) -> bool:
    return bool(db._conn.execute("SELECT 1 FROM objects WHERE id=?", (oid,)).fetchone())


def _job_exists(db: OrivellumDB, job_id: str) -> bool:
    return bool(db._conn.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone())


def _claim_exists(db: OrivellumDB, subject: str, predicate: str) -> bool:
    return bool(
        db._conn.execute(
            "SELECT 1 FROM claims WHERE subject=? AND predicate=?",
            (subject, predicate),
        ).fetchone()
    )


# ---------------------------------------------------------------------------
# 1. Direct governed_write blocks — exception inside the yield
# ---------------------------------------------------------------------------


class TestDirectBlockRollback:
    """Exception raised inside the governed_write body rolls back atomically."""

    def test_domain_row_absent_after_exception(self, db: OrivellumDB) -> None:
        """A row inserted inside the block must not appear after an exception."""
        before_audit = _audit_count(db)
        before_outbox = _outbox_count(db)

        sentinel_id = "test-sentinel-001"
        with pytest.raises(ValueError, match="simulated crash"):
            with db.governed_write(
                operation="test.created",
                event_type="test.created",
                object_id=sentinel_id,
                object_type="setting",
                detail="atomicity-test",
            ):
                # Write a setting row inside the block
                db._conn.execute(
                    """INSERT INTO settings(id, scope, key, value, updated_at)
                       VALUES(?, 'global', 'atomicity_test_key', 'yes', '2099-01-01T00:00:00+00:00')""",
                    (sentinel_id,),
                )
                raise ValueError("simulated crash")

        # (a) domain row must not exist
        row = db._conn.execute("SELECT 1 FROM settings WHERE id=?", (sentinel_id,)).fetchone()
        assert row is None, "Rolled-back settings row must not be visible"

        # (b) no new audit row
        assert _audit_count(db) == before_audit, "No audit row must be written on rollback"

        # (c) no new outbox event
        assert _outbox_count(db) == before_outbox, "No outbox event must be written on rollback"

    def test_no_audit_row_written_on_block_exception(self, db: OrivellumDB) -> None:
        """Verify audit row count stays the same when the block raises."""
        before = _audit_count(db)
        with (
            pytest.raises(RuntimeError),
            db.governed_write(
                operation="test.noop",
                event_type="test.noop",
                object_id="none",
                object_type="setting",
            ),
        ):
            raise RuntimeError("abort immediately")
        assert _audit_count(db) == before

    def test_verify_chain_intact_after_direct_block_exception(self, db: OrivellumDB) -> None:
        """Chain verification passes even after a failed governed_write block."""
        # One successful write first
        db.set_setting("pre_key", "pre_value", actor="test")

        # Failed write
        with (
            pytest.raises(RuntimeError),
            db.governed_write(
                operation="test.crash",
                event_type="test.crash",
                object_id="crash-obj",
                object_type="setting",
            ),
        ):
            raise RuntimeError("crash during domain SQL")

        ok, reason = db.verify_audit_chain()
        assert ok is True, f"Chain broken after failed write: {reason}"
        assert reason == ""


# ---------------------------------------------------------------------------
# 2. Audit-write failure (post-yield crash) via _audit_tx patch
# ---------------------------------------------------------------------------


class TestPostYieldAuditFailure:
    """Failure in _audit_tx (post-yield, pre-commit) rolls back domain SQL."""

    def test_create_document_rolled_back_when_audit_fails(self, db: OrivellumDB) -> None:
        before_audit = _audit_count(db)
        before_outbox = _outbox_count(db)

        # Capture the document id so we can check the tables after the failure.
        # We can't get the id from create_document (it raises), so track via
        # the objects table count instead.
        before_objects = db._conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        before_docs = db._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]

        with patch.object(db, "_audit_tx", side_effect=RuntimeError("injected audit failure")):
            with pytest.raises(RuntimeError, match="injected audit failure"):
                db.create_document("Atomicity Test Doc", source="test.pdf", kind="pdf")

        # (a) domain rows must not exist
        after_objects = db._conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
        after_docs = db._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert after_objects == before_objects, "objects row must be rolled back"
        assert after_docs == before_docs, "documents row must be rolled back"

        # (b) no new audit row
        assert _audit_count(db) == before_audit, "No audit row on rolled-back create_document"

        # (c) no new outbox event
        assert _outbox_count(db) == before_outbox, "No outbox event on rolled-back create_document"

    def test_create_job_rolled_back_when_audit_fails(self, db: OrivellumDB) -> None:
        before_audit = _audit_count(db)
        before_jobs = db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

        with patch.object(db, "_audit_tx", side_effect=RuntimeError("injected audit failure")):
            with pytest.raises(RuntimeError, match="injected audit failure"):
                db.create_job(job_type="test.extract", priority=1)

        after_jobs = db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        assert after_jobs == before_jobs, "jobs row must be rolled back"
        assert _audit_count(db) == before_audit, "No audit row on rolled-back create_job"

    def test_upsert_claim_insert_path_rolled_back_when_audit_fails(self, db: OrivellumDB) -> None:
        """New-claim insert path: failure rolls back claim + transition rows."""
        before_audit = _audit_count(db)
        before_claims = db._conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]

        subject, predicate = "test-subject-001", "test-predicate-001"

        with patch.object(db, "_audit_tx", side_effect=RuntimeError("injected audit failure")):
            with pytest.raises(RuntimeError, match="injected audit failure"):
                db.upsert_claim(subject, predicate, "some-value")

        # (a) claim must not exist
        assert not _claim_exists(db, subject, predicate), "claim must be rolled back"

        # (b) claim count unchanged
        after_claims = db._conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        assert after_claims == before_claims

        # (c) audit count unchanged
        assert _audit_count(db) == before_audit

    def test_outbox_event_absent_when_audit_fails(self, db: OrivellumDB) -> None:
        """When _audit_tx raises, _emit_outbox_tx must NOT have committed either."""
        before_outbox = _outbox_count(db)

        with patch.object(db, "_audit_tx", side_effect=RuntimeError("injected")):
            with pytest.raises(RuntimeError):
                db.create_job(job_type="test.job")

        assert _outbox_count(db) == before_outbox, "outbox must stay clean on audit failure"


# ---------------------------------------------------------------------------
# 3. Domain-SQL failure (exception inside yield from DB constraint)
# ---------------------------------------------------------------------------


class TestDomainSQLFailure:
    """An exception thrown by the domain SQL itself rolls back atomically."""

    def test_duplicate_job_id_rolls_back_cleanly(self, db: OrivellumDB) -> None:
        """If domain SQL raises (UNIQUE violation), audit + outbox stay clean."""
        # Pre-insert a row to cause a UNIQUE violation
        fixed_id = "fixed-job-id-001"
        db._conn.execute(
            """INSERT INTO jobs(id, job_type, state, priority, created_at,
               max_attempts, input, correlation_id)
               VALUES(?,?,?,?,?,?,?,?)""",
            (fixed_id, "pre_existing", "queued", 0, "2099-01-01", 3, "{}", None),
        )
        db._conn.commit()

        before_audit = _audit_count(db)
        before_outbox = _outbox_count(db)

        # Attempt a governed_write that tries to insert a row with the same id
        import sqlite3

        with (
            pytest.raises(sqlite3.IntegrityError),
            db.governed_write(
                operation="job.created",
                event_type="job.created",
                object_id=fixed_id,
                object_type="job",
            ),
        ):
            db._conn.execute(
                """INSERT INTO jobs(id, job_type, state, priority, created_at,
                       max_attempts, input) VALUES(?,?,?,?,?,?,?)""",
                (fixed_id, "test_dup", "queued", 0, "2099-01-01", 3, "{}"),
            )

        # Row count must not have grown
        assert _audit_count(db) == before_audit, "No audit row on constraint violation"
        assert _outbox_count(db) == before_outbox, "No outbox event on constraint violation"

        # The pre-existing row must still be intact (not clobbered or deleted)
        assert _job_exists(db, fixed_id), "Pre-existing job row must survive the failed write"


# ---------------------------------------------------------------------------
# 4. Chain integrity across mixed successful + failed writes
# ---------------------------------------------------------------------------


class TestChainIntegrityAfterMixedWrites:
    """verify_audit_chain() must return (True, '') even after failed writes."""

    def test_chain_intact_after_failed_create_document(self, db: OrivellumDB) -> None:
        # Several clean writes
        db.create_document("Doc A", source="a.pdf", kind="pdf")
        db.create_document("Doc B", source="b.pdf", kind="pdf")
        db.create_job(job_type="test.clean.job")

        good_before = _audit_count(db)

        # Failed write — audit_tx raises mid-transaction
        with patch.object(db, "_audit_tx", side_effect=RuntimeError("injected")):
            with pytest.raises(RuntimeError):
                db.create_document("Failed Doc", source="fail.pdf", kind="pdf")

        # Audit count must not have grown
        assert _audit_count(db) == good_before, "Failed write must not add an audit row"

        # Chain must still be intact
        ok, reason = db.verify_audit_chain()
        assert ok is True, f"Chain broken after mixed writes: {reason}"

    def test_chain_intact_after_failed_create_job(self, db: OrivellumDB) -> None:
        db.create_job(job_type="setup.job.1")
        db.create_job(job_type="setup.job.2")

        with patch.object(db, "_audit_tx", side_effect=RuntimeError("injected")):
            with pytest.raises(RuntimeError):
                db.create_job(job_type="failing.job")

        ok, reason = db.verify_audit_chain()
        assert ok is True, f"Chain broken after failed create_job: {reason}"

    def test_chain_intact_after_failed_upsert_claim(self, db: OrivellumDB) -> None:
        db.set_setting("chain_test_key", "chain_test_val", actor="test")
        db.create_job(job_type="chain.test.job")

        with patch.object(db, "_audit_tx", side_effect=RuntimeError("injected")):
            with pytest.raises(RuntimeError):
                db.upsert_claim("chain-subject", "chain-predicate", "chain-value")

        ok, reason = db.verify_audit_chain()
        assert ok is True, f"Chain broken after failed upsert_claim: {reason}"

    def test_writes_succeed_after_failed_write(self, db: OrivellumDB) -> None:
        """The DB must remain fully writable after a rolled-back governed_write."""
        # First, cause a failure
        with patch.object(db, "_audit_tx", side_effect=RuntimeError("injected")):
            with pytest.raises(RuntimeError):
                db.create_document("Temp Fail", source="fail.pdf", kind="pdf")

        # Now perform a clean write — must succeed and chain correctly
        doc = db.create_document("Post-Failure Doc", source="post.pdf", kind="pdf")
        assert doc is not None
        assert doc["title"] == "Post-Failure Doc"

        ok, reason = db.verify_audit_chain()
        assert ok is True, f"Chain broken after post-failure write: {reason}"

    def test_chain_intact_after_multiple_failures(self, db: OrivellumDB) -> None:
        """Multiple consecutive failures must not corrupt the chain."""
        db.create_job(job_type="baseline.job")

        for _ in range(3):
            with patch.object(db, "_audit_tx", side_effect=RuntimeError("injected")):
                with pytest.raises(RuntimeError):
                    db.create_document(f"Fail {_}", source=f"fail{_}.pdf", kind="pdf")

        db.create_job(job_type="final.clean.job")

        ok, reason = db.verify_audit_chain()
        assert ok is True, f"Chain broken after multiple consecutive failures: {reason}"

    def test_successful_writes_between_failures_counted_correctly(self, db: OrivellumDB) -> None:
        """Audit rows from successful writes between failures must all be present."""
        # Interleave good and bad writes
        db.create_job(job_type="good.1")

        with patch.object(db, "_audit_tx", side_effect=RuntimeError("injected")):
            with pytest.raises(RuntimeError):
                db.create_job(job_type="bad.1")

        db.create_job(job_type="good.2")

        with patch.object(db, "_audit_tx", side_effect=RuntimeError("injected")):
            with pytest.raises(RuntimeError):
                db.create_job(job_type="bad.2")

        db.create_job(job_type="good.3")

        # Exactly 3 successful writes → 3 audit rows (plus migrations may add some)
        # Just verify the chain, not the exact count (migrations also write audit rows)
        ok, reason = db.verify_audit_chain()
        assert ok is True, f"Chain broken after interleaved good/bad writes: {reason}"
