"""RIPPLE — blast-radius simulation on the ATLAS-O world graph (E12 / M17).

Proves by assertion:
- seeding by node id, node name, or canon fact id resolves the right nodes
  and refuses ambiguous/empty seed specs (exactly one selector);
- the walk is breadth-first with a shortest evidence path per reached node,
  crosses edges in BOTH directions, and honors the depth limit;
- the report groups affected chapters (with node names + evidence quotes),
  characters, and downstream canon facts — seed facts are never reported
  as their own blast radius;
- ripple_for_chapter seeds with every node evidenced in the chapter and
  NEVER reports the edited chapter as affected by itself; a chapter with
  no graph evidence returns an explicit empty report with a note;
- an unbuilt graph and unknown seeds are refused loudly (RippleError),
  never returned as a silently empty report;
- the node ceiling truncates the walk and says so.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orivellum.capabilities.ripple import (
    RippleError,
    ripple_for_chapter,
    simulate_ripple,
)
from orivellum.database.db import OrivellumDB, _now


def _seed_chapter(db: OrivellumDB, work_id: str, seq: int, title: str) -> str:
    oid = db._create_object("book_chapter")
    with db._lock:
        db._conn.execute(
            """INSERT INTO book_chapters(id, work_id, seq, level, title, text,
               source_doc_id, status, meta, created_at, updated_at)
               VALUES(?,?,?,1,?,?,NULL,'draft','{}',?,?)""",
            (oid, work_id, seq, title, f"Text of {title}.", _now(), _now()),
        )
        db._conn.commit()
    return oid


class RippleBase(unittest.TestCase):
    """A small world: Mara (ch1) uses the Iron Key (ch1); the Key opens —
    is part_of — the Gate (ch2); the Fall of the Gate event occurs in ch3
    and Tobin performs it.  Node chain: Mara — Key — Gate — Fall — Tobin."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.work_id = self.db.create_work("Ripple Book", work_type="writing")["id"]
        self.ch = {i: _seed_chapter(self.db, self.work_id, i, f"Chapter {i}") for i in (1, 2, 3)}
        self.fact_id = self._create_fact("The Iron Key opens the Gate.")
        n = self._node
        self.mara = n("Character", "Mara", self.ch[1])
        self.key = n("Object", "Iron Key", self.ch[1], canon_fact_id=self.fact_id)
        self.gate = n("Location", "The Gate", self.ch[2])
        self.fall = n("Event", "Fall of the Gate", self.ch[3])
        self.tobin = n("Character", "Tobin", self.ch[3])
        e = self._edge
        e(self.mara, self.key, "uses", self.ch[1], "Mara turned the iron key.")
        e(self.key, self.gate, "part_of", self.ch[2], "The key of the Gate.")
        e(self.fall, self.gate, "occurs_at", self.ch[3], "The Gate fell at dusk.")
        e(self.tobin, self.fall, "performs", self.ch[3], "Tobin brought it down.")

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _create_fact(self, statement: str) -> str:
        from orivellum.database.canon_store import CanonStore

        return CanonStore(self.db).create_fact(
            statement=statement,
            classification="INVENTED",
            work_id=self.work_id,
            signed_by="author",
        )["id"]

    def _node(self, node_type: str, name: str, chapter_id: str, **kw) -> str:
        return self.db.create_graph_node(
            work_id=self.work_id,
            chapter_id=chapter_id,
            node_type=node_type,
            name=name,
            evidence_quote=f"{name} stood there.",
            evidence_offset=0,
            **kw,
        )

    def _edge(self, src: str, dst: str, edge_type: str, chapter_id: str, quote: str):
        self.db.create_graph_edge(
            work_id=self.work_id,
            chapter_id=chapter_id,
            src=src,
            dst=dst,
            edge_type=edge_type,
            evidence_quote=quote,
            evidence_offset=0,
        )


class TestSimulateRipple(RippleBase):
    def test_seed_by_node_id_walks_both_directions(self):
        r = simulate_ripple(self.db, self.work_id, node_id=self.gate, depth=3)
        names = {n["name"] for n in r["affected_nodes"]}
        # Upstream (Key→Mara) AND downstream (Fall→Tobin) from the Gate.
        self.assertEqual(names, {"Iron Key", "Mara", "Fall of the Gate", "Tobin"})
        self.assertEqual(r["counts"]["nodes"], 4)
        self.assertFalse(r["truncated"])

    def test_depth_limit_and_shortest_paths(self):
        r = simulate_ripple(self.db, self.work_id, node_id=self.mara, depth=1)
        names = {n["name"] for n in r["affected_nodes"]}
        self.assertEqual(names, {"Iron Key"})
        r3 = simulate_ripple(self.db, self.work_id, node_id=self.mara, depth=4)
        tobin = next(n for n in r3["affected_nodes"] if n["name"] == "Tobin")
        self.assertEqual(tobin["depth"], 4)
        self.assertEqual(len(tobin["path"]), 4)
        hop = tobin["path"][0]
        self.assertEqual(hop["from_name"], "Mara")
        self.assertEqual(hop["edge_type"], "uses")
        self.assertTrue(hop["evidence_quote"])

    def test_seed_by_name_and_by_canon_fact(self):
        by_name = simulate_ripple(self.db, self.work_id, name="iron key")
        by_fact = simulate_ripple(self.db, self.work_id, canon_fact_id=self.fact_id)
        self.assertEqual(
            {n["name"] for n in by_name["affected_nodes"]},
            {n["name"] for n in by_fact["affected_nodes"]},
        )
        self.assertEqual(by_fact["seeds"][0]["name"], "Iron Key")

    def test_affected_chapters_characters_facts(self):
        r = simulate_ripple(self.db, self.work_id, canon_fact_id=self.fact_id, depth=4)
        seqs = {c["seq"] for c in r["affected_chapters"]}
        self.assertEqual(seqs, {1, 2, 3})
        ch3 = next(c for c in r["affected_chapters"] if c["seq"] == 3)
        self.assertIn("Tobin", ch3["nodes"])
        self.assertTrue(ch3["evidence"])
        # Every affected chapter carries an evidence PATH back to the seed.
        for c in r["affected_chapters"]:
            self.assertTrue(c["path"])
            self.assertTrue(all(h["evidence_quote"] for h in c["path"]))
        self.assertEqual({c["name"] for c in r["affected_characters"]}, {"Mara", "Tobin"})
        # The seed's own fact is never its own blast radius.
        self.assertEqual(r["affected_facts"], [])

    def test_downstream_fact_reported_with_statement(self):
        r = simulate_ripple(self.db, self.work_id, node_id=self.tobin, depth=4)
        facts = r["affected_facts"]
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["canon_fact_id"], self.fact_id)
        self.assertEqual(facts[0]["statement"], "The Iron Key opens the Gate.")
        self.assertIn("Iron Key", facts[0]["via_nodes"])
        # The fact carries the evidence path of its shallowest carrier node.
        self.assertTrue(facts[0]["path"])
        self.assertEqual(facts[0]["path"][0]["from_name"], "Tobin")

    def test_refusals_are_loud(self):
        with self.assertRaises(RippleError):
            simulate_ripple(self.db, self.work_id)  # no seed
        with self.assertRaises(RippleError):
            simulate_ripple(self.db, self.work_id, node_id=self.mara, name="Mara")
        with self.assertRaises(RippleError):
            simulate_ripple(self.db, self.work_id, node_id="missing")
        with self.assertRaises(RippleError):
            simulate_ripple(self.db, self.work_id, name="Nobody Here")
        with self.assertRaises(RippleError):
            simulate_ripple(self.db, self.work_id, canon_fact_id="unlinked-fact")
        empty_work = self.db.create_work("Empty", work_type="writing")["id"]
        with self.assertRaises(RippleError):
            simulate_ripple(self.db, empty_work, name="Mara")
        with self.assertRaises(RippleError):
            simulate_ripple(self.db, self.work_id, name="Mara", depth=0)

    def test_output_identical_when_loader_order_is_scrambled(self):
        """Determinism must not depend on the DB's edge/node ordering —
        a reversed loader feed yields the byte-identical report."""
        from unittest.mock import patch

        baseline = simulate_ripple(self.db, self.work_id, canon_fact_id=self.fact_id, depth=4)
        real_edges = self.db.list_graph_edges
        real_nodes = self.db.list_graph_nodes

        def rev_edges(**kw):
            return list(reversed(real_edges(**kw)))

        def rev_nodes(**kw):
            return list(reversed(real_nodes(**kw)))

        with (
            patch.object(self.db, "list_graph_edges", rev_edges),
            patch.object(self.db, "list_graph_nodes", rev_nodes),
        ):
            scrambled = simulate_ripple(self.db, self.work_id, canon_fact_id=self.fact_id, depth=4)
        self.assertEqual(baseline, scrambled)

    def test_saturated_edge_load_is_reported_truncated(self):
        """The DB clamps edge queries at 20 000: a graph at or beyond that
        ceiling must NEVER be reported as a complete blast radius."""
        rows = [
            (
                f"bulk-{i:06d}",
                self.work_id,
                self.ch[1],
                self.mara,
                self.key,
                "references",
                "inter_event",
                "bulk evidence.",
                0,
                f"2026-01-01T00:00:{i % 60:02d}",
            )
            for i in range(20_001)
        ]
        with self.db._lock:
            self.db._conn.executemany(
                """INSERT INTO graph_edge(id, work_id, chapter_id, src, dst,
                   edge_type, edge_group, evidence_quote, evidence_offset,
                   created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            self.db._conn.commit()
        r = simulate_ripple(self.db, self.work_id, node_id=self.mara, depth=2)
        self.assertTrue(r["truncated"])

    def test_output_is_deterministic(self):
        # Multi-seed walks must not depend on set/hash iteration order.
        runs = [
            simulate_ripple(self.db, self.work_id, canon_fact_id=self.fact_id, depth=4)
            for _ in range(3)
        ]
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])

    def test_truncation_is_reported(self):
        r = simulate_ripple(self.db, self.work_id, node_id=self.mara, depth=4, max_nodes=2)
        self.assertTrue(r["truncated"])
        self.assertLessEqual(len(r["affected_nodes"]) + len(r["seeds"]), 2)


class TestRippleForChapter(RippleBase):
    def test_edited_chapter_is_never_its_own_blast_radius(self):
        r = ripple_for_chapter(self.db, self.work_id, self.ch[1], depth=4)
        self.assertEqual({s["name"] for s in r["seeds"]}, {"Mara", "Iron Key"})
        seqs = {c["seq"] for c in r["affected_chapters"]}
        self.assertEqual(seqs, {2, 3})
        self.assertNotIn(self.ch[1], {c["chapter_id"] for c in r["affected_chapters"]})
        self.assertIn("Tobin", {c["name"] for c in r["affected_characters"]})
        # The Iron Key's fact belongs to a SEED node — not downstream.
        self.assertEqual(r["affected_facts"], [])

    def test_chapter_without_graph_evidence_is_explicit(self):
        ch4 = _seed_chapter(self.db, self.work_id, 4, "Chapter 4")
        r = ripple_for_chapter(self.db, self.work_id, ch4)
        self.assertEqual(r["counts"]["nodes"], 0)
        self.assertIn("no graph nodes", r["note"])


if __name__ == "__main__":
    unittest.main()
