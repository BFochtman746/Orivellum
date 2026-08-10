"""No-pollution gate tests for the Universal Intake pipeline.

These tests enforce the core invariant:
    intake NEVER creates a Work for a non-CANON object.

The tests are entirely in-process — no HTTP, no live LLM, no real DB write.
They run fast and must stay green when anything in the intake pipeline changes.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_db(doc: dict) -> MagicMock:
    """Return a fake DB where get_document() returns *doc*."""
    db = MagicMock()
    db.get_document.return_value = doc
    db.get_work.return_value = None
    # _conn and _lock needed by the tier-persist path
    db._conn = MagicMock()
    db._conn.execute.return_value.fetchone.return_value = None
    db._conn.execute.return_value.fetchall.return_value = []
    db._lock = MagicMock()
    db._lock.__enter__ = MagicMock(return_value=None)
    db._lock.__exit__ = MagicMock(return_value=False)
    return db


def _make_cfg(data_dir: str = "/tmp") -> MagicMock:
    cfg = MagicMock()
    cfg.data_dir = data_dir
    cfg.llm = MagicMock()
    cfg.llm.model = "gpt-3.5-turbo"
    return cfg


def _run(doc: dict, *, research: bool = False):
    """Run intake on a fake document and return the profile."""
    from orivellum.capabilities.intake import run_intake

    db = _make_db(doc)
    cfg = _make_cfg()
    with patch("orivellum.capabilities.embeddings.embed_chunks_for_doc", return_value=0):
        profile = run_intake(doc["id"], db=db, cfg=cfg, research=research)
    return profile, db


# ── Test: ARTIFACT objects never get slot_book or create_work actions ──────────


class TestNoWorkPollution:
    def test_artifact_doc_has_no_slot_book_action(self):
        """ARTIFACT-tiered documents must never produce a slot_book action."""
        doc = {
            "id": "doc-artifact-001",
            "title": "A01_MIGRATION_BATCH_011",
            "kind": "docx",
            "tier": "artifact",
            "source": "A01_MIGRATION_BATCH_011.docx",
            "readiness": "ready",
            "work_id": None,
            "content_path": None,
            "word_count": 500,
        }
        profile, db = _run(doc)
        action_ids = {a.id for a in profile.suggested_actions}
        assert "slot_book" not in action_ids, (
            f"ARTIFACT doc produced a slot_book action: {action_ids}"
        )
        assert "create_work" not in action_ids
        assert profile.tier == "artifact"

    def test_system_doc_has_no_slot_book_action(self):
        """SYSTEM-tiered documents must never produce a slot_book action."""
        doc = {
            "id": "doc-system-001",
            "title": "package.json",
            "kind": "json",
            "tier": "system",
            "source": "package.json",
            "readiness": "ready",
            "work_id": None,
            "content_path": None,
            "word_count": 0,
        }
        profile, db = _run(doc)
        action_ids = {a.id for a in profile.suggested_actions}
        assert "slot_book" not in action_ids
        assert profile.tier == "system"

    def test_conversation_doc_has_no_slot_book_action(self):
        """CONVERSATION-tiered documents must never produce slot_book."""
        doc = {
            "id": "doc-conv-001",
            "title": "chat_export_2024.json",
            "kind": "json",
            "tier": "conversation",
            "source": "chat_export_2024.json",
            "readiness": "ready",
            "work_id": None,
            "content_path": None,
            "word_count": 200,
        }
        profile, db = _run(doc)
        action_ids = {a.id for a in profile.suggested_actions}
        assert "slot_book" not in action_ids
        assert profile.tier == "conversation"

    def test_source_doc_has_no_slot_book_action(self):
        """SOURCE-tiered documents must not produce slot_book (only CANON does)."""
        doc = {
            "id": "doc-source-001",
            "title": "research_paper.pdf",
            "kind": "pdf",
            "tier": "source",
            "source": "research_paper.pdf",
            "readiness": "ready",
            "work_id": None,
            "content_path": None,
            "word_count": 3000,
        }
        profile, db = _run(doc)
        action_ids = {a.id for a in profile.suggested_actions}
        assert "slot_book" not in action_ids
        assert profile.tier == "source"

    def test_canon_doc_has_slot_book_action(self):
        """CANON-tiered documents SHOULD produce a slot_book action."""
        doc = {
            "id": "doc-canon-001",
            "title": "Chapter 01 - The Beginning",
            "kind": "docx",
            "tier": "canon",
            "source": "chapter_01_the_beginning.docx",
            "readiness": "ready",
            "work_id": None,
            "content_path": None,
            "word_count": 8000,
        }
        profile, db = _run(doc)
        action_ids = {a.id for a in profile.suggested_actions}
        assert "slot_book" in action_ids, (
            f"CANON doc did not produce slot_book action; got: {action_ids}"
        )
        assert profile.tier == "canon"


# ── Test: classifier enforces ARTIFACT correctly ───────────────────────────────


class TestClassifierGate:
    def test_migration_batch_classified_artifact(self):
        """Migration batch filenames must be classified as ARTIFACT, not SOURCE."""
        from orivellum.capabilities.classify import Tier, classify_object

        result = classify_object("A01_MIGRATION_BATCH_011.docx", kind="docx")
        assert result.tier == Tier.ARTIFACT, f"Expected ARTIFACT, got {result.tier}"

    def test_run_script_classified_artifact(self):
        from orivellum.capabilities.classify import Tier, classify_object

        result = classify_object("Run-001_Core_Function_Test.docx", kind="docx")
        assert result.tier == Tier.ARTIFACT

    def test_chapter_classified_canon(self):
        from orivellum.capabilities.classify import Tier, classify_object

        result = classify_object("chapter_01_ash_and_silence.docx", kind="docx")
        assert result.tier == Tier.CANON

    def test_research_paper_classified_source(self):
        from orivellum.capabilities.classify import Tier, classify_object

        result = classify_object("narrative_theory_review.pdf", kind="pdf")
        assert result.tier == Tier.SOURCE

    def test_receipt_image_classified_source(self):
        from orivellum.capabilities.classify import Tier, classify_object

        result = classify_object("receipt_starbucks_2024.jpg", kind="image")
        assert result.tier == Tier.SOURCE


# ── Test: intake profile shape ─────────────────────────────────────────────────


class TestIntakeProfileShape:
    def test_profile_has_required_fields(self):

        doc = {
            "id": "doc-shape-001",
            "title": "some_document.pdf",
            "kind": "pdf",
            "tier": "source",
            "source": "some_document.pdf",
            "readiness": "ready",
            "work_id": None,
            "content_path": None,
            "word_count": 100,
        }
        profile, _ = _run(doc)
        assert profile.doc_id == "doc-shape-001"
        assert profile.tier in ("canon", "source", "artifact", "system", "conversation")
        assert 0.0 <= profile.confidence <= 1.0
        assert isinstance(profile.what_it_is, str) and profile.what_it_is
        assert isinstance(profile.summary, str)
        assert isinstance(profile.suggested_actions, list)
        assert len(profile.suggested_actions) > 0
        # chat action is always present
        assert any(a.id == "chat" for a in profile.suggested_actions)

    def test_missing_doc_returns_error_profile(self):
        """Requesting intake for a non-existent doc returns an error profile, not an exception."""
        from orivellum.capabilities.intake import run_intake

        db = _make_db(None)  # get_document returns None
        db.get_document.return_value = None
        cfg = _make_cfg()
        profile = run_intake("nonexistent-doc-id", db=db, cfg=cfg)
        assert profile.error is not None
        assert "not found" in profile.error.lower()

    def test_receipt_image_gets_file_taxes_action(self):
        doc = {
            "id": "doc-receipt-001",
            "title": "receipt_starbucks_jan2024.jpg",
            "kind": "image",
            "tier": "source",
            "source": "receipt_starbucks_jan2024.jpg",
            "readiness": "ready",
            "work_id": None,
            "content_path": None,
            "word_count": 0,
        }
        profile, _ = _run(doc)
        action_ids = {a.id for a in profile.suggested_actions}
        assert "file_taxes" in action_ids, f"Receipt did not get file_taxes; got {action_ids}"

    def test_whiteboard_image_gets_extract_actions(self):
        doc = {
            "id": "doc-whiteboard-001",
            "title": "whiteboard_planning_session.png",
            "kind": "image",
            "tier": "source",
            "source": "whiteboard_planning_session.png",
            "readiness": "ready",
            "work_id": None,
            "content_path": None,
            "word_count": 0,
        }
        profile, _ = _run(doc)
        action_ids = {a.id for a in profile.suggested_actions}
        assert "extract_actions" in action_ids, (
            f"Whiteboard did not get extract_actions; got {action_ids}"
        )

    def test_research_not_run_by_default(self):
        """Stage 4 (research) must not run unless research=True is passed."""
        doc = {
            "id": "doc-src-002",
            "title": "research_paper_2.pdf",
            "kind": "pdf",
            "tier": "source",
            "source": "research_paper_2.pdf",
            "readiness": "ready",
            "work_id": None,
            "content_path": None,
            "word_count": 5000,
        }
        profile, _ = _run(doc, research=False)
        assert profile.research_summary is None
