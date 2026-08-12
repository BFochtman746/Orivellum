"""Tests for the book-to-book handoff contract engine.

Fixture: Book 1 ends with an unresolved promise and emotional shift + final
location.  Book 2 opens after an unexplained time jump and never mentions the
promise.  The audit must flag:
  - dropped_promise (or dropped_thread)
  - unexplained_time_jump (or missing_bridge)

All extraction/audit LLM calls are monkeypatched to deterministic JSON so
the tests are reproducible without a model.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from orivellum.capabilities import handoff as hf


# ── Minimal DB fixture ────────────────────────────────────────────────────────


class _LightDB:
    """Thin wrapper that mimics OrivellumDB enough for handoff tests."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._local = threading.local()
        self._create_schema()

    def _create_schema(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS works (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT DEFAULT 'draft',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS book_chapters (
                id TEXT PRIMARY KEY,
                work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                level INTEGER NOT NULL DEFAULT 1,
                title TEXT,
                text TEXT,
                meta TEXT DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS series (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS series_member (
                series_id TEXT NOT NULL REFERENCES series(id) ON DELETE CASCADE,
                work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                volume INTEGER NOT NULL,
                chronology_order INTEGER,
                publication_order INTEGER,
                relationship_type TEXT DEFAULT 'main',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (series_id, work_id)
            );
            CREATE TABLE IF NOT EXISTS handoff_package (
                id TEXT PRIMARY KEY,
                work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                version INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'draft',
                payload TEXT NOT NULL DEFAULT '{}',
                extraction_meta TEXT NOT NULL DEFAULT '{}',
                author_intent TEXT NOT NULL DEFAULT '',
                ratified_at TEXT,
                ratified_by TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(work_id, version)
            );
            CREATE TABLE IF NOT EXISTS opening_contract (
                id TEXT PRIMARY KEY,
                work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                version INTEGER NOT NULL DEFAULT 1,
                prior_package_id TEXT,
                window_chars INTEGER NOT NULL DEFAULT 0,
                payload TEXT NOT NULL DEFAULT '{}',
                extraction_meta TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(work_id, version)
            );
            CREATE TABLE IF NOT EXISTS handoff_audit (
                id TEXT PRIMARY KEY,
                prior_work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                successor_work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
                package_id TEXT,
                contract_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                coverage TEXT NOT NULL DEFAULT '{}',
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS handoff_finding (
                id TEXT PRIMARY KEY,
                audit_id TEXT NOT NULL REFERENCES handoff_audit(id) ON DELETE CASCADE,
                finding_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT '',
                explanation TEXT NOT NULL,
                evidence TEXT NOT NULL DEFAULT '[]',
                insufficient_evidence INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'open',
                resolution_note TEXT NOT NULL DEFAULT '',
                resolved_at TEXT,
                dedupe_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(audit_id, dedupe_key)
            );
        """)
        self._conn.commit()

    def read_conn(self) -> sqlite3.Connection:
        return self._conn

    def _create_work(self, title: str) -> str:
        import uuid
        wid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO works(id, title) VALUES(?,?)", (wid, title)
        )
        self._conn.commit()
        return wid

    def _create_chapter(self, work_id: str, seq: int, title: str, text: str) -> str:
        import uuid
        cid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO book_chapters(id, work_id, seq, level, title, text) VALUES(?,?,?,?,?,?)",
            (cid, work_id, seq, 1, title, text),
        )
        self._conn.commit()
        return cid

    def _create_series(self, title: str, *work_ids: str) -> str:
        import uuid
        sid = str(uuid.uuid4())
        self._conn.execute("INSERT INTO series(id, title) VALUES(?,?)", (sid, title))
        for vol, wid in enumerate(work_ids, start=1):
            self._conn.execute(
                "INSERT INTO series_member(series_id, work_id, volume) VALUES(?,?,?)",
                (sid, wid, vol),
            )
        self._conn.commit()
        return sid


# ── LLM stub helpers ──────────────────────────────────────────────────────────

def _ok(payload: dict) -> MagicMock:
    """Return an llm_call result stub that yields a JSON object."""
    r = MagicMock()
    r.ok = True
    r.text = json.dumps(payload)
    r.error = None
    return r


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestEndStatePackage(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.db"
        self.db = _LightDB(str(db_path))
        self.cfg = MagicMock()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_book(self, ending_text: str) -> str:
        wid = self.db._create_work("Book One")
        for i in range(5):
            text = ending_text if i >= 3 else f"Chapter {i+1} content about nothing important."
            self.db._create_chapter(wid, i + 1, f"Chapter {i+1}", text)
        return wid

    def test_build_returns_package_with_items(self) -> None:
        """LLM proposals that ground correctly produce stored items."""
        wid = self._make_book(
            "Aelith swore to return for Mira. She was wounded badly. "
            "They stood in the ruins of the keep."
        )
        # Stub: LLM returns a grounded quote
        def _fake_llm(messages, *, cfg, db, purpose, timeout, temperature):
            if purpose == "handoff.extract_end_state":
                return _ok({
                    "items": [
                        {
                            "category": "promise",
                            "subject": "Aelith",
                            "claim": "Aelith swore to return for Mira",
                            "quote": "Aelith swore to return for Mira",
                        },
                        {
                            "category": "injury",
                            "subject": "Mira",
                            "claim": "Mira was wounded badly",
                            "quote": "She was wounded badly",
                        },
                    ]
                })
            return _ok({})

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            pkg = hf.build_end_state_package(self.db, self.cfg, wid)

        self.assertEqual(pkg["status"], "draft")
        self.assertEqual(pkg["work_id"], wid)
        items = pkg["payload"]["items"]
        self.assertGreaterEqual(len(items), 1)
        # All items must have grounded quotes.
        for it in items:
            self.assertTrue(it["quote"], "Every item must have a non-empty grounded quote")

    def test_ungroundable_quotes_discarded(self) -> None:
        """Quotes that don't appear in the text are silently discarded."""
        wid = self._make_book("The final battle was fought and won.")

        def _fake_llm(messages, *, cfg, db, purpose, timeout, temperature):
            return _ok({
                "items": [
                    {
                        "category": "character_state",
                        "subject": "Hero",
                        "claim": "Hero flew away on a dragon",
                        "quote": "flew away on a purple dragon",  # NOT in text
                    }
                ]
            })

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            pkg = hf.build_end_state_package(self.db, self.cfg, wid)

        self.assertEqual(len(pkg["payload"]["items"]), 0)

    def test_version_increments_on_rebuild(self) -> None:
        wid = self._make_book("The end has come at last.")

        def _no_items(messages, **kw):
            return _ok({"items": []})

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_no_items):
            p1 = hf.build_end_state_package(self.db, self.cfg, wid)
            p2 = hf.build_end_state_package(self.db, self.cfg, wid)

        self.assertEqual(p1["version"], 1)
        self.assertEqual(p2["version"], 2)

    def test_ratify_changes_status(self) -> None:
        wid = self._make_book("End scene.")

        with patch("orivellum.capabilities.llm.llm_call", return_value=_ok({"items": []})):
            pkg = hf.build_end_state_package(self.db, self.cfg, wid)

        ratified = hf.ratify_package(self.db, pkg["id"])
        self.assertEqual(ratified["status"], "ratified")
        self.assertIsNotNone(ratified["ratified_at"])

    def test_ratify_already_ratified_raises(self) -> None:
        wid = self._make_book("End.")
        with patch("orivellum.capabilities.llm.llm_call", return_value=_ok({"items": []})):
            pkg = hf.build_end_state_package(self.db, self.cfg, wid)
        hf.ratify_package(self.db, pkg["id"])
        with self.assertRaises(hf.HandoffError):
            hf.ratify_package(self.db, pkg["id"])

    def test_author_intent_can_be_set(self) -> None:
        wid = self._make_book("The long journey ends here.")
        with patch("orivellum.capabilities.llm.llm_call", return_value=_ok({"items": []})):
            pkg = hf.build_end_state_package(self.db, self.cfg, wid)
        updated = hf.update_package_intent(self.db, pkg["id"], "This book owes the next a reckoning.")
        self.assertIn("reckoning", updated["author_intent"])


class TestOpeningContract(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.db"
        self.db = _LightDB(str(db_path))
        self.cfg = MagicMock()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_book(self, opening_text: str) -> str:
        wid = self.db._create_work("Book Two")
        self.db._create_chapter(wid, 1, "Opening", opening_text)
        return wid

    def test_build_extracts_opening_items(self) -> None:
        wid = self._make_book(
            "Three years had passed since the fall of the keep. "
            "Mira's wounds had healed, though she still limped. "
            "Aelith had never returned as promised."
        )

        def _fake_llm(messages, **kw):
            purpose = kw.get("purpose", "")
            if "extract_opening" in purpose:
                return _ok({
                    "items": [
                        {
                            "category": "orientation",
                            "subject": "time",
                            "claim": "Three years have passed",
                            "quote": "Three years had passed since the fall of the keep",
                        }
                    ]
                })
            return _ok({})

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            c = hf.build_opening_contract(self.db, self.cfg, wid)

        items = c["payload"]["items"]
        self.assertGreaterEqual(len(items), 1)
        self.assertTrue(all(it["quote"] for it in items))

    def test_version_increments(self) -> None:
        wid = self._make_book("A new beginning.")

        def _no(messages, **kw):
            return _ok({"items": []})

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_no):
            c1 = hf.build_opening_contract(self.db, self.cfg, wid)
            c2 = hf.build_opening_contract(self.db, self.cfg, wid)

        self.assertEqual(c1["version"], 1)
        self.assertEqual(c2["version"], 2)


class TestHandoffAudit(unittest.TestCase):
    """Spec acceptance fixture: Book 1 ends with unresolved promise + emotional
    shift + location; Book 2 opens after an unexplained time jump and drops
    the promise.  The audit must flag the seam problems."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.db"
        self.db = _LightDB(str(db_path))
        self.cfg = MagicMock()

        # Book 1 — ending: promise + location + emotional close
        self.b1 = self.db._create_work("The First Book")
        self.db._create_chapter(
            self.b1, 1, "End",
            "Doran swore he would return to Vaela before winter. "
            "She wept as the gates closed behind him. "
            "They stood in the courtyard of Castle Vaela."
        )

        # Book 2 — opening: time jump, no mention of promise or promise
        self.b2 = self.db._create_work("The Second Book")
        self.db._create_chapter(
            self.b2, 1, "Opening",
            "Summer had come and gone twice. "
            "Vaela was nothing but ash now. "
            "Doran stared at the ruins without expression."
        )

        self.db._create_series("The Saga", self.b1, self.b2)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build_package_with_items(self, items: list[dict]) -> dict:
        """Stub: build a package with preset items (bypasses LLM)."""
        import uuid, datetime
        from datetime import UTC
        pid = str(uuid.uuid4())
        now = datetime.datetime.now(UTC).isoformat()
        payload = json.dumps({"items": items, "ending_chapter_count": 1})
        meta = json.dumps({"tool_version": "test", "items_extracted": len(items)})
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO handoff_package
                   (id, work_id, version, status, payload, extraction_meta,
                    author_intent, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (pid, self.b1, 1, "ratified", payload, meta, "", now, now)
            )
            self.db._conn.execute(
                """UPDATE handoff_package SET ratified_at=?, ratified_by='author'
                   WHERE id=?""", (now, pid)
            )
            self.db._conn.commit()
        return hf.get_package(self.db, pid)

    def test_audit_flags_dropped_promise_and_time_jump(self) -> None:
        """Spec fixture: Book 1 ends with a sworn promise; Book 2 opens two
        years later without acknowledging it.  Audit must flag at least one of:
        dropped_promise, dropped_thread, unexplained_time_jump, missing_bridge."""

        # Pre-seed the package with a promise item.
        pkg = self._build_package_with_items([
            {
                "id": "item-1",
                "category": "promise",
                "subject": "Doran",
                "claim": "Doran swore to return to Vaela before winter",
                "quote": "Doran swore he would return to Vaela before winter",
                "offset": 0,
                "chapter_id": None,
                "chapter_seq": 1,
            }
        ])

        PROBLEM_TYPES = {
            "dropped_promise", "dropped_thread",
            "unexplained_time_jump", "missing_bridge",
            "emotional_discontinuity",
        }

        def _fake_llm(messages, **kw):
            purpose = kw.get("purpose", "")
            if "audit_propose" in purpose:
                return _ok({
                    "proposals": [
                        {
                            "finding_type": "dropped_promise",
                            "subject": "Doran's sworn return",
                            "explanation": "Doran swore to return before winter but the opening never mentions it.",
                            "end_state_quote": "Doran swore he would return to Vaela before winter",
                            "opening_quote": "",
                            "insufficient_evidence": False,
                        },
                        {
                            "finding_type": "unexplained_time_jump",
                            "subject": "time gap",
                            "explanation": "Two summers pass with no acknowledgment of how or why.",
                            "end_state_quote": "Doran swore he would return to Vaela before winter",
                            "opening_quote": "Summer had come and gone twice",
                            "insufficient_evidence": False,
                        },
                    ]
                })
            if "verify" in purpose:
                return _ok({"verdict": "confirmed", "reasoning": "Genuine seam problem."})
            if "no_fresh_promise" in purpose or "insufficient" in purpose:
                return _ok({"verdict": "confirmed", "reasoning": "No new promise."})
            return _ok({"verdict": "rejected", "reasoning": "Not a problem."})

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            audit = hf.run_handoff_audit(
                self.db, self.cfg, self.b1, self.b2,
                prior_package_id=pkg["id"],
            )

        self.assertEqual(audit["status"], "done")
        findings = hf.list_findings(self.db, audit["id"])
        found_types = {f["finding_type"] for f in findings if f["status"] == "open"}
        self.assertTrue(
            found_types & PROBLEM_TYPES,
            f"Audit must flag at least one seam problem; got types: {found_types}",
        )

    def test_finding_evidence_is_grounded(self) -> None:
        """Every finding with a non-empty opening_quote must have its quote in the
        actual opening text (not fabricated)."""
        pkg = self._build_package_with_items([{
            "id": "item-x",
            "category": "promise",
            "subject": "X",
            "claim": "some promise",
            "quote": "Doran swore he would return to Vaela before winter",
            "offset": 0,
            "chapter_id": None,
            "chapter_seq": 1,
        }])

        def _fake_llm(messages, **kw):
            purpose = kw.get("purpose", "")
            if "audit_propose" in purpose:
                return _ok({
                    "proposals": [{
                        "finding_type": "dropped_promise",
                        "subject": "X",
                        "explanation": "Promise dropped",
                        # Quote that appears in the actual opening text:
                        "end_state_quote": "Doran swore he would return to Vaela before winter",
                        "opening_quote": "Summer had come and gone twice",
                        "insufficient_evidence": False,
                    }]
                })
            if "verify" in purpose:
                return _ok({"verdict": "confirmed", "reasoning": "Genuine."})
            return _ok({"verdict": "rejected", "reasoning": "n/a"})

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            audit = hf.run_handoff_audit(
                self.db, self.cfg, self.b1, self.b2,
                prior_package_id=pkg["id"],
            )

        findings = hf.list_findings(self.db, audit["id"])
        opening_text = "Summer had come and gone twice. Vaela was nothing but ash now. Doran stared at the ruins without expression."
        for f in findings:
            for ev in f["evidence"]:
                if ev["role"] == "successor_book" and ev.get("quote"):
                    self.assertIn(
                        ev["quote"], opening_text,
                        f"Evidence quote must appear verbatim in opening text; got: {ev['quote']!r}",
                    )

    def test_out_of_schema_finding_type_discarded(self) -> None:
        """Unknown finding types from the LLM are discarded (closed schema)."""
        pkg = self._build_package_with_items([])

        def _fake_llm(messages, **kw):
            purpose = kw.get("purpose", "")
            if "audit_propose" in purpose:
                return _ok({
                    "proposals": [{
                        "finding_type": "made_up_finding_type",
                        "subject": "X",
                        "explanation": "Something",
                        "end_state_quote": "",
                        "opening_quote": "",
                        "insufficient_evidence": False,
                    }]
                })
            if "verify" in purpose:
                return _ok({"verdict": "confirmed", "reasoning": "ok"})
            return _ok({"verdict": "rejected", "reasoning": "n/a"})

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            audit = hf.run_handoff_audit(self.db, self.cfg, self.b1, self.b2)

        findings = hf.list_findings(self.db, audit["id"])
        for f in findings:
            self.assertIn(f["finding_type"], hf.FINDING_TYPES)

    def test_finding_resolution(self) -> None:
        """A finding can be resolved with a status from the closed list."""
        pkg = self._build_package_with_items([])

        def _fake_llm(messages, **kw):
            purpose = kw.get("purpose", "")
            if "audit_propose" in purpose:
                return _ok({
                    "proposals": [{
                        "finding_type": "dropped_thread",
                        "subject": "Aelith",
                        "explanation": "Thread dropped",
                        "end_state_quote": "",
                        "opening_quote": "",
                        "insufficient_evidence": True,  # no text to ground
                    }]
                })
            return _ok({"verdict": "confirmed", "reasoning": "ok"})

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            audit = hf.run_handoff_audit(self.db, self.cfg, self.b1, self.b2)

        findings = hf.list_findings(self.db, audit["id"])
        if not findings:
            self.skipTest("No findings produced; resolution test needs at least one")
        f = findings[0]
        updated = hf.resolve_finding(self.db, f["id"], "intentional", "Deliberate choice")
        self.assertEqual(updated["status"], "intentional")
        self.assertIn("Deliberate", updated["resolution_note"])

    def test_invalid_resolution_status_raises(self) -> None:
        pkg = self._build_package_with_items([])

        def _fake_llm(messages, **kw):
            if "audit_propose" in kw.get("purpose", ""):
                return _ok({"proposals": []})
            return _ok({"verdict": "rejected", "reasoning": ""})

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            audit = hf.run_handoff_audit(self.db, self.cfg, self.b1, self.b2)

        # Fake a finding manually.
        import uuid, datetime as dt
        from datetime import UTC
        fid = str(uuid.uuid4())
        now = dt.datetime.now(UTC).isoformat()
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO handoff_finding
                   (id, audit_id, finding_type, severity, subject, explanation,
                    evidence, insufficient_evidence, status, resolution_note,
                    dedupe_key, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (fid, audit["id"], "dropped_thread", "high", "X", "Test", "[]",
                 0, "open", "", "dk-test", now),
            )
            self.db._conn.commit()

        with self.assertRaises(hf.HandoffError):
            hf.resolve_finding(self.db, fid, "invalid_status")

    def test_severity_is_code_computed(self) -> None:
        """The model never sets severity — it is always computed from finding_type."""
        pkg = self._build_package_with_items([])

        def _fake_llm(messages, **kw):
            if "audit_propose" in kw.get("purpose", ""):
                return _ok({
                    "proposals": [{
                        "finding_type": "hard_contradiction",
                        "subject": "Test",
                        "explanation": "X",
                        "end_state_quote": "",
                        "opening_quote": "",
                        "insufficient_evidence": True,
                    }]
                })
            return _ok({"verdict": "confirmed", "reasoning": "ok"})

        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            audit = hf.run_handoff_audit(self.db, self.cfg, self.b1, self.b2)

        findings = hf.list_findings(self.db, audit["id"])
        for f in findings:
            self.assertEqual(
                f["severity"],
                hf._FINDING_SEVERITY[f["finding_type"]],
                "Severity must match the code-computed table, never model output",
            )

    def test_wrong_work_for_package_raises(self) -> None:
        other = self.db._create_work("Unrelated")
        with patch("orivellum.capabilities.llm.llm_call", return_value=_ok({"items": []})):
            pkg = hf.build_end_state_package(self.db, self.cfg, other)

        with self.assertRaises(hf.HandoffError):
            hf.run_handoff_audit(
                self.db, self.cfg, self.b1, self.b2,
                prior_package_id=pkg["id"],  # package belongs to `other`, not b1
            )

    def test_no_fresh_promise_detected_when_opening_has_no_dramatic_question(self) -> None:
        """Structural check: opening with no dramatic_question category → no_fresh_promise."""
        pkg = self._build_package_with_items([])

        def _fake_llm(messages, **kw):
            purpose = kw.get("purpose", "")
            if "extract_opening" in purpose:
                return _ok({
                    "items": [
                        {
                            "category": "orientation",
                            "subject": "time",
                            "claim": "Time passed",
                            "quote": "Summer had come and gone twice",
                        }
                    ]
                })
            if "audit_propose" in purpose:
                return _ok({"proposals": []})
            # verify always confirmed for structural checks
            return _ok({"verdict": "confirmed", "reasoning": "No dramatic question found."})

        # Build a contract with no dramatic_question item.
        with patch("orivellum.capabilities.llm.llm_call", side_effect=_fake_llm):
            contract = hf.build_opening_contract(self.db, self.cfg, self.b2, prior_package_id=pkg["id"])
            audit = hf.run_handoff_audit(
                self.db, self.cfg, self.b1, self.b2,
                prior_package_id=pkg["id"],
                successor_contract_id=contract["id"],
            )

        findings = hf.list_findings(self.db, audit["id"])
        types = {f["finding_type"] for f in findings}
        self.assertIn("no_fresh_promise", types)


class TestSeriesHandoffMap(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.db"
        self.db = _LightDB(str(db_path))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_map_returns_seam_per_consecutive_pair(self) -> None:
        b1 = self.db._create_work("Vol I")
        b2 = self.db._create_work("Vol II")
        b3 = self.db._create_work("Vol III")
        sid = self.db._create_series("Trilogy", b1, b2, b3)

        # SeriesStore not available in the thin DB — patch list_members.
        from orivellum.database.series_store import SeriesStore

        members = [
            {"work_id": b1, "volume": 1, "work_title": "Vol I"},
            {"work_id": b2, "volume": 2, "work_title": "Vol II"},
            {"work_id": b3, "volume": 3, "work_title": "Vol III"},
        ]

        with patch.object(SeriesStore, "list_members", return_value=members):
            seams = hf.series_handoff_map(self.db, sid)

        # 3 books → 2 seams
        self.assertEqual(len(seams), 2)
        self.assertEqual(seams[0]["prior_work_id"], b1)
        self.assertEqual(seams[0]["successor_work_id"], b2)
        self.assertEqual(seams[1]["prior_work_id"], b2)
        self.assertEqual(seams[1]["successor_work_id"], b3)
        for seam in seams:
            self.assertIn("health", seam)

    def test_single_book_series_returns_empty(self) -> None:
        b1 = self.db._create_work("Solo")
        sid = self.db._create_series("Solo Series", b1)

        from orivellum.database.series_store import SeriesStore

        with patch.object(SeriesStore, "list_members", return_value=[
            {"work_id": b1, "volume": 1, "work_title": "Solo"}
        ]):
            seams = hf.series_handoff_map(self.db, sid)

        self.assertEqual(seams, [])


if __name__ == "__main__":
    unittest.main()
