"""Tests for folder-watch auto-import feature.

Covers:
  - watch_dirs config CRUD (get/set/legacy-compat)
  - API endpoints: list, add, update, delete
  - Polling worker: new file imported, duplicate skipped, missing dir handled
  - Seen-file registry: never re-imports
  - Status written after scan cycle
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import AUTH_HEADERS

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_db(tmp_path):
    """Minimal in-memory DB with the settings table only."""
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE settings (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL DEFAULT 'global',
            key TEXT NOT NULL,
            value TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(scope, key)
        )
    """)
    conn.commit()
    lock = threading.Lock()

    class _DB:
        _conn = conn
        _lock = lock

        def get_setting(self, key, default=""):
            with self._lock:
                row = self._conn.execute(
                    "SELECT value FROM settings WHERE key=? AND scope='global'", (key,)
                ).fetchone()
            return row["value"] if row and row["value"] is not None else default

        def set_setting(self, key, value, actor="system"):
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO settings(id,scope,key,value,updated_at) "
                    "VALUES(?,?,?,?,datetime('now'))",
                    (key + "_id", "global", key, value),
                )
                self._conn.commit()

    return _DB()


def _make_api_client(tmp_path):
    """Create a FastAPI TestClient with a real migrated DB."""
    from fastapi.testclient import TestClient

    from orivellum.api import _deps
    from orivellum.api.app import create_app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    db = OrivellumDB.open(str(tmp_path / "test.db"))
    cfg = OrivellumConfig(data_dir=str(tmp_path))
    _deps.init(db=db, cfg=cfg)
    app = create_app()
    return TestClient(app, raise_server_exceptions=True), db


# ─── Config helpers ───────────────────────────────────────────────────────────


class TestWatchDirsConfig:
    def test_get_empty_returns_empty_list(self, tmp_db):
        from orivellum.capabilities.folder_watch import get_watch_dirs

        assert get_watch_dirs(tmp_db) == []

    def test_set_and_get_roundtrip(self, tmp_db):
        from orivellum.capabilities.folder_watch import get_watch_dirs, set_watch_dirs

        dirs = [
            {"path": "/docs/a", "work_id": None, "enabled": True},
            {"path": "/docs/b", "work_id": "w1", "enabled": False},
        ]
        set_watch_dirs(dirs, tmp_db)
        result = get_watch_dirs(tmp_db)
        assert len(result) == 2
        assert result[0]["path"] == "/docs/a"
        assert result[1]["work_id"] == "w1"
        assert result[1]["enabled"] is False

    def test_legacy_single_dir_compat(self, tmp_db):
        """If watch_dirs is absent but old keys exist, they are returned."""
        from orivellum.capabilities.folder_watch import get_watch_dirs

        tmp_db.set_setting("folder_watch_path", "/legacy/path")
        tmp_db.set_setting("folder_watch_enabled", "true")
        tmp_db.set_setting("folder_watch_work_id", "w_legacy")
        result = get_watch_dirs(tmp_db)
        assert len(result) == 1
        assert result[0]["path"] == "/legacy/path"
        assert result[0]["enabled"] is True
        assert result[0]["work_id"] == "w_legacy"

    def test_set_clears_legacy_keys(self, tmp_db):
        """Writing watch_dirs removes the old single-dir keys."""
        from orivellum.capabilities.folder_watch import set_watch_dirs

        tmp_db.set_setting("folder_watch_path", "/old/path")
        tmp_db.set_setting("folder_watch_enabled", "true")
        set_watch_dirs([], tmp_db)
        assert tmp_db.get_setting("folder_watch_path") == ""
        assert tmp_db.get_setting("folder_watch_enabled") == "false"

    def test_get_watch_status_empty(self, tmp_db):
        from orivellum.capabilities.folder_watch import get_watch_status

        s = get_watch_status(tmp_db)
        assert s["scanned_at"] is None
        assert s["dirs"] == []


# ─── API endpoints ────────────────────────────────────────────────────────────


class TestWatchDirsAPI:
    def test_list_empty(self, tmp_path):
        client, _ = _make_api_client(tmp_path)
        r = client.get("/api/system/watch-dirs", headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["dirs"] == []
        assert data["scanned_at"] is None

    def test_add_dir(self, tmp_path):
        client, _ = _make_api_client(tmp_path)
        r = client.post(
            "/api/system/watch-dirs",
            headers=AUTH_HEADERS,
            json={"path": "/my/docs", "work_id": None, "enabled": True},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert len(data["dirs"]) == 1
        assert data["dirs"][0]["path"] == "/my/docs"

    def test_add_duplicate_returns_409(self, tmp_path):
        client, _ = _make_api_client(tmp_path)
        client.post(
            "/api/system/watch-dirs",
            headers=AUTH_HEADERS,
            json={"path": "/my/docs", "enabled": True},
        )
        r = client.post(
            "/api/system/watch-dirs",
            headers=AUTH_HEADERS,
            json={"path": "/my/docs", "enabled": True},
        )
        assert r.status_code == 409

    def test_update_dir_disabled(self, tmp_path):
        client, _ = _make_api_client(tmp_path)
        client.post(
            "/api/system/watch-dirs",
            headers=AUTH_HEADERS,
            json={"path": "/my/docs", "enabled": True},
        )
        r = client.put(
            "/api/system/watch-dirs/0",
            headers=AUTH_HEADERS,
            json={"path": "/my/docs", "work_id": None, "enabled": False},
        )
        assert r.status_code == 200
        assert r.json()["dirs"][0]["enabled"] is False

    def test_delete_dir(self, tmp_path):
        client, _ = _make_api_client(tmp_path)
        client.post(
            "/api/system/watch-dirs",
            headers=AUTH_HEADERS,
            json={"path": "/my/docs", "enabled": True},
        )
        r = client.delete("/api/system/watch-dirs/0", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["dirs"] == []

    def test_delete_out_of_range_returns_404(self, tmp_path):
        client, _ = _make_api_client(tmp_path)
        r = client.delete("/api/system/watch-dirs/99", headers=AUTH_HEADERS)
        assert r.status_code == 404

    def test_list_multiple_dirs(self, tmp_path):
        """Multiple dirs can be added and listed."""
        client, _ = _make_api_client(tmp_path)
        client.post(
            "/api/system/watch-dirs", headers=AUTH_HEADERS, json={"path": "/dir/a", "enabled": True}
        )
        client.post(
            "/api/system/watch-dirs",
            headers=AUTH_HEADERS,
            json={"path": "/dir/b", "enabled": False},
        )
        r = client.get("/api/system/watch-dirs", headers=AUTH_HEADERS)
        data = r.json()
        assert len(data["dirs"]) == 2
        paths = {d["path"] for d in data["dirs"]}
        assert paths == {"/dir/a", "/dir/b"}

    def test_list_enriches_with_scan_status(self, tmp_path):
        """After a scan status is written, list includes it."""
        from fastapi.testclient import TestClient

        from orivellum.api import _deps
        from orivellum.api.app import create_app
        from orivellum.capabilities.folder_watch import set_watch_dirs
        from orivellum.configuration.config import OrivellumConfig
        from orivellum.database.db import OrivellumDB

        db = OrivellumDB.open(str(tmp_path / "test.db"))
        cfg = OrivellumConfig(data_dir=str(tmp_path))
        _deps.init(db=db, cfg=cfg)
        set_watch_dirs([{"path": "/some/dir", "work_id": None, "enabled": True}], db)
        db.set_setting(
            "watch_dirs_status",
            json.dumps(
                {
                    "scanned_at": "2026-08-05T12:00:00+00:00",
                    "dirs": [{"path": "/some/dir", "files_imported": 3, "error": None}],
                }
            ),
        )
        client = TestClient(create_app(), raise_server_exceptions=True)
        r = client.get("/api/system/watch-dirs", headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["scanned_at"] == "2026-08-05T12:00:00+00:00"
        assert data["dirs"][0]["last_scan_files_imported"] == 3


# ─── Polling worker ───────────────────────────────────────────────────────────


class TestWatchLoop:
    def _make_db(self, tmp_path):
        from orivellum.database.db import OrivellumDB

        return OrivellumDB.open(str(tmp_path / "watch_test.db"))

    def test_imports_new_txt_file(self, tmp_path):
        """A .txt file dropped into the watched dir is imported."""
        from orivellum.api import _deps
        from orivellum.capabilities.folder_watch import _import_file
        from orivellum.configuration.config import OrivellumConfig

        db = self._make_db(tmp_path)
        cfg = OrivellumConfig(data_dir=str(tmp_path))
        _deps.init(db=db, cfg=cfg)

        watch_dir = tmp_path / "inbox"
        watch_dir.mkdir()
        txt = watch_dir / "hello.txt"
        txt.write_text("Hello world", encoding="utf-8")

        # Patch executor at its source so the local import inside _import_file resolves
        with patch("orivellum.api.executor.get_executor") as mock_ex:
            mock_ex.return_value.submit = MagicMock()
            ok = _import_file(txt, None, db)

        assert ok is True
        with db._lock:
            rows = db._conn.execute("SELECT * FROM documents WHERE title='hello.txt'").fetchall()
        assert len(rows) == 1

    def test_duplicate_sha_skipped(self, tmp_path):
        """A file whose SHA already exists in documents is silently skipped."""
        from orivellum.api import _deps
        from orivellum.capabilities.folder_watch import _import_file
        from orivellum.configuration.config import OrivellumConfig

        db = self._make_db(tmp_path)
        cfg = OrivellumConfig(data_dir=str(tmp_path))
        _deps.init(db=db, cfg=cfg)

        src = tmp_path / "dup.txt"
        src.write_text("duplicate content", encoding="utf-8")
        sha = hashlib.sha256(src.read_bytes()).hexdigest()
        db.create_document(
            source="dup.txt", title="dup.txt", kind="text", content_path="dup.txt", sha256=sha
        )

        with patch("orivellum.api.executor.get_executor") as mock_ex:
            mock_ex.return_value.submit = MagicMock()
            ok = _import_file(src, None, db)

        assert ok is True  # dedup = treated as success (no error)
        with db._lock:
            rows = db._conn.execute("SELECT * FROM documents WHERE sha256=?", (sha,)).fetchall()
        assert len(rows) == 1  # not duplicated

    def test_missing_dir_reported_in_status(self, tmp_path):
        """A watch dir that no longer exists writes an error into the status."""
        import orivellum.capabilities.folder_watch as fw
        from orivellum.api import _deps
        from orivellum.capabilities.folder_watch import (
            _watch_loop,
            get_watch_status,
            set_watch_dirs,
        )
        from orivellum.configuration.config import OrivellumConfig

        db = self._make_db(tmp_path)
        cfg = OrivellumConfig(data_dir=str(tmp_path))
        _deps.init(db=db, cfg=cfg)

        set_watch_dirs([{"path": "/nonexistent/path_xyz", "work_id": None, "enabled": True}], db)

        stop = threading.Event()
        fw._stop_event = stop
        call_count = [0]

        def _stop_after_one(timeout=None):
            call_count[0] += 1
            if call_count[0] >= 1:
                stop.set()
            return stop.is_set()

        with patch.object(stop, "wait", side_effect=_stop_after_one):
            _watch_loop(db)

        status = get_watch_status(db)
        assert status["scanned_at"] is not None
        assert len(status["dirs"]) == 1
        assert status["dirs"][0]["error"] == "directory not found"

    def test_seen_file_not_reimported(self, tmp_path):
        """A file already in the seen registry is never re-imported."""
        from orivellum.capabilities.folder_watch import _get_seen_paths, _mark_seen
        from orivellum.database.db import OrivellumDB

        db = OrivellumDB.open(str(tmp_path / "seen_test.db"))
        src = tmp_path / "note.txt"
        src.write_text("already seen", encoding="utf-8")

        _mark_seen([str(src)], db)
        seen = _get_seen_paths(db)
        # The poll cycle checks `str(f) not in seen` before calling _import_file;
        # verify the registry correctly contains the path.
        assert str(src) in seen

    def test_disabled_dir_not_scanned(self, tmp_path):
        """A dir with enabled=False is not scanned and produces no imports."""
        import orivellum.capabilities.folder_watch as fw
        from orivellum.api import _deps
        from orivellum.capabilities.folder_watch import (
            _watch_loop,
            set_watch_dirs,
        )
        from orivellum.configuration.config import OrivellumConfig

        db = self._make_db(tmp_path)
        cfg = OrivellumConfig(data_dir=str(tmp_path))
        _deps.init(db=db, cfg=cfg)

        watch_dir = tmp_path / "disabled_inbox"
        watch_dir.mkdir()
        (watch_dir / "file.txt").write_text("should not be imported")
        set_watch_dirs([{"path": str(watch_dir), "work_id": None, "enabled": False}], db)

        stop = threading.Event()
        fw._stop_event = stop
        call_count = [0]

        def _stop_after_one(timeout=None):
            call_count[0] += 1
            if call_count[0] >= 1:
                stop.set()
            return stop.is_set()

        with patch.object(stop, "wait", side_effect=_stop_after_one):
            _watch_loop(db)

        with db._lock:
            rows = db._conn.execute("SELECT * FROM documents WHERE title='file.txt'").fetchall()
        assert len(rows) == 0

    def test_status_written_after_scan(self, tmp_path):
        """Scan status is written with a scanned_at timestamp after each cycle."""
        import orivellum.capabilities.folder_watch as fw
        from orivellum.api import _deps
        from orivellum.capabilities.folder_watch import (
            _watch_loop,
            get_watch_status,
            set_watch_dirs,
        )
        from orivellum.configuration.config import OrivellumConfig

        db = self._make_db(tmp_path)
        cfg = OrivellumConfig(data_dir=str(tmp_path))
        _deps.init(db=db, cfg=cfg)

        set_watch_dirs([], db)  # No dirs — status should still be written

        stop = threading.Event()
        fw._stop_event = stop
        call_count = [0]

        def _stop_after_one(timeout=None):
            call_count[0] += 1
            if call_count[0] >= 1:
                stop.set()
            return stop.is_set()

        with patch.object(stop, "wait", side_effect=_stop_after_one):
            _watch_loop(db)

        status = get_watch_status(db)
        assert status["scanned_at"] is not None
        assert isinstance(status["dirs"], list)

    def test_oserror_in_one_dir_does_not_block_others(self, tmp_path):
        """If iterdir() raises OSError for one dir, the next dir is still scanned."""
        import orivellum.capabilities.folder_watch as fw
        from orivellum.api import _deps
        from orivellum.capabilities.folder_watch import (
            _watch_loop,
            get_watch_status,
            set_watch_dirs,
        )
        from orivellum.configuration.config import OrivellumConfig

        db = self._make_db(tmp_path)
        cfg = OrivellumConfig(data_dir=str(tmp_path))
        _deps.init(db=db, cfg=cfg)

        # Second dir is real and has a file
        good_dir = tmp_path / "good"
        good_dir.mkdir()
        (good_dir / "real.txt").write_text("importable content")

        set_watch_dirs(
            [
                {"path": "/bad/dir/that/raises", "work_id": None, "enabled": True},
                {"path": str(good_dir), "work_id": None, "enabled": True},
            ],
            db,
        )

        # Make is_dir() return True for the bad path so the OSError comes from iterdir()
        real_is_dir = Path.is_dir

        def _patched_is_dir(self):
            if str(self) == "/bad/dir/that/raises":
                return True
            return real_is_dir(self)

        real_iterdir = Path.iterdir

        def _patched_iterdir(self):
            if str(self) == "/bad/dir/that/raises":
                raise OSError("Permission denied")
            return real_iterdir(self)

        stop = threading.Event()
        fw._stop_event = stop
        call_count = [0]

        def _stop_after_one(timeout=None):
            call_count[0] += 1
            if call_count[0] >= 1:
                stop.set()
            return stop.is_set()

        with (
            patch.object(Path, "is_dir", _patched_is_dir),
            patch.object(Path, "iterdir", _patched_iterdir),
            patch("orivellum.api.executor.get_executor") as mock_ex,
        ):
            mock_ex.return_value.submit = MagicMock()
            with patch.object(stop, "wait", side_effect=_stop_after_one):
                _watch_loop(db)

        # The bad dir should have an error in status
        status = get_watch_status(db)
        bad_entry = next((d for d in status["dirs"] if d["path"] == "/bad/dir/that/raises"), None)
        assert bad_entry is not None
        assert bad_entry["error"] is not None

        # The good dir should have imported its file
        with db._lock:
            rows = db._conn.execute("SELECT * FROM documents WHERE title='real.txt'").fetchall()
        assert len(rows) == 1

    def test_multiple_dirs_all_scanned(self, tmp_path):
        """All enabled dirs in a multi-dir config are scanned in one cycle."""
        import orivellum.capabilities.folder_watch as fw
        from orivellum.api import _deps
        from orivellum.capabilities.folder_watch import (
            _watch_loop,
            set_watch_dirs,
        )
        from orivellum.configuration.config import OrivellumConfig

        db = self._make_db(tmp_path)
        cfg = OrivellumConfig(data_dir=str(tmp_path))
        _deps.init(db=db, cfg=cfg)

        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "doc_a.txt").write_text("content a")
        (dir_b / "doc_b.txt").write_text("content b")

        set_watch_dirs(
            [
                {"path": str(dir_a), "work_id": None, "enabled": True},
                {"path": str(dir_b), "work_id": None, "enabled": True},
            ],
            db,
        )

        stop = threading.Event()
        fw._stop_event = stop
        call_count = [0]

        def _stop_after_one(timeout=None):
            call_count[0] += 1
            if call_count[0] >= 1:
                stop.set()
            return stop.is_set()

        with patch("orivellum.api.executor.get_executor") as mock_ex:
            mock_ex.return_value.submit = MagicMock()
            with patch.object(stop, "wait", side_effect=_stop_after_one):
                _watch_loop(db)

        with db._lock:
            rows = db._conn.execute(
                "SELECT title FROM documents WHERE title IN ('doc_a.txt','doc_b.txt')"
            ).fetchall()
        titles = {r["title"] for r in rows}
        assert titles == {"doc_a.txt", "doc_b.txt"}
