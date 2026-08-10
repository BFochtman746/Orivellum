"""Tests for evidence scoring, contradiction detection, and embeddings plumbing."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from orivellum.capabilities.embeddings import (
    cosine,
    hybrid_search_knowledge,
    pack_vector,
    unpack_vector,
)
from orivellum.capabilities.evidence import (
    compute_evidence_score,
    detect_contradictions,
    rescore_work,
)

# ── compute_evidence_score ───────────────────────────────────────────────────


class TestComputeEvidenceScore:
    def _item(self, **kw):
        base = {"kind": "claim", "review_status": "auto", "meta": {}}
        base.update(kw)
        return base

    def test_approved_corroborated_recent_scores_high(self):
        now = datetime.now(UTC)
        src = (now - timedelta(days=10)).isoformat()
        score, comps = compute_evidence_score(
            self._item(kind="summary", review_status="approved"), 3, src, now
        )
        assert score >= 0.85
        assert comps["corroboration"] == 1.0

    def test_uncorroborated_ai_auto_scores_low(self):
        now = datetime.now(UTC)
        old = (now - timedelta(days=900)).isoformat()
        score, _ = compute_evidence_score(
            self._item(kind="entity", review_status="ai_auto", meta={"source": "llm"}), 0, old, now
        )
        assert score < 0.5

    def test_llm_meta_caps_base(self):
        now = datetime.now(UTC)
        s_rule, _ = compute_evidence_score(self._item(kind="summary"), 0, None, now)
        s_llm, c_llm = compute_evidence_score(
            self._item(kind="summary", meta={"source": "llm"}), 0, None, now
        )
        assert s_llm < s_rule
        assert c_llm["base"] == 0.70

    def test_unknown_recency_is_neutral(self):
        _, comps = compute_evidence_score(self._item(), 0, None)
        assert comps["recency"] == 0.5

    def test_score_bounds(self):
        s, _ = compute_evidence_score(
            self._item(kind="entity", review_status="rejected"), 0, "2000-01-01T00:00:00+00:00"
        )
        assert 0.05 <= s <= 1.0


# ── rescore_work / detect_contradictions (DB-backed) ─────────────────────────


@pytest.fixture
def db(tmp_path):
    from orivellum.database.db import OrivellumDB

    d = OrivellumDB(str(tmp_path / "test.db"))
    yield d
    d.close()


@pytest.fixture
def work_with_items(db):
    work_id = db.create_work("Evidence Test Work")["id"]
    doc_a = db.create_document(title="Doc A", kind="pdf", source="a.pdf", work_id=work_id)["id"]
    doc_b = db.create_document(title="Doc B", kind="pdf", source="b.pdf", work_id=work_id)["id"]
    return work_id, doc_a, doc_b


class TestRescoreWork:
    def test_rescore_updates_confidence_and_meta(self, db, work_with_items):
        work_id, doc_a, doc_b = work_with_items
        # Same subject from two docs → corroborated
        k1 = db.create_knowledge_item(
            work_id,
            "claim",
            "Python is dynamically typed",
            subject="python",
            predicate="typing",
            obj="dynamic",
            confidence=0.7,
            source_doc_id=doc_a,
        )
        db.create_knowledge_item(
            work_id,
            "claim",
            "Python uses dynamic typing",
            subject="python",
            predicate="style",
            obj="dynamic",
            confidence=0.7,
            source_doc_id=doc_b,
        )
        changed = rescore_work(work_id, db)
        assert changed >= 1
        item = next(i for i in db.list_knowledge(work_id=work_id) if i["id"] == k1)
        import json as _json

        meta = _json.loads(item["meta"]) if isinstance(item["meta"], str) else item["meta"]
        assert "evidence" in meta
        assert meta["evidence"]["corroborating_sources"] == 1

    def test_stable_score_still_persists_evidence_meta(self, db, work_with_items):
        """Even when confidence doesn't change, meta.evidence must be written."""
        import json as _json

        work_id, doc_a, _ = work_with_items
        kid = db.create_knowledge_item(
            work_id, "claim", "Stable claim", subject="stable", confidence=0.7, source_doc_id=doc_a
        )
        rescore_work(work_id, db)  # first pass sets score + meta
        rescore_work(work_id, db)  # second pass: score stable
        item = next(i for i in db.list_knowledge(work_id=work_id) if i["id"] == kid)
        meta = _json.loads(item["meta"]) if isinstance(item["meta"], str) else item["meta"]
        assert "evidence" in meta

    def test_rejected_items_skipped(self, db, work_with_items):
        work_id, doc_a, _ = work_with_items
        kid = db.create_knowledge_item(
            work_id,
            "claim",
            "Bad claim",
            subject="x",
            confidence=0.9,
            source_doc_id=doc_a,
            review_status="rejected",
        )
        rescore_work(work_id, db)
        item = next(i for i in db.list_knowledge(work_id=work_id) if i["id"] == kid)
        assert item["confidence"] == 0.9  # untouched


class TestDetectContradictions:
    def test_conflicting_values_detected(self, db, work_with_items):
        work_id, doc_a, doc_b = work_with_items
        db.create_knowledge_item(
            work_id,
            "claim",
            "The boiling point is 100C",
            subject="water",
            predicate="boiling_point",
            obj="100C",
            source_doc_id=doc_a,
        )
        db.create_knowledge_item(
            work_id,
            "claim",
            "The boiling point is 90C",
            subject="water",
            predicate="boiling_point",
            obj="90C",
            source_doc_id=doc_b,
        )
        assert detect_contradictions(work_id, db) == 1
        conflicts = db.list_conflicts(resolved=False)
        assert len(conflicts) == 1
        assert conflicts[0]["conflict_type"] == "conflicting_values"

    def test_negation_detected(self, db, work_with_items):
        work_id, doc_a, doc_b = work_with_items
        db.create_knowledge_item(
            work_id,
            "claim",
            "The framework supports async execution by default",
            subject="framework",
            source_doc_id=doc_a,
        )
        db.create_knowledge_item(
            work_id,
            "claim",
            "The framework does not support async execution by default",
            subject="framework",
            source_doc_id=doc_b,
        )
        assert detect_contradictions(work_id, db) == 1

    def test_duplicate_pairs_not_recorded_twice(self, db, work_with_items):
        work_id, doc_a, doc_b = work_with_items
        db.create_knowledge_item(
            work_id, "claim", "X is 1", subject="x", predicate="value", obj="1", source_doc_id=doc_a
        )
        db.create_knowledge_item(
            work_id, "claim", "X is 2", subject="x", predicate="value", obj="2", source_doc_id=doc_b
        )
        assert detect_contradictions(work_id, db) == 1
        assert detect_contradictions(work_id, db) == 0

    def test_agreeing_claims_no_conflict(self, db, work_with_items):
        work_id, doc_a, doc_b = work_with_items
        db.create_knowledge_item(
            work_id, "claim", "X is 1", subject="x", predicate="value", obj="1", source_doc_id=doc_a
        )
        db.create_knowledge_item(
            work_id, "claim", "X is 1", subject="x", predicate="value", obj="1", source_doc_id=doc_b
        )
        assert detect_contradictions(work_id, db) == 0

    def test_high_cardinality_subject_is_capped(self, db, work_with_items):
        """Many claims on one subject must not explode into O(n²) conflicts."""
        work_id, doc_a, doc_b = work_with_items
        for i in range(60):
            db.create_knowledge_item(
                work_id,
                "claim",
                f"The system does not support feature {i} at all",
                subject="bigsubject",
                source_doc_id=doc_a,
            )
            db.create_knowledge_item(
                work_id,
                "claim",
                f"The system supports feature {i} at all times",
                subject="bigsubject",
                source_doc_id=doc_b,
            )
        import time

        t0 = time.monotonic()
        n = detect_contradictions(work_id, db)
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0
        assert n <= 200  # negation pairs capped per subject

    def test_hybrid_semantic_hits_have_canonical_shape(self, db, work_with_items):
        """Semantic hits must carry provenance fields like FTS hits do."""
        from orivellum.capabilities.embeddings import pack_vector, semantic_search

        work_id, doc_a, _ = work_with_items
        kid = db.create_knowledge_item(
            work_id, "fact", "Canonical shape item", subject="shape", source_doc_id=doc_a
        )
        db.store_vector(kid, "knowledge", pack_vector([1.0, 0.0]), 2)
        with patch("orivellum.capabilities.embeddings.embed_texts", return_value=[[1.0, 0.0]]):
            hits = semantic_search("shape", db, "knowledge", limit=5)
        assert len(hits) == 1
        h = hits[0]
        for field in ("source_doc_id", "predicate", "object", "created_at", "meta"):
            assert field in h
        assert h["source_doc_id"] == doc_a

    def test_resolve_rejects_loser(self, db, work_with_items):
        work_id, doc_a, doc_b = work_with_items
        ka = db.create_knowledge_item(
            work_id, "claim", "X is 1", subject="x", predicate="value", obj="1", source_doc_id=doc_a
        )
        kb = db.create_knowledge_item(
            work_id, "claim", "X is 2", subject="x", predicate="value", obj="2", source_doc_id=doc_b
        )
        detect_contradictions(work_id, db)
        cid = db.list_conflicts()[0]["id"]
        assert db.resolve_conflict(cid, "keep_a") is True
        items = {i["id"]: i for i in db.list_knowledge(work_id=work_id)}
        assert items[kb]["review_status"] == "rejected"
        assert items[ka]["review_status"] != "rejected"
        assert db.list_conflicts(resolved=False) == []
        assert len(db.list_conflicts(resolved=True)) == 1

    def test_resolve_twice_fails(self, db, work_with_items):
        work_id, doc_a, doc_b = work_with_items
        db.create_knowledge_item(
            work_id, "claim", "X is 1", subject="x", predicate="value", obj="1", source_doc_id=doc_a
        )
        db.create_knowledge_item(
            work_id, "claim", "X is 2", subject="x", predicate="value", obj="2", source_doc_id=doc_b
        )
        detect_contradictions(work_id, db)
        cid = db.list_conflicts()[0]["id"]
        db.resolve_conflict(cid, "keep_both")
        assert db.resolve_conflict(cid, "keep_a") is False


# ── Embeddings plumbing ──────────────────────────────────────────────────────


class TestEmbeddings:
    def test_pack_unpack_roundtrip(self):
        vec = [0.1, -0.5, 2.0, 0.0]
        out = unpack_vector(pack_vector(vec), 4)
        assert out == pytest.approx(vec)

    def test_cosine(self):
        assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
        assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
        assert cosine([1, 1], [1, 1]) == pytest.approx(1.0)
        assert cosine([0, 0], [1, 1]) == 0.0
        assert cosine([1, 2], [1, 2, 3]) == 0.0  # dim mismatch

    def test_hybrid_falls_back_to_fts_when_embeddings_down(self, db, work_with_items):
        work_id, doc_a, _ = work_with_items
        db.create_knowledge_item(
            work_id, "fact", "Solar panels convert sunlight", subject="solar", source_doc_id=doc_a
        )
        with patch("orivellum.capabilities.embeddings.embed_texts", return_value=None):
            hits = hybrid_search_knowledge("solar", db, limit=5)
        assert any("Solar" in h["text"] for h in hits)

    def test_store_and_count_vectors(self, db, work_with_items):
        work_id, doc_a, _ = work_with_items
        kid = db.create_knowledge_item(work_id, "fact", "Vector test item", source_doc_id=doc_a)
        db.store_vector(kid, "knowledge", pack_vector([1.0, 2.0]), 2)
        assert db.count_vectors("knowledge") == 1
        # replace, not duplicate
        db.store_vector(kid, "knowledge", pack_vector([3.0, 4.0]), 2)
        assert db.count_vectors("knowledge") == 1
