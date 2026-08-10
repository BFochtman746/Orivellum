"""Tests for nightly memory consolidation — dedup, promote, conflict registry.

Covers:
  - Schema v100: memory_conflicts table + indexes
  - DB methods: record_memory_conflict, get_memory_conflicts,
                resolve_memory_conflict
  - Nightshift pass: _pass_memory_dedup
  - Nightshift pass: _pass_memory_promote
  - _memory_text_similarity helper
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC
from pathlib import Path

# Ensure src/ is on the path before any import
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _make_db(path: str):
    from orivellum.database.db import OrivellumDB

    return OrivellumDB(path)


# ─── Schema v100 ──────────────────────────────────────────────────────────────


class TestSchemaV100(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def test_memory_conflicts_table_exists(self):
        row = self.db._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_conflicts'"
        ).fetchone()
        self.assertIsNotNone(row, "memory_conflicts table must exist after v100 migration")

    def test_memory_conflicts_columns(self):
        cols = {
            r[1] for r in self.db._conn.execute("PRAGMA table_info(memory_conflicts)").fetchall()
        }
        for col in (
            "id",
            "memory_id_a",
            "memory_id_b",
            "detected_at",
            "resolved",
            "resolution",
            "resolved_at",
        ):
            self.assertIn(col, cols, f"Column '{col}' must exist in memory_conflicts")

    def test_memory_conflicts_unique_constraint(self):
        """INSERT OR IGNORE on the same pair must produce exactly one row."""
        import uuid

        aid, bid = str(uuid.uuid4()), str(uuid.uuid4())
        # Normalise order the way the DB method does
        id_a, id_b = sorted([aid, bid])
        now = "2026-01-01T00:00:00"
        for _ in range(3):
            self.db._conn.execute(
                "INSERT OR IGNORE INTO memory_conflicts(id, memory_id_a, memory_id_b, detected_at, resolved)"
                " VALUES(?,?,?,?,0)",
                (str(uuid.uuid4()), id_a, id_b, now),
            )
        self.db._conn.commit()
        count = self.db._conn.execute(
            "SELECT COUNT(*) FROM memory_conflicts WHERE memory_id_a=? AND memory_id_b=?",
            (id_a, id_b),
        ).fetchone()[0]
        self.assertEqual(count, 1, "UNIQUE constraint must prevent duplicate pairs")


# ─── DB methods ───────────────────────────────────────────────────────────────


class TestRecordMemoryConflict(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def _seed_memory(self, key: str, value: str) -> str:
        self.db.upsert_memory_fact(key, value)
        row = self.db._conn.execute(
            "SELECT id FROM user_memory WHERE key=? AND valid_to IS NULL",
            (key,),
        ).fetchone()
        return row["id"]

    def test_returns_conflict_id(self):
        aid = self._seed_memory("lang_a", "Python")
        bid = self._seed_memory("lang_b", "Go")
        cid = self.db.record_memory_conflict(aid, bid)
        self.assertIsNotNone(cid)
        self.assertIsInstance(cid, str)
        self.assertGreater(len(cid), 0)

    def test_idempotent_returns_same_id(self):
        aid = self._seed_memory("theme_a", "dark")
        bid = self._seed_memory("theme_b", "light")
        cid1 = self.db.record_memory_conflict(aid, bid)
        cid2 = self.db.record_memory_conflict(aid, bid)
        self.assertEqual(cid1, cid2, "Same pair must return same conflict id")

    def test_order_independent(self):
        """(A,B) and (B,A) must resolve to the same row."""
        aid = self._seed_memory("color_a", "red")
        bid = self._seed_memory("color_b", "blue")
        cid1 = self.db.record_memory_conflict(aid, bid)
        cid2 = self.db.record_memory_conflict(bid, aid)
        self.assertEqual(cid1, cid2, "Pair order must not produce two distinct rows")

    def test_conflict_stored_with_resolved_false(self):
        aid = self._seed_memory("x", "foo")
        bid = self._seed_memory("y", "bar")
        cid = self.db.record_memory_conflict(aid, bid)
        row = self.db._conn.execute(
            "SELECT resolved FROM memory_conflicts WHERE id=?", (cid,)
        ).fetchone()
        self.assertEqual(row["resolved"], 0)


class TestGetMemoryConflicts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def _seed(self, key: str, value: str) -> str:
        self.db.upsert_memory_fact(key, value)
        return self.db._conn.execute(
            "SELECT id FROM user_memory WHERE key=? AND valid_to IS NULL", (key,)
        ).fetchone()["id"]

    def test_unresolved_conflicts_returned(self):
        a = self._seed("k1", "v1")
        b = self._seed("k2", "v2")
        self.db.record_memory_conflict(a, b)
        results = self.db.get_memory_conflicts(resolved=False)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["resolved"], 0)

    def test_resolved_conflicts_excluded_by_default(self):
        a = self._seed("x1", "v1")
        b = self._seed("x2", "v2")
        cid = self.db.record_memory_conflict(a, b)
        self.db.resolve_memory_conflict(cid, "dismissed")
        unresolved = self.db.get_memory_conflicts(resolved=False)
        self.assertEqual(len(unresolved), 0)

    def test_resolved_filter_returns_resolved(self):
        a = self._seed("p1", "v1")
        b = self._seed("p2", "v2")
        cid = self.db.record_memory_conflict(a, b)
        self.db.resolve_memory_conflict(cid, "keep_a")
        resolved = self.db.get_memory_conflicts(resolved=True)
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]["resolution"], "keep_a")

    def test_joined_memory_key_values_present(self):
        a = self._seed("joined_key_a", "some value A")
        b = self._seed("joined_key_b", "some value B")
        self.db.record_memory_conflict(a, b)
        items = self.db.get_memory_conflicts(resolved=False)
        self.assertEqual(len(items), 1)
        c = items[0]
        # key_a and key_b will be whichever was stored in the sorted order
        keys = {c.get("key_a"), c.get("key_b")}
        self.assertIn("joined_key_a", keys)
        self.assertIn("joined_key_b", keys)


class TestResolveMemoryConflict(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def _seed(self, key: str, value: str) -> str:
        self.db.upsert_memory_fact(key, value)
        return self.db._conn.execute(
            "SELECT id FROM user_memory WHERE key=? AND valid_to IS NULL", (key,)
        ).fetchone()["id"]

    def test_resolve_sets_resolved_flag(self):
        a = self._seed("rk1", "v1")
        b = self._seed("rk2", "v2")
        cid = self.db.record_memory_conflict(a, b)
        result = self.db.resolve_memory_conflict(cid, "keep_a")
        self.assertTrue(result)
        row = self.db._conn.execute(
            "SELECT resolved, resolution FROM memory_conflicts WHERE id=?", (cid,)
        ).fetchone()
        self.assertEqual(row["resolved"], 1)
        self.assertEqual(row["resolution"], "keep_a")

    def test_resolve_nonexistent_returns_false(self):
        self.assertFalse(self.db.resolve_memory_conflict("no-such-id", "dismissed"))

    def test_resolve_already_resolved_returns_false(self):
        a = self._seed("rk3", "v3")
        b = self._seed("rk4", "v4")
        cid = self.db.record_memory_conflict(a, b)
        self.db.resolve_memory_conflict(cid, "keep_b")
        # Second resolution attempt
        result = self.db.resolve_memory_conflict(cid, "dismissed")
        self.assertFalse(result, "Already-resolved conflict must return False")

    def test_invalid_resolution_falls_back_to_dismissed(self):
        a = self._seed("rk5", "v5")
        b = self._seed("rk6", "v6")
        cid = self.db.record_memory_conflict(a, b)
        self.db.resolve_memory_conflict(cid, "INVALID_RESOLUTION")
        row = self.db._conn.execute(
            "SELECT resolution FROM memory_conflicts WHERE id=?", (cid,)
        ).fetchone()
        self.assertEqual(row["resolution"], "dismissed")


# ─── resolve_memory_conflict_atomic ──────────────────────────────────────────


class TestConflictCallerOrderPreserved(unittest.TestCase):
    """Verify that record_memory_conflict stores in caller-provided order."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def _seed(self, key: str, value: str) -> str:
        self.db.upsert_memory_fact(key, value)
        return self.db._conn.execute(
            "SELECT id FROM user_memory WHERE key=? AND valid_to IS NULL", (key,)
        ).fetchone()["id"]

    def test_memory_id_a_is_caller_first_arg(self):
        """memory_id_a must be the first argument as provided by the caller."""
        a = self._seed("newer_fact", "Python is my favourite")
        b = self._seed("older_fact", "Go is my favourite")
        cid = self.db.record_memory_conflict(a, b)
        row = self.db._conn.execute(
            "SELECT memory_id_a, memory_id_b FROM memory_conflicts WHERE id=?", (cid,)
        ).fetchone()
        self.assertEqual(
            row["memory_id_a"],
            a,
            "memory_id_a must be the first caller argument (caller-provided order)",
        )
        self.assertEqual(row["memory_id_b"], b, "memory_id_b must be the second caller argument")

    def test_reverse_order_returns_same_conflict_id(self):
        """(A,B) and (B,A) calls must return the same conflict id (idempotent)."""
        a = self._seed("fact_x", "X value one")
        b = self._seed("fact_y", "Y value two")
        cid1 = self.db.record_memory_conflict(a, b)
        cid2 = self.db.record_memory_conflict(b, a)
        self.assertEqual(
            cid1, cid2, "Reverse-order call must return same conflict id (no duplicate row)"
        )

    def test_keep_a_retains_intended_fact(self):
        """keep_a must retain the memory whose content was passed as memory_id_a.

        This verifies that the semantic ordering is end-to-end: the caller (dedup
        pass) stores (newer_id, older_id) → user clicks 'keep_a' (Keep newer) →
        the newer fact survives in the DB.
        """
        newer_id = self._seed("lang_newer", "I use Rust for systems work")
        older_id = self._seed("lang_older", "I use C for systems work")
        # Explicitly pass (newer, older) — dedup convention
        cid = self.db.record_memory_conflict(newer_id, older_id)
        ok, reason = self.db.resolve_memory_conflict_atomic(cid, "keep_a")
        self.assertTrue(ok, reason)
        # The newer fact (Rust) must still be current
        row = self.db._conn.execute(
            "SELECT valid_to FROM user_memory WHERE id=?", (newer_id,)
        ).fetchone()
        self.assertIsNone(row["valid_to"], "keep_a must leave memory_id_a (newer, Rust) current")
        # The older fact (C) must be soft-deleted
        old_row = self.db._conn.execute(
            "SELECT valid_to FROM user_memory WHERE id=?", (older_id,)
        ).fetchone()
        self.assertIsNotNone(old_row["valid_to"], "keep_a must soft-delete memory_id_b (older, C)")

    def test_keep_b_retains_intended_fact(self):
        """keep_b must retain the memory whose content was passed as memory_id_b."""
        newer_id = self._seed("pref_newer", "I prefer coffee in the morning")
        older_id = self._seed("pref_older", "I prefer tea in the morning")
        cid = self.db.record_memory_conflict(newer_id, older_id)
        ok, reason = self.db.resolve_memory_conflict_atomic(cid, "keep_b")
        self.assertTrue(ok, reason)
        # The older fact (tea) is memory_id_b and must survive
        row_b = self.db._conn.execute(
            "SELECT valid_to FROM user_memory WHERE id=?", (older_id,)
        ).fetchone()
        self.assertIsNone(row_b["valid_to"], "keep_b must leave memory_id_b (older, tea) current")
        # The newer fact (coffee) is memory_id_a and must be soft-deleted
        row_a = self.db._conn.execute(
            "SELECT valid_to FROM user_memory WHERE id=?", (newer_id,)
        ).fetchone()
        self.assertIsNotNone(
            row_a["valid_to"], "keep_b must soft-delete memory_id_a (newer, coffee)"
        )


class TestResolveMemoryConflictAtomic(unittest.TestCase):
    """Verify the atomic keep-side conflict resolution path."""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def _seed(self, key: str, value: str) -> str:
        self.db.upsert_memory_fact(key, value)
        return self.db._conn.execute(
            "SELECT id FROM user_memory WHERE key=? AND valid_to IS NULL", (key,)
        ).fetchone()["id"]

    def _is_current(self, memory_id: str) -> bool:
        row = self.db._conn.execute(
            "SELECT valid_to FROM user_memory WHERE id=?", (memory_id,)
        ).fetchone()
        return row is not None and row["valid_to"] is None

    def test_keep_a_soft_deletes_b(self):
        """keep_a must soft-delete memory B, leave A current."""
        a = self._seed("ka_lang", "Python")
        b = self._seed("kb_lang", "Go")
        cid = self.db.record_memory_conflict(a, b)
        ok, reason = self.db.resolve_memory_conflict_atomic(cid, "keep_a")
        self.assertTrue(ok, f"Expected success, got: {reason}")
        # Identify which id was stored as memory_id_a vs memory_id_b
        conflict = self.db._conn.execute(
            "SELECT memory_id_a, memory_id_b FROM memory_conflicts WHERE id=?", (cid,)
        ).fetchone()
        keep_id = conflict["memory_id_a"]
        drop_id = conflict["memory_id_b"]
        self.assertTrue(self._is_current(keep_id), "Kept side must still be current")
        self.assertFalse(self._is_current(drop_id), "Losing side must be soft-deleted")

    def test_keep_b_soft_deletes_a(self):
        """keep_b must soft-delete memory A, leave B current."""
        a = self._seed("kb_theme", "dark")
        b = self._seed("ka_theme", "light")
        cid = self.db.record_memory_conflict(a, b)
        ok, reason = self.db.resolve_memory_conflict_atomic(cid, "keep_b")
        self.assertTrue(ok, f"Expected success, got: {reason}")
        conflict = self.db._conn.execute(
            "SELECT memory_id_a, memory_id_b FROM memory_conflicts WHERE id=?", (cid,)
        ).fetchone()
        drop_id = conflict["memory_id_a"]
        keep_id = conflict["memory_id_b"]
        self.assertTrue(self._is_current(keep_id), "Kept side must still be current")
        self.assertFalse(self._is_current(drop_id), "Losing side must be soft-deleted")

    def test_dismissed_leaves_both_rows_current(self):
        """dismissed must mark conflict resolved without touching either memory row."""
        a = self._seed("dis_x", "value X")
        b = self._seed("dis_y", "value Y")
        cid = self.db.record_memory_conflict(a, b)
        ok, _ = self.db.resolve_memory_conflict_atomic(cid, "dismissed")
        self.assertTrue(ok)
        self.assertTrue(self._is_current(a), "Row A must remain current after dismiss")
        self.assertTrue(self._is_current(b), "Row B must remain current after dismiss")

    def test_conflict_marked_resolved_after_atomic_call(self):
        a = self._seed("res_p", "p val")
        b = self._seed("res_q", "q val")
        cid = self.db.record_memory_conflict(a, b)
        self.db.resolve_memory_conflict_atomic(cid, "keep_a")
        row = self.db._conn.execute(
            "SELECT resolved, resolution FROM memory_conflicts WHERE id=?", (cid,)
        ).fetchone()
        self.assertEqual(row["resolved"], 1)
        self.assertIn(row["resolution"], ("keep_a", "keep_b"))

    def test_nonexistent_conflict_returns_false(self):
        ok, reason = self.db.resolve_memory_conflict_atomic("no-such-uuid", "dismissed")
        self.assertFalse(ok)
        self.assertIn("not found", reason.lower())

    def test_already_resolved_returns_false(self):
        a = self._seed("ar_m", "m val")
        b = self._seed("ar_n", "n val")
        cid = self.db.record_memory_conflict(a, b)
        self.db.resolve_memory_conflict_atomic(cid, "keep_a")
        ok, reason = self.db.resolve_memory_conflict_atomic(cid, "keep_b")
        self.assertFalse(ok)
        self.assertIn("already resolved", reason.lower())

    def test_invalid_resolution_falls_back_to_dismissed(self):
        a = self._seed("inv_u", "u val")
        b = self._seed("inv_v", "v val")
        cid = self.db.record_memory_conflict(a, b)
        ok, _ = self.db.resolve_memory_conflict_atomic(cid, "BOGUS_VALUE")
        self.assertTrue(ok)
        row = self.db._conn.execute(
            "SELECT resolution FROM memory_conflicts WHERE id=?", (cid,)
        ).fetchone()
        self.assertEqual(row["resolution"], "dismissed")

    def test_losing_side_already_expired_still_succeeds(self):
        """If the losing memory row is already expired, resolution must still succeed."""
        a = self._seed("exp_a", "already gone")
        b = self._seed("exp_b", "still here")
        cid = self.db.record_memory_conflict(a, b)
        # Manually expire one row before resolving
        self.db._conn.execute(
            "UPDATE user_memory SET valid_to='2020-01-01T00:00:00' WHERE id=?", (a,)
        )
        self.db._conn.commit()
        # Resolve with keep_b (so A is the intended loser — already expired)
        conflict = self.db._conn.execute(
            "SELECT memory_id_a FROM memory_conflicts WHERE id=?", (cid,)
        ).fetchone()
        # If a is memory_id_a, keep_b drops a; if it's memory_id_b, keep_a drops a
        mem_a_id = conflict["memory_id_a"]
        resolution = "keep_b" if mem_a_id == a else "keep_a"
        ok, reason = self.db.resolve_memory_conflict_atomic(cid, resolution)
        self.assertTrue(ok, f"Should succeed even when loser is already expired: {reason}")

    def test_no_memory_rows_changed_after_failed_resolution(self):
        """A failed resolution must not leave any memory row in a partially-updated state."""
        a = self._seed("fail_c", "c val")
        b = self._seed("fail_d", "d val")
        cid = self.db.record_memory_conflict(a, b)
        # Simulate failure by resolving twice — second call must not touch rows
        self.db.resolve_memory_conflict_atomic(cid, "dismissed")
        # Both rows are still current (dismissed doesn't delete either)
        ok, _ = self.db.resolve_memory_conflict_atomic(cid, "keep_a")
        self.assertFalse(ok, "Second resolution must fail")
        # Memory rows must be unchanged (both still current)
        self.assertTrue(self._is_current(a))
        self.assertTrue(self._is_current(b))


# ─── _memory_text_similarity ──────────────────────────────────────────────────


class TestMemoryTextSimilarity(unittest.TestCase):
    def _sim(self, a: str, b: str) -> float:
        from orivellum.capabilities.nightshift import _memory_text_similarity

        return _memory_text_similarity(a, b)

    def test_identical_strings_score_one(self):
        self.assertAlmostEqual(self._sim("I prefer Python", "I prefer Python"), 1.0)

    def test_empty_strings_score_one(self):
        self.assertAlmostEqual(self._sim("", ""), 1.0)

    def test_one_empty_scores_zero(self):
        self.assertAlmostEqual(self._sim("some text", ""), 0.0)

    def test_completely_different_scores_low(self):
        score = self._sim("I prefer Python programming language", "strawberry ice cream dessert")
        self.assertLess(score, 0.20)

    def test_near_duplicate_scores_high(self):
        score = self._sim(
            "My favourite language is Python",
            "My favorite programming language is Python",
        )
        self.assertGreater(score, 0.50)

    def test_punctuation_normalised(self):
        # Punctuation should not inflate dissimilarity
        score = self._sim("I like Python.", "I like Python!")
        self.assertGreater(score, 0.90)


# ─── _pass_memory_dedup ───────────────────────────────────────────────────────


class TestPassMemoryDedup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def _run_dedup(self):
        from orivellum.capabilities.nightshift import _pass_memory_dedup

        report: list[str] = []
        _pass_memory_dedup(self.db, report)
        return report

    def _current_rows(self):
        return [
            dict(r)
            for r in self.db._conn.execute(
                "SELECT id, key, value, valid_to FROM user_memory WHERE valid_to IS NULL"
            ).fetchall()
        ]

    def test_no_facts_runs_without_error(self):
        """Pass must be a no-op and not raise when there are no memory rows."""
        report = self._run_dedup()
        # No crash and no unexpected report entries
        self.assertNotIn("⚠", " ".join(report))

    def test_same_key_exact_duplicate_merged(self):
        """Two current rows with the same key and near-identical value → only one survives."""
        # Seed the same key twice by bypassing the upsert (which would soft-delete the first)
        import uuid as _uuid

        now = "2026-01-01T00:00:00+00:00"
        older = "2026-01-01T00:00:00+00:00"
        newer = "2026-01-02T00:00:00+00:00"
        self.db._conn.executemany(
            "INSERT INTO user_memory(id, key, value, memory_type, valid_from, txn_time, created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            [
                (str(_uuid.uuid4()), "dup_key", "I prefer Python", "semantic", older, older, older),
                (
                    str(_uuid.uuid4()),
                    "dup_key",
                    "I prefer Python.",
                    "semantic",
                    newer,
                    newer,
                    newer,
                ),
            ],
        )
        self.db._conn.commit()
        self._run_dedup()
        current = self._current_rows()
        dup_rows = [r for r in current if r["key"] == "dup_key"]
        self.assertEqual(len(dup_rows), 1, "Only one current row must survive after dedup")

    def test_same_key_conflict_flagged(self):
        """Same key but contradictory values → conflict recorded, neither row deleted.

        Values are chosen so that word-set Jaccard similarity is well below 0.50:
          A: "morning sunrise coffee before sunrise breakfast"  → {"morning","sunrise","coffee","before","breakfast"}
          B: "night owl late evening deadlines midnight"        → {"night","owl","late","evening","deadlines","midnight"}
          intersection = {} → Jaccard = 0.0
        """
        import uuid as _uuid

        t1 = "2026-01-01T00:00:00+00:00"
        t2 = "2026-01-02T00:00:00+00:00"
        self.db._conn.executemany(
            "INSERT INTO user_memory(id, key, value, memory_type, valid_from, txn_time, created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            [
                (
                    str(_uuid.uuid4()),
                    "conflict_key",
                    "morning sunrise coffee before breakfast",
                    "semantic",
                    t1,
                    t1,
                    t1,
                ),
                (
                    str(_uuid.uuid4()),
                    "conflict_key",
                    "night owl late evening deadlines midnight",
                    "semantic",
                    t2,
                    t2,
                    t2,
                ),
            ],
        )
        self.db._conn.commit()
        self._run_dedup()
        # Both rows must still be current (dedup doesn't delete conflicts)
        current = self._current_rows()
        conflict_rows = [r for r in current if r["key"] == "conflict_key"]
        self.assertEqual(len(conflict_rows), 2, "Conflict rows must not be deleted by dedup")
        # A conflict record must have been registered
        conflicts = self.db.get_memory_conflicts(resolved=False)
        self.assertGreater(len(conflicts), 0, "Conflict must be recorded for contradictory values")

    def test_cross_key_near_duplicate_registered_as_conflict(self):
        """Cross-key near-duplicates must be registered as conflicts, NOT auto-deleted.

        Different keys may encode distinct facts even when text is similar.
        Auto-deletion is too aggressive; the dedup pass must flag them for
        user review instead.
        """
        import uuid as _uuid

        t1 = "2026-01-01T00:00:00+00:00"
        t2 = "2026-01-02T00:00:00+00:00"
        aid = str(_uuid.uuid4())
        bid = str(_uuid.uuid4())
        self.db._conn.executemany(
            "INSERT INTO user_memory(id, key, value, memory_type, valid_from, txn_time, created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            [
                (
                    aid,
                    "pref_lang",
                    "My favourite language is Rust and systems programming",
                    "semantic",
                    t1,
                    t1,
                    t1,
                ),
                (
                    bid,
                    "fav_language",
                    "My favourite language is Rust and systems programming",
                    "semantic",
                    t2,
                    t2,
                    t2,
                ),
            ],
        )
        self.db._conn.commit()
        self._run_dedup()
        # BOTH rows must still be current — no auto-deletion for cross-key facts
        current = self._current_rows()
        cross_rows = [r for r in current if r["key"] in ("pref_lang", "fav_language")]
        self.assertEqual(
            len(cross_rows),
            2,
            "Cross-key near-dups must not be auto-deleted; both rows must remain",
        )
        # A conflict must have been registered for user review
        conflicts = self.db.get_memory_conflicts(resolved=False)
        cross_conflict = [
            c for c in conflicts if set([c.get("memory_id_a"), c.get("memory_id_b")]) == {aid, bid}
        ]
        self.assertGreater(
            len(cross_conflict),
            0,
            "A conflict record must be created for cross-key near-duplicates",
        )

    def test_dedup_is_idempotent(self):
        """Running dedup twice must produce the same result as running it once."""
        import uuid as _uuid

        t1 = "2026-01-01T00:00:00+00:00"
        t2 = "2026-01-02T00:00:00+00:00"
        self.db._conn.executemany(
            "INSERT INTO user_memory(id, key, value, memory_type, valid_from, txn_time, created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            [
                (str(_uuid.uuid4()), "idem_key", "I prefer dark mode", "semantic", t1, t1, t1),
                (str(_uuid.uuid4()), "idem_key", "I prefer dark mode.", "semantic", t2, t2, t2),
            ],
        )
        self.db._conn.commit()
        self._run_dedup()
        count_after_first = len(self._current_rows())
        self._run_dedup()
        count_after_second = len(self._current_rows())
        self.assertEqual(
            count_after_first,
            count_after_second,
            "Idempotency: second dedup run must not change row count",
        )


# ─── _pass_memory_promote ─────────────────────────────────────────────────────


class TestPassMemoryPromote(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        self.db = _make_db(self.tmp)

    def tearDown(self):
        self.db._conn.close()
        Path(self.tmp).unlink(missing_ok=True)

    def _run_promote(self):
        from orivellum.capabilities.nightshift import _pass_memory_promote

        report: list[str] = []
        _pass_memory_promote(self.db, report)
        return report

    def _seed_episodic_history(self, key: str, values: list[str]) -> None:
        """Insert multiple episodic rows for a key (historical rows + one current)."""
        import uuid as _uuid
        from datetime import datetime, timedelta

        base = datetime(2026, 1, 1, tzinfo=UTC)
        for i, value in enumerate(values):
            ts = (base + timedelta(hours=i)).isoformat()
            next_ts = (base + timedelta(hours=i + 1)).isoformat() if i < len(values) - 1 else None
            self.db._conn.execute(
                "INSERT INTO user_memory(id, key, value, memory_type, valid_from, valid_to, txn_time, created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (str(_uuid.uuid4()), key, value, "episodic", ts, next_ts, ts, ts),
            )
        self.db._conn.commit()

    def test_fewer_than_threshold_not_promoted(self):
        """2 episodic occurrences must not trigger promotion (threshold = 3)."""
        self._seed_episodic_history("rare_key", ["visit A", "visit B"])
        self._run_promote()
        row = self.db._conn.execute(
            "SELECT memory_type FROM user_memory WHERE key='rare_key' AND valid_to IS NULL"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(
            row["memory_type"], "episodic", "Key with < 3 episodic rows must not be promoted"
        )

    def test_three_or_more_episodic_gets_promoted(self):
        """3 episodic occurrences must promote the current row to semantic."""
        self._seed_episodic_history("frequent_key", ["event A", "event B", "event C"])
        self._run_promote()
        row = self.db._conn.execute(
            "SELECT memory_type FROM user_memory WHERE key='frequent_key' AND valid_to IS NULL"
        ).fetchone()
        self.assertIsNotNone(row, "A current row must exist after promotion")
        self.assertEqual(
            row["memory_type"],
            "semantic",
            "Current row must be promoted to semantic after 3+ episodic rows",
        )

    def test_already_semantic_not_double_promoted(self):
        """A key whose current row is already semantic must not be touched."""
        self.db.upsert_memory_fact("semantic_key", "I always do X", memory_type="semantic")
        before_count = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM user_memory WHERE key='semantic_key'"
        ).fetchone()["n"]
        self._run_promote()
        after_count = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM user_memory WHERE key='semantic_key'"
        ).fetchone()["n"]
        self.assertEqual(
            before_count, after_count, "Already-semantic key must not create an extra row"
        )

    def test_promotion_is_idempotent(self):
        """Running promote twice must produce the same result as once."""
        self._seed_episodic_history("idem_promote", ["ev A", "ev B", "ev C"])
        self._run_promote()
        count_1 = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM user_memory WHERE key='idem_promote' AND valid_to IS NULL"
        ).fetchone()["n"]
        self._run_promote()
        count_2 = self.db._conn.execute(
            "SELECT COUNT(*) AS n FROM user_memory WHERE key='idem_promote' AND valid_to IS NULL"
        ).fetchone()["n"]
        self.assertEqual(
            count_1, count_2, "Promotion is idempotent: second run must not create extra rows"
        )

    def test_promotion_preserves_source_evidence_id(self):
        """Promoted semantic row must carry the same source_evidence_id as the
        original episodic row — evidence provenance survives promotion."""
        import uuid as _uuid

        evidence_id = str(_uuid.uuid4())
        conv_id = str(_uuid.uuid4())
        key = "ev_pres"
        now_t = "2026-01-01T00:00:00+00:00"
        # Insert 3 episodic rows directly; set evidence on the current one
        for i in range(3):
            ts = f"2026-01-0{i + 1}T00:00:00+00:00"
            is_current = i == 2
            self.db._conn.execute(
                """INSERT INTO user_memory
                   (id, key, value, memory_type, valid_from, valid_to,
                    txn_time, created_at, source_conv_id, source_evidence_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(_uuid.uuid4()),
                    key,
                    f"fact v{i}",
                    "episodic",
                    ts,
                    None if is_current else ts,
                    ts,
                    ts,
                    conv_id if is_current else None,
                    evidence_id if is_current else None,
                ),
            )
        self.db._conn.commit()
        self._run_promote()
        # Fetch the new semantic row
        sem = self.db._conn.execute(
            "SELECT source_conv_id, source_evidence_id FROM user_memory "
            "WHERE key=? AND memory_type='semantic' AND valid_to IS NULL",
            (key,),
        ).fetchone()
        self.assertIsNotNone(sem, "A semantic row must exist after promotion")
        self.assertEqual(
            sem["source_evidence_id"],
            evidence_id,
            "source_evidence_id must be preserved across promotion",
        )
        self.assertEqual(
            sem["source_conv_id"], conv_id, "source_conv_id must be preserved across promotion"
        )

    def test_promotion_report_mentions_promoted_count(self):
        """The report list must mention the promoted count when promotion happens."""
        self._seed_episodic_history("report_key", ["ev 1", "ev 2", "ev 3"])
        report = self._run_promote()
        combined = " ".join(report)
        self.assertIn(
            "promote", combined.lower(), "Report must contain 'promote' when facts are promoted"
        )


if __name__ == "__main__":
    unittest.main()
