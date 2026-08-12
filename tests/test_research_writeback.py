"""Tests for research writeback + review gate + plan import (task T-M2/T-M3).

The non-negotiable pairing under test: web-derived research claims land as
knowledge PROPOSALS (review_status='proposed') and can never ground a
learning question or answer key until a human ratifies them to 'approved'.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from orivellum.capabilities.learning import (
    _QUESTION_SAFE_REVIEW,
    _knowledge_for_concept,
    import_training_plan,
    validate_prereq_graph,
)
from orivellum.capabilities.research_import import (
    import_research_digests,
    import_research_run,
)
from orivellum.database.db import OrivellumDB

# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        d = OrivellumDB(str(Path(tmp) / "test.db"))
        yield d
        d.close()


@pytest.fixture
def work_id(db):
    return db.create_work("Test Work", "study")["id"]


def _digests(topic="quantum computing"):
    return {
        "topic": topic,
        "digests": [
            {
                "query": "what is a qubit",
                "origin": "gap_table",
                "gap_id": "gap-1",
                "sources": [
                    {
                        "id": "S1",
                        "url": "https://example.org/qubits",
                        "title": "Qubits explained",
                        "retrieved": "2026-08-12",
                        "kind": "web",
                    },
                ],
                "claims": [
                    {
                        "claim": "A qubit can exist in superposition of 0 and 1.",
                        "sources": ["S1"],
                        "quote": "a qubit exists in a superposition of both states",
                        "confidence": "high",
                    },
                    {
                        # cites an unknown source id — must be skipped
                        "claim": "Unsourced claim that must not land.",
                        "sources": ["S9"],
                        "quote": "irrelevant",
                        "confidence": "low",
                    },
                ],
            }
        ],
    }


def _plan_items():
    return [
        {
            "topic": "Qubit basics",
            "why": "Foundation for everything else",
            "evidence": ["https://example.org/qubits"],
            "read": "Read the superposition primer",
            "check": "Explain superposition in one sentence",
            "question": "What distinguishes a qubit from a classical bit?",
            "prereq": [],
            "schedule": {"start_day": 1, "review_after_days": [1, 3, 7, 14]},
        },
        {
            "topic": "Entanglement",
            "why": "Builds on qubit state",
            "evidence": [],
            "read": "Read the entanglement chapter",
            "check": "Describe a Bell pair",
            "question": "Why can't entangled qubits be described independently?",
            "prereq": ["Qubit basics"],
            "schedule": {"start_day": 2, "review_after_days": [1, 3, 7, 14]},
        },
    ]


# ─── Writeback: claims land as proposals ─────────────────────────────────────


class TestWriteback:
    def test_claims_land_as_proposed_with_provenance(self, db, work_id):
        result = import_research_digests(db, work_id, _digests())
        assert result["proposals_created"] == 1
        assert result["skipped_unsourced"] == 1  # unknown source id never stored
        items = db.list_knowledge(work_id=work_id, kind="research_claim")
        assert len(items) == 1
        item = items[0]
        assert item["review_status"] == "proposed"
        meta = item["meta"]
        assert meta["sources"][0]["url"] == "https://example.org/qubits"
        assert meta["sources"][0]["retrieved"] == "2026-08-12"
        assert "superposition" in meta["quote"]

    def test_reimport_is_idempotent(self, db, work_id):
        import_research_digests(db, work_id, _digests())
        result = import_research_digests(db, work_id, _digests())
        assert result["proposals_created"] == 0
        assert result["duplicates"] == 1
        assert len(db.list_knowledge(work_id=work_id, kind="research_claim")) == 1

    def test_reimport_never_touches_ratified_claim(self, db, work_id):
        import_research_digests(db, work_id, _digests())
        item = db.list_knowledge(work_id=work_id, kind="research_claim")[0]
        assert (
            db.update_knowledge_review_status(item["id"], "approved", expected_status=("proposed",))
            == "updated"
        )
        import_research_digests(db, work_id, _digests())
        refreshed = db.list_knowledge(work_id=work_id, kind="research_claim")
        assert len(refreshed) == 1
        assert refreshed[0]["review_status"] == "approved"

    def test_claim_cap_refuses_oversized_import(self, db, work_id):
        dg = _digests()
        claim = dg["digests"][0]["claims"][0]
        dg["digests"][0]["claims"] = [{**claim, "claim": f"Claim number {i}"} for i in range(501)]
        with pytest.raises(ValueError):
            import_research_digests(db, work_id, dg)


# ─── Review gate: proposed can't reach a question without ratification ──────


class TestReviewGate:
    def test_proposed_claim_never_grounds_question(self, db, work_id):
        import_research_digests(db, work_id, _digests())
        items = _knowledge_for_concept(db, work_id, "qubit superposition")
        assert items == []

    def test_ratified_claim_becomes_question_safe(self, db, work_id):
        import_research_digests(db, work_id, _digests())
        item = db.list_knowledge(work_id=work_id, kind="research_claim")[0]
        db.update_knowledge_review_status(item["id"], "approved")
        items = _knowledge_for_concept(db, work_id, "qubit superposition")
        assert any(i["id"] == item["id"] for i in items)

    def test_rejected_stays_excluded(self, db, work_id):
        import_research_digests(db, work_id, _digests())
        item = db.list_knowledge(work_id=work_id, kind="research_claim")[0]
        db.update_knowledge_review_status(item["id"], "rejected")
        assert _knowledge_for_concept(db, work_id, "qubit superposition") == []

    def test_allowlist_fails_closed(self):
        assert "proposed" not in _QUESTION_SAFE_REVIEW
        assert "rejected" not in _QUESTION_SAFE_REVIEW

    def test_proposed_is_valid_review_transition(self, db, work_id):
        kid = db.create_knowledge_item(work_id, "fact", "some text", review_status="proposed")
        # Claim-first CAS from awaiting-review statuses covers 'proposed'
        assert (
            db.update_knowledge_review_status(
                kid, "approved", expected_status=("auto", "ai_auto", "proposed")
            )
            == "updated"
        )

    def test_review_filter_in_list_and_search(self, db, work_id):
        db.create_knowledge_item(work_id, "fact", "approved fact", review_status="approved")
        db.create_knowledge_item(work_id, "fact", "proposed fact", review_status="proposed")
        listed = db.list_knowledge(work_id=work_id, review_status_in=("approved",))
        assert [i["text"] for i in listed] == ["approved fact"]
        hits = db.search_knowledge("fact", work_id=work_id, review_status_in=("approved",))
        assert all(h["review_status"] == "approved" for h in hits)


# ─── Plan importer round-trip ────────────────────────────────────────────────


class TestPlanImport:
    def test_round_trip_preserves_shape(self, db, work_id):
        result = import_training_plan(db, work_id, _plan_items())
        assert result["concepts_created"] == 2
        assert result["items_stored"] == 2
        assert result["prereq_edges_added"] == 1
        with db._lock:
            rows = db._conn.execute(
                """SELECT c.subject, c.description, i.question, i.read_text,
                          i.check_text, i.evidence_json, i.schedule_json
                   FROM work_concepts c JOIN work_concept_items i ON i.concept_id = c.id
                   WHERE c.work_id=? ORDER BY c.subject""",
                (work_id,),
            ).fetchall()
        by_subject = {r["subject"]: dict(r) for r in rows}
        qb = by_subject["Qubit basics"]
        assert qb["description"] == "Foundation for everything else"
        assert qb["question"] == "What distinguishes a qubit from a classical bit?"
        assert qb["read_text"] == "Read the superposition primer"
        assert qb["check_text"] == "Explain superposition in one sentence"
        assert "example.org" in qb["evidence_json"]
        assert "review_after_days" in by_subject["Entanglement"]["schedule_json"]

    def test_reimport_is_idempotent(self, db, work_id):
        import_training_plan(db, work_id, _plan_items())
        result = import_training_plan(db, work_id, _plan_items())
        assert result["concepts_created"] == 0
        assert result["concepts_reused"] == 2
        assert result["items_stored"] == 0
        assert result["prereq_edges_added"] == 0
        with db._lock:
            n = db._conn.execute(
                "SELECT COUNT(*) AS n FROM work_concepts WHERE work_id=?", (work_id,)
            ).fetchone()["n"]
        assert n == 2

    def test_invalid_items_skipped(self, db, work_id):
        items = _plan_items() + [{"topic": "", "question": "q"}, {"topic": "No question"}]
        result = import_training_plan(db, work_id, items)
        assert result["skipped"] == 2

    def test_combined_run_import(self, db, work_id):
        result = import_research_run(db, work_id, _digests(), {"items": _plan_items()})
        assert result["writeback"]["proposals_created"] == 1
        assert result["plan_import"]["concepts_created"] == 2


# ─── Cycle guard ─────────────────────────────────────────────────────────────


class TestCycleGuard:
    def _concept(self, db, work_id, subject, created_at):
        import uuid

        cid = str(uuid.uuid4())
        with db._lock:
            db._conn.execute(
                "INSERT INTO work_concepts(id,work_id,subject,created_at) VALUES(?,?,?,?)",
                (cid, work_id, subject, created_at),
            )
            db._conn.commit()
        return cid

    def _edge(self, db, cid, pid):
        with db._lock:
            db._conn.execute(
                "INSERT OR IGNORE INTO work_concept_prereqs(concept_id,prereq_id) VALUES(?,?)",
                (cid, pid),
            )
            db._conn.commit()

    def test_acyclic_graph_untouched(self, db, work_id):
        a = self._concept(db, work_id, "A", "2026-01-01T00:00:00")
        b = self._concept(db, work_id, "B", "2026-01-02T00:00:00")
        self._edge(db, b, a)
        assert validate_prereq_graph(db, work_id) == []
        with db._lock:
            n = db._conn.execute("SELECT COUNT(*) AS n FROM work_concept_prereqs").fetchone()["n"]
        assert n == 1

    def test_cycle_broken_deterministically(self, db, work_id):
        a = self._concept(db, work_id, "A", "2026-01-01T00:00:00")
        b = self._concept(db, work_id, "B", "2026-01-02T00:00:00")
        c = self._concept(db, work_id, "C", "2026-01-03T00:00:00")
        # A -> B -> C -> A  (cycle)
        self._edge(db, a, b)
        self._edge(db, b, c)
        self._edge(db, c, a)
        removed1 = validate_prereq_graph(db, work_id)
        assert len(removed1) == 1
        # Now acyclic; second run removes nothing.
        assert validate_prereq_graph(db, work_id) == []
        with db._lock:
            n = db._conn.execute("SELECT COUNT(*) AS n FROM work_concept_prereqs").fetchone()["n"]
        assert n == 2

    def test_importer_breaks_cross_item_cycle(self, db, work_id):
        items = _plan_items()
        items[0]["prereq"] = ["Entanglement"]  # closes Qubit basics <-> Entanglement
        result = import_training_plan(db, work_id, items)
        assert result["cycle_edges_removed"] == 1
