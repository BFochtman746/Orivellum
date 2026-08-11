"""FORCE — the Story Force engines 11–17 as ASSAY instruments (E11 / M16).

Proves by assertion:
- all seven FORCE contracts are registered as Tier-2 deterministic Engine
  Contracts and enter SHADOW mode on first registration (never plain
  advisory), with a ledgered system transition;
- a re-seed never overrides an author's deliberate demotion to advisory
  (a ledger row exists, so the auto-shadow entry is skipped);
- each detector fires on its trigger fixture with measures plus quoted
  evidence, and stays silent on the clean fixture;
- book-level (story) findings carry unit='story' and no chapter_id;
- a chapter-scoped run computes full-book context but reports findings for
  the requested chapter only, and never duplicates story-level findings;
- FORCE runs through the ASSAY runner are labeled shadow and can never
  block (blocking is computed).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orivellum.capabilities import assay
from orivellum.capabilities.assay import force
from orivellum.database.db import OrivellumDB, _now

# ── Fixtures ─────────────────────────────────────────────────────────────────

# Balanced chapter: names, dialogue, tension, conflict, causation.
LIVELY = (
    'Mara crossed the yard before dawn, afraid of what the silence meant. '
    '"Did you sleep?" asked Tobin. "Not since the rains came," Mara said. '
    "Because the gate had been forced, they argued over the watch, and the "
    "quarrel led to a threat neither meant. Her heart pounded at the danger. "
    "So that no one else would hear, she drew him behind the wall. "
) * 10

# Stalled chapter: long sentences, no dialogue, no tension, no causation,
# no new names — momentum stall + purpose-unclear trigger.
STALL_SENTENCE = (
    "The valley lay quiet under the pale morning while the mist moved slowly "
    "across the terraces of the lower fields and gathered along the stone "
    "walls that divided one plot from another in the old manner of the hill "
    "villages whose people had always measured their seasons by the water. "
)
STALLED = STALL_SENTENCE * 24

# No causal connectives but plenty of length (narrative-physics trigger).
NO_CAUSE = (
    "Mara walked the road. Tobin counted wagons. The dogs barked at the fence. "
    "A trader waved from the well. The bread was warm. The sky stayed grey. "
) * 30


def _chapter(i: int, text: str, cid: str | None = None) -> dict:
    return {"id": cid or f"ch{i}", "seq": i, "title": f"Chapter {i}", "text": text}


def _run(key: str, chapters: list[dict], th: dict | None = None, **kw) -> dict:
    return force.run_detector(key, chapters, th or {}, **kw)


def _issues(result: dict) -> list[str]:
    return [f["issue_type"] for f in result["findings"]]


class TestForceDetectorsUnit(unittest.TestCase):
    def test_structural_outliers_and_monolith(self):
        chapters = [_chapter(i, LIVELY) for i in range(1, 5)]
        chapters.append(_chapter(5, STALL_SENTENCE * 90))  # huge, no scene breaks
        r = _run("force.structural_enforcement", chapters)
        self.assertEqual(r["verdict"], "detected")
        issues = _issues(r)
        self.assertIn("chapter_length_outlier_long", issues)
        self.assertIn("monolithic_chapter", issues)
        for f in r["findings"]:
            self.assertIn("measures", f["evidence"])
            self.assertIn("quote", f["evidence"])

    def test_structural_clean_on_even_book(self):
        chapters = [_chapter(i, LIVELY) for i in range(1, 6)]
        r = _run("force.structural_enforcement", chapters)
        self.assertEqual(r["verdict"], "clean")
        self.assertEqual(r["findings"], [])

    def test_narrative_physics_consequence_gap(self):
        r = _run("force.narrative_physics", [_chapter(1, NO_CAUSE)])
        self.assertIn("consequence_gap", _issues(r))
        f = r["findings"][0]
        self.assertLess(
            f["evidence"]["measures"]["causal_markers_per_1000_words"],
            f["evidence"]["measures"]["floor"],
        )

    def test_narrative_physics_silent_with_causation(self):
        r = _run("force.narrative_physics", [_chapter(1, LIVELY)])
        self.assertEqual(r["findings"], [])

    def test_pressure_curve_flat(self):
        chapters = [_chapter(i, LIVELY) for i in range(1, 7)]  # identical tension
        r = _run("force.pressure_curve", chapters)
        issues = _issues(r)
        self.assertIn("flat_pressure_curve", issues)
        story = next(f for f in r["findings"] if f["issue_type"] == "flat_pressure_curve")
        self.assertEqual(story["unit"], "story")
        self.assertIsNone(story["chapter_id"])
        # Story-level detections still carry grounded, quoted evidence.
        self.assertTrue(story["evidence"]["quote"])
        self.assertIn("evidence_chapter", story["evidence"])
        self.assertIn("curve", r["evidence"]["summary"])

    def test_pressure_all_zero_book_is_flat_not_clean(self):
        """A book with ZERO tension signal everywhere is the flattest
        possible curve — it must never be reported clean."""
        chapters = [_chapter(i, STALLED) for i in range(1, 7)]
        r = _run("force.pressure_curve", chapters)
        flat = [f for f in r["findings"] if f["issue_type"] == "flat_pressure_curve"]
        self.assertTrue(flat)
        self.assertEqual(flat[0]["evidence"]["measures"]["mean_tension_per_1k"], 0.0)
        self.assertTrue(flat[0]["evidence"]["quote"])
        self.assertEqual(r["evidence"]["summary"]["cv"], 0.0)

    def test_pressure_sag_against_rolling_mean(self):
        chapters = [_chapter(i, LIVELY) for i in range(1, 5)]
        chapters.append(_chapter(5, STALLED))  # tension collapses mid-book
        chapters.append(_chapter(6, LIVELY))
        r = _run("force.pressure_curve", chapters)
        sags = [f for f in r["findings"] if f["issue_type"] == "pressure_sag"]
        self.assertTrue(sags)
        self.assertEqual(sags[0]["chapter_id"], "ch5")

    def test_conflict_escalation_absent_and_flat(self):
        chapters = [_chapter(1, LIVELY), _chapter(2, LIVELY)]
        chapters += [_chapter(i, STALLED) for i in range(3, 7)]  # conflict dies out
        r = _run("force.conflict_escalation", chapters)
        issues = _issues(r)
        self.assertIn("no_conflict_escalation", issues)
        self.assertIn("conflict_absent", issues)
        for f in r["findings"]:
            self.assertTrue(f["evidence"]["quote"], f["issue_type"])

    def test_conflict_all_zero_book_never_escalates(self):
        """Conflict that never appears never escalates — the all-zero book
        must emit the story-level finding, not pass silently."""
        chapters = [_chapter(i, STALLED) for i in range(1, 7)]
        r = _run("force.conflict_escalation", chapters)
        issues = _issues(r)
        self.assertIn("no_conflict_escalation", issues)
        story = next(f for f in r["findings"]
                     if f["issue_type"] == "no_conflict_escalation")
        self.assertEqual(story["evidence"]["measures"]["first_third_mean"], 0.0)
        self.assertEqual(story["evidence"]["measures"]["final_third_mean"], 0.0)
        self.assertTrue(story["evidence"]["quote"])

    def test_conflict_escalation_clean_when_rising(self):
        chapters = [_chapter(i, STALLED) for i in range(1, 3)]
        chapters += [_chapter(i, LIVELY) for i in range(3, 7)]
        r = _run("force.conflict_escalation", chapters)
        self.assertNotIn("no_conflict_escalation", _issues(r))

    def test_scene_purpose_unclear(self):
        chapters = [_chapter(1, LIVELY), _chapter(2, STALLED), _chapter(3, LIVELY)]
        r = _run("force.scene_purpose", chapters)
        flagged = [f for f in r["findings"] if f["issue_type"] == "purpose_unclear"]
        self.assertEqual([f["chapter_id"] for f in flagged], ["ch2"])
        m = flagged[0]["evidence"]["measures"]
        self.assertEqual(m["new_named_entities"], 0)
        self.assertEqual(m["conflict_hits"], 0)

    def test_momentum_stall_and_flatline(self):
        chapters = [_chapter(1, LIVELY)]
        chapters += [_chapter(i, STALLED) for i in range(2, 5)]  # 3 consecutive
        chapters.append(_chapter(5, LIVELY))
        r = _run("force.story_momentum", chapters)
        issues = _issues(r)
        self.assertIn("momentum_stall", issues)
        self.assertIn("momentum_flatline", issues)
        flat = next(f for f in r["findings"] if f["issue_type"] == "momentum_flatline")
        self.assertEqual(flat["unit"], "story")
        self.assertEqual(flat["severity"], "high")
        self.assertTrue(flat["evidence"]["quote"])

    def test_theme_dropout(self):
        themed = LIVELY + (" The covenant held them; the covenant was water and "
                           "the covenant was debt. ") * 6
        chapters = [_chapter(i, themed) for i in range(1, 4)]
        chapters += [_chapter(i, NO_CAUSE) for i in range(4, 8)]
        r = _run("force.theme_integrity", chapters)
        self.assertIn("covenant", r["evidence"]["summary"]["motifs"])
        drops = [f for f in r["findings"] if f["issue_type"] == "theme_dropout"]
        self.assertTrue(drops)
        for f in drops:
            self.assertEqual(f["evidence"]["measures"]["motifs_present"], 0)

    def test_theme_silent_when_no_motifs_derivable(self):
        chapters = [_chapter(i, STALLED) for i in range(1, 7)]
        r = _run("force.theme_integrity", chapters)
        # Either no motifs (note) or motifs that persist — never an invented
        # dropout without a derivable motif set.
        if not r["evidence"]["summary"]["motifs"]:
            self.assertEqual(r["findings"], [])

    def test_chapter_scoped_run_reports_only_that_chapter(self):
        chapters = [_chapter(1, LIVELY), _chapter(2, STALLED), _chapter(3, STALLED),
                    _chapter(4, STALLED), _chapter(5, LIVELY)]
        book = _run("force.story_momentum", chapters)
        self.assertTrue(any(f["chapter_id"] is None for f in book["findings"]))
        scoped = _run("force.story_momentum", chapters, chapter_id="ch3")
        self.assertTrue(scoped["findings"])
        for f in scoped["findings"]:
            self.assertEqual(f["chapter_id"], "ch3")
        # Story-level findings are book-run-only — never duplicated per chapter.
        self.assertFalse(any(f["chapter_id"] is None for f in scoped["findings"]))


# ── Registry + runner integration ────────────────────────────────────────────


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


class ForceRegistryBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.work_id = self.db.create_work("Ripple Book", work_type="writing")["id"]
        assay.seed_instruments(self.db)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()


class TestForceRegistry(ForceRegistryBase):
    def test_contracts_registered_tier2_shadow(self):
        for key in force.FORCE_KEYS:
            inst = self.db.get_assay_instrument(key)
            self.assertIsNotNone(inst, key)
            self.assertEqual(inst["tier"], 2)
            self.assertEqual(inst["certification"], "shadow")
            self.assertFalse(assay.is_blocking(inst))
            self.assertIn("authority override", inst["forbidden_ops"])
            # The shadow entry is ledgered with a system actor.
            events = self.db.list_assay_certification_events(inst["id"])
            self.assertTrue(events)
            self.assertEqual(events[0]["to_status"], "shadow")
            self.assertEqual(events[0]["actor"], "system")

    def test_reseed_does_not_reenter_shadow_after_author_demotion(self):
        key = force.FORCE_KEYS[0]
        self.db.set_assay_certification(key, "advisory", actor="author",
                                        note="deliberate demotion")
        assay.seed_instruments(self.db)
        inst = self.db.get_assay_instrument(key)
        self.assertEqual(inst["certification"], "advisory")

    def test_run_through_assay_is_shadow_labeled_and_persists_findings(self):
        for i in range(1, 4):
            _seed_chapter(self.db, self.work_id, i, f"Ch {i}", LIVELY)
        _seed_chapter(self.db, self.work_id, 4, "Ch 4", NO_CAUSE)
        run = assay.run_instrument(
            self.db, None, key="force.narrative_physics", work_id=self.work_id
        )
        self.assertEqual(run["status"], "done")
        auth = run["evidence"]["authority"]
        self.assertTrue(auth["shadow"])
        self.assertFalse(auth["blocking"])
        findings = self.db.list_assay_findings(run["id"])
        self.assertTrue(findings)
        for f in findings:
            self.assertEqual(f["force_check"], "force.narrative_physics")
            self.assertTrue(f["evidence"].get("shadow"))

    def test_chapter_scoped_run_via_runner(self):
        ids = {}
        for i in range(1, 4):
            ids[i] = _seed_chapter(self.db, self.work_id, i, f"Ch {i}", LIVELY)
        ids[4] = _seed_chapter(self.db, self.work_id, 4, "Ch 4", NO_CAUSE)
        run = assay.run_instrument(
            self.db, None, key="force.narrative_physics",
            work_id=self.work_id, chapter_id=ids[4],
        )
        findings = self.db.list_assay_findings(run["id"])
        self.assertTrue(findings)
        for f in findings:
            self.assertEqual(f["chapter_id"], ids[4])
        # Scoping to a clean chapter reports nothing, even though the book
        # contains a flagged one.
        run2 = assay.run_instrument(
            self.db, None, key="force.narrative_physics",
            work_id=self.work_id, chapter_id=ids[1],
        )
        self.assertEqual(self.db.list_assay_findings(run2["id"]), [])


if __name__ == "__main__":
    unittest.main()
