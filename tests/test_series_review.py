"""Whole-series continuity reviews (task: evidence-backed map/reduce reviews
that prove exactly what was checked).

Covers:
- Migration integrity: review_run / book_ledger / ledger_item / review_finding
- Stage B ledgers: span provenance on every item, candidate-until-approved,
  rebuild carries approved/rejected forward, fingerprint tracks content
- Stage C reconciliation — cross-book contradiction fixture: timeline year
  conflict (canon-classed severity floor), age regression, possession
  conflict, relationship drift, state drift — each with evidence spans on
  ALL affected passages and code-computed severity
- Spoiler-chronology fixture: knowledge in book 1 of an entity introduced in
  book 2 → spoiler_leakage in reading order
- Terminology: cross-book surface-form variants + entity-type conflicts
- Partial-coverage fixture: skipped/failed/stale chapters and missing
  ledgers force partial=True with named unreviewed regions — a run can
  never claim a full review it did not perform
- Findings lifecycle: closed resolution list, disposition inheritance across
  re-runs by dedupe key (never resurrected as open)
- Modes: terminology_audit restricts comparators; release_gate verdict is
  computed (blocked on open high findings OR partial coverage)
- Durable ops: registered step actions execute against OpContext
- API routes: auth required; create → poll → findings → disposition
"""

from __future__ import annotations

import tempfile
import time
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from orivellum.capabilities import series_review as sr
from orivellum.database.canon_store import CanonStore
from orivellum.database.db import OrivellumDB
from orivellum.database.series_store import SeriesStore
from tests.conftest import AUTH_HEADERS


def _now() -> str:
    return datetime.now(UTC).isoformat()


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.series_store = SeriesStore(self.db)
        self.canon = CanonStore(self.db)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _work(self, title: str) -> dict:
        return self.db.create_work(title=title)

    def _series(self, title: str, *works: dict) -> dict:
        s = self.series_store.create_series(title=title)
        for i, w in enumerate(works, start=1):
            self.series_store.add_member(s["id"], w["id"], volume=i)
        return s

    def _chapter(self, work_id: str, seq: int, title: str, text: str) -> str:
        cid = self.db._create_object("book_chapter")
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO book_chapters(id, work_id, seq, level, title,
                   text, source_doc_id, status, meta, created_at, updated_at)
                   VALUES(?,?,?,1,?,?,NULL,'draft','{}',?,?)""",
                (cid, work_id, seq, title, text, _now(), _now()),
            )
            self.db._conn.commit()
        return cid

    def _ensure_quote(self, chapter_id: str, quote: str) -> None:
        """Make the quote verifiably present in the chapter text.

        Real ATLAS extraction guarantees grounded quotes (LAW 3); the ledger
        now verifies them, so fixtures must honor the same contract.
        """
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT text FROM book_chapters WHERE id=?", (chapter_id,)
            ).fetchone()
            if row is not None and quote not in (row["text"] or ""):
                self.db._conn.execute(
                    "UPDATE book_chapters SET text = COALESCE(text,'') || ' ' || ? "
                    "WHERE id=?", (quote, chapter_id))
                self.db._conn.commit()

    def _node(self, work_id, chapter_id, node_type, name, **kw):
        quote = kw.pop("quote", f"...{name}...")
        self._ensure_quote(chapter_id, quote)
        return self.db.create_graph_node(
            work_id=work_id, chapter_id=chapter_id, node_type=node_type,
            name=name, evidence_quote=quote,
            evidence_offset=kw.pop("offset", 0), **kw)

    def _edge(self, work_id, chapter_id, src, dst, edge_type, **kw):
        quote = kw.pop("quote", "...")
        self._ensure_quote(chapter_id, quote)
        return self.db.create_graph_edge(
            work_id=work_id, chapter_id=chapter_id, src=src, dst=dst,
            edge_type=edge_type, evidence_quote=quote,
            evidence_offset=kw.pop("offset", 0))


class MigrationTests(_Base):
    def test_tables_exist(self):
        names = {
            r["name"]
            for r in self.db.read_conn().execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for t in ("review_run", "book_ledger", "ledger_item", "review_finding"):
            self.assertIn(t, names)


class LedgerTests(_Base):
    def test_every_item_carries_span_provenance_and_is_candidate(self):
        w = self._work("Book A")
        ch = self._chapter(w["id"], 1, "One", "Kael raised the Emberblade high.")
        kael = self._node(w["id"], ch, "Character", "Kael",
                          quote="Kael raised", offset=0,
                          attributes={"state": "healthy"})
        blade = self._node(w["id"], ch, "Object", "Emberblade",
                           quote="the Emberblade", offset=11)
        self._edge(w["id"], ch, kael, blade, "possesses",
                   quote="raised the Emberblade", offset=5)
        out = sr.build_book_ledger(self.db, w["id"])
        self.assertGreater(out["item_count"], 0)
        items = sr.list_ledger_items(self.db, w["id"])
        for it in items:
            self.assertEqual(it["review_status"], "candidate")
            self.assertIn(it["kind"], sr.LEDGER_KINDS)
        # graph-derived items cite chapter + offset + verbatim quote
        graph_items = [i for i in items if i["chapter_id"]]
        self.assertTrue(graph_items)
        for it in graph_items:
            self.assertEqual(it["chapter_id"], ch)
            self.assertIsNotNone(it["span_offset"])
            self.assertTrue(it["quote"])
        kinds = {i["kind"] for i in items}
        self.assertIn("terminology", kinds)
        self.assertIn("character_state", kinds)

    def test_rebuild_carries_approved_and_rejected_forward(self):
        w = self._work("Book A")
        ch = self._chapter(w["id"], 1, "One", "The comet came.")
        self._node(w["id"], ch, "Event", "Cometfall", quote="The comet came", offset=0)
        sr.build_book_ledger(self.db, w["id"])
        item = sr.list_ledger_items(self.db, w["id"], kind="event")[0]
        sr.set_ledger_item_status(self.db, item["id"], "approved")
        out2 = sr.build_book_ledger(self.db, w["id"])
        again = sr.list_ledger_items(self.db, w["id"], kind="event")[0]
        self.assertEqual(again["review_status"], "approved")
        self.assertNotEqual(again["id"], item["id"])  # new row, same identity
        self.assertEqual(out2["fingerprint"],
                         sr.get_ledger(self.db, w["id"])["fingerprint"])

    def test_fingerprint_tracks_chapter_content(self):
        w = self._work("Book A")
        ch = self._chapter(w["id"], 1, "One", "First text.")
        self._node(w["id"], ch, "Event", "E1", quote="First text", offset=0)
        f1 = sr.build_book_ledger(self.db, w["id"])["fingerprint"]
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE book_chapters SET text='Changed text.' WHERE id=?", (ch,))
            self.db._conn.commit()
        f2 = sr.build_book_ledger(self.db, w["id"])["fingerprint"]
        self.assertNotEqual(f1, f2)

    def test_rejected_ledger_item_never_feeds_comparators(self):
        b1, b2 = self._work("B1"), self._work("B2")
        self._series("S", b1, b2)
        c1 = self._chapter(b1["id"], 1, "One", "x")
        c2 = self._chapter(b2["id"], 1, "One", "y")
        k1 = self._node(b1["id"], c1, "Character", "Kael",
                        attributes={"arm": "whole"})
        k2 = self._node(b2["id"], c2, "Character", "Kael",
                        attributes={"arm": "severed"})
        del k1, k2
        sr.build_book_ledger(self.db, b1["id"])
        sr.build_book_ledger(self.db, b2["id"])
        scope = sr.resolve_scope(self.db, mode="full_series",
                                 work_id=b1["id"], series_id=None)
        findings = sr.reconcile(self.db, mode="full_series", scope=scope)
        self.assertTrue(any(f["finding_type"] in ("state_drift", "injury_drift")
                            for f in findings))
        # author rejects the book-1 evidence → the contradiction dissolves
        item = next(i for i in sr.list_ledger_items(self.db, b1["id"],
                                                    kind="character_state"))
        sr.set_ledger_item_status(self.db, item["id"], "rejected")
        findings = sr.reconcile(self.db, mode="full_series", scope=scope)
        self.assertFalse(any(f["finding_type"] in ("state_drift", "injury_drift")
                             for f in findings))


class CrossBookContradictionTests(_Base):
    """The spec's fixture series: book 2 contradicts book 1 four ways."""

    def setUp(self):
        super().setUp()
        self.b1, self.b2 = self._work("Vol I"), self._work("Vol II")
        self.series = self._series("Saga", self.b1, self.b2)
        self.c1 = self._chapter(self.b1["id"], 1, "One", "Book one text.")
        self.c2 = self._chapter(self.b2["id"], 1, "One", "Book two text.")
        # Book 1: Kael, 20, holds the Emberblade, allied with Mira, year 512
        kael1 = self._node(self.b1["id"], self.c1, "Character", "Kael",
                           attributes={"age": "is 20 years old"})
        mira1 = self._node(self.b1["id"], self.c1, "Character", "Mira")
        blade1 = self._node(self.b1["id"], self.c1, "Object", "Emberblade")
        self._node(self.b1["id"], self.c1, "TimePoint", "The Sundering",
                   description="It happened in 512.")
        self._edge(self.b1["id"], self.c1, kael1, blade1, "possesses")
        self._edge(self.b1["id"], self.c1, kael1, mira1, "affinity_with")
        # Book 2: Kael is 18 (regression), Mira holds the blade (no transfer),
        # hostility, and the Sundering moves to 515
        kael2 = self._node(self.b2["id"], self.c2, "Character", "Kael",
                           attributes={"age": "is 18 years old"})
        mira2 = self._node(self.b2["id"], self.c2, "Character", "Mira")
        blade2 = self._node(self.b2["id"], self.c2, "Object", "Emberblade")
        self._node(self.b2["id"], self.c2, "TimePoint", "The Sundering",
                   description="It happened in 515.")
        self._edge(self.b2["id"], self.c2, mira2, blade2, "possesses")
        self._edge(self.b2["id"], self.c2, kael2, mira2, "hostility_with")
        sr.build_book_ledger(self.db, self.b1["id"])
        sr.build_book_ledger(self.db, self.b2["id"])
        self.scope = sr.resolve_scope(self.db, mode="full_series",
                                      work_id=self.b1["id"], series_id=None)
        self.findings = sr.reconcile(self.db, mode="full_series", scope=self.scope)

    def _of(self, ftype: str) -> list[dict]:
        return [f for f in self.findings if f["finding_type"] == ftype]

    def test_all_four_contradictions_found_with_evidence_on_both_books(self):
        for ftype in ("timeline_date_conflict", "age_regression",
                      "possession_conflict", "relationship_drift"):
            found = self._of(ftype)
            self.assertTrue(found, f"missing {ftype}")
            for f in found:
                works = {s["work_id"] for s in f["evidence"]}
                self.assertEqual(
                    works, {self.b1["id"], self.b2["id"]},
                    f"{ftype} must cite spans in BOTH books")
                for s in f["evidence"]:
                    self.assertIn("quote", s)
                    self.assertIn("offset", s)

    def test_severity_is_computed_in_code(self):
        self.assertEqual(self._of("timeline_date_conflict")[0]["severity"], "high")
        self.assertEqual(self._of("age_regression")[0]["severity"], "high")
        self.assertEqual(self._of("possession_conflict")[0]["severity"], "medium")
        with self.assertRaises(ValueError):
            sr.compute_severity("made_up_type")

    def test_canon_year_conflict_gets_authority_floor(self):
        self.canon.create_fact(statement="The comet returned in 512.",
                               classification="HISTORICAL",
                               work_id=self.b1["id"], signed_by="author",
                               source_ref="Chronicle of Years, f.12")
        self.canon.create_fact(statement="The comet returned in 515.",
                               classification="HISTORICAL",
                               work_id=self.b2["id"], signed_by="author",
                               source_ref="Chronicle of Years, f.15")
        sr.build_book_ledger(self.db, self.b1["id"])
        sr.build_book_ledger(self.db, self.b2["id"])
        findings = sr.reconcile(self.db, mode="full_series", scope=self.scope)
        canon_hits = [f for f in findings
                      if f["finding_type"] == "timeline_date_conflict"
                      and f["canon_fact_id"]]
        self.assertTrue(canon_hits)
        self.assertEqual(canon_hits[0]["severity"], "critical")
        self.assertEqual(canon_hits[0]["canon_class"], "HISTORICAL")


class SpoilerChronologyTests(_Base):
    def test_early_book_references_late_book_entity(self):
        b1, b2 = self._work("Vol I"), self._work("Vol II")
        self._series("Saga", b1, b2)
        c1 = self._chapter(b1["id"], 1, "One", "x")
        c2 = self._chapter(b2["id"], 1, "One", "y")
        kael = self._node(b1["id"], c1, "Character", "Kael")
        # Kael references the Shadow King — who is only introduced in book 2
        ghost = self._node(b1["id"], c1, "Event", "Shadow King's fall",
                           quote="the Shadow King's fall", offset=3)
        self._edge(b1["id"], c1, kael, ghost, "references")
        # remove book-1 terminology for the entity so its FIRST introduction
        # is genuinely book 2 (the reference itself is the leak)
        self._node(b2["id"], c2, "Event", "Shadow King's fall",
                   quote="the Shadow King finally fell", offset=0)
        sr.build_book_ledger(self.db, b1["id"])
        sr.build_book_ledger(self.db, b2["id"])
        scope = sr.resolve_scope(self.db, mode="full_series",
                                 work_id=b1["id"], series_id=None)
        findings = sr.reconcile(self.db, mode="full_series", scope=scope)
        # the entity also appears in book 1's own terminology (the reference
        # grounded it), so first appearance is book 1 — no spoiler. Delete the
        # book-1 terminology item to model an unnamed forward allusion.
        with self.db._lock:
            self.db._conn.execute(
                "DELETE FROM ledger_item WHERE work_id=? AND kind='terminology' "
                "AND subject LIKE 'shadow king%'", (b1["id"],))
            self.db._conn.commit()
        findings = sr.reconcile(self.db, mode="full_series", scope=scope)
        leaks = [f for f in findings if f["finding_type"] == "spoiler_leakage"]
        self.assertTrue(leaks)
        self.assertEqual(leaks[0]["severity"], "high")
        works_cited = {s["work_id"] for s in leaks[0]["evidence"]}
        self.assertEqual(works_cited, {b1["id"], b2["id"]})


class TerminologyTests(_Base):
    def test_cross_book_surface_variants_and_type_conflicts(self):
        b1, b2 = self._work("Vol I"), self._work("Vol II")
        self._series("Saga", b1, b2)
        c1 = self._chapter(b1["id"], 1, "One", "x")
        c2 = self._chapter(b2["id"], 1, "One", "y")
        self._node(b1["id"], c1, "Location", "Black-water Keep")
        self._node(b2["id"], c2, "Location", "Blackwater Keep")
        self._node(b1["id"], c1, "Character", "Vesper")
        self._node(b2["id"], c2, "Location", "Vesper")
        sr.build_book_ledger(self.db, b1["id"])
        sr.build_book_ledger(self.db, b2["id"])
        scope = sr.resolve_scope(self.db, mode="terminology_audit",
                                 work_id=b1["id"], series_id=None)
        findings = sr.reconcile(self.db, mode="terminology_audit", scope=scope)
        types = {f["finding_type"] for f in findings}
        self.assertIn("terminology_variant", types)
        self.assertIn("entity_type_conflict", types)
        # terminology_audit runs ONLY the terminology comparator
        self.assertTrue(types <= {"terminology_variant", "entity_type_conflict"})


class CoverageManifestTests(_Base):
    def test_partial_coverage_is_forced_and_named(self):
        b1, b2 = self._work("Vol I"), self._work("Vol II")
        self._series("Saga", b1, b2)
        c_ok = self._chapter(b1["id"], 1, "Good", "Solid text.")
        self._chapter(b1["id"], 2, "Empty", "   ")           # skipped
        c_fail = self._chapter(b1["id"], 3, "Unextracted", "Text, no graph.")
        c_stale = self._chapter(b1["id"], 4, "Stale", "Original.")
        self._node(b1["id"], c_ok, "Event", "E1")
        self._node(b1["id"], c_stale, "Event", "E2")
        sr.build_book_ledger(self.db, b1["id"])
        # book 2 has NO ledger at all; chapter 4's text changes after build
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE book_chapters SET text='Rewritten.' WHERE id=?", (c_stale,))
            self.db._conn.commit()
        scope = sr.resolve_scope(self.db, mode="full_series",
                                 work_id=b1["id"], series_id=None)
        manifest = sr.build_manifest(self.db, mode="full_series", scope=scope)
        self.assertTrue(manifest["partial"])
        reasons = {(u.get("chapter_id"), u["reason"])
                   for u in manifest["unreviewed_regions"]}
        self.assertIn((None, "no ledger built"),
                      {(u.get("chapter_id"), u["reason"])
                       for u in manifest["unreviewed_regions"]
                       if u["work_id"] == b2["id"]})
        self.assertIn((c_fail, "failed"), reasons)
        self.assertIn((c_stale, "stale"), reasons)
        self.assertTrue(any(u["reason"] == "skipped"
                            for u in manifest["unreviewed_regions"]))
        self.assertEqual(manifest["tool_version"], sr.TOOL_VERSION)
        b1_entry = next(b for b in manifest["books"] if b["work_id"] == b1["id"])
        self.assertTrue(b1_entry["ledger"]["fingerprint"])

    def test_clean_full_coverage_is_not_partial(self):
        b1 = self._work("Solo")
        ch = self._chapter(b1["id"], 1, "One", "Text.")
        self._node(b1["id"], ch, "Event", "E1")
        sr.build_book_ledger(self.db, b1["id"])
        scope = sr.resolve_scope(self.db, mode="full_series",
                                 work_id=b1["id"], series_id=None)
        manifest = sr.build_manifest(self.db, mode="full_series", scope=scope)
        self.assertFalse(manifest["partial"])
        self.assertEqual(manifest["unreviewed_regions"], [])


class RunLifecycleTests(_Base):
    def _seeded_pair(self):
        b1, b2 = self._work("Vol I"), self._work("Vol II")
        self._series("Saga", b1, b2)
        c1 = self._chapter(b1["id"], 1, "One", "x")
        c2 = self._chapter(b2["id"], 1, "One", "y")
        self._node(b1["id"], c1, "TimePoint", "The Fall",
                   description="in 100")
        self._node(b2["id"], c2, "TimePoint", "The Fall",
                   description="in 200")
        sr.build_book_ledger(self.db, b1["id"])
        sr.build_book_ledger(self.db, b2["id"])
        return b1, b2

    def _finalize(self, run):
        scope = sr.resolve_scope(self.db, mode=run["mode"],
                                 work_id=run["work_id"],
                                 series_id=run["series_id"])
        findings = sr.reconcile(self.db, mode=run["mode"], scope=scope,
                                chapter_id=run["chapter_id"])
        manifest = sr.build_manifest(self.db, mode=run["mode"], scope=scope)
        return sr.finalize_run(self.db, run["id"], findings=findings,
                               manifest=manifest)

    def test_disposition_inherited_across_reruns_never_resurrected(self):
        b1, _ = self._seeded_pair()
        run1 = sr.create_run(self.db, mode="full_series",
                             work_id=b1["id"], series_id=None)
        done1 = self._finalize(run1)
        self.assertEqual(done1["status"], "done")
        f = sr.list_findings(self.db, run1["id"])[0]
        self.assertEqual(f["status"], "open")
        out = sr.set_finding_disposition(
            self.db, f["id"], status="intentional",
            resolution="accept_intentional_ambiguity", note="time is broken")
        self.assertEqual(out["status"], "intentional")
        run2 = sr.create_run(self.db, mode="full_series",
                             work_id=b1["id"], series_id=None)
        self._finalize(run2)
        f2 = next(x for x in sr.list_findings(self.db, run2["id"])
                  if x["dedupe_key"] == f["dedupe_key"])
        self.assertEqual(f2["status"], "intentional")
        self.assertEqual(f2["resolution"], "accept_intentional_ambiguity")
        self.assertEqual(f2["resolution_note"], "time is broken")

    def test_closing_a_finding_requires_a_resolution_from_the_closed_list(self):
        b1, _ = self._seeded_pair()
        run = sr.create_run(self.db, mode="full_series",
                            work_id=b1["id"], series_id=None)
        self._finalize(run)
        f = sr.list_findings(self.db, run["id"])[0]
        with self.assertRaises(sr.SeriesReviewError):
            sr.set_finding_disposition(self.db, f["id"], status="resolved")
        with self.assertRaises(sr.SeriesReviewError):
            sr.set_finding_disposition(self.db, f["id"], status="resolved",
                                       resolution="auto_rewrite_the_book")

    def test_release_gate_blocks_on_open_highs_and_on_partial_coverage(self):
        b1, b2 = self._seeded_pair()
        run = sr.create_run(self.db, mode="release_gate",
                            work_id=b1["id"], series_id=None)
        done = self._finalize(run)
        self.assertEqual(done["gate"]["verdict"], "blocked")
        self.assertGreater(done["gate"]["blocking_findings"], 0)
        # author accepts the contradiction as intentional → next gate hinges
        # only on coverage (which is clean here)
        for f in sr.list_findings(self.db, run["id"]):
            sr.set_finding_disposition(self.db, f["id"], status="intentional",
                                       resolution="accept_intentional_ambiguity")
        run2 = sr.create_run(self.db, mode="release_gate",
                             work_id=b1["id"], series_id=None)
        done2 = self._finalize(run2)
        self.assertEqual(done2["gate"]["verdict"], "passable")
        # partial coverage alone blocks the gate, even with zero findings
        self._chapter(b2["id"], 2, "New", "Fresh unledgered chapter.")
        run3 = sr.create_run(self.db, mode="release_gate",
                             work_id=b1["id"], series_id=None)
        done3 = self._finalize(run3)
        self.assertEqual(done3["gate"]["verdict"], "blocked")
        self.assertTrue(done3["gate"]["partial_coverage"])

    def test_book_vs_series_scopes_to_prior_volumes_only(self):
        b1, b2 = self._seeded_pair()
        b3 = self._work("Vol III")
        s = self.series_store.series_for_work(b1["id"])
        self.series_store.add_member(s["series_id"], b3["id"], volume=3)
        scope = sr.resolve_scope(self.db, mode="book_vs_series",
                                 work_id=b2["id"], series_id=None)
        self.assertEqual([x["work_id"] for x in scope], [b1["id"], b2["id"]])


class OperationStepTests(_Base):
    def test_registered_actions_execute_end_to_end(self):
        from orivellum.capabilities.operations.registry import (
            OpContext,
            get_op_registry,
        )

        reg = get_op_registry()
        self.assertIn("series_review.ledger", reg)
        self.assertIn("series_review.reconcile", reg)
        b1, b2 = self._work("Vol I"), self._work("Vol II")
        self._series("Saga", b1, b2)
        c1 = self._chapter(b1["id"], 1, "One", "x")
        c2 = self._chapter(b2["id"], 1, "One", "y")
        self._node(b1["id"], c1, "TimePoint", "The Fall", description="in 100")
        self._node(b2["id"], c2, "TimePoint", "The Fall", description="in 200")
        run = sr.create_run(self.db, mode="full_series",
                            work_id=b1["id"], series_id=None)
        ctx = OpContext(db=self.db, cfg=None, operation_id="op-test",
                        work_id=None, params={"run_id": run["id"]})
        for wid in (b1["id"], b2["id"]):
            out = reg["series_review.ledger"].execute(ctx, {"work_id": wid})
            self.assertGreater(out["item_count"], 0)
        out = reg["series_review.reconcile"].execute(ctx, {"run_id": run["id"]})
        self.assertGreater(out["findings"], 0)
        done = sr.get_run(self.db, run["id"])
        self.assertEqual(done["status"], "done")
        self.assertFalse(done["partial"])
        self.assertTrue(done["coverage"]["books"])


class ApiTests(_Base):
    def setUp(self):
        super().setUp()
        from orivellum.api import _deps
        from orivellum.api.app import app
        from orivellum.configuration.config import OrivellumConfig

        _deps.init(db=self.db, cfg=OrivellumConfig(data_dir=self._tmp.name))
        self.client = TestClient(app, raise_server_exceptions=True)

    def _seed(self):
        b1, b2 = self._work("Vol I"), self._work("Vol II")
        self._series("Saga", b1, b2)
        c1 = self._chapter(b1["id"], 1, "One", "x")
        c2 = self._chapter(b2["id"], 1, "One", "y")
        self._node(b1["id"], c1, "TimePoint", "The Fall", description="in 100")
        self._node(b2["id"], c2, "TimePoint", "The Fall", description="in 200")
        return b1, b2

    def test_auth_required(self):
        self.assertIn(self.client.get("/api/review-runs").status_code, (401, 403))
        self.assertIn(self.client.get("/api/review-runs/modes").status_code,
                      (401, 403))

    def test_modes_endpoint_exposes_closed_lists(self):
        r = self.client.get("/api/review-runs/modes", headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(set(body["modes"]), set(sr.MODES))
        self.assertEqual(set(body["resolution_choices"]),
                         set(sr.RESOLUTION_CHOICES))

    def test_create_poll_findings_disposition_roundtrip(self):
        b1, _ = self._seed()
        r = self.client.post("/api/review-runs", headers=AUTH_HEADERS,
                             json={"mode": "full_series", "work_id": b1["id"]})
        self.assertEqual(r.status_code, 200, r.text)
        run_id = r.json()["run"]["id"]
        self.assertEqual(len(r.json()["scope"]), 2)
        deadline = time.time() + 20
        run = None
        while time.time() < deadline:
            run = self.client.get(f"/api/review-runs/{run_id}",
                                  headers=AUTH_HEADERS).json()["run"]
            if run["effective_status"] in ("done", "failed"):
                break
            time.sleep(0.2)
        self.assertIsNotNone(run)
        self.assertEqual(run["effective_status"], "done", run)
        self.assertFalse(run["partial"])
        self.assertTrue(run["coverage"]["books"])
        rf = self.client.get(f"/api/review-runs/{run_id}/findings",
                             headers=AUTH_HEADERS)
        findings = rf.json()["findings"]
        self.assertTrue(findings)
        fid = findings[0]["id"]
        bad = self.client.patch(f"/api/review-findings/{fid}",
                                headers=AUTH_HEADERS,
                                json={"status": "resolved"})
        self.assertEqual(bad.status_code, 422)
        ok = self.client.patch(
            f"/api/review-findings/{fid}", headers=AUTH_HEADERS,
            json={"status": "resolved", "resolution": "update_book_text",
                  "note": "fixed in draft 2"})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["finding"]["status"], "resolved")

    def test_ledger_routes(self):
        b1, _ = self._seed()
        sr.build_book_ledger(self.db, b1["id"])
        r = self.client.get(f"/api/works/{b1['id']}/ledger", headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ledger"]["fingerprint"])
        self.assertTrue(body["items"])
        item_id = body["items"][0]["id"]
        pr = self.client.patch(f"/api/ledger-items/{item_id}",
                               headers=AUTH_HEADERS,
                               json={"status": "approved"})
        self.assertEqual(pr.status_code, 200)
        self.assertEqual(pr.json()["item"]["review_status"], "approved")
        bad = self.client.patch(f"/api/ledger-items/{item_id}",
                                headers=AUTH_HEADERS,
                                json={"status": "canonical"})
        self.assertEqual(bad.status_code, 422)

    def test_invalid_mode_rejected(self):
        b1, _ = self._seed()
        r = self.client.post("/api/review-runs", headers=AUTH_HEADERS,
                             json={"mode": "vibes_check", "work_id": b1["id"]})
        self.assertEqual(r.status_code, 422)


class ReviewHardeningTests(_Base):
    """Coverage-truth and mode-completeness guarantees added after review."""

    def _two_book_series(self):
        b1, b2 = self._work("Book One"), self._work("Book Two")
        self._series("Duology", b1, b2)
        c1 = self._chapter(b1["id"], 1, "One", "Mira rode north to the keep.")
        c2 = self._chapter(b2["id"], 1, "One", "Mira arrived at the keep at last.")
        return b1, b2, c1, c2

    def test_chapter_modes_require_chapter_id(self):
        b1, _, _, _ = self._two_book_series()
        for mode in ("chapter_vs_book", "change_impact"):
            with self.assertRaises(sr.SeriesReviewError):
                sr.create_run(self.db, mode=mode, work_id=b1["id"],
                              series_id=None)

    def test_run_scope_is_snapshotted_at_creation(self):
        b1, b2, _, _ = self._two_book_series()
        run = sr.create_run(self.db, mode="full_series",
                            work_id=b1["id"], series_id=None)
        stored = (sr.get_run(self.db, run["id"])["params"] or {}).get("scope")
        self.assertIsNotNone(stored)
        self.assertEqual([s["work_id"] for s in stored],
                         [b1["id"], b2["id"]])
        # A membership change AFTER creation must not alter the stored scope.
        b3 = self._work("Book Three")
        self._chapter(b3["id"], 1, "One", "A late addition.")
        series = self.series_store.list_series()[0]
        self.series_store.add_member(series["id"], b3["id"], volume=3)
        stored_after = (sr.get_run(self.db, run["id"])["params"] or {}).get("scope")
        self.assertEqual([s["work_id"] for s in stored_after],
                         [b1["id"], b2["id"]])

    def test_unverifiable_span_is_excluded_and_forces_partial(self):
        b1, b2, c1, c2 = self._two_book_series()
        # Grounded node in book 1
        self._node(b1["id"], c1, "Character", "Mira",
                   quote="Mira rode north to the keep.")
        # Node whose quote does NOT appear in the chapter text — bypass the
        # test helper's quote-seeding so the item is genuinely unverifiable.
        self.db.create_graph_node(
            work_id=b2["id"], chapter_id=c2, node_type="Character",
            name="Mira", evidence_quote="A sentence that is not in the text.",
            evidence_offset=0, attributes={"status": "dead"})
        sr.build_book_ledger(self.db, b1["id"])
        sr.build_book_ledger(self.db, b2["id"])
        scope = sr.resolve_scope(self.db, mode="full_series",
                                 work_id=b1["id"], series_id=None)
        # The unverified item never feeds comparators…
        findings = sr.reconcile(self.db, mode="full_series", scope=scope)
        self.assertFalse(any(f["finding_type"] in ("state_drift", "injury_drift")
                             for f in findings))
        # …and the manifest names the chapter and forces a partial review.
        manifest = sr.build_manifest(self.db, mode="full_series", scope=scope)
        self.assertTrue(manifest["partial"])
        reasons = [u["reason"] for u in manifest["unreviewed_regions"]]
        self.assertTrue(any("unverifiable" in r for r in reasons))

    def test_canon_evidence_is_labeled_not_faked_as_passage(self):
        b1, b2, _, _ = self._two_book_series()
        self.canon.create_fact(statement="The war ended in 512.",
                               classification="HISTORICAL",
                               work_id=b1["id"], signed_by="author",
                               source_ref="Chronicle, f.3")
        self.canon.create_fact(statement="The war ended in 515.",
                               classification="HISTORICAL",
                               work_id=b2["id"], signed_by="author",
                               source_ref="Chronicle, f.9")
        sr.build_book_ledger(self.db, b1["id"])
        sr.build_book_ledger(self.db, b2["id"])
        scope = sr.resolve_scope(self.db, mode="full_series",
                                 work_id=b1["id"], series_id=None)
        findings = sr.reconcile(self.db, mode="full_series", scope=scope)
        canon_spans = [e for f in findings for e in f["evidence"]
                       if e.get("source") == "canon"]
        self.assertTrue(canon_spans)
        for e in canon_spans:
            self.assertTrue(e["source_ref"])
            self.assertIsNone(e["chapter_id"])


if __name__ == "__main__":
    unittest.main()
