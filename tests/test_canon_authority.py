"""Canon authority tests (task: classified, sourced facts for the trilogy).

Covers the CanonStore insert-path refusals (HISTORICAL without source,
INFERRED without parents, INVENTED without signature), explicit
supersede/retract arbitration, the G3 Canon Seed parser + gate seeding
(idempotent), and machine-proposal ratification (author-signed, claim-based,
reclassify supported).  Runs against a real OrivellumDB temp instance so the
schema is authoritative.
"""

from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from orivellum.capabilities.genesis.canon_seed import parse_canon_seed, seed_canon_facts
from orivellum.database.canon_store import CanonFactError, CanonStore
from orivellum.database.db import OrivellumDB


class CanonStoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.store = CanonStore(self.db)
        self.work = self.db.create_work(title="Ash and Silence")

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    # ── Insert-path refusals ──────────────────────────────────────────────

    def test_refuses_historical_without_source(self):
        with self.assertRaises(CanonFactError):
            self.store.create_fact(
                statement="Job lived in Uz", classification="HISTORICAL", signed_by="b"
            )

    def test_refuses_inferred_without_parents(self):
        with self.assertRaises(CanonFactError):
            self.store.create_fact(
                statement="Uz lay east", classification="INFERRED", signed_by="b"
            )

    def test_refuses_inferred_with_dead_parent(self):
        h = self.store.create_fact(
            statement="Job lived in Uz",
            classification="HISTORICAL",
            source_ref="Job 1:1",
            signed_by="b",
        )
        self.store.retract_fact(h["id"], signed_by="b")
        with self.assertRaises(CanonFactError):
            self.store.create_fact(
                statement="derived",
                classification="INFERRED",
                parent_ids=[h["id"]],
                signed_by="b",
            )

    def test_refuses_invented_without_signature(self):
        with self.assertRaises(CanonFactError):
            self.store.create_fact(
                statement="A walled courtyard", classification="INVENTED", signed_by=""
            )

    def test_refuses_bad_classification(self):
        with self.assertRaises(CanonFactError):
            self.store.create_fact(statement="x", classification="GUESSED", signed_by="b")

    # ── Valid inserts ─────────────────────────────────────────────────────

    def test_valid_inserts_and_scoping(self):
        series = self.store.create_fact(
            statement="Job was blameless",
            classification="HISTORICAL",
            source_ref="Job 1:1",
            signed_by="b",
        )  # work_id None = series-wide
        book = self.store.create_fact(
            statement="The Adversary appears",
            classification="HISTORICAL",
            source_ref="Job 1:6",
            signed_by="b",
            work_id=self.work["id"],
        )
        self.assertIsNone(series["work_id"])
        self.assertEqual(book["work_id"], self.work["id"])
        # A book's canon includes series-wide facts by default
        both = self.store.list_facts(work_id=self.work["id"])
        self.assertEqual(len(both), 2)
        only_book = self.store.list_facts(work_id=self.work["id"], include_series=False)
        self.assertEqual(len(only_book), 1)
        only_series = self.store.list_facts(series_only=True)
        self.assertEqual(len(only_series), 1)

    # ── Supersede / retract arbitration ───────────────────────────────────

    def test_supersede_is_explicit_and_atomic(self):
        old = self.store.create_fact(
            statement="Job lived in Uz",
            classification="HISTORICAL",
            source_ref="Job 1:1",
            signed_by="b",
        )
        new = self.store.create_fact(
            statement="Job lived near Edom",
            classification="HISTORICAL",
            source_ref="Lam 4:21",
            signed_by="b",
            supersedes=old["id"],
        )
        refreshed = self.store.get_fact(old["id"])
        self.assertEqual(refreshed["status"], "superseded")
        self.assertEqual(refreshed["superseded_by"], new["id"])
        # Cannot supersede a non-active fact — no silent overwrite chain forks
        with self.assertRaises(CanonFactError):
            self.store.create_fact(
                statement="third version",
                classification="HISTORICAL",
                source_ref="x",
                signed_by="b",
                supersedes=old["id"],
            )

    def test_retract_requires_signature_and_active(self):
        f = self.store.create_fact(statement="s", classification="INVENTED", signed_by="b")
        with self.assertRaises(CanonFactError):
            self.store.retract_fact(f["id"], signed_by="")
        self.assertEqual(self.store.retract_fact(f["id"], signed_by="b"), "ok")
        self.assertEqual(self.store.retract_fact(f["id"], signed_by="b"), "conflict")
        self.assertEqual(self.store.retract_fact("nope", signed_by="b"), "not_found")
        got = self.store.get_fact(f["id"])
        self.assertEqual(got["status"], "retracted")
        self.assertEqual(got["retracted_by"], "b")


_G3_CONTENT = """# G3 — Canon Seed
## Canon facts (tiered)
| Fact | Tier | source_pointer | Scope |
|------|------|----------------|-------|
| Job was blameless and upright | HISTORICAL | Job 1:1 | SERIES |
| The Adversary appears before God | HISTORICAL | Job 1:6 | WORK |
| Uz lay east of the Jordan | INFERRED | #1 geographic reasoning | WORK |
| Job kept a walled courtyard | INVENTED |  | WORK |

## Research-question backlog
- done
"""


class CanonSeedTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.store = CanonStore(self.db)
        self.work = self.db.create_work(title="Ash and Silence")

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_parse_valid_table(self):
        rows, errors = parse_canon_seed(_G3_CONTENT)
        self.assertEqual(errors, [])
        self.assertEqual(len(rows), 4)
        self.assertTrue(rows[0]["series"])
        self.assertEqual(rows[2]["parent_rows"], [1])
        self.assertEqual(rows[2]["source_ref"], "geographic reasoning")

    def test_parse_refusals(self):
        bad = """## Canon facts
| Fact | Tier | source_pointer |
|---|---|---|
| No source claim | HISTORICAL |  |
| No parents | INFERRED | some text |
| Bad tier | GUESSED | x |
| Forward ref | INFERRED | #9 |
"""
        _, errors = parse_canon_seed(bad)
        self.assertEqual(len(errors), 4)
        self.assertIn("source_pointer", errors[0])
        self.assertIn("#N", errors[1])
        self.assertIn("GUESSED", errors[2])
        self.assertIn("earlier rows", errors[3])

    def test_seed_writes_and_is_idempotent(self):
        out = seed_canon_facts(self.db, self.work["id"], _G3_CONTENT, "Brian")
        self.assertEqual(out["created"], 4)
        facts = self.store.list_facts(work_id=self.work["id"])
        self.assertEqual(len(facts), 4)
        inferred = [f for f in facts if f["classification"] == "INFERRED"][0]
        self.assertEqual(len(inferred["parent_ids"]), 1)
        self.assertTrue(all(f["signed_by"] == "Brian" for f in facts))
        self.assertTrue(all(f["origin"] == "g3_seed" for f in facts))
        # Re-passing the gate never double-writes
        out2 = seed_canon_facts(self.db, self.work["id"], _G3_CONTENT, "Brian")
        self.assertEqual(out2["created"], 0)
        self.assertEqual(out2["skipped"], 4)
        self.assertEqual(len(self.store.list_facts(work_id=self.work["id"])), 4)

    def test_seed_blocks_on_errors(self):
        bad = (
            "## Canon facts\n| Fact | Tier | source_pointer |\n"
            "|---|---|---|\n| x | HISTORICAL |  |\n"
        )
        with self.assertRaises(ValueError):
            seed_canon_facts(self.db, self.work["id"], bad, "Brian")
        self.assertEqual(len(self.store.list_facts()), 0)


class CanonRatifyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.store = CanonStore(self.db)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _insert_proposal(self, classification="HISTORICAL") -> str:
        pid = str(uuid.uuid4())
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO wa_canon_proposals
                   (id, fact_title, fact_text, classification, scope, source_path,
                    source_location, status, created_at)
                   VALUES (?,?,?,?,?,?,?,'proposed',datetime('now'))""",
                (
                    pid,
                    "Uz location",
                    "Job lived in the land of Uz",
                    classification,
                    "series",
                    "bible_data/job.md",
                    "1:1",
                ),
            )
            self.db._conn.commit()
        return pid

    def test_approve_creates_signed_fact(self):
        pid = self._insert_proposal()
        result = self.store.ratify_proposal(pid, decision="approve", author="Brian")
        self.assertEqual(result["result"], "ok")
        fact = result["fact"]
        self.assertEqual(fact["signed_by"], "Brian")
        self.assertEqual(fact["origin"], "wa_archive")
        self.assertEqual(fact["proposal_id"], pid)
        self.assertIn("Job lived in the land of Uz", fact["statement"])
        # source defaults from the proposal's path#location
        self.assertEqual(fact["source_ref"], "bible_data/job.md#1:1")
        # Claim: second ratification conflicts — nothing auto-canon twice
        again = self.store.ratify_proposal(pid, decision="approve", author="Brian")
        self.assertEqual(again["result"], "conflict")

    def test_reject_creates_no_fact(self):
        pid = self._insert_proposal()
        result = self.store.ratify_proposal(pid, decision="reject", author="Brian")
        self.assertEqual(result["result"], "ok")
        self.assertIsNone(result["fact"])
        self.assertEqual(len(self.store.list_facts()), 0)

    def test_ratify_requires_author(self):
        pid = self._insert_proposal()
        with self.assertRaises(CanonFactError):
            self.store.ratify_proposal(pid, decision="approve", author="  ")

    def test_reclassify_on_approve(self):
        pid = self._insert_proposal(classification="HISTORICAL")
        result = self.store.ratify_proposal(
            pid, decision="approve", author="Brian", classification="INVENTED"
        )
        self.assertEqual(result["fact"]["classification"], "INVENTED")

    def test_refused_fact_reverts_claim(self):
        # INFERRED proposal without parents: guards refuse, proposal stays ratifiable
        pid = self._insert_proposal(classification="INFERRED")
        with self.assertRaises(CanonFactError):
            self.store.ratify_proposal(pid, decision="approve", author="Brian")
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT status FROM wa_canon_proposals WHERE id=?", (pid,)
            ).fetchone()
        self.assertEqual(row["status"], "proposed")
        # Author reclassifies to HISTORICAL (source comes from the proposal) — works
        result = self.store.ratify_proposal(
            pid, decision="approve", author="Brian", classification="HISTORICAL"
        )
        self.assertEqual(result["result"], "ok")

    def test_ratify_unknown_proposal(self):
        result = self.store.ratify_proposal("missing", decision="approve", author="b")
        self.assertEqual(result["result"], "not_found")

    def test_series_scope_maps_to_null_work(self):
        pid = self._insert_proposal()  # scope "series"
        result = self.store.ratify_proposal(pid, decision="approve", author="Brian")
        self.assertIsNone(result["fact"]["work_id"])

    def test_non_series_scope_requires_explicit_work(self):
        pid = str(uuid.uuid4())
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO wa_canon_proposals
                   (id, fact_title, fact_text, classification, scope, source_path,
                    source_location, status, created_at)
                   VALUES (?,?,?,?,?,?,?,'proposed',datetime('now'))""",
                (pid, "t", "x happened", "HISTORICAL", "book:one", "a.md", "1"),
            )
            self.db._conn.commit()
        # No work chosen: refuse (and rollback keeps the proposal ratifiable)
        with self.assertRaises(CanonFactError):
            self.store.ratify_proposal(pid, decision="approve", author="Brian")
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT status FROM wa_canon_proposals WHERE id=?", (pid,)
            ).fetchone()
        self.assertEqual(row["status"], "proposed")
        # Explicit work wins
        wid = self.db.create_work(title="Book One")["id"]
        result = self.store.ratify_proposal(pid, decision="approve", author="Brian", work_id=wid)
        self.assertEqual(result["result"], "ok")
        self.assertEqual(result["fact"]["work_id"], wid)


class CanonBatchAtomicityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "t.db"))
        self.store = CanonStore(self.db)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_late_refusal_rolls_back_whole_batch(self):
        rows = [
            {
                "statement": "Job lived in Uz",
                "classification": "HISTORICAL",
                "work_id": None,
                "source_ref": "Job 1:1",
                "parent_rows": [],
            },
            {
                # HISTORICAL without a source — refused; row 1 must NOT survive
                "statement": "Uz was in Edom",
                "classification": "HISTORICAL",
                "work_id": None,
                "source_ref": "",
                "parent_rows": [],
            },
        ]
        with self.assertRaises(CanonFactError):
            self.store.create_facts_batch(rows, signed_by="Brian", origin="g3_seed")
        self.assertEqual(len(self.store.list_facts()), 0)

    def test_batch_inferred_can_cite_in_batch_parent(self):
        rows = [
            {
                "statement": "Job lived in Uz",
                "classification": "HISTORICAL",
                "work_id": None,
                "source_ref": "Job 1:1",
                "parent_rows": [],
            },
            {
                "statement": "Job was a westerner",
                "classification": "INFERRED",
                "work_id": None,
                "source_ref": "",
                "parent_rows": [1],
            },
        ]
        out = self.store.create_facts_batch(rows, signed_by="Brian", origin="g3_seed")
        self.assertEqual(out["created"], 2)
        # Idempotent re-run: everything skipped, parents still resolve
        again = self.store.create_facts_batch(rows, signed_by="Brian", origin="g3_seed")
        self.assertEqual(again["created"], 0)
        self.assertEqual(again["skipped"], 2)


if __name__ == "__main__":
    unittest.main()
