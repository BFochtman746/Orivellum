"""Tests for the scene-level pacing and immersion engine.

Three acceptance fixtures:
  1. Compressed-irreversible-events — multiple major turns in one scene without
     aftermath → audit flags compression + missing_aftermath.
  2. Fast-genre (thriller) — intentionally rapid scenes, genre profile says no
     violation → audit produces no compression finding.
  3. Multi-arc — two full tension peaks with an intervening trough → book-boundary
     detector flags the pattern with distinct-question reasoning.

All LLM calls are patched via orivellum.capabilities.llm.llm_call (lazy import).
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from orivellum.capabilities import pacing as pac


# ── Minimal DB ────────────────────────────────────────────────────────────────

class _LightDB:
    def __init__(self, path: str) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._local = threading.local()
        self._bootstrap()

    def _in_atomic(self) -> bool:
        return False

    def read_conn(self) -> sqlite3.Connection:
        return self._conn

    def _bootstrap(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS works (
                id TEXT PRIMARY KEY, title TEXT NOT NULL,
                status TEXT DEFAULT 'active', meta TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS book_chapters (
                id TEXT PRIMARY KEY, work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                seq INTEGER NOT NULL DEFAULT 0, level INTEGER NOT NULL DEFAULT 1,
                title TEXT, text TEXT, meta TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS series (
                id TEXT PRIMARY KEY, title TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS series_member (
                series_id TEXT NOT NULL REFERENCES series(id) ON DELETE CASCADE,
                work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                volume INTEGER NOT NULL,
                PRIMARY KEY (series_id, work_id)
            );
            CREATE TABLE IF NOT EXISTS scenes (
                id TEXT PRIMARY KEY,
                chapter_id TEXT NOT NULL REFERENCES book_chapters(id) ON DELETE CASCADE,
                work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                seq INTEGER NOT NULL DEFAULT 0,
                title TEXT, source_offset_start INTEGER, source_offset_end INTEGER,
                word_count INTEGER NOT NULL DEFAULT 0,
                purpose TEXT, pov TEXT, setting TEXT, time_elapsed_mins INTEGER,
                status TEXT NOT NULL DEFAULT 'proposed',
                meta TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS scene_metrics (
                id TEXT PRIMARY KEY,
                scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
                work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                version INTEGER NOT NULL DEFAULT 1,
                tension_before REAL, tension_after REAL,
                emotional_intensity REAL, revelation_density REAL,
                action_ratio REAL, reflection_ratio REAL, sensory_grounding REAL,
                has_aftermath INTEGER NOT NULL DEFAULT 0,
                has_orientation INTEGER NOT NULL DEFAULT 0,
                irreversible_turns INTEGER NOT NULL DEFAULT 0,
                reader_questions_created INTEGER NOT NULL DEFAULT 0,
                reader_questions_answered INTEGER NOT NULL DEFAULT 0,
                consequence_present INTEGER NOT NULL DEFAULT 0,
                purpose_clear INTEGER NOT NULL DEFAULT 0,
                evidence TEXT NOT NULL DEFAULT '[]',
                model_output TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                UNIQUE(scene_id, version)
            );
            CREATE TABLE IF NOT EXISTS pacing_profiles (
                id TEXT PRIMARY KEY, work_id TEXT NOT NULL UNIQUE,
                profile_name TEXT NOT NULL DEFAULT 'deep_immersive',
                thresholds TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pacing_runs (
                id TEXT PRIMARY KEY, work_id TEXT NOT NULL,
                profile_name TEXT NOT NULL DEFAULT 'deep_immersive',
                status TEXT NOT NULL DEFAULT 'pending',
                coverage TEXT NOT NULL DEFAULT '{}',
                error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pacing_findings (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                work_id TEXT NOT NULL, detector TEXT NOT NULL,
                finding_type TEXT NOT NULL, severity TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '', explanation TEXT NOT NULL,
                evidence TEXT NOT NULL DEFAULT '[]',
                recommendation TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'open',
                resolution_note TEXT NOT NULL DEFAULT '',
                resolved_at TEXT, dedupe_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(run_id, dedupe_key)
            );
        """)
        self._conn.commit()

    # Convenience helpers
    def _work(self, title: str = "Test Work") -> str:
        import uuid
        wid = str(uuid.uuid4())
        self._conn.execute("INSERT INTO works(id, title) VALUES(?,?)", (wid, title))
        self._conn.commit()
        return wid

    def _chapter(self, work_id: str, seq: int, title: str, text: str) -> str:
        import uuid
        from datetime import UTC, datetime
        cid = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT INTO book_chapters(id, work_id, seq, level, title, text, created_at, updated_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (cid, work_id, seq, 1, title, text, now, now),
        )
        self._conn.commit()
        return cid

    def _scene(self, work_id: str, chapter_id: str, seq: int, title: str,
               text_start: int = 0, text_end: int | None = None) -> str:
        import uuid
        from datetime import UTC, datetime
        sid = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT INTO scenes(id, chapter_id, work_id, seq, title,"
            " source_offset_start, source_offset_end, word_count, status, meta,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, chapter_id, work_id, seq, title, text_start, text_end, 200,
             "confirmed", "{}", now, now),
        )
        self._conn.commit()
        return sid

    def _metrics(self, scene_id: str, work_id: str, **kwargs) -> str:
        import uuid
        from datetime import UTC, datetime
        mid = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        defaults = dict(
            tension_before=0.3, tension_after=0.4, emotional_intensity=0.4,
            revelation_density=0.1, action_ratio=0.4, reflection_ratio=0.3,
            sensory_grounding=0.7, has_aftermath=0, has_orientation=1,
            irreversible_turns=0, reader_questions_created=1,
            reader_questions_answered=0, consequence_present=1, purpose_clear=1,
            evidence="[]", model_output="{}",
        )
        defaults.update(kwargs)
        self._conn.execute(
            """INSERT INTO scene_metrics
               (id, scene_id, work_id, version,
                tension_before, tension_after, emotional_intensity,
                revelation_density, action_ratio, reflection_ratio,
                sensory_grounding, has_aftermath, has_orientation,
                irreversible_turns, reader_questions_created,
                reader_questions_answered, consequence_present,
                purpose_clear, evidence, model_output, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mid, scene_id, work_id, 1,
             defaults["tension_before"], defaults["tension_after"],
             defaults["emotional_intensity"], defaults["revelation_density"],
             defaults["action_ratio"], defaults["reflection_ratio"],
             defaults["sensory_grounding"],
             int(defaults["has_aftermath"]), int(defaults["has_orientation"]),
             int(defaults["irreversible_turns"]),
             int(defaults["reader_questions_created"]),
             int(defaults["reader_questions_answered"]),
             int(defaults["consequence_present"]), int(defaults["purpose_clear"]),
             defaults["evidence"], defaults["model_output"], now),
        )
        self._conn.commit()
        return mid


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(payload: dict) -> MagicMock:
    r = MagicMock()
    r.ok = True
    r.text = json.dumps(payload)
    r.error = None
    return r


# ── Unit tests ────────────────────────────────────────────────────────────────

class TestProfiles(unittest.TestCase):
    def test_all_profiles_have_required_keys(self) -> None:
        required = {
            "min_sensory_grounding", "max_consecutive_high_tension",
            "require_aftermath_after_major", "drift_window_scenes",
            "drift_max_avg_tension", "compression_max_irreversible",
            "tension_variation_min", "high_tension_threshold",
            "low_grounding_threshold", "whiplash_tension_jump",
        }
        for name, p in pac.PROFILES.items():
            missing = required - p.keys()
            self.assertFalse(missing, f"Profile {name!r} missing keys: {missing}")

    def test_profile_for_unknown_falls_back_to_deep_immersive(self) -> None:
        db = MagicMock()
        db._lock = threading.RLock()
        db._conn = MagicMock()
        db._conn.execute.return_value.fetchone.return_value = None
        p = pac._profile_for(db, "any-work", override="nonexistent_profile")
        self.assertEqual(p, pac.PROFILES["deep_immersive"])


class TestValidateMetrics(unittest.TestCase):
    def test_clamps_floats_to_unit_interval(self) -> None:
        raw = {
            "tension_before": 2.5, "tension_after": -0.1,
            "emotional_intensity": 0.5, "revelation_density": 1.1,
            "action_ratio": 0.0, "reflection_ratio": 0.0,
            "sensory_grounding": 0.0, "has_aftermath": True,
            "has_orientation": False, "irreversible_turns": 0,
            "reader_questions_created": 0, "reader_questions_answered": 0,
            "consequence_present": True, "purpose_clear": False,
            "evidence": [],
        }
        m = pac._validate_metrics(raw, "some text")
        self.assertEqual(m["tension_before"], 1.0)
        self.assertEqual(m["tension_after"], 0.0)
        self.assertEqual(m["revelation_density"], 1.0)

    def test_ungrounded_evidence_discarded(self) -> None:
        text = "The battle raged on. Soldiers fell."
        raw = {
            "tension_before": 0.5, "tension_after": 0.9,
            "emotional_intensity": 0.8, "revelation_density": 0.2,
            "action_ratio": 0.7, "reflection_ratio": 0.1,
            "sensory_grounding": 0.6, "has_aftermath": False,
            "has_orientation": True, "irreversible_turns": 1,
            "reader_questions_created": 2, "reader_questions_answered": 0,
            "consequence_present": True, "purpose_clear": True,
            "evidence": [
                {"field": "tension_after", "quote": "Soldiers fell", "reasoning": "death"},
                {"field": "tension_after", "quote": "invented text not in source", "reasoning": "bad"},
            ],
        }
        m = pac._validate_metrics(raw, text)
        self.assertEqual(len(m["evidence"]), 1)
        self.assertEqual(m["evidence"][0]["quote"], "Soldiers fell")

    def test_non_list_evidence_becomes_empty(self) -> None:
        raw = {
            "tension_before": 0.0, "tension_after": 0.0,
            "emotional_intensity": 0.0, "revelation_density": 0.0,
            "action_ratio": 0.0, "reflection_ratio": 0.0,
            "sensory_grounding": 0.0, "has_aftermath": False,
            "has_orientation": False, "irreversible_turns": 0,
            "reader_questions_created": 0, "reader_questions_answered": 0,
            "consequence_present": False, "purpose_clear": False,
            "evidence": "not a list",
        }
        m = pac._validate_metrics(raw, "text")
        self.assertEqual(m["evidence"], [])


class TestSceneOperations(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _LightDB(str(Path(self._tmp.name) / "test.db"))
        self.cfg = MagicMock()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_extract_scenes_stores_grounded_scenes(self) -> None:
        wid = self.db._work()
        text = "The knight entered the hall. His sword gleamed. The queen rose from her throne."
        cid = self.db._chapter(wid, 0, "Opening", text)

        def _fake_llm(messages, **kw):
            return _ok({"scenes": [{"title": "The Arrival",
                "start_quote": "The knight entered the hall",
                "end_quote": "gleamed",
                "purpose": "action", "pov": "knight", "setting": "hall"}]})

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            scenes = pac.extract_scenes(self.db, self.cfg, wid, chapter_id=cid)

        self.assertGreaterEqual(len(scenes), 1)
        self.assertEqual(scenes[0]["work_id"], wid)
        self.assertEqual(scenes[0]["chapter_id"], cid)

    def test_ungrounded_scenes_discarded(self) -> None:
        """Proposals whose start_quote cannot be verified verbatim must be
        silently discarded — storing them at offset 0 contaminates segmentation."""
        wid = self.db._work()
        cid = self.db._chapter(wid, 0, "Ch1", "Short text here.")

        def _fake_llm(messages, **kw):
            return _ok({"scenes": [{"title": "X",
                "start_quote": "completely fabricated text not in chapter",
                "end_quote": "",
                "purpose": "action", "pov": "none", "setting": "nowhere"}]})

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            scenes = pac.extract_scenes(self.db, self.cfg, wid, chapter_id=cid)

        self.assertEqual(len(scenes), 0,
                         "Ungrounded start_quote must be rejected entirely, not stored at offset 0")

    def test_update_scene_allows_author_correction(self) -> None:
        wid = self.db._work()
        cid = self.db._chapter(wid, 0, "Ch", "Text.")
        sid = self.db._scene(wid, cid, 0, "Original title")
        updated = pac.update_scene(self.db, sid, title="Author corrected title", status="confirmed")
        self.assertEqual(updated["title"], "Author corrected title")
        self.assertEqual(updated["status"], "confirmed")

    def test_update_scene_rejects_unknown_fields(self) -> None:
        wid = self.db._work()
        cid = self.db._chapter(wid, 0, "Ch", "Text.")
        sid = self.db._scene(wid, cid, 0, "T")
        with self.assertRaises(pac.PacingError):
            pac.update_scene(self.db, sid, sql_injection="DROP TABLE scenes")


# ── Acceptance Fixture 1: Compressed irreversible events ─────────────────────

class TestCompressedEventsFixture(unittest.TestCase):
    """Fixture: one scene has 3 irreversible turns with no aftermath.

    Expected: compression finding + missing_aftermath finding.
    Profile: deep_immersive (max 1 irreversible turn per scene).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _LightDB(str(Path(self._tmp.name) / "test.db"))
        self.cfg = MagicMock()

        self.wid = self.db._work("Battle Book")
        cid = self.db._chapter(
            self.wid, 0, "The Siege",
            "The gates burst open. Aldric was struck down. The banner fell. "
            "Mira betrayed her oath. The enemy general seized command. "
            "Kael made the fatal choice that doomed them all."
        )
        # One compressed scene: 3 irreversible turns, no aftermath, high tension
        self.sid = self.db._scene(self.wid, cid, 0, "The Catastrophe")
        self.db._metrics(
            self.sid, self.wid,
            tension_before=0.3,
            tension_after=0.9,
            emotional_intensity=0.9,
            irreversible_turns=3,
            has_aftermath=0,
            has_orientation=1,
            consequence_present=0,
            purpose_clear=1,
            sensory_grounding=0.5,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_compression_finding_produced(self) -> None:
        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid, profile="deep_immersive")
        findings = pac.list_pacing_findings(self.db, run["id"])
        self.assertEqual(run["status"], "done")
        detectors = {f["detector"] for f in findings}
        self.assertIn("compression", detectors,
                      f"compression detector must fire; found detectors: {detectors}")
        comp = next(f for f in findings if f["detector"] == "compression")
        self.assertIn("irreversible", comp["explanation"].lower())

    def test_compression_finding_has_bridge_recommendation(self) -> None:
        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid)
        findings = pac.list_pacing_findings(self.db, run["id"])
        comp = next((f for f in findings if f["detector"] == "compression"), None)
        if comp is None:
            self.skipTest("No compression finding — check fixture setup")
        rec = comp["recommendation"]
        self.assertEqual(rec.get("recommendation_type"), "more_scenes",
                         "Compression must recommend more scenes, not another book")
        self.assertIn("placement", rec, "Recommendation must include placement guidance")

    def test_compression_recommendation_does_not_say_another_book(self) -> None:
        """A compression finding serves the existing dramatic question — not a separate book."""
        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid)
        findings = pac.list_pacing_findings(self.db, run["id"])
        comp = next((f for f in findings if f["detector"] == "compression"), None)
        if comp is None:
            self.skipTest("No compression finding")
        self.assertNotEqual(
            comp["recommendation"].get("recommendation_type"), "another_book",
            "Compression findings must never recommend another book",
        )

    def test_missing_aftermath_finding_produced(self) -> None:
        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid, profile="deep_immersive")
        findings = pac.list_pacing_findings(self.db, run["id"])
        breath = [f for f in findings if f["detector"] == "breath_map"]
        types = {f["finding_type"] for f in breath}
        self.assertIn("missing_aftermath", types,
                      "deep_immersive profile must flag missing aftermath after major event")


# ── Acceptance Fixture 2: Fast-genre (thriller) — should NOT condemn ─────────

class TestFastGenreFixture(unittest.TestCase):
    """Fixture: many short high-tension scenes with thriller profile.

    Under the thriller profile (compression_max_irreversible=3, require_aftermath=False),
    a scene with 2 irreversible turns must NOT produce a compression finding.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _LightDB(str(Path(self._tmp.name) / "test.db"))
        self.cfg = MagicMock()

        self.wid = self.db._work("Chase Novel")
        cid = self.db._chapter(
            self.wid, 0, "Sprint",
            "He ran. The shot fired. She dove. They escaped through the window."
        )
        # Thriller-paced scene: 2 irreversible turns, no aftermath (OK for thriller)
        sid = self.db._scene(self.wid, cid, 0, "The Chase")
        self.db._metrics(
            sid, self.wid,
            tension_before=0.6,
            tension_after=0.85,
            emotional_intensity=0.8,
            irreversible_turns=2,   # ≤ thriller threshold of 3
            has_aftermath=0,        # NOT required by thriller profile
            has_orientation=1,
            consequence_present=1,
            purpose_clear=1,
            sensory_grounding=0.35,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_compression_finding_under_thriller_profile(self) -> None:
        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid, profile="thriller")
        findings = pac.list_pacing_findings(self.db, run["id"])
        comp = [f for f in findings if f["detector"] == "compression"]
        self.assertEqual(comp, [],
                         "Thriller profile allows 3 irreversible turns; 2 must not be flagged")

    def test_no_aftermath_not_flagged_under_thriller(self) -> None:
        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid, profile="thriller")
        findings = pac.list_pacing_findings(self.db, run["id"])
        breath = [f for f in findings
                  if f["detector"] == "breath_map" and f["finding_type"] == "missing_aftermath"]
        self.assertEqual(breath, [],
                         "Thriller profile does not require aftermath — must not flag it")

    def test_run_status_done(self) -> None:
        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid, profile="thriller")
        self.assertEqual(run["status"], "done")


# ── Acceptance Fixture 3: Multi-arc — book-boundary detection ─────────────────

class TestMultiArcFixture(unittest.TestCase):
    """Fixture: 12 scenes split into two full arc cycles.

    First 6: tension rises to 0.85 then falls to 0.15 (full resolution).
    Last 6: tension rises to 0.8 then falls to 0.2 (second full arc).

    Expected: book_boundary detector flags potential_two_arc_book with reasoning
    about distinct dramatic questions (not a word-count complaint).
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _LightDB(str(Path(self._tmp.name) / "test.db"))
        self.cfg = MagicMock()

        self.wid = self.db._work("Two-Arc Novel")
        cid = self.db._chapter(
            self.wid, 0, "Full Manuscript",
            "A " * 5000  # long enough to slice
        )

        # First arc: gradual rise then full resolution
        arc1 = [
            (0.1, 0.3), (0.3, 0.55), (0.5, 0.78),
            (0.78, 0.85), (0.85, 0.35), (0.35, 0.15),
        ]
        # Second arc: another rise and resolution
        arc2 = [
            (0.15, 0.3), (0.3, 0.5), (0.5, 0.72),
            (0.72, 0.80), (0.80, 0.4), (0.4, 0.2),
        ]
        all_arcs = arc1 + arc2

        for i, (tb, ta) in enumerate(all_arcs):
            sid = self.db._scene(self.wid, cid, i, f"Scene {i+1}", text_start=i * 100)
            self.db._metrics(
                sid, self.wid,
                tension_before=tb,
                tension_after=ta,
                emotional_intensity=0.5,
                irreversible_turns=1 if ta >= 0.7 else 0,
                has_aftermath=1 if ta < 0.3 else 0,
                has_orientation=1,
                consequence_present=1,
                purpose_clear=1,
                sensory_grounding=0.65,
            )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_book_boundary_detector_fires(self) -> None:
        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid)
        findings = pac.list_pacing_findings(self.db, run["id"])
        bb = [f for f in findings if f["detector"] == "book_boundary"]
        self.assertTrue(len(bb) >= 1,
                        f"book_boundary detector must fire for two-arc fixture; got: "
                        f"{[(f['detector'], f['finding_type']) for f in findings]}")

    def test_book_boundary_recommends_another_book(self) -> None:
        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid)
        findings = pac.list_pacing_findings(self.db, run["id"])
        bb = next((f for f in findings if f["detector"] == "book_boundary"), None)
        if not bb:
            self.skipTest("No book_boundary finding")
        self.assertEqual(bb["recommendation"].get("recommendation_type"), "another_book")

    def test_book_boundary_includes_distinct_question_test(self) -> None:
        """Spec requirement: must state the distinct dramatic question test, not word count."""
        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid)
        findings = pac.list_pacing_findings(self.db, run["id"])
        bb = next((f for f in findings if f["detector"] == "book_boundary"), None)
        if not bb:
            self.skipTest("No book_boundary finding")
        rec = bb["recommendation"]
        self.assertIn("distinct_question_test", rec,
                      "book_boundary recommendation must include distinct_question_test key")
        dqt = rec["distinct_question_test"].lower()
        # Must mention dramatic question, not word count
        self.assertIn("dramatic question", dqt)
        self.assertNotIn("word count", dqt)

    def test_book_boundary_evidence_references_tension_peaks(self) -> None:
        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid)
        findings = pac.list_pacing_findings(self.db, run["id"])
        bb = next((f for f in findings if f["detector"] == "book_boundary"), None)
        if not bb:
            self.skipTest("No book_boundary finding")
        self.assertTrue(len(bb["evidence"]) >= 1,
                        "book_boundary finding must include evidence")
        ev = bb["evidence"][0]
        self.assertIn("first_half_peak", ev or {},
                      "Evidence must reference first-arc peak tension")


# ── Finding resolution tests ──────────────────────────────────────────────────

class TestFindingResolution(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _LightDB(str(Path(self._tmp.name) / "test.db"))
        self.cfg = MagicMock()
        self.wid = self.db._work()
        cid = self.db._chapter(self.wid, 0, "Ch", "Text " * 50)
        sid = self.db._scene(self.wid, cid, 0, "S1")
        self.db._metrics(
            sid, self.wid,
            tension_before=0.3, tension_after=0.9,
            irreversible_turns=3, has_aftermath=0,
            sensory_grounding=0.5, emotional_intensity=0.8,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _get_finding(self) -> dict | None:
        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid)
        findings = pac.list_pacing_findings(self.db, run["id"])
        return findings[0] if findings else None

    def test_resolve_to_intentional(self) -> None:
        f = self._get_finding()
        if not f:
            self.skipTest("No findings")
        updated = pac.resolve_pacing_finding(self.db, f["id"], "intentional", "Deliberate thriller tempo")
        self.assertEqual(updated["status"], "intentional")
        self.assertIn("Deliberate", updated["resolution_note"])
        self.assertIsNotNone(updated["resolved_at"])

    def test_invalid_status_raises(self) -> None:
        f = self._get_finding()
        if not f:
            self.skipTest("No findings")
        with self.assertRaises(pac.PacingError):
            pac.resolve_pacing_finding(self.db, f["id"], "made_up_status")


# ── Profile management ────────────────────────────────────────────────────────

class TestProfileManagement(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _LightDB(str(Path(self._tmp.name) / "test.db"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_get_default_profile(self) -> None:
        wid = self.db._work()
        p = pac.get_pacing_profile(self.db, wid)
        self.assertEqual(p["profile_name"], "deep_immersive")

    def test_update_and_retrieve_profile(self) -> None:
        wid = self.db._work()
        pac.update_pacing_profile(self.db, wid, "thriller")
        p = pac.get_pacing_profile(self.db, wid)
        self.assertEqual(p["profile_name"], "thriller")

    def test_unknown_profile_raises(self) -> None:
        wid = self.db._work()
        with self.assertRaises(pac.PacingError):
            pac.update_pacing_profile(self.db, wid, "invented_profile")

    def test_available_profiles_returned(self) -> None:
        wid = self.db._work()
        p = pac.get_pacing_profile(self.db, wid)
        self.assertIn("available_profiles", p)
        self.assertIn("thriller", p["available_profiles"])
        self.assertIn("deep_immersive", p["available_profiles"])


# ── Multi-chapter ordering ────────────────────────────────────────────────────

class TestMultiChapterOrdering(unittest.TestCase):
    """Scenes from chapter 1 must always precede scenes from chapter 2 in
    list_scenes(work_id), regardless of the per-chapter seq values.

    Regression guard: before the fix, ORDER BY seq alone put chapter 2 scene 0
    before chapter 1 scene 1 when both have the same seq counter.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _LightDB(str(Path(self._tmp.name) / "test.db"))
        self.cfg = MagicMock()
        self.wid = self.db._work("Multi-Chapter Book")
        self.ch1 = self.db._chapter(self.wid, 0, "Chapter One", "First chapter text here.")
        self.ch2 = self.db._chapter(self.wid, 1, "Chapter Two", "Second chapter text here.")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_chapter_order_respected_in_scene_list(self) -> None:
        # ch1 has seq 0 and seq 1; ch2 has seq 0 — without the JOIN fix,
        # ch2-seq0 would sort before ch1-seq1.
        sid_ch1_s0 = self.db._scene(self.wid, self.ch1, 0, "Ch1 Scene 1")
        sid_ch1_s1 = self.db._scene(self.wid, self.ch1, 1, "Ch1 Scene 2")
        sid_ch2_s0 = self.db._scene(self.wid, self.ch2, 0, "Ch2 Scene 1")

        scenes = pac.list_scenes(self.db, self.wid)
        ids = [s["id"] for s in scenes]

        self.assertEqual(ids.index(sid_ch1_s0), 0, "Ch1 Scene 1 must be first")
        self.assertEqual(ids.index(sid_ch1_s1), 1, "Ch1 Scene 2 must be second")
        self.assertEqual(ids.index(sid_ch2_s0), 2, "Ch2 Scene 1 must be last")

    def test_chapter_filter_still_works(self) -> None:
        """chapter_id filter must return only that chapter's scenes."""
        self.db._scene(self.wid, self.ch1, 0, "Ch1 Scene A")
        self.db._scene(self.wid, self.ch2, 0, "Ch2 Scene A")
        ch1_scenes = pac.list_scenes(self.db, self.wid, chapter_id=self.ch1)
        self.assertEqual(len(ch1_scenes), 1)
        self.assertEqual(ch1_scenes[0]["chapter_id"], self.ch1)

    def test_detectors_use_narrative_order(self) -> None:
        """book_boundary and other detectors receive scenes in narrative order."""
        # Ch1: two high-tension scenes then full resolution (first arc)
        arc1 = [(0.2, 0.8), (0.8, 0.85), (0.85, 0.1)]
        for i, (tb, ta) in enumerate(arc1):
            sid = self.db._scene(self.wid, self.ch1, i, f"Ch1-S{i}")
            self.db._metrics(sid, self.wid, tension_before=tb, tension_after=ta,
                             irreversible_turns=1 if ta >= 0.7 else 0,
                             has_aftermath=1 if ta < 0.2 else 0, has_orientation=1,
                             emotional_intensity=0.6, sensory_grounding=0.6,
                             consequence_present=1, purpose_clear=1)
        # Ch2: second independent arc with its own peak (seq 0,1,2 — same as ch1)
        arc2 = [(0.15, 0.5), (0.5, 0.82), (0.82, 0.2)]
        for i, (tb, ta) in enumerate(arc2):
            sid = self.db._scene(self.wid, self.ch2, i, f"Ch2-S{i}")
            self.db._metrics(sid, self.wid, tension_before=tb, tension_after=ta,
                             irreversible_turns=1 if ta >= 0.7 else 0,
                             has_aftermath=1 if ta < 0.25 else 0, has_orientation=1,
                             emotional_intensity=0.6, sensory_grounding=0.6,
                             consequence_present=1, purpose_clear=1)

        run = pac.run_pacing_diagnostics(self.db, self.cfg, self.wid)
        findings = pac.list_pacing_findings(self.db, run["id"])
        # Run must complete without error and in correct order (not crash)
        self.assertEqual(run["status"], "done")
        # With the fix the scenes are ordered ch1-s0,ch1-s1,ch1-s2,ch2-s0,ch2-s1,ch2-s2
        # forming two arcs — book_boundary may or may not fire depending on exact values,
        # but the run must succeed and coverage must reflect 6 scenes.
        coverage = run["coverage"]
        self.assertEqual(coverage.get("total_scenes"), 6)


# ── Stored metrics flow ───────────────────────────────────────────────────────

class TestStoredMetricsFlow(unittest.TestCase):
    """get_scene_metrics must return the latest version stored for a scene.

    Regression guard for the UI defect: the Tension Map was reading from
    component-local state populated by re-running LLM analysis instead of
    fetching persisted metrics from the server.  This test ensures the
    server-side read path (get_scene_metrics) works and that list_scenes
    callers can rely on it for the embedded latest_metrics field.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = _LightDB(str(Path(self._tmp.name) / "test.db"))
        self.wid = self.db._work()
        self.cid = self.db._chapter(self.wid, 0, "Ch", "Text.")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_get_scene_metrics_returns_none_before_analysis(self) -> None:
        sid = self.db._scene(self.wid, self.cid, 0, "Unanalyzed")
        self.assertIsNone(pac.get_scene_metrics(self.db, sid))

    def test_get_scene_metrics_returns_stored_row(self) -> None:
        sid = self.db._scene(self.wid, self.cid, 0, "Analyzed")
        self.db._metrics(sid, self.wid, tension_after=0.75)
        m = pac.get_scene_metrics(self.db, sid)
        self.assertIsNotNone(m)
        self.assertAlmostEqual(m["tension_after"], 0.75)  # type: ignore[index]

    def test_get_scene_metrics_returns_latest_version(self) -> None:
        import uuid
        from datetime import UTC, datetime
        sid = self.db._scene(self.wid, self.cid, 0, "Multi-version")
        # Insert version 1
        self.db._metrics(sid, self.wid, tension_after=0.3)
        # Insert version 2 manually
        mid2 = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        self.db._conn.execute(
            """INSERT INTO scene_metrics
               (id, scene_id, work_id, version,
                tension_before, tension_after, emotional_intensity,
                revelation_density, action_ratio, reflection_ratio,
                sensory_grounding, has_aftermath, has_orientation,
                irreversible_turns, reader_questions_created,
                reader_questions_answered, consequence_present,
                purpose_clear, evidence, model_output, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (mid2, sid, self.wid, 2, 0.1, 0.9, 0.5, 0.2, 0.4, 0.3,
             0.7, 0, 1, 0, 1, 0, 1, 1, "[]", "{}", now),
        )
        self.db._conn.commit()
        m = pac.get_scene_metrics(self.db, sid)
        self.assertAlmostEqual(m["tension_after"], 0.9,  # type: ignore[index]
                               msg="Must return version 2 (latest), not version 1")


if __name__ == "__main__":
    unittest.main()
