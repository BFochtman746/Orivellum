"""Tests for near-duplicate detection and resolution.

Covers:
- MinHash computation and storage
- Jaccard similarity estimation
- Near-duplicate pair detection and persistence
- DB resolve_near_duplicate with all three actions
- GET /api/library/duplicates (route ordering fix — 'duplicates' is literal, not a doc_id)
- POST /api/library/duplicates/{id}/resolve
- Resolution filters list_near_duplicates correctly
"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from orivellum.capabilities.dedup import (
    _jaccard,
    _minhash,
    _shingles,
    compute_and_store,
    find_and_record_near_duplicates,
)
from orivellum.database.db import OrivellumDB

# ── Test app factory (matches conftest pattern) ───────────────────────────────


def _make_app(tmp: str):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_doc(db: OrivellumDB, title: str = "Doc", work_id: str | None = None) -> dict:
    return db.create_document(
        title=title,
        source="/tmp/x.pdf",
        sha256=uuid.uuid4().hex,
        kind="pdf",
        work_id=work_id,
    )


def _long_text(words: int = 200, seed: str = "alpha") -> str:
    """Return a text blob of ~`words` distinct hashed tokens."""
    import hashlib

    return " ".join(hashlib.sha256(f"{seed}{i}".encode()).hexdigest()[:8] for i in range(words))


def _make_pair(db: OrivellumDB, similarity: float = 0.9) -> tuple[dict, dict, str]:
    doc_a = _make_doc(db, "Doc A")
    doc_b = _make_doc(db, "Doc B")
    dupe_id = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            """INSERT INTO doc_dupes(id, doc_a_id, doc_b_id, similarity, kind, created_at)
               VALUES(?,?,?,?,'near_duplicate',datetime('now'))""",
            (dupe_id, doc_a["id"], doc_b["id"], similarity),
        )
        db._conn.commit()
    return doc_a, doc_b, dupe_id


# ── Unit: MinHash ─────────────────────────────────────────────────────────────


class TestMinHashUnit(unittest.TestCase):
    def test_shingles_returns_ngrams(self):
        s = _shingles("a b c d e", k=3)
        self.assertIn("a b c", s)
        self.assertIn("c d e", s)

    def test_shingles_short_text(self):
        s = _shingles("a b", k=5)
        self.assertEqual(s, {"a b"})

    def test_minhash_returns_correct_bytes(self):
        sig = _minhash({"hello world", "foo bar"}, num_perm=32)
        self.assertEqual(len(sig), 32 * 4)

    def test_jaccard_identical_sketches(self):
        sig = _minhash({"hello world"}, num_perm=64)
        self.assertEqual(_jaccard(sig, sig, num_perm=64), 1.0)

    def test_jaccard_different_sketches(self):
        a = _minhash({"aaa bbb ccc ddd eee"}, num_perm=64)
        b = _minhash({"zzz yyy xxx www vvv"}, num_perm=64)
        self.assertLess(_jaccard(a, b, num_perm=64), 0.5)

    def test_jaccard_similar_text_produces_high_score(self):
        base = [f"word{i}" for i in range(50)]
        text_a = " ".join(base)
        text_b = " ".join(base[:48] + ["extra1", "extra2"])
        a = _minhash(_shingles(text_a))
        b = _minhash(_shingles(text_b))
        self.assertGreater(_jaccard(a, b), 0.5)

    def test_jaccard_mismatched_sizes_returns_zero(self):
        self.assertEqual(_jaccard(b"\x00" * 16, b"\x00" * 32, num_perm=4), 0.0)


# ── Unit: compute_and_store ───────────────────────────────────────────────────


class TestComputeAndStore(unittest.TestCase):
    def test_stores_sig_for_long_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = _make_app(tmp)
            doc = _make_doc(db)
            sig = compute_and_store(doc["id"], _long_text(200), db)
            self.assertIsNotNone(sig)
            with db._lock:
                row = db._conn.execute(
                    "SELECT sig FROM minhash_sig WHERE doc_id=?", (doc["id"],)
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(len(bytes(row["sig"])), 128 * 4)

    def test_returns_none_for_short_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = _make_app(tmp)
            doc = _make_doc(db)
            sig = compute_and_store(doc["id"], "too short", db)
            self.assertIsNone(sig)

    def test_upsert_replaces_existing_sig(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = _make_app(tmp)
            doc = _make_doc(db)
            sig1 = compute_and_store(doc["id"], _long_text(200, "alpha"), db)
            sig2 = compute_and_store(doc["id"], _long_text(200, "beta"), db)
            self.assertIsNotNone(sig1)
            self.assertIsNotNone(sig2)
            self.assertNotEqual(sig1, sig2)


# ── Unit: find_and_record_near_duplicates ─────────────────────────────────────


class TestFindNearDuplicates(unittest.TestCase):
    def test_near_identical_text_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = _make_app(tmp)
            doc_a = _make_doc(db, "Doc A")
            doc_b = _make_doc(db, "Doc B")

            base = [f"word{i}" for i in range(100)]
            text_a = " ".join(base)
            text_b = " ".join(base[:-2] + ["zzzz", "yyyy"])

            sig_a = compute_and_store(doc_a["id"], text_a, db)
            sig_b = compute_and_store(doc_b["id"], text_b, db)
            self.assertIsNotNone(sig_a)
            self.assertIsNotNone(sig_b)

            hits = find_and_record_near_duplicates(doc_b["id"], sig_b, db)
            self.assertTrue(any(h[0] == doc_a["id"] for h in hits))

            with db._lock:
                row = db._conn.execute(
                    """SELECT * FROM doc_dupes
                       WHERE (doc_a_id=? AND doc_b_id=?) OR (doc_a_id=? AND doc_b_id=?)""",
                    (doc_a["id"], doc_b["id"], doc_b["id"], doc_a["id"]),
                ).fetchone()
            self.assertIsNotNone(row)
            self.assertGreater(row["similarity"], 0.5)

    def test_no_false_positive_for_disjoint_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = _make_app(tmp)
            doc_a = _make_doc(db, "Doc A")
            doc_b = _make_doc(db, "Doc B")

            sig_a = compute_and_store(doc_a["id"], _long_text(200, "alpha"), db)
            sig_b = compute_and_store(doc_b["id"], _long_text(200, "zeta"), db)
            self.assertIsNotNone(sig_a)
            self.assertIsNotNone(sig_b)

            hits = find_and_record_near_duplicates(doc_b["id"], sig_b, db)
            self.assertEqual(len(hits), 0)

    def test_duplicate_pair_not_written_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = _make_app(tmp)
            doc_a = _make_doc(db, "Doc A")
            doc_b = _make_doc(db, "Doc B")

            base = [f"word{i}" for i in range(100)]
            text_a = " ".join(base)
            text_b = " ".join(base[:-1] + ["zzzz"])

            sig_a = compute_and_store(doc_a["id"], text_a, db)
            sig_b = compute_and_store(doc_b["id"], text_b, db)
            self.assertIsNotNone(sig_a)
            self.assertIsNotNone(sig_b)

            find_and_record_near_duplicates(doc_b["id"], sig_b, db)
            find_and_record_near_duplicates(doc_b["id"], sig_b, db)  # second call

            with db._lock:
                count = db._conn.execute(
                    """SELECT COUNT(*) FROM doc_dupes
                       WHERE (doc_a_id=? AND doc_b_id=?) OR (doc_a_id=? AND doc_b_id=?)""",
                    (doc_a["id"], doc_b["id"], doc_b["id"], doc_a["id"]),
                ).fetchone()[0]
            self.assertEqual(count, 1)


# ── Unit: resolve_near_duplicate ──────────────────────────────────────────────


class TestResolveNearDuplicate(unittest.TestCase):
    def test_keep_both_marks_resolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = _make_app(tmp)
            _, _, dupe_id = _make_pair(db)
            result = db.resolve_near_duplicate(dupe_id, "keep_both")
            self.assertIsNotNone(result)
            with db._lock:
                row = db._conn.execute(
                    "SELECT resolved, resolution FROM doc_dupes WHERE id=?", (dupe_id,)
                ).fetchone()
            self.assertEqual(row["resolved"], 1)
            self.assertEqual(row["resolution"], "keep_both")

    def test_mark_versions_creates_relationship(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = _make_app(tmp)
            doc_a, doc_b, dupe_id = _make_pair(db)
            db.resolve_near_duplicate(dupe_id, "mark_versions")
            with db._lock:
                rel = db._conn.execute(
                    """SELECT * FROM relationships
                       WHERE source_id=? AND target_id=? AND kind='DERIVED_FROM'""",
                    (doc_b["id"], doc_a["id"]),
                ).fetchone()
            self.assertIsNotNone(rel)

    def test_mark_superseded_updates_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = _make_app(tmp)
            doc_a, doc_b, dupe_id = _make_pair(db)
            db.resolve_near_duplicate(dupe_id, "mark_superseded")
            doc_b_after = db.get_document(doc_b["id"])
            self.assertEqual(doc_b_after["lifecycle"], "superseded")

    def test_invalid_action_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = _make_app(tmp)
            _, _, dupe_id = _make_pair(db)
            with self.assertRaises(ValueError, msg="action must be"):
                db.resolve_near_duplicate(dupe_id, "explode")

    def test_unknown_id_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = _make_app(tmp)
            result = db.resolve_near_duplicate(str(uuid.uuid4()), "keep_both")
            self.assertIsNone(result)

    def test_resolved_excluded_from_default_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, db = _make_app(tmp)
            _, _, dupe_id = _make_pair(db)
            # Appears before resolution
            self.assertTrue(
                any(r["id"] == dupe_id for r in db.list_near_duplicates(resolved=False))
            )
            # Disappears after
            db.resolve_near_duplicate(dupe_id, "keep_both")
            self.assertFalse(
                any(r["id"] == dupe_id for r in db.list_near_duplicates(resolved=False))
            )
            # Surfaces with resolved=True
            self.assertTrue(any(r["id"] == dupe_id for r in db.list_near_duplicates(resolved=True)))


# ── API: GET /library/duplicates ──────────────────────────────────────────────


class TestLibraryDuplicatesEndpoint(unittest.TestCase):
    def _setup(self):
        from fastapi.testclient import TestClient

        from tests.conftest import AUTH_HEADERS

        tmp = tempfile.mkdtemp()
        app, db = _make_app(tmp)
        client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
        return client, db

    def test_get_duplicates_returns_pairs(self):
        client, db = self._setup()
        _make_pair(db)
        resp = client.get("/api/library/duplicates")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertGreaterEqual(body["count"], 1)
        self.assertIn("pairs", body)

    def test_get_duplicates_not_captured_as_doc_id(self):
        """'duplicates' must NOT be matched by GET /library/{doc_id}."""
        client, db = self._setup()
        resp = client.get("/api/library/duplicates")
        # Must never return 404 "Document 'duplicates' not found"
        self.assertNotEqual(resp.status_code, 404)

    def test_get_duplicates_excludes_resolved_by_default(self):
        client, db = self._setup()
        _, _, dupe_id = _make_pair(db)
        db.resolve_near_duplicate(dupe_id, "keep_both")
        resp = client.get("/api/library/duplicates")
        body = resp.json()
        self.assertFalse(any(p["id"] == dupe_id for p in body["pairs"]))

    def test_get_duplicates_resolved_filter(self):
        client, db = self._setup()
        _, _, dupe_id = _make_pair(db)
        db.resolve_near_duplicate(dupe_id, "keep_both")
        resp = client.get("/api/library/duplicates?resolved=true")
        body = resp.json()
        self.assertTrue(any(p["id"] == dupe_id for p in body["pairs"]))

    def test_pair_includes_titles(self):
        client, db = self._setup()
        _make_pair(db)
        resp = client.get("/api/library/duplicates")
        self.assertEqual(resp.status_code, 200)
        pair = resp.json()["pairs"][0]
        self.assertIn("doc_a_title", pair)
        self.assertIn("doc_b_title", pair)
        self.assertIsNotNone(pair["doc_a_title"])


# ── API: POST /library/duplicates/{id}/resolve ────────────────────────────────


class TestResolveDuplicateEndpoint(unittest.TestCase):
    def _setup(self):
        from fastapi.testclient import TestClient

        from tests.conftest import AUTH_HEADERS

        tmp = tempfile.mkdtemp()
        app, db = _make_app(tmp)
        client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
        return client, db

    def test_keep_both(self):
        client, db = self._setup()
        _, _, dupe_id = _make_pair(db)
        resp = client.post(
            f"/api/library/duplicates/{dupe_id}/resolve",
            json={"action": "keep_both"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

    def test_mark_versions(self):
        client, db = self._setup()
        doc_a, doc_b, dupe_id = _make_pair(db)
        resp = client.post(
            f"/api/library/duplicates/{dupe_id}/resolve",
            json={"action": "mark_versions"},
        )
        self.assertEqual(resp.status_code, 200)
        with db._lock:
            rel = db._conn.execute(
                "SELECT * FROM relationships WHERE source_id=? AND kind='DERIVED_FROM'",
                (doc_b["id"],),
            ).fetchone()
        self.assertIsNotNone(rel)

    def test_mark_superseded_updates_doc_lifecycle(self):
        client, db = self._setup()
        doc_a, doc_b, dupe_id = _make_pair(db)
        resp = client.post(
            f"/api/library/duplicates/{dupe_id}/resolve",
            json={"action": "mark_superseded"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(db.get_document(doc_b["id"])["lifecycle"], "superseded")

    def test_invalid_action_returns_400(self):
        client, db = self._setup()
        _, _, dupe_id = _make_pair(db)
        resp = client.post(
            f"/api/library/duplicates/{dupe_id}/resolve",
            json={"action": "destroy"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_unknown_pair_returns_404(self):
        client, db = self._setup()
        resp = client.post(
            f"/api/library/duplicates/{uuid.uuid4()}/resolve",
            json={"action": "keep_both"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_resolved_pair_no_longer_in_default_list(self):
        client, db = self._setup()
        _, _, dupe_id = _make_pair(db)
        client.post(
            f"/api/library/duplicates/{dupe_id}/resolve",
            json={"action": "keep_both"},
        )
        resp = client.get("/api/library/duplicates")
        self.assertFalse(any(p["id"] == dupe_id for p in resp.json()["pairs"]))
