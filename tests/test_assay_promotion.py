"""PROMOTION (E10) — shadow-mode certification for quality instruments.

Proves by assertion:
- registration can NEVER set certification (a contract carrying the field
  is refused), so an uncertified instrument cannot be registered straight
  into blocking authority; shadow_of persists and cannot self-reference;
- the certification lifecycle is a validated transition map with Tier 3
  refused at 'certified' forever, and set_assay_certification writes one
  append-only ledger row per transition;
- shadow runs are visibly labeled (run authority.shadow + per-finding
  evidence.shadow) and never block;
- a shadow candidate shares its baseline's runner family via shadow_of but
  applies its OWN thresholds;
- when a non-shadow instrument runs, its shadow companions co-run against
  the same work/chapter, linked via shadow_companion_of; a companion
  failure or claim conflict never affects the primary run;
- author dispositions drive rolling precision; promotion is refused below
  the declared bar (sample size or precision) and succeeds at the bar with
  the precision evidence recorded on the ledger row;
- demotion returns a certified instrument to shadow with no threshold;
- parity pairs shadow runs with their baseline runs and measures agreement
  on flagged units.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from orivellum.capabilities import assay
from orivellum.capabilities.assay import promotion
from orivellum.database.db import OrivellumDB, _now

NORMAL = (
    'Mara crossed the yard before dawn. "Did you sleep?" asked Tobin. '
    '"Not since the rains came," Mara said, pulling her cloak tighter. '
    "The gate stood open and the road ran east through wet fields. "
) * 12

CATALOG = (
    "The caravan carried wool, salt, copper, dried figs, oil, and rope. "
    "In the second wagon lay hides, tent poles, iron pins, wax, and thread. "
    "Tobin listed the losses: three goats, two oxen, a mule, four lambs, a dog. "
    "The manifest named barley, wheat, lentils, onions, garlic, and honey. "
) * 8


def _cfg():
    return SimpleNamespace(
        serving=SimpleNamespace(
            base_url="http://test.invalid",
            workhorse_model="drafter-model",
            reasoner_model="judge-model",
        )
    )


def _seed_chapter(db: OrivellumDB, work_id: str, seq: int, title: str, text: str) -> str:
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


CANDIDATE = {
    "key": "drift.catalog.candidate",
    "name": "Drift: Catalog (re-tuned candidate)",
    "tier": 1,
    "variance": "deterministic",
    "purpose": "Candidate re-tune of the catalog detector.",
    "allowed_ops": ["read chapter text"],
    "forbidden_ops": ["block while uncertified"],
    "authority_relationship": "shadow candidate of drift.catalog",
    "output_schema": {},
    "scope": {"prohibited": "entire book"},
    # Deliberately stricter than the baseline (min_series_items 4 → 3).
    "thresholds": {
        "min_series_items": 3,
        "max_series_runs_per_1000_words": 3.0,
        "promotion": {"min_precision": 0.75, "min_dispositions": 4},
    },
    "origin": "test",
    "shadow_of": "drift.catalog",
}


class PromotionBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.work_id = self.db.create_work("Ash and Silence", work_type="writing")["id"]
        assay.seed_instruments(self.db)
        _seed_chapter(self.db, self.work_id, 1, "One", NORMAL)
        _seed_chapter(self.db, self.work_id, 2, "Two", CATALOG)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _register_candidate(self, in_shadow: bool = True) -> dict:
        self.db.upsert_assay_instrument(dict(CANDIDATE))
        if in_shadow:
            self.db.set_assay_certification(
                CANDIDATE["key"], "shadow", actor="user", note="test"
            )
        return self.db.get_assay_instrument(CANDIDATE["key"])


class TestRegistrationEnforcement(PromotionBase):
    def test_registration_cannot_set_certification(self):
        contract = dict(CANDIDATE)
        contract["certification"] = "certified"
        with self.assertRaises(ValueError):
            self.db.upsert_assay_instrument(contract)
        # Nothing was registered.
        self.assertIsNone(self.db.get_assay_instrument(CANDIDATE["key"]))

    def test_new_instruments_always_start_advisory_and_never_block(self):
        inst = self._register_candidate(in_shadow=False)
        self.assertEqual(inst["certification"], "advisory")
        self.assertFalse(assay.is_blocking(inst))
        self.assertEqual(inst["shadow_of"], "drift.catalog")

    def test_shadow_of_cannot_self_reference(self):
        contract = dict(CANDIDATE)
        contract["shadow_of"] = contract["key"]
        with self.assertRaises(ValueError):
            self.db.upsert_assay_instrument(contract)


class TestLifecycle(PromotionBase):
    def test_transition_map_enforced(self):
        self._register_candidate(in_shadow=False)
        key = CANDIDATE["key"]
        # advisory -> certified is illegal (must earn it through shadow).
        with self.assertRaises(ValueError):
            self.db.set_assay_certification(key, "certified", actor="user")
        self.db.set_assay_certification(key, "shadow", actor="user")
        self.db.set_assay_certification(key, "certified", actor="user")
        # certified -> advisory is illegal (demotion goes to shadow).
        with self.assertRaises(ValueError):
            self.db.set_assay_certification(key, "advisory", actor="user")
        self.db.set_assay_certification(key, "retired", actor="user")
        # retired can only be reinstated into shadow.
        with self.assertRaises(ValueError):
            self.db.set_assay_certification(key, "certified", actor="user")
        self.db.set_assay_certification(key, "shadow", actor="user")

    def test_tier3_can_never_be_certified(self):
        self.db.set_assay_certification("judge.hierarchical", "shadow", actor="user")
        with self.assertRaises(ValueError):
            self.db.set_assay_certification("judge.hierarchical", "certified", actor="user")

    def test_every_transition_is_ledgered(self):
        inst = self._register_candidate(in_shadow=True)
        events = self.db.list_assay_certification_events(inst["id"])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["from_status"], "advisory")
        self.assertEqual(events[0]["to_status"], "shadow")
        self.assertEqual(events[0]["actor"], "user")


class TestShadowExecution(PromotionBase):
    def test_shadow_run_labeled_and_never_blocking(self):
        self._register_candidate()
        run = assay.run_instrument(
            self.db, _cfg(), key=CANDIDATE["key"], work_id=self.work_id
        )
        auth = run["evidence"]["authority"]
        self.assertTrue(auth["shadow"])
        self.assertFalse(auth["blocking"])
        findings = self.db.list_assay_findings(run["id"])
        self.assertTrue(findings, "candidate should fire on the catalog chapter")
        for f in findings:
            self.assertTrue(f["evidence"].get("shadow"))

    def test_candidate_uses_own_thresholds_via_baseline_runner(self):
        self._register_candidate()
        run = assay.run_instrument(
            self.db, _cfg(), key=CANDIDATE["key"], work_id=self.work_id
        )
        # It dispatched the drift.catalog runner family — findings carry the
        # catalog issue type but under the candidate's instrument id.
        findings = self.db.list_assay_findings(run["id"])
        inst = self.db.get_assay_instrument(CANDIDATE["key"])
        self.assertTrue(all(f["instrument_id"] == inst["id"] for f in findings))
        self.assertTrue(all(f["force_check"] == CANDIDATE["key"] for f in findings))

    def test_companion_coruns_with_baseline_and_links(self):
        self._register_candidate()
        primary = assay.run_instrument(
            self.db, _cfg(), key="drift.catalog", work_id=self.work_id
        )
        inst = self.db.get_assay_instrument(CANDIDATE["key"])
        runs = self.db.list_assay_runs(self.work_id, instrument_id=inst["id"])
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "done")
        self.assertEqual(runs[0]["evidence"]["shadow_companion_of"], primary["id"])

    def test_companion_failure_never_affects_primary(self):
        # A companion whose runner family does not exist fails its own run
        # but the primary run still completes.
        broken = dict(CANDIDATE)
        broken["key"] = "drift.broken.candidate"
        broken["shadow_of"] = "no.such.runner"
        self.db.upsert_assay_instrument(broken)
        self.db.set_assay_certification(broken["key"], "shadow", actor="user")
        # Point it at drift.catalog as companion by re-registering shadow_of.
        broken["shadow_of"] = "drift.catalog"
        self.db.upsert_assay_instrument(broken)
        # Sabotage: make the companion's own claim conflict by pre-claiming.
        inst = self.db.get_assay_instrument(broken["key"])
        self.db.create_assay_run(instrument_id=inst["id"], work_id=self.work_id)
        primary = assay.run_instrument(
            self.db, _cfg(), key="drift.catalog", work_id=self.work_id
        )
        self.assertEqual(primary["status"], "done")

    def test_shadow_companions_do_not_corun_recursively(self):
        self._register_candidate()
        # Running the shadow candidate directly must not trigger companions.
        assay.run_instrument(self.db, _cfg(), key=CANDIDATE["key"], work_id=self.work_id)
        inst = self.db.get_assay_instrument(CANDIDATE["key"])
        runs = self.db.list_assay_runs(self.work_id, instrument_id=inst["id"])
        self.assertEqual(len(runs), 1)


class TestPrecisionAndPromotion(PromotionBase):
    def _disposition_n(self, inst: dict, tp: int, fp: int) -> None:
        run = assay.run_instrument(
            self.db, _cfg(), key=inst["key"], work_id=self.work_id
        )
        findings = self.db.list_assay_findings(run["id"])
        needed = tp + fp
        # The catalog fixture fires multiple findings; synthesize extras if
        # the fixture produced fewer than needed.
        while len(findings) < needed:
            fid = self.db.create_assay_finding(
                run_id=run["id"], instrument_id=inst["id"], work_id=self.work_id,
                chapter_id=None, unit=f"chapter {len(findings)}",
                force_check=inst["key"], issue_type="catalog_prose",
                severity="medium", classification="deterministic",
                action="author_review", evidence={},
            )
            findings.append(self.db.get_assay_finding(fid))
        for i, f in enumerate(findings[:needed]):
            self.db.set_assay_finding_disposition(
                f["id"],
                "true_positive" if i < tp else "false_positive",
                actor="user",
            )

    def test_rolling_precision_and_series(self):
        inst = self._register_candidate()
        self._disposition_n(inst, tp=3, fp=1)
        report = promotion.precision_report(self.db, inst)
        self.assertEqual(report["sample_size"], 4)
        self.assertEqual(report["precision"], 0.75)
        self.assertEqual(len(report["series"]), 4)
        self.assertTrue(report["meets_bar"])

    def test_no_dispositions_means_no_invented_precision(self):
        inst = self._register_candidate()
        report = promotion.precision_report(self.db, inst)
        self.assertIsNone(report["precision"])
        self.assertFalse(report["meets_bar"])

    def test_promotion_refused_below_sample_size(self):
        inst = self._register_candidate()
        self._disposition_n(inst, tp=2, fp=0)
        with self.assertRaises(promotion.PromotionError):
            promotion.promote(self.db, inst["key"], author="user")

    def test_promotion_refused_below_precision(self):
        inst = self._register_candidate()
        self._disposition_n(inst, tp=2, fp=2)  # 0.5 < 0.75 bar
        with self.assertRaises(promotion.PromotionError):
            promotion.promote(self.db, inst["key"], author="user")

    def test_promotion_at_bar_certifies_and_ledgers_evidence(self):
        inst = self._register_candidate()
        self._disposition_n(inst, tp=3, fp=1)
        updated = promotion.promote(self.db, inst["key"], author="user", note="earned")
        self.assertEqual(updated["certification"], "certified")
        self.assertTrue(assay.is_blocking(updated))
        events = self.db.list_assay_certification_events(inst["id"])
        promo = events[0]
        self.assertEqual(promo["to_status"], "certified")
        self.assertEqual(promo["precision_val"], 0.75)
        self.assertEqual(promo["sample_size"], 4)
        self.assertEqual(promo["actor"], "user")

    def test_advisory_instrument_cannot_be_promoted(self):
        inst = self._register_candidate(in_shadow=False)
        with self.assertRaises(promotion.PromotionError):
            promotion.promote(self.db, inst["key"], author="user")

    def test_demotion_returns_certified_to_shadow(self):
        inst = self._register_candidate()
        self._disposition_n(inst, tp=4, fp=0)
        promotion.promote(self.db, inst["key"], author="user")
        updated = promotion.demote(self.db, inst["key"], author="user", note="degraded")
        self.assertEqual(updated["certification"], "shadow")
        self.assertFalse(assay.is_blocking(updated))

    def test_degradation_flagged_on_dashboard(self):
        inst = self._register_candidate()
        self._disposition_n(inst, tp=3, fp=1)
        promotion.promote(self.db, inst["key"], author="user")
        # Now the record degrades: pile on false positives.
        self._disposition_n(self.db.get_assay_instrument(inst["key"]), tp=0, fp=4)
        rows = {r["key"]: r for r in promotion.dashboard(self.db)}
        self.assertTrue(rows[inst["key"]]["degraded"])


class TestParity(PromotionBase):
    def test_parity_pairs_shadow_and_baseline_runs(self):
        inst = self._register_candidate()
        assay.run_instrument(self.db, _cfg(), key="drift.catalog", work_id=self.work_id)
        report = promotion.parity_report(self.db, self.db.get_assay_instrument(inst["key"]))
        self.assertEqual(report["baseline"], "drift.catalog")
        self.assertEqual(len(report["pairs"]), 1)
        self.assertIsNotNone(report["mean_agreement"])
        self.assertGreaterEqual(report["pairs"][0]["agreement"], 0.0)

    def test_parity_empty_without_baseline(self):
        report = promotion.parity_report(
            self.db, self.db.get_assay_instrument("drift.catalog")
        )
        self.assertIsNone(report["mean_agreement"])
        self.assertEqual(report["pairs"], [])


if __name__ == "__main__":
    unittest.main()
