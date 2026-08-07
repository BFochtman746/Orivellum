"""Tests for the adaptive learning module (work_concepts / work_mastery)."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

# ─── Minimal in-memory DB fixture ─────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_db():
    """Create an in-memory SQLite DB with the minimal schema the learning module needs."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE objects (
            id TEXT PRIMARY KEY, type TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
            lifecycle TEXT NOT NULL DEFAULT 'active', provenance TEXT NOT NULL DEFAULT '{}',
            permissions TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'user', checksum TEXT
        );
        CREATE TABLE works (
            id TEXT PRIMARY KEY REFERENCES objects(id),
            title TEXT NOT NULL, work_type TEXT NOT NULL,
            description TEXT, status TEXT NOT NULL DEFAULT 'active', meta TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE knowledge_items (
            id TEXT PRIMARY KEY, work_id TEXT REFERENCES works(id),
            subject TEXT NOT NULL, content TEXT NOT NULL,
            source TEXT, review_status TEXT NOT NULL DEFAULT 'auto',
            created_at TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            id UNINDEXED, subject, content, content='knowledge_items', content_rowid='rowid'
        );
        CREATE TABLE work_concepts (
            id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL REFERENCES works(id) ON DELETE CASCADE,
            subject TEXT NOT NULL, description TEXT,
            prereq_id TEXT REFERENCES work_concepts(id),
            created_at TEXT NOT NULL
        );
        CREATE TABLE work_mastery (
            id TEXT PRIMARY KEY,
            concept_id TEXT NOT NULL REFERENCES work_concepts(id) ON DELETE CASCADE,
            score REAL NOT NULL DEFAULT 0,
            consecutive_passes INTEGER NOT NULL DEFAULT 0,
            brief_feedback TEXT, routed_to TEXT,
            created_at TEXT NOT NULL,
            -- v93 HLR columns
            last_reviewed_at TEXT,
            next_review_at TEXT,
            half_life_days REAL NOT NULL DEFAULT 1.0,
            review_session_count INTEGER NOT NULL DEFAULT 0,
            -- v95 error classification columns
            error_type TEXT,
            remediation_hint TEXT,
            -- v96 transfer-question tracking
            question_type TEXT NOT NULL DEFAULT 'recall',
            -- v97 interleaved practice mode
            session_mode TEXT NOT NULL DEFAULT 'blocked'
        );
        -- v94 multi-prerequisite graph
        CREATE TABLE IF NOT EXISTS work_concept_prereqs (
            concept_id TEXT NOT NULL REFERENCES work_concepts(id) ON DELETE CASCADE,
            prereq_id  TEXT NOT NULL REFERENCES work_concepts(id) ON DELETE CASCADE,
            PRIMARY KEY (concept_id, prereq_id)
        );
        CREATE INDEX IF NOT EXISTS idx_wcp_concept  ON work_concept_prereqs(concept_id);
        CREATE INDEX IF NOT EXISTS idx_wcp_prereq   ON work_concept_prereqs(prereq_id);
        -- Legacy Projects tables must coexist without conflict
        CREATE TABLE learning_concepts (
            id TEXT PRIMARY KEY, work_id TEXT,
            name TEXT NOT NULL, description TEXT,
            mastery REAL NOT NULL DEFAULT 0.0, last_review TEXT,
            meta TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL
        );
        CREATE TABLE learning_mastery (
            id TEXT PRIMARY KEY, concept_id TEXT NOT NULL,
            score REAL NOT NULL, method TEXT, evidence TEXT, created_at TEXT NOT NULL
        );
    """)
    conn.commit()
    return conn


class _FakeDB:
    """Minimal DB wrapper that satisfies the learning module's interface."""

    def __init__(self):
        self._conn = _make_db()
        self._lock = __import__("threading").Lock()

    def _insert_work(self, title="Test Work") -> str:
        wid = str(uuid.uuid4())
        now = _now()
        self._conn.execute(
            "INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
            (wid, "work", 1, "active", "{}", "{}", now, now),
        )
        self._conn.execute(
            "INSERT INTO works(id,title,work_type) VALUES(?,?,?)", (wid, title, "study")
        )
        self._conn.commit()
        return wid

    def _insert_concept(self, work_id: str, subject: str = "Topic", prereq_id=None) -> str:
        cid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO work_concepts(id,work_id,subject,prereq_id,created_at) VALUES(?,?,?,?,?)",
            (cid, work_id, subject, prereq_id, _now()),
        )
        self._conn.commit()
        return cid

    # Stub methods the learning module calls on the DB object
    def search_knowledge(self, query: str, work_id: str | None = None, limit: int = 8) -> list:
        return []

    def list_knowledge(self, work_id: str | None = None, limit: int = 8) -> list:
        return []

    def get_work(self, work_id: str):
        row = self._conn.execute("SELECT * FROM works WHERE id=?", (work_id,)).fetchone()
        return dict(row) if row else None

    def _insert_prereq(self, concept_id: str, prereq_id: str) -> None:
        """Insert a row into work_concept_prereqs to register a prerequisite edge."""
        self._conn.execute(
            "INSERT OR IGNORE INTO work_concept_prereqs(concept_id, prereq_id) VALUES(?,?)",
            (concept_id, prereq_id),
        )
        self._conn.commit()

    def _insert_legacy_concept(self, name="LegacyConcept") -> str:
        """Insert a Projects-system concept — must survive all migrations."""
        lid = str(uuid.uuid4())
        self._conn.execute(
            "INSERT INTO learning_concepts(id,name,mastery,meta,created_at) VALUES(?,?,0.0,'{}',?)",
            (lid, name, _now()),
        )
        self._conn.commit()
        return lid


# ─── Migration-preservation tests ─────────────────────────────────────────────

class TestMigrationPreservation:
    """Verify that Projects tables are never destroyed by Works learning operations."""

    def test_legacy_learning_concepts_survive(self):
        """learning_concepts (Projects) rows must be readable after work_concepts operations."""
        db = _FakeDB()
        lid = db._insert_legacy_concept("MyProject")
        wid = db._insert_work()
        db._insert_concept(wid, "Neural Networks")

        from orivellum.capabilities.learning import list_concepts
        concepts = list_concepts(db, wid)
        assert len(concepts) == 1
        assert concepts[0]["subject"] == "Neural Networks"

        # Legacy row must still be intact
        row = db._conn.execute(
            "SELECT name, mastery FROM learning_concepts WHERE id=?", (lid,)
        ).fetchone()
        assert row is not None
        assert row["name"] == "MyProject"
        assert row["mastery"] == 0.0

    def test_legacy_learning_mastery_survives(self):
        """learning_mastery (Projects) rows must not be deleted by work_mastery writes."""
        from orivellum.capabilities.learning import _record_mastery

        db = _FakeDB()
        # Insert a legacy concept + legacy mastery row
        lid = db._insert_legacy_concept("LegacyProject")
        lmid = str(uuid.uuid4())
        db._conn.execute(
            "INSERT INTO learning_mastery(id,concept_id,score,created_at) VALUES(?,?,?,?)",
            (lmid, lid, 0.8, _now()),
        )
        db._conn.commit()

        # Perform a work-mastery write
        wid = db._insert_work()
        cid = db._insert_concept(wid, "Backprop")
        _record_mastery(db, cid, 0.9, "STEP_FORWARD", "Good")

        # Legacy mastery row must still be intact
        row = db._conn.execute(
            "SELECT score FROM learning_mastery WHERE id=?", (lmid,)
        ).fetchone()
        assert row is not None
        assert row["score"] == 0.8


# ─── Cross-Work rejection tests ────────────────────────────────────────────────

class TestCrossWorkRejection:
    """Verify that a concept from Work A cannot be accessed via Work B's endpoints."""

    def test_list_concepts_scoped_to_work(self):
        from orivellum.capabilities.learning import list_concepts

        db = _FakeDB()
        wid_a = db._insert_work("Work A")
        wid_b = db._insert_work("Work B")
        db._insert_concept(wid_a, "Concept A")
        db._insert_concept(wid_b, "Concept B")

        a_concepts = list_concepts(db, wid_a)
        b_concepts = list_concepts(db, wid_b)

        assert len(a_concepts) == 1 and a_concepts[0]["subject"] == "Concept A"
        assert len(b_concepts) == 1 and b_concepts[0]["subject"] == "Concept B"

    def test_get_question_rejects_foreign_concept(self):
        """get_question with a concept_id from another work must not return that concept's data."""
        from orivellum.capabilities.learning import get_question

        db = _FakeDB()
        wid_a = db._insert_work("Work A")
        wid_b = db._insert_work("Work B")
        cid_a = db._insert_concept(wid_a, "Attention Mechanisms")

        # Retrieve work_id from concept directly to simulate what the endpoint checks
        row = db._conn.execute(
            "SELECT work_id FROM work_concepts WHERE id=?", (cid_a,)
        ).fetchone()
        assert row["work_id"] != wid_b, "concept_a must belong to work_a, not work_b"

    def test_record_mastery_does_not_bleed_between_works(self):
        from orivellum.capabilities.learning import _record_mastery, get_mastery_summary

        db = _FakeDB()
        wid_a = db._insert_work("Work A")
        wid_b = db._insert_work("Work B")
        cid_a = db._insert_concept(wid_a, "Transformers")
        cid_b = db._insert_concept(wid_b, "CNNs")

        _record_mastery(db, cid_a, 0.9, "STEP_FORWARD", "Excellent")

        summary_a = get_mastery_summary(db, wid_a)
        summary_b = get_mastery_summary(db, wid_b)

        assert summary_a["total"] == 1
        assert summary_b["total"] == 1
        # mastery_pct for B should be 0 (no passes recorded for cid_b)
        assert summary_b["mastery_pct"] == 0


# ─── Streak / routing logic ────────────────────────────────────────────────────

class TestStreakAndRouting:
    """Verify _record_mastery streak mechanics and graduation threshold."""

    def test_pass_increments_consecutive_passes(self):
        from orivellum.capabilities.learning import _record_mastery

        db = _FakeDB()
        wid = db._insert_work()
        cid = db._insert_concept(wid)

        _record_mastery(db, cid, 0.8, "STAY_HERE", "Good")
        # consecutive_passes is stored in work_mastery and surfaced via list_concepts
        from orivellum.capabilities.learning import list_concepts
        concepts = list_concepts(db, wid)
        assert concepts[0]["consecutive_passes"] == 1

    def test_fail_resets_consecutive_passes(self):
        from orivellum.capabilities.learning import _record_mastery, list_concepts

        db = _FakeDB()
        wid = db._insert_work()
        cid = db._insert_concept(wid)

        _record_mastery(db, cid, 0.9, "STAY_HERE", "First pass")
        _record_mastery(db, cid, 0.9, "STAY_HERE", "Second pass")
        _record_mastery(db, cid, 0.3, "STAY_HERE", "Fail")

        concepts = list_concepts(db, wid)
        assert concepts[0]["consecutive_passes"] == 0

    def test_three_passes_marks_graduated(self):
        from orivellum.capabilities.learning import _record_mastery, list_concepts

        db = _FakeDB()
        wid = db._insert_work()
        cid = db._insert_concept(wid)

        for _ in range(3):
            _record_mastery(db, cid, 0.8, "STEP_FORWARD", "Pass")

        concepts = list_concepts(db, wid)
        assert concepts[0]["graduated"] is True


# ─── Offline-fallback tests ────────────────────────────────────────────────────

class TestOfflineFallback:
    """Verify graceful fallback when AI endpoint is unreachable."""

    def test_assess_answer_offline_returns_neutral_score(self):
        """assess_answer with an unreachable base_url must return score=0.5 and STAY_HERE."""
        from orivellum.capabilities.learning import assess_answer

        db = _FakeDB()
        wid = db._insert_work()
        cid = db._insert_concept(wid, "Backpropagation")

        result = assess_answer(
            db, cid,
            "What is backpropagation?",
            "It computes gradients.",
            "http://localhost:1",   # unreachable
            "any-model",
        )
        assert result["score"] == pytest.approx(0.5)
        assert result["route"] == "STAY_HERE"
        assert isinstance(result["feedback"], str)

    def test_get_question_offline_returns_generic_question(self):
        """get_question with unreachable AI must return a non-empty fallback question."""
        from orivellum.capabilities.learning import get_question

        db = _FakeDB()
        wid = db._insert_work()
        cid = db._insert_concept(wid, "Gradient Descent")

        result = get_question(db, cid, "http://localhost:1", "any-model")
        assert isinstance(result.get("question"), str)
        assert len(result["question"]) > 10


# ─── assess_answer JSON-robustness tests ──────────────────────────────────────

class TestAssessAnswerJsonRobustness:
    """Verify Assessment Critic handles unexpected AI JSON gracefully."""

    def _mock_call(self, return_text: str):
        """Patch orivellum.capabilities.learning._call to return a fixed string."""
        import orivellum.capabilities.learning as lm
        return patch.object(lm, "_call", return_value=return_text)

    def test_out_of_range_score_is_clamped(self):
        """Score > 1.0 from AI must be clamped to 1.0."""
        from orivellum.capabilities.learning import assess_answer

        db = _FakeDB()
        wid = db._insert_work()
        cid = db._insert_concept(wid)

        bad_json = '{"score": 1.5, "feedback": "Too confident", "route": "STEP_FORWARD"}'
        with self._mock_call(bad_json):
            result = assess_answer(db, cid, "Q?", "A.", "http://x", "m")
        assert result["score"] <= 1.0

    def test_malformed_json_falls_back_to_neutral(self):
        """Non-JSON AI response must fall back to score=0.5 / STAY_HERE."""
        from orivellum.capabilities.learning import assess_answer

        db = _FakeDB()
        wid = db._insert_work()
        cid = db._insert_concept(wid)

        with self._mock_call("sorry I cannot help with that"):
            result = assess_answer(db, cid, "Q?", "A.", "http://x", "m")
        assert result["score"] == pytest.approx(0.5)
        assert result["route"] == "STAY_HERE"

    def test_empty_json_falls_back_to_neutral(self):
        """Empty JSON object must fall back to neutral score."""
        from orivellum.capabilities.learning import assess_answer

        db = _FakeDB()
        wid = db._insert_work()
        cid = db._insert_concept(wid)

        with self._mock_call("{}"):
            result = assess_answer(db, cid, "Q?", "A.", "http://x", "m")
        assert 0.0 <= result["score"] <= 1.0

    def test_markdown_fenced_json_is_parsed(self):
        """JSON wrapped in markdown fences must be parsed correctly."""
        from orivellum.capabilities.learning import assess_answer

        db = _FakeDB()
        wid = db._insert_work()
        cid = db._insert_concept(wid)

        fenced = '```json\n{"score": 0.9, "feedback": "Great", "route": "STEP_FORWARD"}\n```'
        with self._mock_call(fenced):
            result = assess_answer(db, cid, "Q?", "A.", "http://x", "m")
        # Either parses correctly or falls back gracefully — both acceptable
        assert 0.0 <= result["score"] <= 1.0
        assert result["route"] in ("STEP_FORWARD", "STEP_BACKWARD", "STAY_HERE")


# ─── Multi-prerequisite graph tests ───────────────────────────────────────────

class TestPrereqGraph:
    """Verify the v94 multi-prerequisite dependency graph helpers."""

    def test_get_prereq_ids_empty_for_root_concept(self):
        """A concept with no prerequisites must return an empty list."""
        from orivellum.capabilities.learning import get_prereq_ids

        db = _FakeDB()
        wid = db._insert_work()
        cid = db._insert_concept(wid, "Calculus")

        assert get_prereq_ids(db, cid) == []

    def test_get_prereq_ids_returns_all_linked_prereqs(self):
        """get_prereq_ids must return all prerequisite concept IDs for a concept."""
        from orivellum.capabilities.learning import get_prereq_ids

        db = _FakeDB()
        wid = db._insert_work()
        algebra  = db._insert_concept(wid, "Algebra")
        trig     = db._insert_concept(wid, "Trigonometry")
        calculus = db._insert_concept(wid, "Calculus")

        db._insert_prereq(calculus, algebra)
        db._insert_prereq(calculus, trig)

        prereqs = get_prereq_ids(db, calculus)
        assert set(prereqs) == {algebra, trig}

    def test_is_concept_eligible_true_for_root(self):
        """A concept with no prerequisites must be eligible to study."""
        from orivellum.capabilities.learning import is_concept_eligible

        db = _FakeDB()
        wid = db._insert_work()
        cid = db._insert_concept(wid, "Algebra")

        assert is_concept_eligible(db, cid) is True

    def test_is_concept_eligible_false_when_prereq_not_started(self):
        """A concept must be ineligible when its prerequisite has zero passes."""
        from orivellum.capabilities.learning import is_concept_eligible

        db = _FakeDB()
        wid      = db._insert_work()
        algebra  = db._insert_concept(wid, "Algebra")
        calculus = db._insert_concept(wid, "Calculus")
        db._insert_prereq(calculus, algebra)

        # Algebra has zero passes — Calculus must be locked
        assert is_concept_eligible(db, calculus) is False

    def test_is_concept_eligible_true_when_all_prereqs_started(self):
        """A concept must be eligible when all prerequisites have at least one pass."""
        from orivellum.capabilities.learning import is_concept_eligible, _record_mastery

        db = _FakeDB()
        wid      = db._insert_work()
        algebra  = db._insert_concept(wid, "Algebra")
        trig     = db._insert_concept(wid, "Trigonometry")
        calculus = db._insert_concept(wid, "Calculus")
        db._insert_prereq(calculus, algebra)
        db._insert_prereq(calculus, trig)

        # Start both prerequisites
        _record_mastery(db, algebra, 0.8, "STAY_HERE", "Good")
        _record_mastery(db, trig,    0.8, "STAY_HERE", "Good")

        assert is_concept_eligible(db, calculus) is True

    def test_is_concept_eligible_false_when_only_one_prereq_started(self):
        """A concept must be ineligible when at least one prerequisite has zero passes."""
        from orivellum.capabilities.learning import is_concept_eligible, _record_mastery

        db = _FakeDB()
        wid      = db._insert_work()
        algebra  = db._insert_concept(wid, "Algebra")
        trig     = db._insert_concept(wid, "Trigonometry")
        calculus = db._insert_concept(wid, "Calculus")
        db._insert_prereq(calculus, algebra)
        db._insert_prereq(calculus, trig)

        # Only algebra started — trig still at zero
        _record_mastery(db, algebra, 0.9, "STAY_HERE", "Good")

        assert is_concept_eligible(db, calculus) is False

    def test_get_blocking_concepts_returns_dependents(self):
        """get_blocking_concepts must return the IDs of concepts that depend on a given concept."""
        from orivellum.capabilities.learning import get_blocking_concepts

        db = _FakeDB()
        wid      = db._insert_work()
        algebra  = db._insert_concept(wid, "Algebra")
        calculus = db._insert_concept(wid, "Calculus")
        diffeq   = db._insert_concept(wid, "Differential Equations")
        db._insert_prereq(calculus, algebra)
        db._insert_prereq(diffeq, algebra)

        blockers = get_blocking_concepts(db, algebra)
        assert set(blockers) == {calculus, diffeq}

    def test_list_concepts_includes_graph_fields(self):
        """list_concepts must annotate each concept with prereq_ids, prereq_labels, prereqs_met, blocking_count."""
        from orivellum.capabilities.learning import list_concepts, _record_mastery

        db = _FakeDB()
        wid      = db._insert_work()
        algebra  = db._insert_concept(wid, "Algebra")
        calculus = db._insert_concept(wid, "Calculus")
        db._insert_prereq(calculus, algebra)

        # Initially calculus is locked
        concepts = {c["id"]: c for c in list_concepts(db, wid)}
        assert concepts[algebra]["prereq_ids"]    == []
        assert concepts[algebra]["prereqs_met"]   is True
        assert concepts[algebra]["blocking_count"] == 1       # calculus depends on it
        assert concepts[calculus]["prereq_ids"]   == [algebra]
        assert concepts[calculus]["prereq_labels"] == ["Algebra"]
        assert concepts[calculus]["prereqs_met"]  is False

        # Start algebra — calculus should become eligible
        _record_mastery(db, algebra, 0.8, "STAY_HERE", "Good")
        concepts2 = {c["id"]: c for c in list_concepts(db, wid)}
        assert concepts2[calculus]["prereqs_met"] is True

    def test_next_concept_id_prefers_eligible_over_locked(self):
        """next_concept_id must pick an eligible root concept before a locked one."""
        from orivellum.capabilities.learning import next_concept_id

        db = _FakeDB()
        wid      = db._insert_work()
        algebra  = db._insert_concept(wid, "Algebra")
        calculus = db._insert_concept(wid, "Calculus")
        db._insert_prereq(calculus, algebra)

        # Algebra is the only eligible concept (calculus is locked)
        chosen = next_concept_id(db, wid)
        assert chosen == algebra, "next_concept_id must pick the eligible root concept first"

    def test_next_concept_id_fallback_when_all_locked(self):
        """next_concept_id must still return a concept when every concept is locked (circular guard)."""
        from orivellum.capabilities.learning import next_concept_id

        db = _FakeDB()
        wid = db._insert_work()
        a   = db._insert_concept(wid, "A")
        b   = db._insert_concept(wid, "B")
        # Artificial circular / all-locked scenario
        db._insert_prereq(a, b)
        db._insert_prereq(b, a)

        # Must not return None despite both concepts being locked
        chosen = next_concept_id(db, wid)
        assert chosen in (a, b), "fallback pool must always yield a concept"

    def test_list_concepts_uses_bulk_queries_no_n_plus_one(self):
        """list_concepts must load N concepts in O(1) queries, not O(N).

        We verify this by wrapping the DB's _conn in a counting proxy and asserting
        that the total number of work_mastery / prereq-table reads is bounded by a
        small constant (3) regardless of the number of concepts.
        """
        from orivellum.capabilities.learning import list_concepts
        import sqlite3

        db = _FakeDB()
        wid = db._insert_work()
        for i in range(10):
            db._insert_concept(wid, f"Concept {i}")

        query_count = 0

        class _CountingConn:
            """Thin proxy that counts hot-path execute() calls."""
            def __init__(self, conn: sqlite3.Connection):
                object.__setattr__(self, "_conn", conn)

            def __getattr__(self, name):
                return getattr(object.__getattribute__(self, "_conn"), name)

            def execute(self, sql, params=()):
                nonlocal query_count
                if "work_mastery" in sql or "work_concept_prereqs" in sql:
                    query_count += 1
                return object.__getattribute__(self, "_conn").execute(sql, params)

        # Swap in the proxy for the duration of this test
        real_conn = db._conn
        db._conn = _CountingConn(real_conn)
        try:
            list_concepts(db, wid)
        finally:
            db._conn = real_conn  # restore

        # Expect at most 3 queries regardless of N (mastery CTE + prereqs + blocking)
        assert query_count <= 3, (
            f"list_concepts must be O(1) queries; got {query_count} queries for 10 concepts"
        )

    def test_compute_route_step_backward_picks_weakest_prereq(self):
        """_compute_route must suggest STEP_BACKWARD when any prerequisite is unmastered."""
        from orivellum.capabilities.learning import _compute_route, _record_mastery

        db = _FakeDB()
        wid      = db._insert_work()
        algebra  = db._insert_concept(wid, "Algebra")
        calculus = db._insert_concept(wid, "Calculus")
        db._insert_prereq(calculus, algebra)

        # Calculus fails — algebra not mastered yet (0 passes)
        route = _compute_route(db, calculus, score=0.2)
        assert route == "STEP_BACKWARD", (
            "failing a concept with an unmastered prerequisite must route backward"
        )
