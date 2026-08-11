"""POSITION audit tests (Masterpiece Pipeline E5, Track B).

Proves by assertion:
- the ten deterministic tests each return true/false WITH evidence (gap
  lists, outlier names, counts by classification, …) and never call a model;
- the acceptance fixture: 40 chapters of prose and NO canon table is
  diagnosed A1 (with-prose qualifier), never B5/mid-drafting — the stage is
  the first failing rung, gaps below always win;
- reconstruction lands as review-gated PROPOSALS only: nothing writes
  canon_fact, nothing installs an assay baseline, until an author signature
  resolves the proposal;
- a HISTORICAL canon claim whose quote cannot be located is proposed as
  INFERRED with the gap flagged;
- proposal ids are deterministic: a re-run never clobbers a proposal the
  author already resolved;
- proposal resolution is an atomic claim (second resolution → conflict), a
  ratified voice spec installs the voice baseline, and a signature is
  mandatory;
- the Repair list weights the 15–30% early-book band above raw severity;
- the audit row is the claim: double-dispatch is refused, and any failure
  finishes the row as 'error' — never a leaked 'running' row;
- an LLM-down reconstruction is recorded as an error while the audit still
  completes with the deterministic evidence.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orivellum.capabilities import position
from orivellum.database.db import OrivellumDB, _now

CHAPTER = (
    'Mara crossed the yard before dawn. "Did you sleep?" asked Tobin. '
    '"Not since the rains came," Mara said, pulling her cloak tighter. '
    "The gate stood open and the road ran east through wet fields. "
    "She counted the wagons, checked the harnesses, and said nothing more. "
) * 25  # ~1250 words — inside the genre band


def _cfg():
    return SimpleNamespace(
        serving=SimpleNamespace(
            base_url="http://test.invalid",
            workhorse_model="drafter-model",
            reasoner_model="judge-model",
        )
    )


def _seed_chapter(db, work_id, seq, title, text) -> str:
    oid = db._create_object("book_chapter")
    with db._lock:
        db._conn.execute(
            """INSERT INTO book_chapters(id, work_id, seq, level, title, text,
               source_doc_id, status, meta, created_at, updated_at)
               VALUES(?,?,?,1,?,?,NULL,'draft','{}',?,?)""",
            (oid, work_id, seq, title, text, _now(), _now()),
        )
        db._conn.commit()
    return oid


class _StubLLM:
    """Dispatch llm_call by purpose; canon items include one HISTORICAL claim
    with an unlocatable quote (must be downgraded to INFERRED, gap flagged)."""

    def __init__(self, down: bool = False):
        self.calls: list[dict] = []
        self.down = down

    def __call__(self, messages, **kwargs):
        purpose = kwargs.get("purpose", "")
        self.calls.append({"purpose": purpose})
        if self.down:
            return SimpleNamespace(ok=False, text=None, error="gateway down")
        if purpose == "position.canon_extract":
            payload = [
                {"statement": "Mara leads the caravan east.",
                 "classification": "INVENTED",
                 "quote": "the road ran east through wet fields"},
                {"statement": "The rainy season here lasts four months.",
                 "classification": "HISTORICAL",
                 "quote": "THIS QUOTE DOES NOT EXIST IN THE CHAPTER"},
                {"statement": "", "classification": "INVENTED", "quote": ""},  # invalid
                {"statement": "Bad class", "classification": "MAYBE", "quote": ""},  # invalid
            ]
        elif purpose == "position.persona":
            payload = [
                {"kind": "attribute", "statement": "Mara rises before dawn.",
                 "chapter": 1, "quote": "Mara crossed the yard before dawn."},
                {"kind": "relationship", "statement": "Mara travels with Tobin.",
                 "chapter": 1, "quote": '"Did you sleep?" asked Tobin.'},
            ]
        else:
            # ConStory / ASSAY purposes are out of scope here — the battery
            # must record those failures, not fabricate clean results.
            return SimpleNamespace(ok=False, text=None, error=f"unknown purpose {purpose}")
        return SimpleNamespace(ok=True, text=json.dumps(payload), error=None)


class PositionBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.work_id = self.db.create_work("Inherited Book", work_type="writing")["id"]
        from orivellum.capabilities import assay

        assay.seed_instruments(self.db)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _seed_book(self, n=40):
        for seq in range(1, n + 1):
            _seed_chapter(self.db, self.work_id, seq, f"Chapter {seq}", CHAPTER)

    def _audit(self, stub=None):
        stub = stub or _StubLLM()
        audit_id = self.db.create_position_audit(self.work_id)
        with patch("orivellum.capabilities.llm.llm_call", stub):
            result = position.run_position_audit(
                self.db, _cfg(), audit_id=audit_id, work_id=self.work_id
            )
        return audit_id, result, stub


# ── The ten deterministic tests ──────────────────────────────────────────────


class TestDeterministicTests(PositionBase):
    def test_all_ten_return_evidence_without_a_model(self):
        self._seed_book(5)
        # Break contiguity + add an empty and an out-of-band chapter.
        _seed_chapter(self.db, self.work_id, 8, "Gapped", CHAPTER)
        _seed_chapter(self.db, self.work_id, 9, "Empty", "   ")
        _seed_chapter(self.db, self.work_id, 10, "Tiny", "Too short.")
        chapters = position._load_chapters(self.db, self.work_id)
        with patch("orivellum.capabilities.llm.llm_call",
                   side_effect=AssertionError("deterministic tests must not call a model")):
            tests = {t["id"]: t for t in position.deterministic_tests(
                self.db, self.work_id, chapters)}
        self.assertEqual(len(tests), 10)
        self.assertFalse(tests["T1"]["passed"])
        self.assertEqual(tests["T1"]["evidence"]["gaps"], [6, 7])
        self.assertFalse(tests["T2"]["passed"])
        self.assertEqual(tests["T2"]["evidence"]["empty_chapters"], [9])
        self.assertFalse(tests["T3"]["passed"])
        outlier_seqs = {o["seq"] for o in tests["T3"]["evidence"]["outliers"]}
        self.assertIn(10, outlier_seqs)
        self.assertFalse(tests["T4"]["passed"])  # no G8 artifact
        self.assertFalse(tests["T5"]["passed"])
        self.assertEqual(tests["T5"]["evidence"]["count_by_classification"], {})
        self.assertFalse(tests["T6"]["passed"])
        self.assertFalse(tests["T7"]["passed"])  # no PRESS row
        self.assertFalse(tests["T8"]["passed"])
        self.assertFalse(tests["T9"]["passed"])
        self.assertTrue(tests["T10"]["passed"])  # instruments seeded in setUp
        for t in tests.values():
            self.assertIsInstance(t["evidence"], dict)

    def test_t5_counts_by_classification(self):
        from orivellum.database.canon_store import CanonStore

        CanonStore(self.db).create_fact(
            statement="The caravan trade predates the war.",
            classification="INVENTED", work_id=self.work_id,
            source_ref="ch1", signed_by="author",
        )
        t5 = position._t5_canon(self.db, self.work_id)
        self.assertTrue(t5["passed"])
        self.assertEqual(t5["evidence"]["count_by_classification"], {"INVENTED": 1})


# ── Stage derivation: the acceptance fixture ─────────────────────────────────


class TestStageDerivation(PositionBase):
    def test_forty_chapters_no_canon_is_a1_with_prose_not_b5(self):
        self._seed_book(40)
        pipeline = self.db.create_book_pipeline(self.work_id, "Inherited Book")
        claimed = pipeline["status"]
        audit_id, result, _ = self._audit()

        self.assertEqual(result["derived_stage"], "A1")
        self.assertNotEqual(result["derived_stage"], "B5")
        deriv = result["evidence"]["stage_derivation"]
        self.assertEqual(deriv["qualifier"], "with-prose")
        row = self.db.get_position_audit(audit_id)
        self.assertEqual(row["status"], "done")
        self.assertEqual(row["derived_stage"], "A1")
        self.assertEqual(row["claimed_stage"], claimed)
        # Step 7 — the discrepancy is reported in the row's evidence.
        disc = row["evidence"]["discrepancy"]
        self.assertEqual(disc["derived_stage"], "A1")
        self.assertEqual(disc["claimed_stage"], claimed)

    def test_gaps_below_always_win(self):
        """B-rung passes never lift the stage over a failing A-rung."""
        self._seed_book(3)
        tests = {t["id"]: t for t in position.deterministic_tests(
            self.db, self.work_id, position._load_chapters(self.db, self.work_id))}
        battery = {"constory": {"status": "done"}, "_open_findings": [], "instruments": []}
        stage = position.derive_stage(
            tests, battery, position._load_chapters(self.db, self.work_id), {})
        self.assertEqual(stage["derived_stage"], "A1")
        ladder = {s["stage"]: s for s in stage["ladder"]}
        self.assertTrue(ladder["B4"]["passed"])  # prose is fine …
        self.assertFalse(ladder["A1"]["passed"])  # … but canon is the gap

    def test_battery_rungs_fail_closed_on_missing_or_errored_instruments(self):
        """An errored or ABSENT battery instrument can never count as clean —
        the audit must not report a high stage on fabricated results."""
        done = [
            {"key": k, "status": "done", "verdict": "clean", "findings_count": 0}
            for k in ("voice.envelope", "drift.theology_lecture", "drift.catalog",
                      "drift.elihu", "drift.restoration", "gate.d13", "gate.d14",
                      "judge.hierarchical")
        ]
        clean = {"constory": {"status": "done"}, "_open_findings": [], "instruments": done}
        self.assertTrue(position._drift_clean(clean))
        self.assertTrue(position._judge_recorded(clean))

        # Missing instruments entirely → fail closed.
        empty = {"constory": {"status": "done"}, "_open_findings": [], "instruments": []}
        self.assertFalse(position._drift_clean(empty))
        self.assertFalse(position._judge_recorded(empty))

        # One errored run (gateway down) → that rung fails.
        errored = {**clean, "instruments": [
            i if i["key"] != "gate.d13" else {"key": "gate.d13", "status": "error",
                                              "error": "boom"}
            for i in done
        ]}
        self.assertFalse(position._drift_clean(errored))
        self.assertTrue(position._judge_recorded(errored))

        # A failing verdict → fails even when status is done.
        drifted = {**clean, "instruments": [
            i if i["key"] != "gate.d14" else {"key": "gate.d14", "status": "done",
                                              "verdict": "confirmed_drift"}
            for i in done
        ]}
        self.assertFalse(position._drift_clean(drifted))

        # An errored ConStory run is never continuity-clean.
        broken = {"constory": {"status": "error", "error": "gateway"},
                  "_open_findings": [], "instruments": done}
        self.assertFalse(position._battery_clean(broken, severities=("critical", "high")))


# ── Reconstruction proposals — review-gated, never auto-authority ────────────


class TestReconstruction(PositionBase):
    def test_everything_lands_as_proposals_nothing_becomes_authority(self):
        self._seed_book(4)
        _, result, stub = self._audit()
        recon = result["evidence"]["reconstruction"]

        proposals = self.db.list_position_proposals(work_id=self.work_id)
        kinds = {p["kind"] for p in proposals}
        self.assertIn("blueprint", kinds)
        self.assertIn("voice_spec", kinds)
        self.assertIn("persona", kinds)
        self.assertTrue(all(p["status"] == "proposed" for p in proposals))

        # Canon claims are staged as proposals, never facts.
        self.assertGreater(recon["proposals"]["canon_fact"]["proposed"], 0)
        with self.db._lock:
            facts = self.db._conn.execute("SELECT COUNT(*) c FROM canon_fact").fetchone()["c"]
            staged = self.db._conn.execute(
                "SELECT COUNT(*) c FROM wa_canon_proposals WHERE status='proposed'"
            ).fetchone()["c"]
        self.assertEqual(facts, 0)
        self.assertGreater(staged, 0)
        # No voice baseline installed without a signature.
        self.assertIsNone(self.db.get_assay_baseline(self.work_id, "voice_envelope"))
        self.assertTrue(any(c["purpose"] == "position.canon_extract" for c in stub.calls))

    def test_historical_without_locatable_source_becomes_inferred_with_gap(self):
        self._seed_book(1)
        self._audit()
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT fact_text, classification, source_location FROM wa_canon_proposals"
            ).fetchall()
        by_text = {r["fact_text"]: r for r in rows}
        gapped = next(r for t, r in by_text.items() if "source gap" in t)
        self.assertEqual(gapped["classification"], "INFERRED")
        self.assertEqual(gapped["source_location"], "unlocated")
        located = by_text["Mara leads the caravan east."]
        self.assertEqual(located["classification"], "INVENTED")
        self.assertTrue(located["source_location"].startswith("offset:"))

    def test_rerun_never_clobbers_a_resolved_proposal(self):
        self._seed_book(2)
        self._audit()
        blueprint = next(p for p in self.db.list_position_proposals(work_id=self.work_id)
                         if p["kind"] == "blueprint")
        self.assertEqual(
            self.db.resolve_position_proposal(
                blueprint["id"], decision="approved", author="author"),
            "ok",
        )
        self._audit()  # deterministic ids → INSERT OR IGNORE
        again = self.db.get_position_proposal(blueprint["id"])
        self.assertEqual(again["status"], "approved")
        self.assertEqual(again["resolved_by"], "author")
        blueprints = [p for p in self.db.list_position_proposals(work_id=self.work_id)
                      if p["kind"] == "blueprint"]
        self.assertEqual(len(blueprints), 1)

    def test_llm_down_is_recorded_and_audit_still_completes(self):
        self._seed_book(3)
        audit_id, result, _ = self._audit(stub=_StubLLM(down=True))
        recon = result["evidence"]["reconstruction"]
        self.assertTrue(any("gateway down" in e for e in recon["errors"]))
        # Deterministic proposals still exist; audit finished 'done'.
        kinds = {p["kind"] for p in self.db.list_position_proposals(work_id=self.work_id)}
        self.assertEqual({"blueprint", "voice_spec"}, kinds)
        self.assertEqual(self.db.get_position_audit(audit_id)["status"], "done")


# ── Proposal resolution — atomic claim + signature ───────────────────────────


class TestResolution(PositionBase):
    def _proposal(self, kind="voice_spec"):
        self._seed_book(2)
        self._audit()
        return next(p for p in self.db.list_position_proposals(work_id=self.work_id)
                    if p["kind"] == kind)

    def test_resolution_is_an_atomic_claim(self):
        p = self._proposal("blueprint")
        self.assertEqual(self.db.resolve_position_proposal(
            p["id"], decision="approved", author="author"), "ok")
        self.assertEqual(self.db.resolve_position_proposal(
            p["id"], decision="rejected", author="author"), "conflict")
        self.assertEqual(self.db.resolve_position_proposal(
            "nope", decision="approved", author="author"), "not_found")

    def test_signature_is_mandatory(self):
        p = self._proposal("blueprint")
        with self.assertRaises(ValueError):
            self.db.resolve_position_proposal(p["id"], decision="approved", author="  ")

    def test_review_gate_approving_voice_spec_installs_baseline(self):
        from orivellum.api.routes.review import ResolveBody, _resolve_position

        p = self._proposal("voice_spec")
        self.assertIsNone(self.db.get_assay_baseline(self.work_id, "voice_envelope"))
        out = _resolve_position(
            self.db, p["id"], ResolveBody(decision="approve", author="author"))
        self.assertEqual(out["installed"], "voice_envelope baseline")
        baseline = self.db.get_assay_baseline(self.work_id, "voice_envelope")
        self.assertIsNotNone(baseline)
        self.assertIn("metrics", baseline)

    def test_review_gate_requires_signature(self):
        from fastapi import HTTPException

        from orivellum.api.routes.review import ResolveBody, _resolve_position

        p = self._proposal("blueprint")
        with self.assertRaises(HTTPException) as ctx:
            _resolve_position(self.db, p["id"], ResolveBody(decision="approve"))
        self.assertEqual(ctx.exception.status_code, 422)
        self.assertEqual(self.db.get_position_proposal(p["id"])["status"], "proposed")

    def test_failed_baseline_install_rolls_the_approval_back(self):
        from fastapi import HTTPException

        from orivellum.api.routes.review import ResolveBody, _resolve_position

        p = self._proposal("voice_spec")
        with patch.object(self.db, "set_assay_baseline", side_effect=RuntimeError("disk full")), \
                self.assertRaises(HTTPException) as ctx:
            _resolve_position(self.db, p["id"],
                              ResolveBody(decision="approve", author="author"))
        self.assertEqual(ctx.exception.status_code, 500)
        # Returned to the queue — the author can simply retry.
        self.assertEqual(self.db.get_position_proposal(p["id"])["status"], "proposed")
        out = _resolve_position(self.db, p["id"],
                                ResolveBody(decision="approve", author="author"))
        self.assertEqual(out["installed"], "voice_envelope baseline")
        self.assertIsNotNone(self.db.get_assay_baseline(self.work_id, "voice_envelope"))

    def test_rejected_voice_spec_installs_nothing(self):
        from orivellum.api.routes.review import ResolveBody, _resolve_position

        p = self._proposal("voice_spec")
        _resolve_position(self.db, p["id"],
                          ResolveBody(decision="reject", author="author"))
        self.assertIsNone(self.db.get_assay_baseline(self.work_id, "voice_envelope"))


# ── Completion plan ──────────────────────────────────────────────────────────


class TestCompletionPlan(PositionBase):
    def test_repair_weights_the_early_band_over_raw_severity(self):
        findings = [
            {"id": "late-critical", "severity": "critical", "chapter_seq": 35,
             "category": "timeline_plot", "reasoning": "late clash"},
            {"id": "early-medium", "severity": "medium", "chapter_seq": 8,
             "category": "worldbuilding", "reasoning": "early fact drift"},
            {"id": "early-high", "severity": "high", "chapter_seq": 7,
             "category": "characterization", "reasoning": "early contradiction"},
        ]
        repair = position._repair_list(findings, total_chapters=40)
        # ch 7 and 8 sit in the 15–30% band (weight 3) → both outrank the
        # late critical; within the band severity orders them.
        self.assertEqual([r["finding_id"] for r in repair],
                         ["early-high", "early-medium", "late-critical"])
        self.assertEqual(repair[0]["weight"], position.EARLY_BAND_WEIGHT)
        self.assertEqual(repair[2]["weight"], 1.0)

    def test_backfill_lists_failing_origination_artifacts_in_order(self):
        self._seed_book(4)
        audit_id, result, _ = self._audit()
        plan = self.db.get_position_audit(audit_id)["blocking"]
        stages = [b["stage"] for b in plan["backfill"]]
        self.assertEqual(stages, ["A1", "A2", "A3", "A4"])  # A5 passes (seeded)
        # No ratified blueprint → Complete points at the Backfill gap.
        self.assertTrue(plan["complete"])
        self.assertIn("ratified blueprint", plan["complete"][0]["note"])

    def test_complete_lists_remaining_chapters_once_blueprint_ratified(self):
        self._seed_book(3)
        self._audit()
        blueprint = next(p for p in self.db.list_position_proposals(work_id=self.work_id)
                         if p["kind"] == "blueprint")
        # Author ratifies a blueprint that contracts 5 chapters.
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE position_proposal SET payload=? WHERE id=?",
                (json.dumps({"chapters": [], "chapter_count": 5}), blueprint["id"]),
            )
            self.db._conn.commit()
        self.db.resolve_position_proposal(blueprint["id"], decision="approved", author="author")
        audit_id, _, _ = self._audit()
        plan = self.db.get_position_audit(audit_id)["blocking"]
        self.assertEqual([c["seq"] for c in plan["complete"]], [4, 5])


# ── The audit row is the claim ───────────────────────────────────────────────


class TestAuditClaim(PositionBase):
    def test_double_dispatch_refused_while_running(self):
        self.db.create_position_audit(self.work_id)
        with self.assertRaises(RuntimeError):
            self.db.create_position_audit(self.work_id)

    def test_any_failure_finishes_the_row_as_error(self):
        audit_id = self.db.create_position_audit(self.work_id)
        with patch.object(position, "_run", side_effect=RuntimeError("boom")), \
                self.assertRaises(RuntimeError):
            position.run_position_audit(
                self.db, _cfg(), audit_id=audit_id, work_id=self.work_id)
        row = self.db.get_position_audit(audit_id)
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["error"], "boom")
        # The claim is released: a fresh audit can start.
        self.db.create_position_audit(self.work_id)


if __name__ == "__main__":
    unittest.main()
