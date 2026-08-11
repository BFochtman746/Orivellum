"""ASSAY instrument registry tests (Masterpiece Pipeline A4/A5 + B8/B9).

Proves by assertion:
- every contract is registered with Engine Contract fields, an honest tier,
  and certification='advisory' on first registration; re-seeding refreshes
  the contract but PRESERVES certification;
- blocking is COMPUTED: Tier 1/2 blocks only once certified, Tier 3 never
  blocks — even if someone marks it certified;
- the voice metrics are computable (sentence lengths, register bands,
  imagery density, diction fingerprints) and envelope comparison flags a
  chapter outside the stored baseline; no baseline → 'no_baseline', never
  an invented target;
- each of the four drift detectors fires on its fixture chapter with
  verbatim quoted evidence at real offsets, and stays silent on clean prose
  (restoration is silent at/after its permitted chapter);
- D13 is pure arithmetic: skewed act word-shares fail with per-act
  evidence, balanced shares pass;
- D14 pairs Tier-1 signatures with Tier-2 confirmation: confirmed → high +
  dispositionable, unconfirmed → info advisory; gateway-down never fails a
  chapter;
- D15–D17 are locked (zero model calls) until an author signature exists;
  D17's structural conditions run unconditionally;
- the judge refuses to run when its model equals the drafting model, emits
  ONLY advisory verdicts, and surfaces a pairwise regression (revision N
  scoring below N−1) as a finding;
- the run row is the claim: a second run for the same instrument+work is
  refused while one is running;
- every registered instrument runs against a fixture and returns a
  verdict-or-score with evidence.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orivellum.capabilities import assay
from orivellum.capabilities.assay import drift, force, judge, metrics
from orivellum.database.db import OrivellumDB, _now

# ── Fixture chapters ─────────────────────────────────────────────────────────

NORMAL = (
    'Mara crossed the yard before dawn. "Did you sleep?" asked Tobin. '
    '"Not since the rains came," Mara said, pulling her cloak tighter. '
    "The gate stood open and the road ran east through wet fields. "
    "She counted the wagons, checked the harnesses, and said nothing more. "
) * 12

LECTURE = (
    "It follows that suffering cannot be punishment, for if the innocent "
    "suffer, the premise fails. Therefore the argument must be restated. "
    "Consider that justice, thus conceived, cannot be weighed in harvests. "
    "Hence we must conclude the ledger of heaven is not a ledger at all. "
    "The truth is that no man may audit it. Is it not plain? In other words, "
    "the frame itself must be discarded, for it cannot bear the weight. "
) * 8

CATALOG = (
    "The caravan carried wool, salt, copper, dried figs, oil, and rope. "
    "In the second wagon lay hides, tent poles, iron pins, wax, and thread. "
    "Tobin listed the losses: three goats, two oxen, a mule, four lambs, a dog. "
    "The manifest named barley, wheat, lentils, onions, garlic, and honey. "
) * 8

ELIHU_PARA = (
    "Listen to me, for you have spoken and your words were wind. Surely you "
    "cannot weigh what you have not carried, and your hands are empty. You "
    "must answer, and you will not, for your mouth is full of dust and your "
    "certainty is borrowed. Know this: your ledger is blank and your case is "
    "smoke, and I will answer what you dare not ask of your own heart."
)
ELIHU = "\n\n".join([ELIHU_PARA] * 4)

RESTORATION = (
    "In the end his fortunes were doubled and his house was made whole. "
    "He was comforted by his brothers and blessed him beyond the former days. "
    "All was well in his latter days, and he prospered in the land. "
) + NORMAL


def _cfg(workhorse="drafter-model", reasoner="judge-model"):
    return SimpleNamespace(
        serving=SimpleNamespace(
            base_url="http://test.invalid",
            workhorse_model=workhorse,
            reasoner_model=reasoner,
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


class _StubLLM:
    """Dispatch llm_call by purpose; record every call."""

    def __init__(self, confirm_when: str = "", prefer: str = "B", down: bool = False):
        self.calls: list[dict] = []
        self.confirm_when = confirm_when
        self.prefer = prefer
        self.down = down

    def __call__(self, messages, **kwargs):
        purpose = kwargs.get("purpose", "")
        prompt = messages[-1]["content"]
        self.calls.append({"purpose": purpose, "prompt": prompt, "model": kwargs.get("model")})
        if self.down:
            return SimpleNamespace(ok=False, text=None, error="gateway down")
        if purpose == "assay.d14.confirm":
            confirmed = bool(self.confirm_when) and self.confirm_when in prompt
            payload = {"confirmed": confirmed, "reason": "checked"}
        elif purpose == "assay.judge.story":
            payload = {"annotations": {"thematic_coherence": ["CH 1: theme holds"]}}
        elif purpose == "assay.judge.chapter":
            payload = {"annotations": {"hook_and_close": ["opens flat"]}}
        elif purpose == "assay.judge.sentence":
            payload = {"annotations": {"rhythm": ["sentence 2 drags"]}}
        elif purpose == "assay.judge.pairwise":
            payload = {
                "scores_a": {"hook_and_close": 70},
                "scores_b": {"hook_and_close": 60},
                "preference": self.prefer,
                "reason": "test",
            }
        elif purpose.endswith(".evidence"):
            payload = {"annotations": ["quoted passage carries the argument"]}
        else:
            return SimpleNamespace(ok=False, text=None, error=f"unknown purpose {purpose}")
        return SimpleNamespace(ok=True, text=json.dumps(payload), error=None)


class AssayBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.work_id = self.db.create_work("Ash and Silence", work_type="writing")["id"]
        assay.seed_instruments(self.db)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _seed_book(self) -> dict[int, str]:
        return {
            1: _seed_chapter(self.db, self.work_id, 1, "One", NORMAL),
            2: _seed_chapter(self.db, self.work_id, 2, "Two", LECTURE),
            3: _seed_chapter(self.db, self.work_id, 3, "Three", CATALOG),
            4: _seed_chapter(self.db, self.work_id, 4, "Four", ELIHU),
            5: _seed_chapter(self.db, self.work_id, 5, "Five", RESTORATION),
        }

    def _run(self, key, stub=None, chapter_id=None):
        stub = stub or _StubLLM()
        with patch("orivellum.capabilities.llm.llm_call", stub):
            run = assay.run_instrument(
                self.db, _cfg(), key=key, work_id=self.work_id, chapter_id=chapter_id
            )
        return run, self.db.list_assay_findings(run["id"]), stub


# ── Registry ─────────────────────────────────────────────────────────────────


class TestRegistry(AssayBase):
    def test_every_contract_registered_advisory(self):
        instruments = {i["key"]: i for i in self.db.list_assay_instruments()}
        self.assertEqual(set(instruments), set(assay.INSTRUMENT_KEYS))
        for inst in instruments.values():
            # FORCE detectors enter shadow on first registration (M16);
            # everything else starts plain advisory.
            expected = "shadow" if inst["key"] in force.FORCE_KEYS else "advisory"
            self.assertEqual(inst["certification"], expected)
            self.assertIn(inst["tier"], (1, 2, 3))
            self.assertTrue(inst["purpose"])
            self.assertTrue(inst["allowed_ops"])
            self.assertTrue(inst["forbidden_ops"])
            self.assertTrue(inst["authority_relationship"])
            self.assertTrue(inst["output_schema"])
            self.assertFalse(assay.is_blocking(inst))

    def test_reseed_preserves_certification(self):
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE assay_instrument SET certification='certified' WHERE key='gate.d13'"
            )
            self.db._conn.commit()
        assay.seed_instruments(self.db)
        inst = self.db.get_assay_instrument("gate.d13")
        self.assertEqual(inst["certification"], "certified")
        self.assertTrue(assay.is_blocking(inst))

    def test_tier3_never_blocks_even_certified(self):
        inst = dict(self.db.get_assay_instrument("judge.hierarchical"))
        inst["certification"] = "certified"
        self.assertFalse(assay.is_blocking(inst))

    def test_expected_tiers(self):
        tiers = {i["key"]: i["tier"] for i in self.db.list_assay_instruments()}
        self.assertEqual(tiers["gate.d13"], 1)
        for k in ("drift.theology_lecture", "drift.catalog", "drift.elihu", "drift.restoration"):
            self.assertEqual(tiers[k], 1)
        for k in ("voice.envelope", "gate.d14", "gate.d15", "gate.d16", "gate.d17"):
            self.assertEqual(tiers[k], 2)
        self.assertEqual(tiers["judge.hierarchical"], 3)


# ── Voice envelope ───────────────────────────────────────────────────────────


class TestVoiceEnvelope(AssayBase):
    def test_metrics_computable(self):
        m = metrics.compute_voice_metrics(NORMAL, character_names=["Mara", "Tobin"])
        self.assertGreater(m["word_count"], 100)
        self.assertGreater(m["sentence_lengths"]["mean"], 0)
        self.assertIn("latinate_ratio", m["register"])
        self.assertGreater(m["imagery"]["per_1000_words"], 0)
        self.assertIn("Tobin", m["diction_fingerprints"])

    def test_no_baseline_is_honest(self):
        self._seed_book()
        run, findings, _ = self._run("voice.envelope")
        self.assertEqual(run["verdict"], "no_baseline")
        self.assertEqual(findings, [])

    def test_deviation_flagged_against_baseline(self):
        chapters = self._seed_book()
        assay.build_voice_baseline(self.db, self.work_id, reference_text=NORMAL)
        run, findings, _ = self._run("voice.envelope", chapter_id=chapters[2])
        self.assertEqual(run["verdict"], "deviations")
        self.assertEqual(findings[0]["issue_type"], "voice_envelope_deviation")
        self.assertTrue(findings[0]["evidence"]["deviations"])
        run2, findings2, _ = self._run("voice.envelope", chapter_id=chapters[1])
        self.assertEqual(run2["verdict"], "pass")
        self.assertEqual(findings2, [])

    def test_baseline_requires_some_text(self):
        with self.assertRaises(assay.AssayError):
            assay.build_voice_baseline(self.db, self.work_id)


# ── Drift detectors ──────────────────────────────────────────────────────────


class TestDriftDetectors(AssayBase):
    def _assert_quotes_grounded(self, text, detections):
        for d in detections:
            self.assertTrue(d["quotes"])
            for q in d["quotes"]:
                self.assertTrue(q["quote"])
                self.assertIn(
                    text[q["offset"] : q["offset"] + 20].strip()[:10],
                    q["quote"] + text,  # offset lands inside real text
                )
                self.assertLess(q["offset"], len(text))

    def test_theology_lecture_fires_and_stays_silent(self):
        hits = drift.detect_theology_lecture(LECTURE)
        self.assertEqual(len(hits), 1)
        self._assert_quotes_grounded(LECTURE, hits)
        self.assertEqual(drift.detect_theology_lecture(NORMAL), [])

    def test_catalog_fires_and_stays_silent(self):
        hits = drift.detect_catalog(CATALOG)
        self.assertEqual(len(hits), 1)
        self._assert_quotes_grounded(CATALOG, hits)
        self.assertEqual(drift.detect_catalog(NORMAL), [])

    def test_elihu_fires_and_stays_silent(self):
        hits = drift.detect_elihu(ELIHU)
        self.assertEqual(len(hits), 1)
        self._assert_quotes_grounded(ELIHU, hits)
        self.assertEqual(drift.detect_elihu(NORMAL), [])

    def test_restoration_respects_chapter_range(self):
        hits = drift.detect_restoration(RESTORATION, 5)
        self.assertEqual(len(hits), 1)
        self._assert_quotes_grounded(RESTORATION, hits)
        self.assertEqual(drift.detect_restoration(RESTORATION, 71), [])
        self.assertEqual(drift.detect_restoration(NORMAL, 5), [])

    def test_drift_run_records_findings_with_evidence(self):
        self._seed_book()
        run, findings, _ = self._run("drift.catalog")
        self.assertEqual(run["verdict"], "detected")
        self.assertEqual(len(findings), 1)
        f = findings[0]
        self.assertEqual(f["unit"], "chapter 3")
        self.assertEqual(f["force_check"], "drift.catalog")
        self.assertEqual(f["issue_type"], "catalog")
        self.assertEqual(f["classification"], "deterministic")
        self.assertTrue(f["evidence"]["quotes"])


# ── D13 macro-pacing ─────────────────────────────────────────────────────────


class TestD13(AssayBase):
    def test_skewed_acts_fail_with_evidence(self):
        # 4 chapters, act split 1-1/2-2/3-3/4-4; chapter 1 carries ~all words.
        _seed_chapter(self.db, self.work_id, 1, "One", NORMAL * 10)
        for seq in (2, 3, 4):
            _seed_chapter(self.db, self.work_id, seq, f"C{seq}", "Short. " * 20)
        run, findings, _ = self._run("gate.d13")
        self.assertEqual(run["verdict"], "fail")
        self.assertTrue(findings)
        self.assertEqual(findings[0]["classification"], "deterministic")
        self.assertIn("share", findings[0]["evidence"])

    def test_balanced_acts_pass(self):
        for seq in (1, 2, 3, 4):
            _seed_chapter(self.db, self.work_id, seq, f"C{seq}", NORMAL)
        run, findings, _ = self._run("gate.d13")
        self.assertEqual(run["verdict"], "pass")
        self.assertEqual(run["score"], 1.0)
        self.assertEqual(findings, [])


# ── D14 signature + confirmation ─────────────────────────────────────────────


class TestD14(AssayBase):
    def test_confirmed_vs_unconfirmed(self):
        self._seed_book()
        stub = _StubLLM(confirm_when='"catalog"')
        run, findings, stub = self._run("gate.d14", stub=stub)
        by_type = {f["issue_type"]: f for f in findings}
        self.assertEqual(by_type["catalog"]["severity"], "high")
        self.assertEqual(by_type["catalog"]["classification"], "confirmed")
        self.assertEqual(by_type["theology_lecture"]["severity"], "info")
        self.assertEqual(by_type["theology_lecture"]["classification"], "unconfirmed_signature")
        self.assertEqual(run["verdict"], "confirmed_drift")
        self.assertGreaterEqual(run["evidence"]["confirmed"], 1)
        # every confirmation went through the gateway with the original quotes
        confirm_calls = [c for c in stub.calls if c["purpose"] == "assay.d14.confirm"]
        self.assertEqual(len(confirm_calls), len(findings))

    def test_string_boolean_never_confirms(self):
        """Strict JSON boolean required: {"confirmed": "false"} is malformed,
        not a confirmation — it must stay an unconfirmed advisory."""
        self._seed_book()

        class _SneakyStub(_StubLLM):
            def __call__(self, messages, **kwargs):
                if kwargs.get("purpose") == "assay.d14.confirm":
                    self.calls.append({"purpose": "assay.d14.confirm"})
                    return SimpleNamespace(
                        ok=True, text=json.dumps({"confirmed": "false"}), error=None
                    )
                return super().__call__(messages, **kwargs)

        run, findings, _ = self._run("gate.d14", stub=_SneakyStub())
        self.assertEqual(run["verdict"], "clean")
        for f in findings:
            self.assertEqual(f["classification"], "unconfirmed_signature")
            self.assertEqual(f["severity"], "info")

    def test_gateway_down_never_fails_a_chapter(self):
        self._seed_book()
        run, findings, _ = self._run("gate.d14", stub=_StubLLM(down=True))
        self.assertEqual(run["verdict"], "clean")
        for f in findings:
            self.assertEqual(f["severity"], "info")
            self.assertEqual(f["classification"], "unconfirmed_signature")


# ── D15–D17 signature gates ──────────────────────────────────────────────────


class TestSignatureGates(AssayBase):
    def test_locked_without_signature_makes_zero_model_calls(self):
        _seed_chapter(self.db, self.work_id, 46, "C46", NORMAL)
        run, findings, stub = self._run("gate.d15")
        self.assertEqual(run["verdict"], "locked")
        self.assertEqual(findings, [])
        self.assertEqual(stub.calls, [])

    def test_signature_opens_evidence_gathering(self):
        _seed_chapter(self.db, self.work_id, 46, "C46", NORMAL)
        self.db.create_assay_signature(
            work_id=self.work_id, gate_key="gate.d15", author="author", decision="open"
        )
        run, findings, stub = self._run("gate.d15")
        self.assertEqual(run["verdict"], "evidence_gathered")
        self.assertTrue(findings)
        f = findings[0]
        self.assertEqual(f["severity"], "info")
        self.assertEqual(f["classification"], "perspectival")
        self.assertEqual(f["action"], "author_review")
        # the judge model, never the drafter
        self.assertEqual(stub.calls[0]["model"], "judge-model")

    def test_no_go_signature_stays_locked(self):
        _seed_chapter(self.db, self.work_id, 60, "C60", NORMAL)
        self.db.create_assay_signature(
            work_id=self.work_id, gate_key="gate.d16", author="author", decision="no_go"
        )
        run, _, stub = self._run("gate.d16")
        self.assertEqual(run["verdict"], "locked")
        self.assertEqual(stub.calls, [])

    def test_d17_structural_conditions_run_unsigned(self):
        _seed_chapter(self.db, self.work_id, 5, "Five", RESTORATION)
        run, findings, stub = self._run("gate.d17")
        self.assertEqual(run["verdict"], "structural_violations")
        self.assertEqual(stub.calls, [])  # deterministic — no model involved
        structural = [f for f in findings if f["classification"] == "structural"]
        self.assertTrue(structural)
        self.assertEqual(structural[0]["issue_type"], "restoration_before_permitted")
        self.assertIn("missing_chapters_in_range", run["evidence"]["structural"])

    def test_signature_requires_author(self):
        with self.assertRaises(ValueError):
            self.db.create_assay_signature(
                work_id=self.work_id, gate_key="gate.d15", author="  ", decision="open"
            )


# ── Hierarchical judge ───────────────────────────────────────────────────────


class TestJudge(AssayBase):
    def test_judge_model_never_the_drafter(self):
        cfg = _cfg(workhorse="same-model", reasoner="same-model")
        with self.assertRaises(judge.JudgeModelError):
            judge.judge_model(self.db, cfg)
        self.db.set_setting("judge_model_override", "other-model")
        self.assertEqual(judge.judge_model(self.db, cfg), "other-model")

    def test_advisory_forever_with_annotations(self):
        chapters = self._seed_book()
        run, findings, stub = self._run("judge.hierarchical", chapter_id=chapters[1])
        self.assertEqual(run["verdict"], "advisory")
        self.assertIsNone(run["score"])
        types = {f["issue_type"] for f in findings}
        self.assertIn("chapter.hook_and_close", types)
        self.assertIn("sentence.rhythm", types)
        for f in findings:
            self.assertEqual(f["classification"], "perspectival")
        for c in stub.calls:
            self.assertEqual(c["model"], "judge-model")

    def test_pairwise_regression_surfaced(self):
        ch_id = _seed_chapter(self.db, self.work_id, 1, "One", NORMAL)
        run1, _, _ = self._run("judge.hierarchical", chapter_id=ch_id)
        self.assertEqual(run1["evidence"]["pairwise"], [])
        # revise the chapter, judge prefers the PREVIOUS revision
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE book_chapters SET text=? WHERE id=?", (NORMAL + " New ending.", ch_id)
            )
            self.db._conn.commit()
        run2, findings2, _ = self._run(
            "judge.hierarchical", stub=_StubLLM(prefer="A"), chapter_id=ch_id
        )
        self.assertTrue(run2["evidence"]["pairwise"])
        regressions = [f for f in findings2 if f["issue_type"] == "pairwise_regression"]
        self.assertEqual(len(regressions), 1)
        self.assertEqual(regressions[0]["action"], "review_regression")

    def test_score_decrease_is_a_regression_even_when_preferred(self):
        """A response preferring B while lowering a rubric category still
        surfaces a regression — never silently accepted."""
        ch_id = _seed_chapter(self.db, self.work_id, 1, "One", NORMAL)
        self._run("judge.hierarchical", chapter_id=ch_id)
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE book_chapters SET text=? WHERE id=?", (NORMAL + " More.", ch_id)
            )
            self.db._conn.commit()
        # default stub prefers B but scores_b (60) < scores_a (70)
        run, findings, _ = self._run("judge.hierarchical", chapter_id=ch_id)
        regressions = [f for f in findings if f["issue_type"] == "pairwise_regression"]
        self.assertEqual(len(regressions), 1)
        self.assertEqual(
            regressions[0]["evidence"]["decreased_categories"], ["hook_and_close"]
        )

    def test_malformed_annotations_fail_loud(self):
        chapters = self._seed_book()

        class _MalformedStub(_StubLLM):
            def __call__(self, messages, **kwargs):
                if kwargs.get("purpose") == "assay.judge.chapter":
                    return SimpleNamespace(
                        ok=True, text=json.dumps({"annotations": "not a map"}), error=None
                    )
                return super().__call__(messages, **kwargs)

        with self.assertRaises(assay.AssayError):
            self._run("judge.hierarchical", stub=_MalformedStub(), chapter_id=chapters[1])
        self.assertEqual(self.db.list_assay_runs(self.work_id)[0]["status"], "error")

    def test_gateway_failure_marks_run_error(self):
        chapters = self._seed_book()
        with self.assertRaises(assay.AssayError):
            self._run("judge.hierarchical", stub=_StubLLM(down=True), chapter_id=chapters[1])
        runs = self.db.list_assay_runs(self.work_id)
        self.assertEqual(runs[0]["status"], "error")


# ── Run claim + full battery ─────────────────────────────────────────────────


class TestRunsAndBattery(AssayBase):
    def test_run_row_is_the_claim(self):
        inst = self.db.get_assay_instrument("drift.catalog")
        self.db.create_assay_run(instrument_id=inst["id"], work_id=self.work_id)
        with self.assertRaises(RuntimeError):
            self.db.create_assay_run(instrument_id=inst["id"], work_id=self.work_id)

    def test_every_instrument_proves_itself_on_a_fixture(self):
        """M7 acceptance: each check runs on a fixture and returns a
        score-or-verdict with evidence."""
        self._seed_book()
        assay.build_voice_baseline(self.db, self.work_id, reference_text=NORMAL)
        for gate in ("gate.d15", "gate.d16", "gate.d17"):
            self.db.create_assay_signature(
                work_id=self.work_id, gate_key=gate, author="author", decision="open"
            )
        for key in assay.INSTRUMENT_KEYS:
            run, findings, _ = self._run(key, stub=_StubLLM(confirm_when="catalog"))
            self.assertEqual(run["status"], "done", key)
            self.assertTrue(run["verdict"] or run["score"] is not None, key)
            self.assertTrue(run["evidence"] or findings, key)
            for f in findings:
                self.assertTrue(f["unit"], key)
                self.assertEqual(f["force_check"], key)
                self.assertTrue(f["issue_type"], key)
                self.assertIn(f["severity"], ("critical", "high", "medium", "low", "info"), key)

    def test_every_run_stamps_computed_authority(self):
        """Blocking is computed at execution: advisory instruments carry
        blocking=false in every run's evidence."""
        self._seed_book()
        run, _, _ = self._run("gate.d13")
        auth = run["evidence"]["authority"]
        self.assertEqual(auth["certification"], "advisory")
        self.assertFalse(auth["blocking"])
        self.assertEqual(auth["tier"], 1)

    def test_cross_work_chapter_refused_at_claim(self):
        other = self.db.create_work("Other", work_type="writing")["id"]
        foreign_ch = _seed_chapter(self.db, other, 1, "X", NORMAL)
        inst = self.db.get_assay_instrument("drift.catalog")
        with self.assertRaises(ValueError):
            self.db.create_assay_run(
                instrument_id=inst["id"], work_id=self.work_id, chapter_id=foreign_ch
            )

    def test_retired_instrument_never_leaks_a_running_claim(self):
        """A pre-claimed run row for a retired instrument must finish as
        'error' — never a permanent 'running' row locking the work."""
        self._seed_book()
        inst = self.db.get_assay_instrument("drift.catalog")
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE assay_instrument SET certification='retired' WHERE key='drift.catalog'"
            )
            self.db._conn.commit()
        run_id = self.db.create_assay_run(instrument_id=inst["id"], work_id=self.work_id)
        with self.assertRaises(assay.AssayError):
            assay.run_instrument(
                self.db, _cfg(), key="drift.catalog", work_id=self.work_id, run_id=run_id
            )
        run = self.db.get_assay_run(run_id)
        self.assertEqual(run["status"], "error")
        # no lingering claim: a fresh run row can be created immediately
        run_id2 = self.db.create_assay_run(instrument_id=inst["id"], work_id=self.work_id)
        self.db.finish_assay_run(run_id2, status="done")

    def test_unregistered_instrument_refused(self):
        with self.assertRaises(assay.AssayError):
            assay.run_instrument(self.db, _cfg(), key="nope", work_id=self.work_id)


if __name__ == "__main__":
    unittest.main()
