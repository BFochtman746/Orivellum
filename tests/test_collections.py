"""Tests for THE RE-PROJECTION Phases 0-2 (task: collections + batch demotion).

Covers:
1. Migration v144 — demotes A01_MIGRATION_BATCH_* Works into collection rows
   while leaving the substrate (documents / chunks / vectors) byte-for-byte
   untouched: identical counts before and after.
2. Phase 0 — a VERIFIED pre-migration backup is written before any schema
   mutation of an existing database.
3. "A collection is never a subject" — refusal guards on curriculum seeding,
   book-pipeline entry, and knowledge-harvest scoping.
4. Forward path — ZIP explosion and folder watch create collection rows and
   stamp collection_id on the documents they import.
5. GET /api/library/collections provenance endpoint.
"""

from __future__ import annotations

import io
import sqlite3
import sys
import uuid
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from orivellum.capabilities.extraction import ExtractionResult
from orivellum.database.db import OrivellumDB

BATCH_TITLE = "A01_MIGRATION_BATCH_011_EXCEL365_BIBLE_VAULT_v1.0.0"

# ── Helpers ────────────────────────────────────────────────────────────────────


def _insert_chunk(db: OrivellumDB, doc_id: str) -> str:
    cid = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            "INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,"
            "created_at,updated_at,created_by) "
            "VALUES(?,?,1,'draft','{}','{}','2026-01-01','2026-01-01','user')",
            (cid, "chunk"),
        )
        db._conn.execute(
            "INSERT INTO chunks(id,doc_id,page,text,created_at) VALUES(?,?,1,?,'2026-01-01')",
            (cid, doc_id, "chunk text"),
        )
        db._conn.commit()
    return cid


def _insert_vector(db: OrivellumDB, object_id: str) -> None:
    with db._lock:
        db._conn.execute(
            "INSERT INTO vectors(id,object_id,object_type,embedding,dim,created_at) "
            "VALUES(?,?,?,?,4,'2026-01-01')",
            (str(uuid.uuid4()), object_id, "chunk", b"\x00" * 16),
        )
        db._conn.commit()


def _counts(db_path: str) -> dict:
    conn = sqlite3.connect(db_path)
    try:
        return {
            "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "vectors": conn.execute("SELECT COUNT(*) FROM vectors").fetchone()[0],
            "knowledge": conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0],
        }
    finally:
        conn.close()


def _downgrade_schema_version(db_path: str, version: int) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE settings SET value=? WHERE scope='global' AND key='schema_version'",
            (str(version),),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_work_domain_rows(db: OrivellumDB, work_id: str) -> dict:
    """Seed one row of every object-backed cascade class + two plain cascades.

    Returns the object ids of the object-backed children so tests can assert
    that BOTH the child row and its objects parent are removed (no
    governed-object ghosts).
    """
    task = db.create_task(work_id=work_id, text="fake task")
    pipeline = db.create_book_pipeline(work_id, "Fake Pipeline")
    now = "2026-01-01"
    chap_id, pub_id = str(uuid.uuid4()), str(uuid.uuid4())
    with db._lock:
        for oid, otype in ((chap_id, "book_chapter"), (pub_id, "publication")):
            db._conn.execute(
                "INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,"
                "created_at,updated_at,created_by) VALUES(?,?,1,'draft','{}','{}',?,?,'user')",
                (oid, otype, now, now),
            )
        db._conn.execute(
            "INSERT INTO book_chapters(id,work_id,created_at,updated_at) VALUES(?,?,?,?)",
            (chap_id, work_id, now, now),
        )
        db._conn.execute(
            "INSERT INTO publications(id,work_id,format,created_at) VALUES(?,?,'epub',?)",
            (pub_id, work_id, now),
        )
        # Plain (non-object-backed) cascade dependents.
        db._conn.execute(
            "INSERT INTO learning_concepts(id,work_id,name,created_at) VALUES(?,?,?,?)",
            (str(uuid.uuid4()), work_id, "fake concept", now),
        )
        db._conn.execute(
            "INSERT INTO graph_node(id,work_id,node_type,name,evidence_quote,"
            "evidence_offset,created_at) VALUES(?,?,?,?,?,0,?)",
            (str(uuid.uuid4()), work_id, "Concept", "fake node", "quote", now),
        )
        db._conn.commit()
    return {
        "object_backed_ids": [task["id"], pipeline["id"], chap_id, pub_id],
    }


def _seed_batch_corpus(db: OrivellumDB) -> dict:
    """Create one fake batch Work + one real Work, each with substrate."""
    batch = db.create_work(title=BATCH_TITLE, work_type="research")
    real = db.create_work(title="The Moses Trilogy", work_type="book")

    batch_docs, real_docs = [], []
    for i in range(3):
        doc = db.create_document(
            title=f"batch-doc-{i}", sha256=f"batchsha{i}", kind="text", work_id=batch["id"]
        )
        batch_docs.append(doc["id"])
        cid = _insert_chunk(db, doc["id"])
        _insert_vector(db, cid)
        db.create_knowledge_item(
            work_id=batch["id"],
            kind="concept",
            text=f"batch item {i}",
            source_doc_id=doc["id"],
        )
    doc = db.create_document(title="real-doc", sha256="realsha", kind="text", work_id=real["id"])
    real_docs.append(doc["id"])
    _insert_chunk(db, doc["id"])
    db.create_knowledge_item(
        work_id=real["id"], kind="concept", text="real item", source_doc_id=doc["id"]
    )
    # Derived Work-domain rows on BOTH Works: the batch Work's must vanish
    # cleanly (no orphan objects), the real Work's must be untouched.
    batch_domain = _seed_work_domain_rows(db, batch["id"])
    real_domain = _seed_work_domain_rows(db, real["id"])
    return {
        "batch_work_id": batch["id"],
        "real_work_id": real["id"],
        "batch_docs": batch_docs,
        "real_docs": real_docs,
        "batch_object_backed": batch_domain["object_backed_ids"],
        "real_object_backed": real_domain["object_backed_ids"],
    }


# ── 1+2. Migration demotion, substrate invariant, verified backup ─────────────


class TestBatchDemotionMigration:
    def _run_demotion(self, tmp_path: Path) -> tuple[str, dict, dict, OrivellumDB]:
        db_path = str(tmp_path / "demote.db")
        db = OrivellumDB(db_path)
        ids = _seed_batch_corpus(db)
        db.close()

        before = _counts(db_path)
        _downgrade_schema_version(db_path, 143)
        db2 = OrivellumDB(db_path)  # replays v144 → demotion runs
        return db_path, ids, before, db2

    def test_substrate_counts_identical_before_and_after(self, tmp_path: Path) -> None:
        db_path, _, before, db2 = self._run_demotion(tmp_path)
        after = _counts(db_path)
        db2.close()
        assert after == before, f"substrate changed: {before} -> {after}"

    def test_batch_work_becomes_collection_and_is_deleted(self, tmp_path: Path) -> None:
        db_path, ids, _, db2 = self._run_demotion(tmp_path)
        try:
            # Work is gone (works row AND objects row), real Work untouched.
            assert db2.get_work(ids["batch_work_id"]) is None
            assert db2.get_work(ids["real_work_id"]) is not None
            with db2._lock:
                obj = db2._conn.execute(
                    "SELECT 1 FROM objects WHERE id=?", (ids["batch_work_id"],)
                ).fetchone()
            assert obj is None, "objects row for demoted Work must be deleted"

            # Collection row reuses the Work's id and label.
            coll = db2.get_collection(ids["batch_work_id"])
            assert coll is not None
            assert coll["label"] == BATCH_TITLE
            assert coll["source_kind"] == "zip"
            assert coll["meta"].get("demoted_from_work") is True

            # Batch docs: collection_id set, work_id NULL.
            for doc_id in ids["batch_docs"]:
                doc = db2.get_document(doc_id)
                assert doc["work_id"] is None
                assert doc["collection_id"] == ids["batch_work_id"]
            # Real doc keeps its Work and gains no collection.
            real_doc = db2.get_document(ids["real_docs"][0])
            assert real_doc["work_id"] == ids["real_work_id"]
            assert real_doc["collection_id"] is None

            # Knowledge rows all survive; batch ones lose the fake scope.
            with db2._lock:
                scoped = db2._conn.execute(
                    "SELECT COUNT(*) FROM knowledge WHERE work_id=?",
                    (ids["batch_work_id"],),
                ).fetchone()[0]
            assert scoped == 0
        finally:
            db2.close()

    def test_cascade_children_removed_without_object_ghosts(self, tmp_path: Path) -> None:
        """Derived Work-domain rows of the batch Work vanish cleanly.

        Object-backed cascade children (tasks, publications, book_chapters,
        book_pipelines) must lose BOTH their child row and their objects
        parent — a works-side cascade alone would leave governed-object
        ghosts.  Plain cascade rows (learning_concepts, graph_node) must be
        gone too.  The real Work's rows must all survive.
        """
        _, ids, _, db2 = self._run_demotion(tmp_path)
        try:
            with db2._lock:
                for oid in ids["batch_object_backed"]:
                    obj = db2._conn.execute("SELECT 1 FROM objects WHERE id=?", (oid,)).fetchone()
                    assert obj is None, f"orphaned objects row for cascade child {oid}"
                for oid in ids["real_object_backed"]:
                    obj = db2._conn.execute("SELECT 1 FROM objects WHERE id=?", (oid,)).fetchone()
                    assert obj is not None, f"real Work child {oid} was deleted"
                for table in (
                    "tasks",
                    "publications",
                    "book_chapters",
                    "book_pipelines",
                    "learning_concepts",
                    "graph_node",
                ):
                    n_batch = db2._conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE work_id=?",  # noqa: S608
                        (ids["batch_work_id"],),
                    ).fetchone()[0]
                    assert n_batch == 0, f"{table} still has batch-Work rows"
                    n_real = db2._conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE work_id=?",  # noqa: S608
                        (ids["real_work_id"],),
                    ).fetchone()[0]
                    assert n_real == 1, f"{table} lost the real Work's row"
                # No dangling FK references anywhere after the demotion.
                violations = db2._conn.execute("PRAGMA foreign_key_check").fetchall()
                assert violations == [], f"FK violations after demotion: {violations}"
        finally:
            db2.close()

    def test_migration_is_replay_safe(self, tmp_path: Path) -> None:
        """Re-running v144 on an already-demoted DB must be a no-op."""
        db_path, ids, before, db2 = self._run_demotion(tmp_path)
        db2.close()
        _downgrade_schema_version(db_path, 143)
        db3 = OrivellumDB(db_path)
        try:
            after = _counts(db_path)
            assert after == before
            with db3._lock:
                n_coll = db3._conn.execute("SELECT COUNT(*) FROM collection").fetchone()[0]
            assert n_coll == 1
        finally:
            db3.close()

    def test_verified_backup_written_before_migration(self, tmp_path: Path) -> None:
        db_path, _, _, db2 = self._run_demotion(tmp_path)
        db2.close()
        backups = list((tmp_path / "backups").glob("pre-migration-v144-*.db"))
        assert backups, "pre-migration backup must exist"
        # The backup itself must be a valid database containing the corpus.
        conn = sqlite3.connect(f"file:{backups[0]}?mode=ro", uri=True)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 4
        finally:
            conn.close()

    def test_fresh_database_skips_backup(self, tmp_path: Path) -> None:
        db = OrivellumDB(str(tmp_path / "fresh.db"))
        db.close()
        assert not (tmp_path / "backups").exists()


# ── 3. A collection is never a subject ────────────────────────────────────────


class TestCollectionNeverASubject:
    def test_assert_not_collection(self, tmp_path: Path) -> None:
        db = OrivellumDB(str(tmp_path / "t.db"))
        coll = db.create_collection(label="Batch 1", source_kind="zip", source_ref="a.zip")
        assert db.is_collection(coll["id"])
        assert not db.is_collection("nonexistent")
        assert not db.is_collection(None)
        with pytest.raises(ValueError, match="never seed a curriculum"):
            db.assert_not_collection(coll["id"], "seed a curriculum")
        db.assert_not_collection("some-work-id", "seed a curriculum")  # no raise
        db.close()

    def test_harvest_refuses_collection_scope(self, tmp_path: Path) -> None:
        db = OrivellumDB(str(tmp_path / "t.db"))
        coll = db.create_collection(label="Batch 1", source_kind="zip", source_ref="a.zip")
        doc = db.create_document(title="d", sha256="s1", kind="text")
        result = ExtractionResult(kind="text", full_text="hello world", word_count=2)
        from orivellum.capabilities.knowledge_harvest import harvest, llm_harvest

        with pytest.raises(ValueError, match="knowledge harvest"):
            harvest(result, doc["id"], coll["id"], "d", db)
        with pytest.raises(ValueError, match="knowledge harvest"):
            llm_harvest(result, doc["id"], coll["id"], "d", db)
        # A real (or absent) work id passes the guard.
        assert harvest(result, doc["id"], None, "d", db) >= 1
        db.close()

    def test_routes_refuse_collection_as_subject(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from orivellum.api import _deps
        from orivellum.api.app import app
        from orivellum.configuration.config import OrivellumConfig
        from tests.conftest import AUTH_HEADERS

        cfg = OrivellumConfig(data_dir=str(tmp_path))
        db = OrivellumDB(str(tmp_path / "t.db"))
        _deps.init(db=db, cfg=cfg)
        coll = db.create_collection(label="Batch 1", source_kind="zip", source_ref="a.zip")
        client = TestClient(app, headers=AUTH_HEADERS)

        r = client.post(f"/api/works/{coll['id']}/learning/seed")
        assert r.status_code == 422
        assert "never" in r.json()["detail"]

        r = client.post(f"/api/works/{coll['id']}/pipeline", json={})
        assert r.status_code == 422
        assert "never" in r.json()["detail"]

        # A genuine missing Work still 404s (guard did not swallow it).
        r = client.post(f"/api/works/{uuid.uuid4()}/learning/seed")
        assert r.status_code == 404
        db.close()


# ── 4. Forward path: ZIP + folder imports create collections ──────────────────


def _make_zip(tmp_path: Path) -> Path:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("one.txt", b"The quick brown fox jumps over the lazy dog.")
        zf.writestr("two.txt", b"Pack my box with five dozen liquor jugs.")
    p = tmp_path / "archive.zip"
    p.write_bytes(buf.getvalue())
    return p


class TestForwardImportPaths:
    def test_zip_explode_creates_collection(self, tmp_path: Path) -> None:
        db = OrivellumDB(str(tmp_path / "t.db"))
        zip_path = _make_zip(tmp_path)
        parent = db.create_document(
            title="archive.zip", sha256="zipsha1", kind="zip", content_path=str(zip_path)
        )
        (tmp_path / "library").mkdir(exist_ok=True)
        cfg_mock = MagicMock()
        cfg_mock.data_dir = str(tmp_path)
        with (
            patch("orivellum.api._deps.get_config", return_value=cfg_mock),
            patch(
                "orivellum.api.executor._tracked_submit",
                side_effect=Exception("no executor in tests"),
            ),
        ):
            from orivellum.capabilities.pipeline import _explode_zip_into_documents

            children = _explode_zip_into_documents(parent["id"], zip_path, None, "archive.zip", db)
            assert len(children) == 2

            colls = db.list_collections()
            assert len(colls) == 1
            coll = colls[0]
            assert coll["source_kind"] == "zip"
            assert "zipsha1" in coll["source_ref"]
            # Parent archive + both children carry the collection id.
            for did in [parent["id"], *children]:
                assert db.get_document(did)["collection_id"] == coll["id"]
            assert coll["document_count"] == 3

            # Re-exploding the same archive reuses the SAME collection row.
            _explode_zip_into_documents(parent["id"], zip_path, None, "archive.zip", db)
            assert len(db.list_collections()) == 1
        db.close()

    def test_folder_watch_collection_get_or_create(self, tmp_path: Path) -> None:
        from orivellum.capabilities.folder_watch import _collection_for_watch_dir

        db = OrivellumDB(str(tmp_path / "t.db"))
        cid1 = _collection_for_watch_dir("/watch/inbox", db)
        cid2 = _collection_for_watch_dir("/watch/inbox", db)
        assert cid1 is not None and cid1 == cid2
        coll = db.get_collection(cid1)
        assert coll["source_kind"] == "folder"
        assert coll["source_ref"] == "folder:/watch/inbox"
        db.close()


# ── 5. Provenance endpoint ─────────────────────────────────────────────────────


class TestCollectionsEndpoint:
    def test_list_collections_route(self, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from orivellum.api import _deps
        from orivellum.api.app import app
        from orivellum.configuration.config import OrivellumConfig
        from tests.conftest import AUTH_HEADERS

        cfg = OrivellumConfig(data_dir=str(tmp_path))
        db = OrivellumDB(str(tmp_path / "t.db"))
        _deps.init(db=db, cfg=cfg)
        coll = db.create_collection(label="Vault v1", source_kind="zip", source_ref="v.zip")
        db.create_document(title="d1", sha256="s1", kind="text", collection_id=coll["id"])

        client = TestClient(app, headers=AUTH_HEADERS)
        r = client.get("/api/library/collections")
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 1
        assert body["collections"][0]["label"] == "Vault v1"
        assert body["collections"][0]["document_count"] == 1
        db.close()
