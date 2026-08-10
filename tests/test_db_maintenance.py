"""Tests for WAL checkpoint, non-blocking VACUUM, and per-thread read pool.

Design contract verified here:
  - Routine run: wal_checkpoint(TRUNCATE) + ANALYZE always execute
  - Conditional VACUUM: triggered only when freelist ratio > 30 %
  - VACUUM runs on db._conn under db._lock so writes are safely queued
    at the Python mutex level — no SQLITE_BUSY, no data loss
  - Reads via db.read_conn() use a separate per-thread connection and are
    never blocked by db._lock regardless of what VACUUM is doing

All of these are tested with real concurrency (threading), not just mocks,
where the concurrency property is the point being verified.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from orivellum.database.db import OrivellumDB

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_db(tmp_path: Path) -> OrivellumDB:
    return OrivellumDB(str(tmp_path / "test.db"))


# ---------------------------------------------------------------------------
# 1. Freelist ratio gate
# ---------------------------------------------------------------------------


class TestFreelistRatioGate:
    def test_vacuum_skipped_on_fresh_db(self, tmp_path):
        """Routine run on a fresh DB skips VACUUM (low freelist)."""
        from orivellum.capabilities.nightshift import _pass_db_optimise

        db = _fresh_db(tmp_path)
        report: list[str] = []
        _pass_db_optimise(db, report)
        db.close()

        assert report, "report must have at least one entry"
        assert any("VACUUM skipped" in line for line in report), (
            f"Expected 'VACUUM skipped' in report for fresh DB.\nReport: {report}"
        )

    def test_vacuum_skipped_message_includes_freelist_percentage(self, tmp_path):
        """The optimise report line includes the freelist % for operator visibility."""
        from orivellum.capabilities.nightshift import _pass_db_optimise

        db = _fresh_db(tmp_path)
        report: list[str] = []
        _pass_db_optimise(db, report)
        db.close()

        optimise_lines = [
            l for l in report if "checkpoint" in l or "VACUUM" in l or "optimised" in l
        ]
        assert optimise_lines, f"No optimise line in report: {report}"
        assert "%" in optimise_lines[0], (
            f"Freelist percentage missing from report: {optimise_lines[0]}"
        )

    def test_vacuum_triggered_above_threshold(self, tmp_path, monkeypatch):
        """_run_vacuum is called when freelist ratio > 30 %; VACUUM skipped when < 30 %.

        _get_freelist_ratio and _run_vacuum are module-level hooks extracted
        specifically so tests can control them without patching C-extension
        sqlite3.Connection attributes (which are read-only).
        """
        import orivellum.capabilities.nightshift as ns_mod
        from orivellum.capabilities.nightshift import _pass_db_optimise

        db = _fresh_db(tmp_path)

        # Simulate 40 % freelist ratio
        monkeypatch.setattr(ns_mod, "_get_freelist_ratio", lambda conn: (0.40, 400, 1000))

        vacuum_called: list[bool] = []
        monkeypatch.setattr(ns_mod, "_run_vacuum", lambda conn: vacuum_called.append(True))

        report: list[str] = []
        _pass_db_optimise(db, report)
        db.close()

        assert vacuum_called, f"_run_vacuum must be called when ratio > 30 %.\nReport: {report}"
        assert not any("VACUUM skipped" in l for l in report), (
            f"Report should not say 'VACUUM skipped' at 40 % freelist.\nReport: {report}"
        )

    def test_vacuum_not_triggered_below_threshold(self, tmp_path, monkeypatch):
        """_run_vacuum is NOT called when freelist ratio <= 30 %."""
        import orivellum.capabilities.nightshift as ns_mod
        from orivellum.capabilities.nightshift import _pass_db_optimise

        db = _fresh_db(tmp_path)
        monkeypatch.setattr(ns_mod, "_get_freelist_ratio", lambda conn: (0.20, 200, 1000))

        vacuum_called: list[bool] = []
        monkeypatch.setattr(ns_mod, "_run_vacuum", lambda conn: vacuum_called.append(True))

        report: list[str] = []
        _pass_db_optimise(db, report)
        db.close()

        assert not vacuum_called, (
            f"_run_vacuum must NOT be called at 20 % freelist.\nReport: {report}"
        )
        assert any("VACUUM skipped" in l for l in report)


# ---------------------------------------------------------------------------
# 2. Concurrency contract: reads unblocked, writes safely queued
#
# Design: VACUUM runs on db._conn under db._lock.
#   - Reads via db.read_conn() never touch db._lock → completely unaffected
#   - Writes via governed_write/db._lock queue at the Python mutex and
#     complete after VACUUM releases the lock — no SQLITE_BUSY, no data loss
# ---------------------------------------------------------------------------


class TestConcurrencyContract:
    def _slow_vacuum_fixture(
        self, monkeypatch, started: threading.Event, may_finish: threading.Event
    ) -> None:
        """Monkeypatch _run_vacuum to signal start and wait before returning."""
        import orivellum.capabilities.nightshift as ns_mod

        def slow_run_vacuum(conn: object) -> None:
            started.set()
            may_finish.wait(timeout=10.0)
            # Don't actually vacuum — test DB is tiny and we just need the lock

        monkeypatch.setattr(ns_mod, "_run_vacuum", slow_run_vacuum)
        monkeypatch.setattr(ns_mod, "_get_freelist_ratio", lambda conn: (0.40, 400, 1000))

    def test_reads_unblocked_while_vacuum_holds_db_lock(self, tmp_path, monkeypatch):
        """db.get_setting() completes immediately while VACUUM holds db._lock.

        get_setting() uses db.read_conn() which has its own per-thread
        connection — it never acquires db._lock and must not block even
        when VACUUM has held the lock for an extended period.
        """
        from orivellum.capabilities.nightshift import _pass_db_optimise

        db = _fresh_db(tmp_path)
        db.set_setting("probe", "ready")

        started = threading.Event()
        may_finish = threading.Event()
        self._slow_vacuum_fixture(monkeypatch, started, may_finish)

        read_value: list[str] = []
        read_latency: list[float] = []
        errors: list[str] = []

        def optimise_worker() -> None:
            _pass_db_optimise(db, [])

        def reader_worker() -> None:
            started.wait(timeout=5.0)  # wait until VACUUM has db._lock
            t0 = time.monotonic()
            try:
                val = db.get_setting("probe")
                read_value.append(val)
                read_latency.append(time.monotonic() - t0)
            except Exception as exc:
                errors.append(str(exc))

        t_opt = threading.Thread(target=optimise_worker, name="nightshift", daemon=True)
        t_rd = threading.Thread(target=reader_worker, name="reader", daemon=True)

        t_opt.start()
        t_rd.start()
        t_rd.join(timeout=5.0)  # reader must finish LONG before VACUUM is done
        may_finish.set()
        t_opt.join(timeout=10.0)
        db.close()

        assert not errors, f"Read raised an error during VACUUM: {errors}"
        assert read_value == ["ready"], f"Read returned wrong value: {read_value}"
        assert read_latency and read_latency[0] < 1.0, (
            f"Read took {read_latency[0]:.3f}s — it appears to have blocked on "
            f"db._lock instead of using read_conn()."
        )

    def test_writes_queue_and_complete_after_vacuum(self, tmp_path, monkeypatch):
        """A write issued while VACUUM holds db._lock queues at the Python
        mutex and completes successfully once VACUUM releases it.

        This proves writes are SERIALIZED (not FAILED) — no SQLITE_BUSY,
        no data loss.
        """
        from orivellum.capabilities.nightshift import _pass_db_optimise

        db = _fresh_db(tmp_path)

        started = threading.Event()
        may_finish = threading.Event()
        write_queued = threading.Event()
        self._slow_vacuum_fixture(monkeypatch, started, may_finish)

        write_results: list[str] = []
        errors: list[str] = []

        def optimise_worker() -> None:
            _pass_db_optimise(db, [])

        def writer_worker() -> None:
            started.wait(timeout=5.0)  # VACUUM now holds db._lock
            write_queued.set()
            try:
                # This will block at db._lock (Python mutex) until VACUUM finishes
                db.set_setting("after_vacuum", "committed")
                write_results.append(db.get_setting("after_vacuum"))
            except Exception as exc:
                errors.append(str(exc))

        t_opt = threading.Thread(target=optimise_worker, name="nightshift", daemon=True)
        t_wr = threading.Thread(target=writer_worker, name="writer", daemon=True)

        t_opt.start()
        t_wr.start()

        # Give writer a moment to queue behind db._lock, then release VACUUM
        write_queued.wait(timeout=5.0)
        time.sleep(0.05)  # let writer block on the mutex
        may_finish.set()  # release VACUUM → writer gets the lock

        t_wr.join(timeout=10.0)
        t_opt.join(timeout=10.0)
        db.close()

        assert not errors, f"Write raised an exception: {errors}"
        assert write_results == ["committed"], (
            f"Write did not complete or returned wrong value: {write_results}"
        )

    def test_optimise_does_not_deadlock_with_concurrent_writer(self, tmp_path):
        """_pass_db_optimise and a concurrent write both complete without deadlock.

        Uses a real (instant) VACUUM on a fresh DB — proves the lock ordering
        is consistent and there is no deadlock between the optimise pass
        and ordinary writes.
        """
        from orivellum.capabilities.nightshift import _pass_db_optimise

        db = _fresh_db(tmp_path)
        completed_optimise = threading.Event()
        completed_write = threading.Event()
        errors: list[str] = []

        def optimise_worker() -> None:
            try:
                _pass_db_optimise(db, [])
            except Exception as exc:
                errors.append(f"optimise: {exc}")
            finally:
                completed_optimise.set()

        def write_worker() -> None:
            try:
                time.sleep(0.005)  # slight delay so optimise gets the lock first
                db.set_setting("concurrent_key", "concurrent_val")
            except Exception as exc:
                errors.append(f"write: {exc}")
            finally:
                completed_write.set()

        t_opt = threading.Thread(target=optimise_worker, daemon=True)
        t_wr = threading.Thread(target=write_worker, daemon=True)
        t_opt.start()
        t_wr.start()

        assert completed_optimise.wait(timeout=15.0), "optimise timed out — possible deadlock"
        assert completed_write.wait(timeout=5.0), "write timed out — possible deadlock"
        db.close()

        assert not errors, f"Errors during concurrency test: {errors}"


# ---------------------------------------------------------------------------
# 3. read_conn(): per-thread identity, read-only enforcement, data visibility
# ---------------------------------------------------------------------------


class TestReadConnPool:
    def test_same_thread_returns_same_connection(self, tmp_path):
        db = _fresh_db(tmp_path)
        assert db.read_conn() is db.read_conn()
        db.close()

    def test_different_threads_get_different_connections(self, tmp_path):
        db = _fresh_db(tmp_path)
        main_rc = db.read_conn()
        results: dict = {}

        def worker():
            results["rc"] = db.read_conn()

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert results["rc"] is not main_rc
        db.close()

    def test_read_conn_refuses_writes(self, tmp_path):
        db = _fresh_db(tmp_path)
        with pytest.raises(Exception):
            db.read_conn().execute(
                "INSERT INTO settings(id,scope,key,value,updated_at) VALUES(?,?,?,?,?)",
                ("x", "global", "rw_test", "val", "2025-01-01"),
            )
        db.close()

    def test_read_conn_sees_committed_writes(self, tmp_path):
        db = _fresh_db(tmp_path)
        db.set_setting("health", "ok")
        assert db.get_setting("health") == "ok"
        db.close()

    def test_get_setting_default_when_key_absent(self, tmp_path):
        db = _fresh_db(tmp_path)
        assert db.get_setting("no_such_key", "fallback") == "fallback"
        db.close()

    def test_get_setting_does_not_require_db_lock(self, tmp_path):
        """get_setting() completes even when db._lock is held by another thread."""
        db = _fresh_db(tmp_path)
        db.set_setting("concurrent", "value")

        lock_held = threading.Event()
        lock_release = threading.Event()
        setting_value: list[str] = []

        def lock_holder() -> None:
            with db._lock:
                lock_held.set()
                lock_release.wait(timeout=5.0)

        def reader() -> None:
            lock_held.wait(timeout=3.0)
            # get_setting() must complete without waiting for lock_holder
            setting_value.append(db.get_setting("concurrent", ""))

        lh = threading.Thread(target=lock_holder, daemon=True)
        rd = threading.Thread(target=reader, daemon=True)
        lh.start()
        rd.start()
        rd.join(timeout=3.0)
        lock_release.set()
        lh.join(timeout=2.0)
        db.close()

        assert setting_value == ["value"], (
            "get_setting() blocked on db._lock — must use read_conn() instead. "
            f"Got: {setting_value}"
        )

    def test_close_does_not_raise(self, tmp_path):
        db = _fresh_db(tmp_path)
        _ = db.read_conn()
        db.close()  # must not raise


# ---------------------------------------------------------------------------
# 4. In-memory DB regression
#    read_conn() must fall back to self._conn for ":memory:" databases —
#    a second sqlite3.connect(":memory:") opens a completely separate,
#    empty DB that has no schema and will raise OperationalError.
# ---------------------------------------------------------------------------


class TestInMemoryDB:
    def _inmem_db(self) -> OrivellumDB:
        return OrivellumDB(":memory:")

    def test_get_setting_works_on_inmemory_db(self):
        """get_setting() must not raise OperationalError on an in-memory DB."""
        db = self._inmem_db()
        # Fresh in-memory DB has no stored value — must return the default.
        assert db.get_setting("missing", "default") == "default"
        db.close()

    def test_get_setting_round_trip_on_inmemory_db(self):
        """set_setting() / get_setting() round-trip works on an in-memory DB."""
        db = self._inmem_db()
        db.set_setting("key", "value")
        assert db.get_setting("key") == "value"
        db.close()

    def test_get_active_prompt_returns_none_on_inmemory_db(self):
        """get_active_prompt() must return None (not raise) on an in-memory DB."""
        db = self._inmem_db()
        result = db.get_active_prompt("chat.base")
        assert result is None
        db.close()

    def test_read_conn_returns_primary_conn_for_inmemory(self):
        """read_conn() must return self._conn for in-memory DBs (not a new empty DB)."""
        db = self._inmem_db()
        assert db.read_conn() is db._conn, (
            "read_conn() for ':memory:' must return the shared primary connection, "
            "not a new empty in-memory database."
        )
        db.close()
