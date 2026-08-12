"""Tests for scripts/db_reset.py and scripts/db_restore.py.

Covers:
  - dry-run report on a seeded in-memory-like DB (file-backed temp DB)
  - backup produces readable manifest with correct checksums
  - wipe + re-import round-trips all user-data rows
  - re-imported row counts match manifest
  - FK check passes after re-import
  - log tables (access_log, outbox) are cleared and not re-imported
  - restore script round-trips a backup folder
  - topological sort puts parents before children
  - classification logic
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

# Add scripts/ to sys.path so we can import db_reset
_scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

import base64

import db_reset as dr


# ---------------------------------------------------------------------------
# Minimal test schema with FK relationships and a BLOB column
# ---------------------------------------------------------------------------

_SETUP_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS settings (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL DEFAULT 'global',
    key TEXT NOT NULL,
    value TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(scope, key)
);

CREATE TABLE IF NOT EXISTS objects (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS works (
    id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
    title TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
    work_id TEXT REFERENCES works(id) ON DELETE SET NULL,
    title TEXT
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    work_id TEXT REFERENCES works(id) ON DELETE SET NULL,
    title TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    text TEXT NOT NULL
);

-- Operational log tables
CREATE TABLE IF NOT EXISTS access_log (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL,
    ts TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Derived cache
CREATE TABLE IF NOT EXISTS work_gap_cache (
    id TEXT PRIMARY KEY,
    work_id TEXT NOT NULL,
    data TEXT NOT NULL,
    evaluated_at TEXT NOT NULL
);

-- Table with a BLOB column (mirrors real minhash_sig / vectors tables)
CREATE TABLE IF NOT EXISTS embeddings (
    id TEXT PRIMARY KEY REFERENCES objects(id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    embedding BLOB NOT NULL
);

-- FTS virtual table
CREATE VIRTUAL TABLE IF NOT EXISTS works_fts USING fts5(title, work_id UNINDEXED);
"""


def _make_test_db(tmp: str) -> tuple[sqlite3.Connection, str]:
    """Create a file-backed test DB with seed data. Returns (conn, db_path)."""
    db_path = os.path.join(tmp, "test.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SETUP_SQL)
    conn.execute("PRAGMA foreign_keys=ON")

    now = "2026-01-01T00:00:00+00:00"

    # Settings
    conn.execute("INSERT INTO settings VALUES('s1','global','schema_version','42',?)", (now,))

    # Objects
    for oid, otype in [("o1","work"), ("o2","document"), ("o3","chunk"), ("o4","document")]:
        conn.execute("INSERT INTO objects VALUES(?,?,1,?)", (oid, otype, now))

    # Works
    conn.execute("INSERT INTO works VALUES('o1','The Great Work')")

    # Documents
    conn.execute("INSERT INTO documents VALUES('o2','o1','Chapter One')")
    conn.execute("INSERT INTO documents VALUES('o4',NULL,'Orphan Doc')")

    # Chunks
    conn.execute("INSERT INTO chunks VALUES('o3','o2','Some chunk text')")

    # Conversations + messages
    conn.execute("INSERT INTO conversations VALUES('c1','o1','Test Convo')")
    conn.execute("INSERT INTO messages VALUES('m1','c1','Hello world')")
    conn.execute("INSERT INTO messages VALUES('m2','c1','Goodbye world')")

    # Operational log rows (should NOT be re-imported)
    conn.execute("INSERT INTO access_log VALUES('al1','/api/health',?)", (now,))
    conn.execute("INSERT INTO access_log VALUES('al2','/api/works',?)", (now,))
    conn.execute("INSERT INTO outbox VALUES('ob1','{\"event\":\"test\"}',?)", (now,))

    # Derived cache (should BE re-imported)
    conn.execute("INSERT INTO work_gap_cache VALUES('wgc1','o1','{\"gaps\":[]}',?)", (now,))

    # BLOB column — mimics vectors.embedding / minhash_sig.sig
    # 'o5' object needed as parent for embeddings row
    conn.execute("INSERT INTO objects VALUES('o5','embedding',1,?)", (now,))
    blob_data = bytes(range(16))  # 16 bytes: \x00\x01…\x0f
    conn.execute("INSERT INTO embeddings VALUES('o5','o2',?)", (blob_data,))

    # FTS
    conn.execute("INSERT INTO works_fts VALUES('The Great Work','o1')")

    conn.commit()
    conn.close()

    # Reopen with settings matching _open_db
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn, db_path


class TestClassification(unittest.TestCase):

    def test_log_operational(self):
        self.assertEqual(dr.classify_table("access_log", set()), "log_operational")
        self.assertEqual(dr.classify_table("outbox", set()), "log_operational")

    def test_derived_cache(self):
        self.assertEqual(dr.classify_table("work_gap_cache", set()), "derived_cache")
        self.assertEqual(dr.classify_table("minhash_sig", set()), "derived_cache")

    def test_fts_virtual(self):
        fts = {"works_fts", "chunks_fts"}
        self.assertEqual(dr.classify_table("works_fts", fts), "fts_virtual")
        self.assertEqual(dr.classify_table("chunks_fts", fts), "fts_virtual")

    def test_user_data(self):
        self.assertEqual(dr.classify_table("works", set()), "user_data")
        self.assertEqual(dr.classify_table("objects", set()), "user_data")
        self.assertEqual(dr.classify_table("audit_log", set()), "user_data")


class TestTableDiscovery(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn, self.db_path = _make_test_db(self.tmp)

    def tearDown(self):
        self.conn.close()

    def test_tables_excludes_sqlite_internal(self):
        tables, _, _ = dr._get_tables(self.conn)
        self.assertNotIn("sqlite_sequence", tables)
        self.assertNotIn("sqlite_stat1", tables)

    def test_tables_excludes_fts_shadows(self):
        tables, _, _ = dr._get_tables(self.conn)
        # FTS shadow tables should be excluded
        for sfx in dr._FTS_SHADOW_SUFFIXES:
            self.assertNotIn(f"works_fts{sfx}", tables,
                             f"Shadow table works_fts{sfx} should be excluded")

    def test_tables_includes_fts_virtual(self):
        tables, fts_virtual, _ = dr._get_tables(self.conn)
        self.assertIn("works_fts", tables)
        self.assertIn("works_fts", fts_virtual)

    def test_tables_includes_all_user_tables(self):
        tables, _, _ = dr._get_tables(self.conn)
        expected = {"settings", "objects", "works", "documents", "chunks",
                    "conversations", "messages", "access_log", "outbox",
                    "work_gap_cache", "embeddings", "works_fts"}
        for t in expected:
            self.assertIn(t, tables, f"Expected table '{t}' not found")


class TestTopologicalSort(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn, self.db_path = _make_test_db(self.tmp)

    def tearDown(self):
        self.conn.close()

    def test_parents_before_children(self):
        tables, _, _ = dr._get_tables(self.conn)
        deps = dr._build_fk_graph(self.conn, tables)
        ordered = dr.topo_sort(tables, deps)

        idx = {t: i for i, t in enumerate(ordered)}
        # objects must come before works (works FK → objects)
        self.assertLess(idx["objects"], idx["works"],
                        "objects must appear before works in insert order")
        # works must come before documents
        self.assertLess(idx["works"], idx["documents"])
        # documents must come before chunks
        self.assertLess(idx["documents"], idx["chunks"])
        # conversations before messages
        self.assertLess(idx["conversations"], idx["messages"])

    def test_all_tables_included(self):
        tables, _, _ = dr._get_tables(self.conn)
        deps = dr._build_fk_graph(self.conn, tables)
        ordered = dr.topo_sort(tables, deps)
        self.assertEqual(set(ordered), set(tables))


class TestBackupExporter(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn, self.db_path = _make_test_db(self.tmp)

    def tearDown(self):
        self.conn.close()

    def _make_backup(self) -> tuple[Path, dict]:
        tables, fts_virtual, _ = dr._get_tables(self.conn)
        deps = dr._build_fk_graph(self.conn, tables)
        tables_sorted = dr.topo_sort(tables, deps)
        backup_dir = Path(self.tmp) / "backup"
        manifest = dr.export_backup(self.conn, backup_dir, tables_sorted, fts_virtual)
        return backup_dir, manifest

    def test_backup_creates_manifest(self):
        backup_dir, manifest = self._make_backup()
        self.assertTrue((backup_dir / "manifest.json").exists())
        self.assertIn("tables", manifest)
        self.assertIn("created_at", manifest)

    def test_backup_creates_schema_sql(self):
        backup_dir, _ = self._make_backup()
        self.assertTrue((backup_dir / "schema.sql").exists())
        schema = (backup_dir / "schema.sql").read_text()
        self.assertIn("CREATE TABLE", schema)

    def test_backup_creates_readme(self):
        backup_dir, _ = self._make_backup()
        self.assertTrue((backup_dir / "readme.txt").exists())
        readme = (backup_dir / "readme.txt").read_text()
        self.assertIn("db_restore.py", readme)

    def test_backup_json_per_table(self):
        backup_dir, manifest = self._make_backup()
        for t in manifest["tables"]:
            fpath = backup_dir / t["file"]
            self.assertTrue(fpath.exists(), f"JSON file missing for table {t['name']}")
            rows = json.loads(fpath.read_text(encoding="utf-8"))
            self.assertIsInstance(rows, list)

    def test_manifest_row_counts_correct(self):
        backup_dir, manifest = self._make_backup()
        by_name = {t["name"]: t for t in manifest["tables"]}
        self.assertEqual(by_name["works"]["row_count"], 1)
        self.assertEqual(by_name["documents"]["row_count"], 2)
        self.assertEqual(by_name["chunks"]["row_count"], 1)
        self.assertEqual(by_name["messages"]["row_count"], 2)
        self.assertEqual(by_name["access_log"]["row_count"], 2)
        self.assertEqual(by_name["outbox"]["row_count"], 1)

    def test_checksums_are_correct(self):
        backup_dir, manifest = self._make_backup()
        for t in manifest["tables"]:
            fpath = backup_dir / t["file"]
            actual = hashlib.sha256(fpath.read_bytes()).hexdigest()
            self.assertEqual(actual, t["sha256"],
                             f"SHA-256 mismatch for {t['name']}")

    def test_verify_manifest_passes(self):
        backup_dir, _ = self._make_backup()
        # Should not raise
        result = dr.verify_manifest(backup_dir)
        self.assertIn("tables", result)

    def test_verify_manifest_catches_tampered_file(self):
        backup_dir, manifest = self._make_backup()
        # Tamper with one file
        fp = backup_dir / "tables" / "works.json"
        fp.write_text('[{"id":"TAMPERED"}]', encoding="utf-8")
        with self.assertRaises(RuntimeError) as ctx:
            dr.verify_manifest(backup_dir)
        self.assertIn("mismatch", str(ctx.exception).lower())


class TestWipeAndReimport(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn, self.db_path = _make_test_db(self.tmp)
        tables, fts_virtual, _ = dr._get_tables(self.conn)
        deps = dr._build_fk_graph(self.conn, tables)
        self.tables_sorted = dr.topo_sort(tables, deps)
        self.fts_virtual = fts_virtual

        self.backup_dir = Path(self.tmp) / "backup"
        self.manifest = dr.export_backup(
            self.conn, self.backup_dir, self.tables_sorted, self.fts_virtual
        )

    def tearDown(self):
        self.conn.close()

    def _cycle(self):
        """Wipe then re-import, return import stats."""
        dr.wipe_database(self.conn, self.tables_sorted)
        return dr.import_from_backup(
            self.conn, self.tables_sorted, self.backup_dir,
            self.fts_virtual, self.manifest,
        )

    def test_wipe_clears_all_tables(self):
        dr.wipe_database(self.conn, self.tables_sorted)
        for table in self.tables_sorted:
            n = self.conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            self.assertEqual(n, 0, f"Table '{table}' not empty after wipe")

    def test_reimport_restores_user_data_rows(self):
        stats = self._cycle()
        # works: 1 row
        n = self.conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
        self.assertEqual(n, 1)
        # documents: 2 rows
        n = self.conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        self.assertEqual(n, 2)
        # chunks: 1 row
        n = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        self.assertEqual(n, 1)
        # messages: 2 rows
        n = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(n, 2)

    def test_reimport_counts_match_manifest(self):
        stats = self._cycle()
        by_name = {t["name"]: t for t in self.manifest["tables"]}
        for table, s in stats.items():
            if s.get("skipped") and s.get("reason") == "log_operational":
                continue  # log tables intentionally not imported
            expected = by_name.get(table, {}).get("row_count", 0)
            live = self.conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            self.assertEqual(live, expected,
                             f"Row count mismatch for '{table}': expected {expected}, got {live}")

    def test_log_tables_cleared_not_reimported(self):
        stats = self._cycle()
        # access_log and outbox must be 0 rows
        for log_table in ("access_log", "outbox"):
            n = self.conn.execute(f'SELECT COUNT(*) FROM "{log_table}"').fetchone()[0]
            self.assertEqual(n, 0, f"Log table '{log_table}' must be empty after reset")

        # Confirm stats reflect the intent
        self.assertTrue(stats["access_log"]["skipped"])
        self.assertEqual(stats["access_log"]["reason"], "log_operational")
        self.assertEqual(stats["access_log"]["imported"], 0)
        # exported should match original row count from manifest
        self.assertEqual(stats["access_log"]["exported"], 2)

    def test_derived_cache_is_reimported(self):
        stats = self._cycle()
        n = self.conn.execute("SELECT COUNT(*) FROM work_gap_cache").fetchone()[0]
        self.assertEqual(n, 1, "work_gap_cache (derived_cache) should be re-imported")
        self.assertFalse(stats["work_gap_cache"]["skipped"])

    def test_fk_check_passes_after_reimport(self):
        self._cycle()
        violations = self.conn.execute("PRAGMA foreign_key_check").fetchall()
        self.assertEqual(len(violations), 0,
                         f"FK violations after reimport: {[dict(v) for v in violations]}")

    def test_integrity_check_passes(self):
        self._cycle()
        result = self.conn.execute("PRAGMA integrity_check").fetchall()
        self.assertEqual(result[0][0], "ok")

    def test_data_content_preserved(self):
        """Round-trip verifies actual row content, not just counts."""
        self._cycle()
        row = self.conn.execute(
            "SELECT title FROM works WHERE id='o1'"
        ).fetchone()
        self.assertIsNotNone(row, "works row o1 must be present after reimport")
        self.assertEqual(row[0], "The Great Work")

        msg = self.conn.execute(
            "SELECT text FROM messages WHERE id='m1'"
        ).fetchone()
        self.assertIsNotNone(msg)
        self.assertEqual(msg[0], "Hello world")


class TestValidation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn, self.db_path = _make_test_db(self.tmp)
        tables, fts_virtual, _ = dr._get_tables(self.conn)
        deps = dr._build_fk_graph(self.conn, tables)
        self.tables_sorted = dr.topo_sort(tables, deps)
        self.fts_virtual = fts_virtual
        self.backup_dir = Path(self.tmp) / "backup"
        self.manifest = dr.export_backup(
            self.conn, self.backup_dir, self.tables_sorted, self.fts_virtual
        )

    def tearDown(self):
        self.conn.close()

    def test_validation_passes_after_clean_cycle(self):
        dr.wipe_database(self.conn, self.tables_sorted)
        dr.import_from_backup(
            self.conn, self.tables_sorted, self.backup_dir,
            self.fts_virtual, self.manifest,
        )
        result = dr.validate_database(
            self.conn, self.tables_sorted, self.manifest, self.fts_virtual, "42"
        )
        self.assertTrue(result["ok"], f"Validation errors: {result['errors']}")
        self.assertEqual(result["errors"], [])

    def test_validation_catches_row_count_mismatch(self):
        dr.wipe_database(self.conn, self.tables_sorted)
        dr.import_from_backup(
            self.conn, self.tables_sorted, self.backup_dir,
            self.fts_virtual, self.manifest,
        )
        # Secretly insert an extra row that wasn't in the backup
        self.conn.execute(
            "INSERT INTO conversations VALUES('EXTRA',NULL,'extra convo')"
        )
        self.conn.commit()

        result = dr.validate_database(
            self.conn, self.tables_sorted, self.manifest, self.fts_virtual, "42"
        )
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("conversations" in e for e in result["errors"]),
            f"Expected conversations mismatch in errors: {result['errors']}"
        )


class TestRestoreRoundTrip(unittest.TestCase):
    """db_restore.py uses the same wipe+import+validate pipeline."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn, self.db_path = _make_test_db(self.tmp)
        tables, fts_virtual, _ = dr._get_tables(self.conn)
        deps = dr._build_fk_graph(self.conn, tables)
        self.tables_sorted = dr.topo_sort(tables, deps)
        self.fts_virtual = fts_virtual
        self.backup_dir = Path(self.tmp) / "backup"
        self.manifest = dr.export_backup(
            self.conn, self.backup_dir, self.tables_sorted, self.fts_virtual
        )

    def tearDown(self):
        self.conn.close()

    def test_restore_round_trip(self):
        """Backup → wipe → restore via the same pipeline → validate."""
        # Wipe live DB, simulating a corrupted state
        dr.wipe_database(self.conn, self.tables_sorted)
        n = self.conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
        self.assertEqual(n, 0, "Works must be empty after wipe")

        # Restore from backup using the same library functions
        dr.verify_manifest(self.backup_dir)
        dr.import_from_backup(
            self.conn, self.tables_sorted, self.backup_dir,
            self.fts_virtual, self.manifest,
        )

        # Validate
        result = dr.validate_database(
            self.conn, self.tables_sorted, self.manifest, self.fts_virtual, "42"
        )
        self.assertTrue(result["ok"], f"Restore validation errors: {result['errors']}")

        # Check actual data
        n = self.conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
        self.assertEqual(n, 1)
        n = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(n, 2)

    def test_restore_catches_tampered_backup(self):
        """verify_manifest must raise before we touch the database."""
        # Tamper with the backup
        fp = self.backup_dir / "tables" / "works.json"
        fp.write_text('[{"id":"TAMPERED","title":"evil"}]', encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            dr.verify_manifest(self.backup_dir)
        self.assertIn("mismatch", str(ctx.exception).lower())


class TestBlobRoundTrip(unittest.TestCase):
    """BLOB columns must survive JSON serialization and re-import without loss.

    SQLite BLOB columns return Python bytes, which are not JSON serializable
    by default.  db_reset.py uses tagged base64 encoding:
      {"__blob__": "<base64>"}
    and _revive_row() decodes them back on import.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn, self.db_path = _make_test_db(self.tmp)
        tables, fts_virtual, _ = dr._get_tables(self.conn)
        deps = dr._build_fk_graph(self.conn, tables)
        self.tables_sorted = dr.topo_sort(tables, deps)
        self.fts_virtual = fts_virtual
        self.backup_dir = Path(self.tmp) / "backup"
        self.manifest = dr.export_backup(
            self.conn, self.backup_dir, self.tables_sorted, self.fts_virtual
        )

    def tearDown(self):
        self.conn.close()

    def test_blob_column_serialized_as_tagged_base64(self):
        """The JSON file must not contain raw bytes — it must use {__blob__: ...}."""
        json_path = self.backup_dir / "tables" / "embeddings.json"
        self.assertTrue(json_path.exists(), "embeddings.json must be in backup")
        raw = json_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        self.assertEqual(len(data), 1, "One embedding row was inserted")
        val = data[0]["embedding"]
        self.assertIsInstance(val, dict, "BLOB must be encoded as a dict")
        self.assertIn("__blob__", val, "Must use __blob__ tag")
        # Verify the base64 decodes to the original bytes
        decoded = base64.b64decode(val["__blob__"])
        self.assertEqual(decoded, bytes(range(16)))

    def test_blob_roundtrips_correctly(self):
        """After wipe + reimport the BLOB value must equal the original bytes."""
        dr.wipe_database(self.conn, self.tables_sorted)
        dr.import_from_backup(
            self.conn, self.tables_sorted, self.backup_dir,
            self.fts_virtual, self.manifest,
        )
        row = self.conn.execute(
            "SELECT embedding FROM embeddings WHERE id='o5'"
        ).fetchone()
        self.assertIsNotNone(row, "embeddings row must be present after reimport")
        self.assertIsInstance(row[0], bytes, "embedding column must come back as bytes")
        self.assertEqual(row[0], bytes(range(16)),
                         "BLOB value must survive the round-trip unchanged")

    def test_json_dumps_does_not_crash_on_blob(self):
        """_json_default must prevent TypeError for any table with BLOB columns."""
        rows = dr._export_table_rows(self.conn, "embeddings")
        # This must not raise TypeError
        serialized = json.dumps(rows, default=dr._json_default)
        self.assertIn("__blob__", serialized)

    def test_revive_row_decodes_tagged_blob(self):
        """_revive_row must convert {"__blob__": "<b64>"} back to bytes."""
        original = bytes([0xFF, 0x00, 0xAB, 0xCD])
        tagged = {"id": "x", "data": {"__blob__": base64.b64encode(original).decode()}}
        revived = dr._revive_row(tagged)
        self.assertEqual(revived["data"], original)
        self.assertEqual(revived["id"], "x")  # non-blob fields unchanged

    def test_revive_row_leaves_non_blob_dicts_intact(self):
        """A regular dict value must not be mistakenly treated as a blob."""
        row = {"id": "x", "meta": {"foo": "bar", "baz": 42}}
        revived = dr._revive_row(row)
        self.assertEqual(revived["meta"], {"foo": "bar", "baz": 42})


class TestSchemaCompatibility(unittest.TestCase):
    """check_restore_compatibility must raise before any destructive step
    when the backup references tables absent from the target schema."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.conn, self.db_path = _make_test_db(self.tmp)
        tables, fts_virtual, _ = dr._get_tables(self.conn)
        deps = dr._build_fk_graph(self.conn, tables)
        self.tables_sorted = dr.topo_sort(tables, deps)
        self.fts_virtual = fts_virtual
        self.backup_dir = Path(self.tmp) / "backup"
        self.manifest = dr.export_backup(
            self.conn, self.backup_dir, self.tables_sorted, self.fts_virtual
        )

    def tearDown(self):
        self.conn.close()

    def test_compatible_schema_passes(self):
        """A backup taken from the same schema must not raise."""
        # Should not raise
        dr.check_restore_compatibility(self.conn, self.manifest, self.fts_virtual)

    def test_missing_table_raises_before_wipe(self):
        """If the backup has a table the target DB does not, raise RuntimeError."""
        # Inject a fake table entry into the manifest
        fake_manifest = {
            **self.manifest,
            "tables": self.manifest["tables"] + [
                {
                    "name": "future_table_v999",
                    "row_count": 5,
                    "sha256": "abc",
                    "file": "tables/future_table_v999.json",
                    "classification": "user_data",
                }
            ],
        }
        with self.assertRaises(RuntimeError) as ctx:
            dr.check_restore_compatibility(self.conn, fake_manifest, self.fts_virtual)
        err = str(ctx.exception)
        self.assertIn("future_table_v999", err)
        self.assertIn("absent", err.lower())

    def test_missing_log_table_does_not_raise(self):
        """Log-operational tables are cleared and never re-imported, so
        a missing log table in the target must NOT block the restore."""
        fake_manifest = {
            **self.manifest,
            "tables": self.manifest["tables"] + [
                {
                    "name": "ghost_access_log",
                    "row_count": 0,
                    "sha256": "abc",
                    "file": "tables/ghost_access_log.json",
                    "classification": "log_operational",
                }
            ],
        }
        # Must not raise — log tables are skipped
        dr.check_restore_compatibility(self.conn, fake_manifest, self.fts_virtual)

    def test_database_unchanged_when_check_fails(self):
        """The DB must still be intact (all rows present) after a failed check."""
        fake_manifest = {
            **self.manifest,
            "tables": self.manifest["tables"] + [
                {
                    "name": "nonexistent_table",
                    "row_count": 1,
                    "sha256": "abc",
                    "file": "tables/nonexistent_table.json",
                    "classification": "user_data",
                }
            ],
        }
        with self.assertRaises(RuntimeError):
            dr.check_restore_compatibility(self.conn, fake_manifest, self.fts_virtual)

        # DB must still have all original rows
        n = self.conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
        self.assertEqual(n, 1, "works table must be intact after failed compatibility check")
        n = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        self.assertEqual(n, 2, "messages table must be intact after failed compatibility check")


if __name__ == "__main__":
    unittest.main()
