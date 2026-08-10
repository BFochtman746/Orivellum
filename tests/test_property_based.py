"""Property-based tests (Hypothesis) for forensically critical logic.

These hammer the invariants that the product's credibility rests on with
thousands of random inputs, instead of a handful of hand-picked examples:

- the state machine never permits a transition outside its declared graph
- MinHash similarity is a true similarity measure (bounded, symmetric,
  self-identical, deterministic)
- the GENESIS hash-chain ledger detects any tampering with any entry
- Workbench file snapshots record exact SHA-256 / size for arbitrary trees

Run: uv run --with pytest --with hypothesis pytest tests/test_property_based.py
"""

from __future__ import annotations

import hashlib
import pathlib
import sqlite3
import sys
import tempfile
import unittest

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

SETTINGS = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
DB_SETTINGS = settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.too_slow])

# ─── State machine ────────────────────────────────────────────────────────────

_state_names = st.sampled_from(["draft", "review", "final", "archived", "void", "x", "y"])
_graphs = st.dictionaries(_state_names, st.sets(_state_names, max_size=7), max_size=7)


class StateMachineProperties(unittest.TestCase):
    @SETTINGS
    @given(graph=_graphs, frm=_state_names, to=_state_names)
    def test_assert_and_can_agree_and_never_escape_graph(self, graph, frm, to):
        from orivellum.capabilities.state_machine import InvalidTransitionError, StateMachine

        sm = StateMachine(graph)
        allowed = to in graph.get(frm, set())
        self.assertEqual(sm.can_transition(frm, to), allowed)
        if allowed:
            sm.assert_transition(frm, to)  # must not raise
        else:
            with self.assertRaises(InvalidTransitionError):
                sm.assert_transition(frm, to)
        # allowed_from never invents states not in the declared graph
        self.assertTrue(set(sm.allowed_from(frm)) <= graph.get(frm, set()))


# ─── MinHash near-duplicate sketches ─────────────────────────────────────────

_texts = st.text(
    alphabet=st.characters(codec="utf-8", categories=["L", "N", "Zs"]),
    min_size=40,
    max_size=400,
)


class MinHashProperties(unittest.TestCase):
    @SETTINGS
    @given(a=_texts, b=_texts)
    def test_similarity_is_bounded_symmetric_and_self_identical(self, a, b):
        from orivellum.capabilities.dedup import _jaccard, _minhash, _shingles

        sa, sb = _shingles(a), _shingles(b)
        if not sa or not sb:
            return  # degenerate whitespace-only inputs have no shingles
        ha, hb = _minhash(sa), _minhash(sb)
        self.assertEqual(_minhash(sa), ha)  # deterministic
        self.assertEqual(_jaccard(ha, ha), 1.0)  # self-similarity
        sim = _jaccard(ha, hb)
        self.assertEqual(sim, _jaccard(hb, ha))  # symmetric
        self.assertGreaterEqual(sim, 0.0)
        self.assertLessEqual(sim, 1.0)

    @SETTINGS
    @given(t=_texts)
    def test_identical_text_always_sketches_identically(self, t):
        from orivellum.capabilities.dedup import _minhash, _shingles

        s = _shingles(t)
        if not s:
            return
        self.assertEqual(_minhash(s), _minhash(set(s)))


# ─── GENESIS hash-chain ledger ───────────────────────────────────────────────

_payloads = st.lists(
    st.dictionaries(
        st.text(alphabet="abcdefgh_", min_size=1, max_size=10),
        st.one_of(st.text(max_size=40), st.integers(), st.booleans()),
        max_size=4,
    ),
    min_size=1,
    max_size=8,
)


def _ledger_conn(tmp: str) -> sqlite3.Connection:
    from orivellum.database.db import OrivellumDB

    db = OrivellumDB(str(pathlib.Path(tmp) / "prop.db"))
    conn = db._conn  # noqa: SLF001 — tests drive the raw ledger helpers
    # These properties test the hash chain, not referential integrity; skip
    # creating the whole works/objects/genesis_books parent-row chain.
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


class LedgerProperties(unittest.TestCase):
    @DB_SETTINGS
    @given(payloads=_payloads, victim=st.integers(min_value=0, max_value=100))
    def test_any_tampered_entry_breaks_verification(self, payloads, victim):
        from orivellum.capabilities.genesis.gates import ledger_append
        from orivellum.capabilities.genesis.seal import verify_ledger

        with tempfile.TemporaryDirectory() as tmp:
            conn = _ledger_conn(tmp)
            for p in payloads:
                ledger_append(conn, "book-1", "note", p)
            ok, _ = verify_ledger(conn, "book-1")
            self.assertTrue(ok, "freshly written chain must verify")

            seq = victim % len(payloads)  # ledger seq is 0-based
            conn.execute(
                "UPDATE genesis_ledger SET payload = payload || 'X' "
                "WHERE book_id='book-1' AND seq=?",
                (seq,),
            )
            conn.commit()
            ok, reason = verify_ledger(conn, "book-1")
            self.assertFalse(ok, f"tampering seq={seq} must be detected ({reason})")


# ─── Workbench snapshots ─────────────────────────────────────────────────────

_file_trees = st.dictionaries(
    st.from_regex(r"[a-z][a-z0-9_]{0,8}(/[a-z][a-z0-9_]{0,8})?\.(txt|py|json)", fullmatch=True),
    st.binary(max_size=2048),
    min_size=1,
    max_size=10,
)


class SnapshotProperties(unittest.TestCase):
    @DB_SETTINGS
    @given(tree=_file_trees)
    def test_snapshot_records_exact_hash_and_size_for_every_file(self, tree):
        from orivellum.capabilities.workbench import _snapshot

        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            for rel, blob in tree.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(blob)
            snap = {f["name"]: f for f in _snapshot(root)}
            self.assertEqual(set(snap), set(tree))
            for rel, blob in tree.items():
                self.assertEqual(snap[rel]["sha256"], hashlib.sha256(blob).hexdigest())
                self.assertEqual(snap[rel]["size"], len(blob))


if __name__ == "__main__":
    unittest.main()
