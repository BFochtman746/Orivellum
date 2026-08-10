"""tests/test_write_storm.py — Verify that high-frequency pipeline writes
do not flood the outbox or audit log.

Tests:
  1. add_chunk × N produces zero outbox events and zero audit rows for
     "document.chunk_added".
  2. store_vector × N produces zero outbox events and zero audit rows for
     "vector.stored".
  3. create_entity_mention × N produces zero outbox rows for
     "entity.mention_created".
  4. create_entity_edge × N produces zero outbox rows for
     "entity.edge_created".
  5. A "full" governed_write still emits exactly one audit row and one
     outbox event — trace writes do not suppress full writes.
  6. verify_audit_chain() passes after many trace writes are interleaved
     with full writes.
  7. Outbox count stays at O(1) regardless of chunk count — simulates a
     50-chunk document pipeline run.
  8. A trace write still rolls back the domain change on exception — the
     atomicity guarantee is not weakened by skipping the audit/outbox.
"""

from __future__ import annotations

import struct
import uuid
from pathlib import Path

from orivellum.database.db import OrivellumDB

# ── helpers ──────────────────────────────────────────────────────────────────


def _db(tmp_path: Path) -> OrivellumDB:
    return OrivellumDB(str(tmp_path / "test.db"))


def _outbox_count(db: OrivellumDB, event_type: str | None = None) -> int:
    with db._lock:
        if event_type:
            row = db._conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE event_type=?", (event_type,)
            ).fetchone()
        else:
            row = db._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()
    return row[0] if row else 0


def _audit_count(db: OrivellumDB, operation: str | None = None) -> int:
    with db._lock:
        if operation:
            row = db._conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE operation=?", (operation,)
            ).fetchone()
        else:
            row = db._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
    return row[0] if row else 0


def _make_doc(db: OrivellumDB) -> str:
    doc = db.create_document(title="Test Doc", source="test.txt", kind="text")
    return doc["id"]


def _fake_embedding(dim: int = 4) -> bytes:
    return struct.pack(f"{dim}f", *([0.1] * dim))


def _make_entity(db: OrivellumDB) -> str:
    eid = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            """INSERT INTO objects(id, type, version, lifecycle, provenance,
               permissions, created_at, updated_at, created_by)
               VALUES(?, 'entity', 1, 'active', '{}', '{}',
                      datetime('now'), datetime('now'), 'system')""",
            (eid,),
        )
        db._conn.execute(
            """INSERT INTO entities(id, name, kind, meta, created_at)
               VALUES(?, 'Test Entity', 'person', '{}', datetime('now'))""",
            (eid,),
        )
        db._conn.commit()
    return eid


# ── Test 1: add_chunk produces no outbox / audit rows ────────────────────────


def test_add_chunk_produces_no_outbox_events(tmp_path):
    """50 add_chunk calls must not write a single outbox event."""
    db = _db(tmp_path)
    doc_id = _make_doc(db)

    N = 50
    for i in range(N):
        db.add_chunk(doc_id, f"Chunk text number {i}.", page=i)

    assert _outbox_count(db, "document.chunk_added") == 0, (
        "add_chunk must not emit outbox events (audit_level='trace')"
    )


def test_add_chunk_produces_no_audit_rows(tmp_path):
    """50 add_chunk calls must not write a single audit log row."""
    db = _db(tmp_path)
    doc_id = _make_doc(db)

    N = 50
    for i in range(N):
        db.add_chunk(doc_id, f"Chunk text number {i}.", page=i)

    assert _audit_count(db, "document.chunk_added") == 0, (
        "add_chunk must not emit audit rows (audit_level='trace')"
    )


def test_add_chunk_data_is_persisted(tmp_path):
    """Chunks must still be stored in the DB despite suppressed audit/outbox."""
    db = _db(tmp_path)
    doc_id = _make_doc(db)

    db.add_chunk(doc_id, "Hello, trace world!", page=1)

    with db._lock:
        row = db._conn.execute("SELECT text FROM chunks WHERE doc_id=?", (doc_id,)).fetchone()
    assert row is not None, "Chunk row must be persisted"
    assert row["text"] == "Hello, trace world!"


# ── Test 2: store_vector produces no outbox / audit rows ─────────────────────


def test_store_vector_produces_no_outbox_events(tmp_path):
    """50 store_vector calls must not write any outbox events."""
    db = _db(tmp_path)
    doc_id = _make_doc(db)
    emb = _fake_embedding()

    N = 50
    for _ in range(N):
        oid = str(uuid.uuid4())
        # store_vector uses the object_id as the vector key; it doesn't require
        # the object to exist in any other table.
        db.store_vector(oid, "chunk", emb, dim=4)

    assert _outbox_count(db, "vector.stored") == 0, (
        "store_vector must not emit outbox events (audit_level='trace')"
    )


def test_store_vector_produces_no_audit_rows(tmp_path):
    """50 store_vector calls must not write any audit rows."""
    db = _db(tmp_path)
    emb = _fake_embedding()

    N = 50
    for _ in range(N):
        db.store_vector(str(uuid.uuid4()), "chunk", emb, dim=4)

    assert _audit_count(db, "vector.stored") == 0, (
        "store_vector must not emit audit rows (audit_level='trace')"
    )


# ── Test 3: create_entity_mention produces no outbox rows ────────────────────


def test_create_entity_mention_produces_no_outbox_events(tmp_path):
    """N entity mentions must not write any outbox events."""
    db = _db(tmp_path)
    doc_id = _make_doc(db)

    N = 30
    for _ in range(N):
        entity_id = _make_entity(db)
        db.create_entity_mention(entity_id, doc_id)

    assert _outbox_count(db, "entity.mention_created") == 0, (
        "create_entity_mention must not emit outbox events (audit_level='trace')"
    )


# ── Test 4: create_entity_edge produces no outbox rows ───────────────────────


def test_create_entity_edge_produces_no_outbox_events(tmp_path):
    """N entity edges must not write any outbox events."""
    db = _db(tmp_path)

    N = 30
    entities = [_make_entity(db) for _ in range(N + 1)]
    for i in range(N):
        db.create_entity_edge(entities[i], entities[i + 1], "RELATED_TO")

    assert _outbox_count(db, "entity.edge_created") == 0, (
        "create_entity_edge must not emit outbox events (audit_level='trace')"
    )


# ── Test 5: full writes still emit exactly one audit + one outbox ─────────────


def test_full_governed_write_still_emits_audit_and_outbox(tmp_path):
    """A full governed_write must still produce exactly one audit + one outbox row."""
    db = _db(tmp_path)

    # Perform a trace write first to confirm it doesn't interfere
    doc_id = _make_doc(db)
    db.add_chunk(doc_id, "trace chunk", page=0)

    audit_before = _audit_count(db)
    outbox_before = _outbox_count(db)

    # A "full" write (default audit_level)
    with db.governed_write(
        operation="test.full_write",
        event_type="test.full_write",
        object_id=doc_id,
        object_type="document",
        detail="full write test",
    ):
        db._conn.execute("UPDATE documents SET title='Updated' WHERE id=?", (doc_id,))

    assert _audit_count(db) == audit_before + 1, (
        "A full governed_write must add exactly one audit row"
    )
    assert _outbox_count(db) == outbox_before + 1, (
        "A full governed_write must add exactly one outbox event"
    )
    assert _audit_count(db, "test.full_write") == 1
    assert _outbox_count(db, "test.full_write") == 1


# ── Test 6: verify_audit_chain passes after mixed trace + full writes ─────────


def test_audit_chain_intact_after_mixed_writes(tmp_path):
    """Many trace writes interleaved with full writes must leave the chain intact."""
    db = _db(tmp_path)
    doc_id = _make_doc(db)
    emb = _fake_embedding()

    # Interleave trace writes (chunks + vectors) with full writes (settings)
    for i in range(20):
        db.add_chunk(doc_id, f"chunk {i}", page=i)
        db.store_vector(str(uuid.uuid4()), "chunk", emb, dim=4)
        if i % 5 == 0:
            db.set_setting(f"test_key_{i}", f"value_{i}")

    ok, reason = db.verify_audit_chain()
    assert ok, f"Audit chain broken after mixed trace/full writes: {reason}"


# ── Test 7: outbox stays O(1) for a full 50-chunk document pipeline ──────────


def test_outbox_does_not_grow_with_chunk_count(tmp_path):
    """Processing a 50-chunk document must not add any outbox entries."""
    db = _db(tmp_path)
    doc_id = _make_doc(db)
    emb = _fake_embedding()

    outbox_before = _outbox_count(db)

    # Simulate the pipeline: add N chunks then embed each one
    N = 50
    chunk_ids = [db.add_chunk(doc_id, f"text {i}", page=i) for i in range(N)]
    for cid in chunk_ids:
        db.store_vector(cid, "chunk", emb, dim=4)

    outbox_after = _outbox_count(db)
    assert outbox_after == outbox_before, (
        f"Outbox grew by {outbox_after - outbox_before} events for {N} chunks; "
        "expected 0 (all chunk/vector writes are trace-level)"
    )


# ── Test 8: trace write still rolls back on exception ────────────────────────


def test_trace_write_rolls_back_on_exception(tmp_path):
    """A trace governed_write that raises inside the block must roll back."""
    db = _db(tmp_path)
    doc_id = _make_doc(db)

    chunks_before = 0
    with db._lock:
        row = db._conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id=?", (doc_id,)).fetchone()
        chunks_before = row[0] if row else 0

    # Attempt a trace write that fails mid-block
    cid = str(uuid.uuid4())
    now = "2026-01-01T00:00:00+00:00"
    try:
        with db.governed_write(
            operation="document.chunk_added",
            event_type="document.chunk_added",
            object_id=doc_id,
            object_type="document",
            actor="system",
            detail="page=0",
            audit_level="trace",
        ):
            db._conn.execute(
                """INSERT INTO objects(id, type, version, lifecycle, provenance,
                   permissions, created_at, updated_at, created_by)
                   VALUES(?, 'chunk', 1, 'active', '{}', '{}', ?, ?, 'system')""",
                (cid, now, now),
            )
            db._conn.execute(
                "INSERT INTO chunks(id, doc_id, page, text, created_at) VALUES(?,?,0,'x',?)",
                (cid, doc_id, now),
            )
            raise ValueError("simulated pipeline error")
    except ValueError:
        pass  # expected

    with db._lock:
        row = db._conn.execute("SELECT COUNT(*) FROM chunks WHERE doc_id=?", (doc_id,)).fetchone()
        chunks_after = row[0] if row else 0

    assert chunks_after == chunks_before, "Trace write must roll back domain change on exception"
    # No outbox or audit rows from the failed write
    assert _outbox_count(db, "document.chunk_added") == 0
    assert _audit_count(db, "document.chunk_added") == 0
