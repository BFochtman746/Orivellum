"""Tests for the POST /library/scan-duplicates backfill endpoint.

Confirms three failure modes the scan must handle correctly:

1. Near-identical ready documents are paired — after a scan, GET /library/duplicates
   returns the pair with similarity above the near-duplicate threshold.

2. Short documents (< _MIN_WORDS=100 words) are silently skipped — no minhash_sig
   row is created and no pair is recorded; the endpoint returns 200 with no errors.

3. Large documents (100k+ words) are processed successfully — the background task
   completes without timing out or raising, and the pair is still found.

4. The scan is idempotent — calling it twice with all documents already indexed
   returns queued=0 on the second call with no errors.

5. The "short doc skipped" case does not interfere with pairing among long docs
   that appear alongside it in the same scan batch.
"""
from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from orivellum.capabilities.dedup import _MIN_WORDS, _reset_lsh_index


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_app(tmp: str):
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.api import _deps
    from orivellum.api.app import app

    cfg = OrivellumConfig(data_dir=tmp)
    from orivellum.database.db import OrivellumDB
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


def _make_doc(db, title: str = "Doc") -> dict:
    return db.create_document(
        title=title, source=f"/tmp/{uuid.uuid4().hex}.pdf",
        sha256=uuid.uuid4().hex, kind="pdf",
    )


def _set_ready(db, doc_id: str, text: str) -> None:
    """Mark a document as ready and set its extracted_text directly in the DB."""
    with db._lock:
        db._conn.execute(
            "UPDATE documents SET readiness='ready', extracted_text=? WHERE id=?",
            (text, doc_id),
        )
        db._conn.commit()


def _near_identical_texts(words: int = 150) -> tuple[str, str]:
    """Return two texts that share all but 2 words — Jaccard ≈ 0.97."""
    base = [f"word{i}" for i in range(words)]
    text_a = " ".join(base)
    text_b = " ".join(base[:-2] + ["zzz1", "zzz2"])
    return text_a, text_b


def _short_text() -> str:
    """Return a text under _MIN_WORDS words."""
    return " ".join(f"w{i}" for i in range(_MIN_WORDS - 1))


def _large_text(approx_words: int = 100_000) -> str:
    """Return a very long text (~100k words) to stress the background task."""
    import hashlib
    chunk = " ".join(
        hashlib.sha256(f"tok{i}".encode()).hexdigest()[:8]
        for i in range(500)
    )
    # Repeat enough times to reach the target word count
    repetitions = max(1, approx_words // 500)
    return (chunk + " ") * repetitions


def _pair_exists(db, id_a: str, id_b: str) -> bool:
    with db._lock:
        row = db._conn.execute(
            """SELECT id FROM doc_dupes
               WHERE (doc_a_id=? AND doc_b_id=?) OR (doc_a_id=? AND doc_b_id=?)""",
            (id_a, id_b, id_b, id_a),
        ).fetchone()
    return row is not None


def _sig_exists(db, doc_id: str) -> bool:
    with db._lock:
        row = db._conn.execute(
            "SELECT doc_id FROM minhash_sig WHERE doc_id=?", (doc_id,)
        ).fetchone()
    return row is not None


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestScanDuplicatesBackfill(unittest.TestCase):

    def setUp(self):
        _reset_lsh_index()

    def _client(self, tmp: str):
        from fastapi.testclient import TestClient
        from tests.conftest import AUTH_HEADERS
        app, db = _make_app(tmp)
        client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
        return client, db

    # ── 1. Near-identical documents are paired after a scan ───────────────────

    def test_near_identical_docs_produce_pair(self):
        """POST scan-duplicates must detect two near-identical ready docs."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            doc_a = _make_doc(db, "Chapter Draft v1")
            doc_b = _make_doc(db, "Chapter Draft v2")
            text_a, text_b = _near_identical_texts()
            _set_ready(db, doc_a["id"], text_a)
            _set_ready(db, doc_b["id"], text_b)

            resp = client.post("/api/library/scan-duplicates")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["queued"], 2,
                             "Both ready docs must be queued for scanning")

            # Background task has run synchronously inside TestClient
            self.assertTrue(
                _pair_exists(db, doc_a["id"], doc_b["id"]),
                "Near-identical documents must be recorded in doc_dupes after scan",
            )

    def test_pair_appears_in_duplicates_endpoint(self):
        """After a scan, GET /library/duplicates must return the new pair."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            doc_a = _make_doc(db, "Doc A")
            doc_b = _make_doc(db, "Doc B")
            text_a, text_b = _near_identical_texts()
            _set_ready(db, doc_a["id"], text_a)
            _set_ready(db, doc_b["id"], text_b)

            client.post("/api/library/scan-duplicates")

            resp = client.get("/api/library/duplicates")
            self.assertEqual(resp.status_code, 200)
            pairs = resp.json()["pairs"]
            pair_ids = {
                frozenset([p["doc_a_id"], p["doc_b_id"]]) for p in pairs
            }
            expected = frozenset([doc_a["id"], doc_b["id"]])
            self.assertIn(expected, pair_ids,
                          "The new pair must appear in GET /library/duplicates")

    def test_pair_similarity_above_near_duplicate_threshold(self):
        """Detected pair must carry similarity ≥ 0.85 for near-identical text."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            doc_a = _make_doc(db, "Story v1")
            doc_b = _make_doc(db, "Story v2")
            text_a, text_b = _near_identical_texts(words=200)
            _set_ready(db, doc_a["id"], text_a)
            _set_ready(db, doc_b["id"], text_b)

            client.post("/api/library/scan-duplicates")

            with db._lock:
                row = db._conn.execute(
                    """SELECT similarity, kind FROM doc_dupes
                       WHERE (doc_a_id=? AND doc_b_id=?) OR (doc_a_id=? AND doc_b_id=?)""",
                    (doc_a["id"], doc_b["id"], doc_b["id"], doc_a["id"]),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertGreaterEqual(
                row["similarity"], 0.85,
                "Near-identical texts must produce similarity ≥ 0.85",
            )
            self.assertEqual(row["kind"], "near_duplicate")

    # ── 2. Short documents are silently skipped ───────────────────────────────

    def test_short_doc_not_indexed(self):
        """A doc under _MIN_WORDS words must not get a minhash_sig row."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            short_doc = _make_doc(db, "Short Note")
            _set_ready(db, short_doc["id"], _short_text())

            resp = client.post("/api/library/scan-duplicates")
            self.assertEqual(resp.status_code, 200,
                             "Scan must succeed even with a short document in the batch")

            self.assertFalse(
                _sig_exists(db, short_doc["id"]),
                f"Docs under {_MIN_WORDS} words must not receive a minhash_sig",
            )

    def test_short_doc_produces_no_pair(self):
        """A doc under _MIN_WORDS words must not cause any pair to be recorded."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            short_doc = _make_doc(db, "Short Note")
            _set_ready(db, short_doc["id"], _short_text())

            client.post("/api/library/scan-duplicates")

            with db._lock:
                count = db._conn.execute("SELECT COUNT(*) FROM doc_dupes").fetchone()[0]
            self.assertEqual(count, 0, "No pairs must be recorded for a short document")

    def test_short_doc_does_not_block_long_doc_pairing(self):
        """A short doc alongside two near-identical long docs must not prevent pairing."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            short_doc = _make_doc(db, "Short Note")
            doc_a = _make_doc(db, "Long Doc A")
            doc_b = _make_doc(db, "Long Doc B")

            _set_ready(db, short_doc["id"], _short_text())
            text_a, text_b = _near_identical_texts()
            _set_ready(db, doc_a["id"], text_a)
            _set_ready(db, doc_b["id"], text_b)

            resp = client.post("/api/library/scan-duplicates")
            body = resp.json()
            # All three docs were queued — the short one gets skipped internally
            self.assertEqual(body["queued"], 3)

            # Long docs must still be paired
            self.assertTrue(
                _pair_exists(db, doc_a["id"], doc_b["id"]),
                "Short doc must not prevent pairing of near-identical long docs",
            )
            # Short doc must not be indexed
            self.assertFalse(_sig_exists(db, short_doc["id"]))

    # ── 3. Large documents are handled without errors ─────────────────────────

    def test_large_document_indexed_successfully(self):
        """A 100k-word document must be scanned without raising or producing no sig."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            large_doc = _make_doc(db, "Long Novel")
            large_text = _large_text(approx_words=100_000)
            _set_ready(db, large_doc["id"], large_text)

            # Must complete without error
            resp = client.post("/api/library/scan-duplicates")
            self.assertEqual(resp.status_code, 200)

            # Large doc must receive a minhash_sig
            self.assertTrue(
                _sig_exists(db, large_doc["id"]),
                "Large document must be indexed (minhash_sig row created)",
            )

    def test_two_large_near_identical_docs_produce_pair(self):
        """Two large near-identical docs must still be paired."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            # Build a long shared base, change the last 2 words
            base_words = [f"tok{i}" for i in range(5_000)]
            text_a = " ".join(base_words)
            text_b = " ".join(base_words[:-2] + ["ZZZA", "ZZZB"])

            doc_a = _make_doc(db, "Big Novel v1")
            doc_b = _make_doc(db, "Big Novel v2")
            _set_ready(db, doc_a["id"], text_a)
            _set_ready(db, doc_b["id"], text_b)

            resp = client.post("/api/library/scan-duplicates")
            self.assertEqual(resp.status_code, 200)

            self.assertTrue(
                _pair_exists(db, doc_a["id"], doc_b["id"]),
                "Large near-identical documents must be paired after scan",
            )

    # ── 4. Scan is idempotent ─────────────────────────────────────────────────

    def test_second_scan_returns_zero_queued(self):
        """After a full scan, a second POST must report queued=0."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            doc = _make_doc(db, "Single Doc")
            text_a, _ = _near_identical_texts()
            _set_ready(db, doc["id"], text_a)

            # First scan indexes the document
            resp1 = client.post("/api/library/scan-duplicates")
            self.assertEqual(resp1.json()["queued"], 1)

            # Second scan — nothing left to index
            resp2 = client.post("/api/library/scan-duplicates")
            self.assertEqual(resp2.status_code, 200)
            self.assertEqual(
                resp2.json()["queued"], 0,
                "Second scan must report queued=0 (already indexed)",
            )

    def test_second_scan_already_indexed_count(self):
        """already_indexed count on second call must reflect the existing sig."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            doc_a = _make_doc(db, "Doc A")
            doc_b = _make_doc(db, "Doc B")
            text_a, text_b = _near_identical_texts()
            _set_ready(db, doc_a["id"], text_a)
            _set_ready(db, doc_b["id"], text_b)

            client.post("/api/library/scan-duplicates")

            resp2 = client.post("/api/library/scan-duplicates")
            body2 = resp2.json()
            self.assertEqual(body2["queued"], 0)
            self.assertGreaterEqual(
                body2["already_indexed"], 2,
                "already_indexed must count the sigs stored in the first scan",
            )

    def test_scan_idempotent_no_duplicate_pairs(self):
        """Running scan twice must not create duplicate entries in doc_dupes."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            doc_a = _make_doc(db, "Novel A")
            doc_b = _make_doc(db, "Novel B")
            text_a, text_b = _near_identical_texts()
            _set_ready(db, doc_a["id"], text_a)
            _set_ready(db, doc_b["id"], text_b)

            client.post("/api/library/scan-duplicates")
            client.post("/api/library/scan-duplicates")

            with db._lock:
                count = db._conn.execute(
                    """SELECT COUNT(*) FROM doc_dupes
                       WHERE (doc_a_id=? AND doc_b_id=?) OR (doc_a_id=? AND doc_b_id=?)""",
                    (doc_a["id"], doc_b["id"], doc_b["id"], doc_a["id"]),
                ).fetchone()[0]
            self.assertEqual(
                count, 1,
                "Running scan twice must not insert duplicate pair rows",
            )

    # ── 5. Edge cases ─────────────────────────────────────────────────────────

    def test_empty_library_returns_queued_zero(self):
        """Scanning an empty library must return queued=0 without errors."""
        with tempfile.TemporaryDirectory() as tmp:
            client, db = self._client(tmp)
            resp = client.post("/api/library/scan-duplicates")
            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertEqual(body["queued"], 0)
            self.assertIn("already_indexed", body)

    def test_non_ready_docs_excluded_from_scan(self):
        """Documents not in 'ready' state must be excluded from the scan queue."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            # Create docs in non-ready states
            for state in ("imported", "error", "no_text"):
                doc = _make_doc(db, f"Doc {state}")
                text_a, _ = _near_identical_texts()
                with db._lock:
                    db._conn.execute(
                        "UPDATE documents SET readiness=?, extracted_text=? WHERE id=?",
                        (state, text_a, doc["id"]),
                    )
                    db._conn.commit()

            resp = client.post("/api/library/scan-duplicates")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(
                resp.json()["queued"], 0,
                "Non-ready documents must be excluded from the backfill scan",
            )

    def test_scan_response_includes_message(self):
        """Scan response must always include a human-readable message field."""
        with tempfile.TemporaryDirectory() as tmp:
            client, db = self._client(tmp)
            resp = client.post("/api/library/scan-duplicates")
            self.assertIn("message", resp.json(),
                          "Scan response must include a 'message' field")

    def test_unrelated_ready_docs_do_not_produce_pair(self):
        """Two ready documents with completely different text must not be paired."""
        with tempfile.TemporaryDirectory() as tmp:
            _reset_lsh_index()
            client, db = self._client(tmp)

            import hashlib

            def _unique_text(seed: str, n: int = 200) -> str:
                return " ".join(
                    hashlib.sha256(f"{seed}{i}".encode()).hexdigest()[:8]
                    for i in range(n)
                )

            doc_a = _make_doc(db, "Doc Alpha")
            doc_b = _make_doc(db, "Doc Beta")
            _set_ready(db, doc_a["id"], _unique_text("alpha"))
            _set_ready(db, doc_b["id"], _unique_text("zeta"))

            client.post("/api/library/scan-duplicates")

            self.assertFalse(
                _pair_exists(db, doc_a["id"], doc_b["id"]),
                "Unrelated documents must not be paired",
            )


if __name__ == "__main__":
    unittest.main()
