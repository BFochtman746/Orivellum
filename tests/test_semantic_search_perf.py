"""Benchmark and correctness tests for the in-process vector cache.

Covers:
- Cold-path (cache miss) and warm-path (cache hit) latency with ~20k vectors
- Result ordering and cold/warm consistency
- Cache does NOT bleed across two DB instances with equal vector counts
- Replacing an existing vector is reflected in the next query
- Cache is warmed before a mutation is tested (proper stale scenario)
- Math helpers: _norm_vec, _dot
"""
from __future__ import annotations

import os
import random
import time

import pytest

from orivellum.capabilities.embeddings import (
    _dot,
    _norm_vec,
    _load_vecs,
    bump_vector_cache_version,
    invalidate_vector_cache,
    pack_vector,
    semantic_search,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rand_vec(dim: int, rng: random.Random) -> list[float]:
    return [rng.gauss(0, 1) for _ in range(dim)]


def _make_db(tmp_path, name: str = "test.db"):
    from orivellum.database.db import OrivellumDB
    return OrivellumDB(str(tmp_path / name))


def _make_doc(db):
    doc = db.create_document(
        title="Perf test document",
        source="/tmp/perf.txt",
        sha256="a" * 64,
        kind="text",
        work_id=None,
    )
    return doc["id"]


def _seed_chunks(db, doc_id, n: int, dim: int, rng: random.Random) -> list[str]:
    """Insert n chunks with random vectors via the proper DB API."""
    chunk_ids: list[str] = []
    for i in range(n):
        text = f"Perf chunk {i}: " + " ".join(str(rng.randint(0, 9999)) for _ in range(10))
        cid = db.add_chunk(doc_id, text, page=i)
        db.store_vector(cid, "chunk", pack_vector(_rand_vec(dim, rng)), dim)
        chunk_ids.append(cid)
    return chunk_ids


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_cache():
    """Wipe all caches before and after every test for isolation."""
    invalidate_vector_cache()
    yield
    invalidate_vector_cache()


_CHUNK_SQL = """
    SELECT v.object_id, v.embedding, v.dim,
           c.text, c.doc_id, d.title AS doc_title, d.work_id
    FROM vectors v
    JOIN chunks c ON c.id = v.object_id
    JOIN documents d ON d.id = c.doc_id
    WHERE v.object_type='chunk'
"""


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_perf_cold_and_warm(tmp_path):
    """Cold-miss + warm-hit both within latency budget for 20 k vectors."""
    import orivellum.capabilities.embeddings as emb_mod

    db = _make_db(tmp_path)
    doc_id = _make_doc(db)
    rng = random.Random(42)
    DIM, N = 64, 20_000

    _seed_chunks(db, doc_id, N, DIM, rng)

    qvec_raw = _rand_vec(DIM, rng)

    def _fake_embed(texts, timeout=None):
        return [list(qvec_raw) for _ in texts]

    original = emb_mod.embed_texts
    emb_mod.embed_texts = _fake_embed
    try:
        t0 = time.monotonic()
        cold = semantic_search("q", db, object_type="chunk", limit=10)
        cold_s = time.monotonic() - t0

        t1 = time.monotonic()
        warm = semantic_search("q", db, object_type="chunk", limit=10)
        warm_s = time.monotonic() - t1
    finally:
        emb_mod.embed_texts = original

    # CI shared runners are markedly slower than the dev container; the wider
    # budget there still catches order-of-magnitude regressions.
    warm_budget = 1.5 if os.getenv("CI") else 0.5
    assert cold_s < 5.0, f"Cold path {cold_s:.2f}s > 5s budget"
    assert warm_s < warm_budget, f"Warm path {warm_s:.2f}s > {warm_budget}s budget"
    assert [r["id"] for r in cold] == [r["id"] for r in warm], \
        "Warm cache returned different results than cold load"

    if len(cold) > 1:
        for a, b in zip(cold, cold[1:]):
            assert a["score"] >= b["score"], "Results not in descending score order"


def test_no_cross_db_contamination(tmp_path):
    """Two DB instances with equal vector counts must not share cache entries."""
    rng = random.Random(7)
    DIM, N = 32, 50

    db_a = _make_db(tmp_path, "a.db")
    db_b = _make_db(tmp_path, "b.db")

    doc_a = _make_doc(db_a)
    doc_b = _make_doc(db_b)

    ids_a = _seed_chunks(db_a, doc_a, N, DIM, rng)
    ids_b = _seed_chunks(db_b, doc_b, N, DIM, rng)

    # Both DBs have the same count — the bug would let them share a cache entry.
    assert db_a.count_vectors("chunk") == db_b.count_vectors("chunk")

    # Warm cache for db_a
    _load_vecs(db_a, "chunk", _CHUNK_SQL, ())
    # Warm cache for db_b
    _load_vecs(db_b, "chunk", _CHUNK_SQL, ())

    entries_a = _load_vecs(db_a, "chunk", _CHUNK_SQL, ())
    entries_b = _load_vecs(db_b, "chunk", _CHUNK_SQL, ())

    oids_a = {e[0] for e in entries_a}
    oids_b = {e[0] for e in entries_b}

    assert oids_a.isdisjoint(oids_b), \
        "Cache entries from db_a and db_b overlap — cross-DB contamination"
    # Spot-check that the right chunks ended up in the right cache
    assert ids_a[0] in oids_a and ids_a[0] not in oids_b
    assert ids_b[0] in oids_b and ids_b[0] not in oids_a


def test_replacement_invalidates_cache(tmp_path):
    """Replacing an existing vector (same object_id) is reflected after the bump."""
    rng = random.Random(13)
    DIM = 32

    db = _make_db(tmp_path)
    doc_id = _make_doc(db)

    # Seed one chunk and warm the cache
    cid = db.add_chunk(doc_id, "replacement test chunk", page=0)
    original_vec = _rand_vec(DIM, rng)
    db.store_vector(cid, "chunk", pack_vector(original_vec), DIM)

    entries_before = _load_vecs(db, "chunk", _CHUNK_SQL, ())
    assert len(entries_before) == 1
    _, _, nvec_before = entries_before[0]

    # Replace the vector — store_vector bumps the version automatically
    new_vec = _rand_vec(DIM, rng)
    db.store_vector(cid, "chunk", pack_vector(new_vec), DIM)

    # Cache must rebuild on next call (version bumped by store_vector)
    entries_after = _load_vecs(db, "chunk", _CHUNK_SQL, ())
    assert len(entries_after) == 1
    _, _, nvec_after = entries_after[0]

    # The normalized vectors must differ (new_vec ≠ original_vec with overwhelming probability)
    assert nvec_before != nvec_after, \
        "Cache still holds pre-replacement vector after store_vector"


def test_cache_warms_then_stales_on_new_vector(tmp_path):
    """Cache is warm, then a new vector is added, and the rebuilt cache includes it."""
    rng = random.Random(99)
    DIM = 32
    N = 20

    db = _make_db(tmp_path)
    doc_id = _make_doc(db)
    existing_ids = _seed_chunks(db, doc_id, N, DIM, rng)

    # ── Warm the cache (this is the stale-scenario pre-condition) ──────────────
    entries_warm = _load_vecs(db, "chunk", _CHUNK_SQL, ())
    assert len(entries_warm) == N
    warm_ids = {e[0] for e in entries_warm}
    assert set(existing_ids) == warm_ids, "Warm cache missing pre-existing chunks"

    # ── Add a new chunk + vector (store_vector bumps version) ─────────────────
    cid_new = db.add_chunk(doc_id, "brand new chunk for stale test", page=N)
    db.store_vector(cid_new, "chunk", pack_vector(_rand_vec(DIM, rng)), DIM)

    # ── Next _load_vecs must rebuild and include the new entry ─────────────────
    entries_fresh = _load_vecs(db, "chunk", _CHUNK_SQL, ())
    fresh_ids = {e[0] for e in entries_fresh}
    assert cid_new in fresh_ids, \
        "Newly stored vector not present in cache after version bump"
    assert len(entries_fresh) == N + 1


def test_ai_auto_items_included_in_semantic_search(tmp_path):
    """semantic_search must return ai_auto items (eligible, not yet reviewed).

    ai_auto items are LLM-extracted and pending human review. They must appear
    in semantic search results (eligibility filter: review_status != 'rejected').
    This test catches the regression where IN ('auto','approved') excluded them.
    """
    import orivellum.capabilities.embeddings as emb_mod

    rng = random.Random(44)
    DIM = 32

    db = _make_db(tmp_path)
    doc_id = _make_doc(db)

    kid = db.create_knowledge_item(
        work_id=None,
        text="AI-auto knowledge item that must appear in semantic search",
        kind="fact",
        source_doc_id=doc_id,
        review_status="ai_auto",
    )
    # Store the SAME vector as the query so cosine = 1.0 — always above noise floor.
    qvec_raw = _rand_vec(DIM, rng)
    db.store_vector(kid, "knowledge", pack_vector(qvec_raw), DIM)

    def _fake_embed(texts, timeout=None):
        return [list(qvec_raw) for _ in texts]

    original = emb_mod.embed_texts
    emb_mod.embed_texts = _fake_embed
    try:
        results = semantic_search("test query", db, object_type="knowledge", limit=20)
    finally:
        emb_mod.embed_texts = original

    result_ids = {r["id"] for r in results}
    # ai_auto item has a vector and review_status != 'rejected' → must appear
    assert kid in result_ids, \
        "ai_auto knowledge item missing from semantic_search results (eligibility filter too strict)"


def test_batch_review_invalidates_knowledge_cache(tmp_path):
    """Batch-rejecting ai_auto items must remove them from the next semantic search.

    Tests the full end-to-end path: ai_auto item visible in semantic_search →
    batch-reject via raw SQL + bump → item absent from next semantic_search.
    """
    import orivellum.capabilities.embeddings as emb_mod

    rng = random.Random(55)
    DIM = 32

    db = _make_db(tmp_path)
    doc_id = _make_doc(db)

    kid = db.create_knowledge_item(
        work_id=None,
        text="AI-auto item to be batch-rejected end-to-end",
        kind="fact",
        source_doc_id=doc_id,
        review_status="ai_auto",
    )
    # Store the SAME vector as the query so cosine = 1.0 — always above noise floor.
    qvec_raw = _rand_vec(DIM, rng)
    db.store_vector(kid, "knowledge", pack_vector(qvec_raw), DIM)

    def _fake_embed(texts, timeout=None):
        return [list(qvec_raw) for _ in texts]

    original = emb_mod.embed_texts
    emb_mod.embed_texts = _fake_embed
    try:
        # Before batch-reject: item must be visible (ai_auto is eligible)
        before = semantic_search("test query", db, object_type="knowledge", limit=20)
        assert any(r["id"] == kid for r in before), \
            "ai_auto item not visible in semantic search before batch-reject"

        # Simulate the batch-review endpoint: raw SQL + explicit bump
        with db._lock:
            db._conn.execute(
                "UPDATE knowledge SET review_status='rejected' WHERE id=? AND review_status='ai_auto'",
                (kid,),
            )
            db._conn.commit()
        bump_vector_cache_version(db._path, "knowledge")

        # After batch-reject: item must be absent (rejected is excluded)
        after = semantic_search("test query", db, object_type="knowledge", limit=20)
        assert not any(r["id"] == kid for r in after), \
            "Batch-rejected ai_auto item still appears in semantic search"
    finally:
        emb_mod.embed_texts = original


def test_delete_knowledge_item_invalidates_cache(tmp_path):
    """Deleting a knowledge item (raw SQL route path) must remove it from the cache."""
    rng = random.Random(77)
    DIM = 32

    db = _make_db(tmp_path)
    doc_id = _make_doc(db)

    kid = db.create_knowledge_item(
        work_id=None,
        text="Knowledge item to be deleted",
        kind="fact",
        source_doc_id=doc_id,
        review_status="approved",
    )
    db.store_vector(kid, "knowledge", pack_vector(_rand_vec(DIM, rng)), DIM)

    knowledge_sql = """
        SELECT v.object_id, v.embedding, v.dim,
               k.text, k.subject, k.predicate, k.object, k.kind,
               k.work_id, k.confidence, k.review_status,
               k.source_doc_id, k.source_chunk_id, k.source_offset,
               k.meta, k.created_at
        FROM vectors v JOIN knowledge k ON k.id = v.object_id
        WHERE v.object_type='knowledge' AND k.review_status IN ('auto','approved','ai_auto')
    """

    # Warm cache — item present
    entries_before = _load_vecs(db, "knowledge", knowledge_sql, ())
    assert any(e[0] == kid for e in entries_before)

    # Simulate the DELETE /knowledge/{id} route: raw SQL + explicit bump
    with db._lock:
        db._conn.execute("DELETE FROM knowledge WHERE id=?", (kid,))
        db._conn.commit()
    bump_vector_cache_version(db._path, "knowledge")

    entries_after = _load_vecs(db, "knowledge", knowledge_sql, ())
    assert not any(e[0] == kid for e in entries_after), \
        "Deleted knowledge item still in cache after bump"


def test_delete_chunks_invalidates_cache(tmp_path):
    """delete_chunks (called during re-extraction) must clear the chunk cache."""
    rng = random.Random(88)
    DIM = 32

    db = _make_db(tmp_path)
    doc_id = _make_doc(db)

    cid = db.add_chunk(doc_id, "chunk that will be cleared", page=0)
    db.store_vector(cid, "chunk", pack_vector(_rand_vec(DIM, rng)), DIM)

    # Warm cache
    entries_before = _load_vecs(db, "chunk", _CHUNK_SQL, ())
    assert any(e[0] == cid for e in entries_before)

    # delete_chunks calls bump_vector_cache_version internally
    db.delete_chunks(doc_id)

    entries_after = _load_vecs(db, "chunk", _CHUNK_SQL, ())
    assert not any(e[0] == cid for e in entries_after), \
        "Cleared chunk still in cache after delete_chunks"


def test_delete_document_invalidates_cache(tmp_path):
    """Deleting a document must invalidate both chunk and knowledge caches."""
    rng = random.Random(101)
    DIM = 32

    db = _make_db(tmp_path)
    doc_id = _make_doc(db)

    cid = db.add_chunk(doc_id, "chunk in doomed document", page=0)
    db.store_vector(cid, "chunk", pack_vector(_rand_vec(DIM, rng)), DIM)

    # Warm chunk cache
    entries_before = _load_vecs(db, "chunk", _CHUNK_SQL, ())
    assert any(e[0] == cid for e in entries_before)

    # delete_document calls bump for both "chunk" and "knowledge"
    db.delete_document(doc_id)

    entries_after = _load_vecs(db, "chunk", _CHUNK_SQL, ())
    assert not any(e[0] == cid for e in entries_after), \
        "Chunk of deleted document still in cache after delete_document"


def test_work_reassignment_invalidates_chunk_cache(tmp_path):
    """Re-assigning a document to a different work must invalidate the chunk cache.

    Chunks carry work_id from the JOIN on documents.  A work reassignment
    changes that value so work-scoped semantic queries must see the new scope.
    """
    rng = random.Random(202)
    DIM = 32

    db = _make_db(tmp_path)
    doc_id = _make_doc(db)

    cid = db.add_chunk(doc_id, "chunk with changing work scope", page=0)
    db.store_vector(cid, "chunk", pack_vector(_rand_vec(DIM, rng)), DIM)

    # Warm cache and note the initial work_id (None)
    entries_before = _load_vecs(db, "chunk", _CHUNK_SQL, ())
    entry = next(e for e in entries_before if e[0] == cid)
    assert entry[1].get("work_id") is None

    # Create a work and reassign — update_document_work bumps "chunk"
    work_id = db.create_work(title="Target Work")["id"]
    db.update_document_work(doc_id, work_id)

    entries_after = _load_vecs(db, "chunk", _CHUNK_SQL, ())
    entry_after = next(e for e in entries_after if e[0] == cid)
    assert entry_after[1].get("work_id") == work_id, \
        "Chunk still shows old work_id in cache after update_document_work"


def test_nightshift_orphan_cleanup_invalidates_chunk_cache(tmp_path):
    """Orphan cleanup removes chunk-type orphaned vectors → chunk cache bumped.

    When a document is deleted (FK CASCADE removes its chunks), the chunk
    vectors are NOT cascade-deleted (no FK on vectors.object_id). Nightshift
    detects these orphaned chunk vectors, deletes them (vc_deleted > 0), and
    must bump the "chunk" cache so semantic search no longer returns them.
    """
    from orivellum.capabilities.nightshift import _pass_orphan_cleanup
    from orivellum.capabilities.embeddings import _vec_cache, _version_counters, _cache_lock

    rng = random.Random(303)
    DIM = 32

    db = _make_db(tmp_path)
    doc_id = _make_doc(db)

    cid = db.add_chunk(doc_id, "chunk whose vector will be orphaned", page=0)
    db.store_vector(cid, "chunk", pack_vector(_rand_vec(DIM, rng)), DIM)

    # Warm the chunk cache, then manually reset version to 0 so we can
    # verify the bump fires during cleanup (bypasses delete_document bump)
    _load_vecs(db, "chunk", _CHUNK_SQL, ())
    with db._lock:
        # Delete document directly — FK CASCADE removes the chunk row but
        # leaves the vector row in place (vectors has no FK on object_id)
        db._conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
        db._conn.execute("UPDATE objects SET lifecycle='deleted' WHERE id=?", (doc_id,))
        db._conn.commit()

    # Freeze the cache to simulate a warm snapshot that predates the delete
    with _cache_lock:
        _version_counters[(db._path, "chunk")] = 0
        _vec_cache.pop((db._path, "chunk"), None)   # force rebuild on next call
        # Insert stale version so next _load_vecs reads version=0 and rebuilds
        # only when version_counter > 0 (i.e. after the bump)

    report: list[str] = []
    _pass_orphan_cleanup(db, report)
    assert any("Orphan" in r for r in report), "Cleanup should report deleted rows"

    with _cache_lock:
        chunk_ver = _version_counters.get((db._path, "chunk"), 0)
    assert chunk_ver > 0, \
        "chunk cache version not bumped after orphan vector cleanup"


def test_nightshift_orphan_cleanup_invalidates_knowledge_cache(tmp_path):
    """Orphan cleanup removes knowledge-type orphaned vectors → knowledge cache bumped.

    Creates a vector row with object_type='knowledge' pointing to a nonexistent
    knowledge ID. Nightshift detects this orphaned vector (vk_deleted > 0) and
    must bump the 'knowledge' cache version.
    """
    from orivellum.capabilities.nightshift import _pass_orphan_cleanup
    from orivellum.capabilities.embeddings import _version_counters, _cache_lock

    import uuid as _uuid_mod
    rng = random.Random(404)
    DIM = 32

    db = _make_db(tmp_path)
    doc_id = _make_doc(db)

    # Insert a knowledge-type vector whose object_id does NOT exist in the
    # knowledge table — immediately an orphan
    fake_kid = str(_uuid_mod.uuid4())
    with db._lock:
        db._conn.execute(
            """INSERT INTO vectors(id, object_id, object_type, embedding, dim, created_at)
               VALUES(?,?,?,?,?,datetime('now'))""",
            (str(_uuid_mod.uuid4()), fake_kid, "knowledge",
             pack_vector(_rand_vec(DIM, rng)), DIM),
        )
        db._conn.commit()

    # Reset version counter so we can verify it gets bumped
    with _cache_lock:
        _version_counters[(db._path, "knowledge")] = 0

    report: list[str] = []
    _pass_orphan_cleanup(db, report)
    assert any("Orphan" in r for r in report), "Cleanup should report deleted rows"

    with _cache_lock:
        know_ver = _version_counters.get((db._path, "knowledge"), 0)
    assert know_ver > 0, \
        "knowledge cache version not bumped after orphan knowledge vector cleanup"


def test_norm_vec_and_dot():
    """Unit tests for the pre-normalization math helpers."""
    v = [3.0, 4.0]
    nv = _norm_vec(v)
    assert abs(sum(x * x for x in nv) - 1.0) < 1e-9

    a = _norm_vec([1.0, 0.0])
    b = _norm_vec([0.0, 1.0])
    assert abs(_dot(a, b)) < 1e-9, "orthogonal unit vectors must have zero dot product"

    c = _norm_vec([1.0, 1.0])
    assert abs(_dot(c, c) - 1.0) < 1e-9, "unit vector dotted with itself must be 1"

    z = _norm_vec([0.0, 0.0])
    assert z == [0.0, 0.0], "zero vector must not raise"
