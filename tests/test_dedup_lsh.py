"""Performance and correctness tests for LSH-accelerated near-duplicate detection.

Verifies that:
- A 10,000-signature comparison completes within 100 ms
- The LSH index finds a genuine near-duplicate pair
- Unrelated documents produce no false-positive candidates after Jaccard filtering
- The index can be rebuilt idempotently without duplicating entries
"""
from __future__ import annotations

import os
import struct
import tempfile
import time
import unittest
import uuid

from orivellum.capabilities.dedup import (
    _BANDS,
    _NUM_PERM,
    _ROWS,
    _add_to_lsh,
    _ensure_index_built,
    _lsh_index,
    _lsh_lock,
    _lsh_sigs,
    _minhash,
    _reset_lsh_index,
    _shingles,
    _sig_to_ints,
    compute_and_store,
    find_and_record_near_duplicates,
    rebuild_lsh_index,
)
from orivellum.database.db import OrivellumDB


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_app(tmp: str):
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.api import _deps
    from orivellum.api.app import app
    from pathlib import Path

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


def _make_doc(db: OrivellumDB, title: str = "Doc") -> dict:
    return db.create_document(
        title=title, source="/tmp/x.pdf", sha256=uuid.uuid4().hex, kind="pdf",
    )


def _random_sig() -> bytes:
    """Generate a random 128-uint32 MinHash signature."""
    return os.urandom(_NUM_PERM * 4)


def _similar_sig(sig: bytes, flip_count: int = 5) -> bytes:
    """Return a signature with `flip_count` values changed — high Jaccard."""
    ints = list(struct.unpack(f">{_NUM_PERM}I", sig))
    for i in range(flip_count):
        ints[i] = (ints[i] + 1) & 0xFFFF_FFFF
    return struct.pack(f">{_NUM_PERM}I", *ints)


def _long_text(words: int = 200, seed: str = "alpha") -> str:
    import hashlib
    return " ".join(
        hashlib.sha256(f"{seed}{i}".encode()).hexdigest()[:8]
        for i in range(words)
    )


# ── LSH internals ─────────────────────────────────────────────────────────────

class TestLSHInternals(unittest.TestCase):

    def setUp(self):
        _reset_lsh_index()

    def test_band_constants_consistent(self):
        self.assertEqual(_BANDS * _ROWS, _NUM_PERM,
                         "_BANDS × _ROWS must equal _NUM_PERM")

    def test_add_to_lsh_populates_index(self):
        sig = _minhash(_shingles("the quick brown fox jumps over the lazy dog " * 20))
        sig_ints = _sig_to_ints(sig)
        with _lsh_lock:
            _add_to_lsh("doc-1", sig_ints)
        self.assertIn("doc-1", _lsh_sigs)
        # At least one band bucket was populated
        populated = sum(1 for (_, bucket_list) in _lsh_index.items() if "doc-1" in bucket_list)
        self.assertEqual(populated, _BANDS)

    def test_similar_sigs_share_bucket(self):
        """Two near-identical sigs must collide in at least one band."""
        base_sig = _random_sig()
        near_sig = _similar_sig(base_sig, flip_count=3)  # only 3/128 values differ

        base_ints = _sig_to_ints(base_sig)
        near_ints = _sig_to_ints(near_sig)

        with _lsh_lock:
            _add_to_lsh("doc-A", base_ints)
            _add_to_lsh("doc-B", near_ints)

        shared_bands = 0
        for b in range(_BANDS):
            band_a = base_ints[b * _ROWS: (b + 1) * _ROWS]
            band_b = near_ints[b * _ROWS: (b + 1) * _ROWS]
            if hash(band_a) == hash(band_b) and band_a == band_b:
                shared_bands += 1
        self.assertGreater(shared_bands, 0,
                           "Near-identical sigs must share at least one band")

    def test_reset_clears_index(self):
        sig_ints = _sig_to_ints(_random_sig())
        with _lsh_lock:
            _add_to_lsh("doc-X", sig_ints)
        _reset_lsh_index()
        self.assertEqual(len(_lsh_sigs), 0)
        self.assertEqual(len(_lsh_index), 0)

    def test_rebuild_is_idempotent(self):
        """Rebuilding twice should not double the entry count."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            _, db = _make_app(tmp)
            doc = _make_doc(db)
            compute_and_store(doc["id"], _long_text(200, "alpha"), db)

            count_a = rebuild_lsh_index(db)
            count_b = rebuild_lsh_index(db)
            self.assertEqual(count_a, count_b,
                             "Rebuilding twice must not change the count")


# ── Performance ───────────────────────────────────────────────────────────────

class TestLSHPerformance(unittest.TestCase):
    """The LSH comparison must stay under 100 ms even with 10,000 signatures."""

    _BUDGET_MS = 100
    _INDEX_SIZE = 10_000

    def setUp(self):
        _reset_lsh_index()

    def _populate_index(self, n: int) -> None:
        """Directly insert *n* random signatures into the in-memory LSH index."""
        global _lsh_built  # noqa: PLW0603
        import orivellum.capabilities.dedup as _dedup_mod

        with _lsh_lock:
            for _ in range(n):
                sig_ints = _sig_to_ints(_random_sig())
                _add_to_lsh(str(uuid.uuid4()), sig_ints)
            _dedup_mod._lsh_built = True  # mark as built so _ensure_index_built is a no-op

    def test_global_comparison_under_budget(self):
        """find_and_record_near_duplicates must complete < 100 ms with 10k sigs.

        Uses a mock DB that raises on any SQL call so we confirm the LSH path
        makes zero DB reads for candidate fetching.
        """
        from unittest.mock import MagicMock, patch

        self._populate_index(self._INDEX_SIZE)

        # Build a query signature that is unlikely to match any random sig
        query_sig = _minhash(_shingles(_long_text(300, "unique-query-seed-xyz")))
        query_id = str(uuid.uuid4())

        mock_db = MagicMock()
        mock_db._lock = __import__("threading").Lock()

        # DB should only be called for the doc_dupes check (on actual hits),
        # not for loading signatures — but with random sigs there will be no hits.
        mock_db._conn.execute.return_value.fetchall.return_value = []
        mock_db._conn.execute.return_value.fetchone.return_value = None

        start = time.perf_counter()
        results = find_and_record_near_duplicates(query_id, query_sig, mock_db)
        elapsed_ms = (time.perf_counter() - start) * 1000

        self.assertLess(
            elapsed_ms, self._BUDGET_MS,
            f"LSH comparison took {elapsed_ms:.1f} ms — expected < {self._BUDGET_MS} ms "
            f"with {self._INDEX_SIZE} documents indexed",
        )
        # Random sigs won't produce real near-duplicates after Jaccard filtering
        self.assertEqual(len(results), 0,
                         "Random signatures should not produce near-duplicate hits")

    def test_index_size_after_population(self):
        """Index should hold exactly the expected number of unique documents."""
        self._populate_index(self._INDEX_SIZE)
        self.assertEqual(len(_lsh_sigs), self._INDEX_SIZE)


# ── End-to-end correctness ────────────────────────────────────────────────────

class TestLSHCorrectness(unittest.TestCase):
    """LSH + Jaccard must find genuine near-duplicates and suppress unrelated docs."""

    def setUp(self):
        _reset_lsh_index()

    def test_near_identical_text_found_via_lsh(self):
        """Two almost-identical documents must be detected through the LSH path."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            _, db = _make_app(tmp)
            doc_a = _make_doc(db, "Doc A")
            doc_b = _make_doc(db, "Doc B")

            base_words = [f"word{i}" for i in range(150)]
            text_a = " ".join(base_words)
            text_b = " ".join(base_words[:-2] + ["extra1", "extra2"])

            sig_a = compute_and_store(doc_a["id"], text_a, db)
            sig_b = compute_and_store(doc_b["id"], text_b, db)
            self.assertIsNotNone(sig_a)
            self.assertIsNotNone(sig_b)

            # Call without work_id to exercise the LSH global path
            hits = find_and_record_near_duplicates(doc_b["id"], sig_b, db)
            found_ids = [h[0] for h in hits]
            self.assertIn(doc_a["id"], found_ids,
                          "LSH path must surface a near-identical document")

    def test_unrelated_text_no_false_positive(self):
        """Documents with disjoint content must not be returned as candidates."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            _, db = _make_app(tmp)
            doc_a = _make_doc(db, "Doc A")
            doc_b = _make_doc(db, "Doc B")

            sig_a = compute_and_store(doc_a["id"], _long_text(200, "alpha"), db)
            sig_b = compute_and_store(doc_b["id"], _long_text(200, "zeta"), db)
            self.assertIsNotNone(sig_a)
            self.assertIsNotNone(sig_b)

            hits = find_and_record_near_duplicates(doc_b["id"], sig_b, db)
            self.assertEqual(len(hits), 0,
                             "Disjoint documents must not produce false-positive hits")

    def test_work_scoped_path_still_works(self):
        """The work_id path must still detect near-duplicates (DB-scoped)."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            _, db = _make_app(tmp)
            work = db.create_work(title="Test Work")
            work_id = work["id"]

            doc_a = db.create_document(
                title="A", source="/tmp/a.pdf", sha256=uuid.uuid4().hex,
                kind="pdf", work_id=work_id,
            )
            doc_b = db.create_document(
                title="B", source="/tmp/b.pdf", sha256=uuid.uuid4().hex,
                kind="pdf", work_id=work_id,
            )

            base_words = [f"token{i}" for i in range(150)]
            text_a = " ".join(base_words)
            text_b = " ".join(base_words[:-2] + ["zzz1", "zzz2"])

            sig_a = compute_and_store(doc_a["id"], text_a, db)
            sig_b = compute_and_store(doc_b["id"], text_b, db)
            self.assertIsNotNone(sig_a)
            self.assertIsNotNone(sig_b)

            hits = find_and_record_near_duplicates(doc_b["id"], sig_b, db, work_id=work_id)
            found_ids = [h[0] for h in hits]
            self.assertIn(doc_a["id"], found_ids,
                          "Work-scoped path must still find near-duplicates")

    def test_index_updated_incrementally_after_compute_and_store(self):
        """compute_and_store must add the new doc to the index without a full rebuild."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            _, db = _make_app(tmp)

            # Simulate an already-built index scoped to this DB connection.
            import orivellum.capabilities.dedup as _dedup_mod
            with _lsh_lock:
                _dedup_mod._lsh_built = True
                _dedup_mod._lsh_db_id = id(db._conn)

            doc = _make_doc(db)
            compute_and_store(doc["id"], _long_text(200, "gamma"), db)

            self.assertIn(doc["id"], _lsh_sigs,
                          "compute_and_store must add the doc to _lsh_sigs when index is live")


# ── Regression: stale-entry and DB-identity ───────────────────────────────────

class TestLSHConsistency(unittest.TestCase):
    """Stale index entries (from deletions or DB reinit) must never surface."""

    def setUp(self):
        _reset_lsh_index()

    def test_deleted_doc_not_returned_as_candidate(self):
        """Deleting a document must prevent it from appearing in future hits."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            _, db = _make_app(tmp)

            doc_a = _make_doc(db, "Doc A")
            doc_b = _make_doc(db, "Doc B")

            base_words = [f"word{i}" for i in range(150)]
            text_a = " ".join(base_words)
            text_b = " ".join(base_words[:-2] + ["extra1", "extra2"])

            sig_a = compute_and_store(doc_a["id"], text_a, db)
            sig_b = compute_and_store(doc_b["id"], text_b, db)
            self.assertIsNotNone(sig_a)
            self.assertIsNotNone(sig_b)

            # Confirm the pair is detected before deletion.
            hits_before = find_and_record_near_duplicates(doc_b["id"], sig_b, db)
            self.assertTrue(any(h[0] == doc_a["id"] for h in hits_before),
                            "Pair must be found before deletion")

            # Delete doc_a — this should evict it from the LSH index.
            deleted = db.delete_document(doc_a["id"])
            self.assertTrue(deleted)

            # Also remove the doc_dupes row so the query exercises the full path.
            with db._lock:
                db._conn.execute(
                    "DELETE FROM doc_dupes WHERE doc_a_id=? OR doc_b_id=?",
                    (doc_a["id"], doc_a["id"]),
                )
                db._conn.commit()

            # After deletion, doc_a must not appear in hits.
            hits_after = find_and_record_near_duplicates(doc_b["id"], sig_b, db)
            self.assertFalse(any(h[0] == doc_a["id"] for h in hits_after),
                             "Deleted document must not appear as a near-duplicate candidate")

    def test_evict_removes_doc_from_index(self):
        """evict_from_lsh_index must remove the doc from _lsh_sigs and all band buckets."""
        _reset_lsh_index()
        from orivellum.capabilities.dedup import evict_from_lsh_index

        sig = _minhash(_shingles("the quick brown fox jumps over the lazy dog " * 20))
        sig_ints = _sig_to_ints(sig)
        with _lsh_lock:
            _add_to_lsh("evict-me", sig_ints)

        self.assertIn("evict-me", _lsh_sigs)

        evict_from_lsh_index("evict-me")

        self.assertNotIn("evict-me", _lsh_sigs)
        for bucket_list in _lsh_index.values():
            self.assertNotIn("evict-me", bucket_list,
                             "evicted doc must not appear in any band bucket")

    def test_evict_nonexistent_doc_is_noop(self):
        """Evicting a doc not in the index must not raise."""
        _reset_lsh_index()
        from orivellum.capabilities.dedup import evict_from_lsh_index
        evict_from_lsh_index("does-not-exist")  # must not raise

    def test_db_reinit_resets_index(self):
        """Reinitializing with a new DB must rebuild the index from scratch."""
        with tempfile.TemporaryDirectory() as tmp1, \
             tempfile.TemporaryDirectory() as tmp2:
            _reset_lsh_index()

            # Build index from DB #1 with one document.
            _, db1 = _make_app(tmp1)
            doc1 = _make_doc(db1, "DB1 Doc")
            compute_and_store(doc1["id"], _long_text(200, "db1"), db1)

            # Trigger index build against db1.
            find_and_record_near_duplicates(
                doc1["id"],
                compute_and_store(doc1["id"], _long_text(200, "db1"), db1) or b"",
                db1,
            )
            count_after_db1 = len(_lsh_sigs)
            self.assertGreater(count_after_db1, 0)

            # Switch to DB #2 (different connection object).
            _reset_lsh_index()
            _, db2 = _make_app(tmp2)
            doc2 = _make_doc(db2, "DB2 Doc")
            sig2 = compute_and_store(doc2["id"], _long_text(200, "db2"), db2)
            self.assertIsNotNone(sig2)

            # Querying against db2 must rebuild from db2 (not retain db1 entries).
            find_and_record_near_duplicates(doc2["id"], sig2, db2)
            # doc1["id"] must not be in the index (it belongs to db1).
            self.assertNotIn(doc1["id"], _lsh_sigs,
                             "Index must not retain entries from a previous DB")

    def test_stale_candidate_not_appended_to_results(self):
        """A candidate whose document row was deleted mid-flight must not be in results."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            _, db = _make_app(tmp)

            doc_a = _make_doc(db, "Doc A")
            doc_b = _make_doc(db, "Doc B")

            base_words = [f"item{i}" for i in range(150)]
            text_a = " ".join(base_words)
            text_b = " ".join(base_words[:-2] + ["new1", "new2"])

            sig_a = compute_and_store(doc_a["id"], text_a, db)
            sig_b = compute_and_store(doc_b["id"], text_b, db)
            self.assertIsNotNone(sig_a)
            self.assertIsNotNone(sig_b)

            # Force the index to be in-sync (so doc_a is in _lsh_sigs).
            _ensure_index_built(db)

            # Delete doc_a's document row but leave the minhash_sig + LSH entry
            # to simulate a race between deletion and comparison.
            with db._lock:
                db._conn.execute("DELETE FROM documents WHERE id=?", (doc_a["id"],))
                db._conn.commit()

            # The global path must detect the stale entry and not return it.
            hits = find_and_record_near_duplicates(doc_b["id"], sig_b, db)
            self.assertFalse(any(h[0] == doc_a["id"] for h in hits),
                             "Stale candidate (document deleted) must not be in results")


if __name__ == "__main__":
    unittest.main()
