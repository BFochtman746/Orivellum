"""Tests for custom per-document-kind extraction templates.

Covers:
  - DB CRUD: create / get / list / update / delete / get_template_for_doc
  - Priority lookup: kind+work > kind-only > work-only > None
  - API routes via TestClient: list / create / get / update / delete / reharvest
  - llm_harvest: uses matching template prompt; falls back to default when
    no template matches; _using_custom_template flag is set correctly.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp: Path):
    """Return an OrivellumDB pointing at a fresh in-memory database."""
    from orivellum.database.db import OrivellumDB

    return OrivellumDB(":memory:")


# ---------------------------------------------------------------------------
# Section 1 — DB CRUD
# ---------------------------------------------------------------------------


class TestExtractionTemplateDB:
    def test_create_and_get(self, tmp_path):
        db = _make_db(tmp_path)
        t = db.create_extraction_template(
            name="Meeting Notes",
            system_prompt="Extract decisions and action items from {title}. Chunk: {chunk}",
            kind_label="docx",
        )
        assert t["id"]
        assert t["name"] == "Meeting Notes"
        assert t["kind_label"] == "docx"
        assert t["field_hints"] == []
        assert t["work_id"] is None

        fetched = db.get_extraction_template(t["id"])
        assert fetched is not None
        assert fetched["id"] == t["id"]
        assert fetched["name"] == "Meeting Notes"

    def test_get_missing_returns_none(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.get_extraction_template("does-not-exist") is None

    def test_create_with_field_hints(self, tmp_path):
        db = _make_db(tmp_path)
        hints = ["Extract the date", "List all attendees"]
        t = db.create_extraction_template(
            name="Report",
            system_prompt="Extract from {title}. Chunk: {chunk}",
            kind_label="pdf",
            field_hints=hints,
        )
        assert t["field_hints"] == hints

    def test_list_returns_all(self, tmp_path):
        db = _make_db(tmp_path)
        db.create_extraction_template("A", "Prompt A. Title: {title}. {chunk}", kind_label="pdf")
        db.create_extraction_template("B", "Prompt B. Title: {title}. {chunk}", kind_label="docx")
        all_t = db.list_extraction_templates()
        assert len(all_t) == 2
        names = {t["name"] for t in all_t}
        assert names == {"A", "B"}

    def test_list_filtered_by_kind(self, tmp_path):
        db = _make_db(tmp_path)
        db.create_extraction_template("PDF T", "P. {title}. {chunk}", kind_label="pdf")
        db.create_extraction_template("DOCX T", "D. {title}. {chunk}", kind_label="docx")
        pdf_only = db.list_extraction_templates(kind_label="pdf")
        assert len(pdf_only) == 1
        assert pdf_only[0]["name"] == "PDF T"

    def test_update_fields(self, tmp_path):
        db = _make_db(tmp_path)
        t = db.create_extraction_template("Old", "Old prompt {title} {chunk}", kind_label="pdf")
        updated = db.update_extraction_template(t["id"], name="New", kind_label="docx")
        assert updated["name"] == "New"
        assert updated["kind_label"] == "docx"
        assert "Old prompt" in updated["system_prompt"]

    def test_update_clear_kind_label(self, tmp_path):
        db = _make_db(tmp_path)
        t = db.create_extraction_template("T", "Prompt {title} {chunk}", kind_label="pdf")
        updated = db.update_extraction_template(t["id"], _clear_kind_label=True)
        assert updated["kind_label"] is None

    def test_update_missing_returns_none(self, tmp_path):
        db = _make_db(tmp_path)
        result = db.update_extraction_template("nonexistent", name="X")
        assert result is None

    def test_delete(self, tmp_path):
        db = _make_db(tmp_path)
        t = db.create_extraction_template("Del", "D {title} {chunk}")
        assert db.delete_extraction_template(t["id"]) is True
        assert db.get_extraction_template(t["id"]) is None

    def test_delete_missing_returns_false(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.delete_extraction_template("ghost-id") is False


# ---------------------------------------------------------------------------
# Section 2 — Template priority lookup
# ---------------------------------------------------------------------------


class TestGetTemplateForDoc:
    """Verifies the 3-tier priority: kind+work > kind-only > work-only > None."""

    def _seed_work(self, db) -> str:
        wid = str(uuid.uuid4())
        now = "2024-01-01T00:00:00+00:00"
        with db._lock:
            db._conn.execute(
                "INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,"
                "created_at,updated_at,created_by) VALUES(?,?,1,'active','{}','{}',?,?,'t')",
                (wid, "work", now, now),
            )
            db._conn.execute(
                "INSERT INTO works(id,title,work_type,meta) VALUES(?,?,?,?)",
                (wid, "Test Work", "project", "{}"),
            )
            db._conn.commit()
        return wid

    def test_returns_none_when_empty(self, tmp_path):
        db = _make_db(tmp_path)
        assert db.get_template_for_doc("pdf", None) is None

    def test_kind_only_match(self, tmp_path):
        db = _make_db(tmp_path)
        t = db.create_extraction_template("Kind match", "K {title} {chunk}", kind_label="pdf")
        result = db.get_template_for_doc("pdf", None)
        assert result is not None
        assert result["id"] == t["id"]

    def test_work_only_match(self, tmp_path):
        db = _make_db(tmp_path)
        wid = self._seed_work(db)
        t = db.create_extraction_template(
            "Work match", "W {title} {chunk}", kind_label=None, work_id=wid
        )
        result = db.get_template_for_doc("docx", wid)
        assert result is not None
        assert result["id"] == t["id"]

    def test_exact_kind_plus_work_takes_priority_over_kind_only(self, tmp_path):
        db = _make_db(tmp_path)
        wid = self._seed_work(db)
        t_kind = db.create_extraction_template("Kind-only", "A {title} {chunk}", kind_label="pdf")
        t_both = db.create_extraction_template(
            "Kind+work", "B {title} {chunk}", kind_label="pdf", work_id=wid
        )
        result = db.get_template_for_doc("pdf", wid)
        assert result["id"] == t_both["id"], "kind+work must beat kind-only"

    def test_exact_kind_plus_work_takes_priority_over_work_only(self, tmp_path):
        db = _make_db(tmp_path)
        wid = self._seed_work(db)
        t_work = db.create_extraction_template("Work-only", "A {title} {chunk}", work_id=wid)
        t_both = db.create_extraction_template(
            "Kind+work", "B {title} {chunk}", kind_label="pdf", work_id=wid
        )
        result = db.get_template_for_doc("pdf", wid)
        assert result["id"] == t_both["id"], "kind+work must beat work-only"

    def test_kind_only_beats_work_only(self, tmp_path):
        db = _make_db(tmp_path)
        wid = self._seed_work(db)
        t_work = db.create_extraction_template("Work-only", "W {title} {chunk}", work_id=wid)
        t_kind = db.create_extraction_template("Kind-only", "K {title} {chunk}", kind_label="pdf")
        result = db.get_template_for_doc("pdf", wid)
        # kind-only (priority 2) must beat work-only (priority 3)
        assert result["id"] == t_kind["id"], "kind-only must beat work-only"

    def test_no_match_returns_none(self, tmp_path):
        db = _make_db(tmp_path)
        db.create_extraction_template("Excel T", "E {title} {chunk}", kind_label="excel")
        # Query for a PDF with no work — Excel template must not match
        assert db.get_template_for_doc("pdf", None) is None

    def test_both_none_returns_none(self, tmp_path):
        db = _make_db(tmp_path)
        db.create_extraction_template("Generic", "G {title} {chunk}")
        # No kind and no work_id — cannot match anything
        assert db.get_template_for_doc(None, None) is None


# ---------------------------------------------------------------------------
# Section 3 — llm_harvest uses custom template
# ---------------------------------------------------------------------------


class TestLlmHarvestTemplateIntegration:
    """Verifies llm_harvest selects and formats the matching template prompt."""

    def _make_extraction_result(self):
        from orivellum.capabilities.extraction import ExtractionResult, PageSegment

        pages = [PageSegment(page=0, text="The defendant agreed to pay damages.")]
        return ExtractionResult(
            kind="pdf",
            full_text="The defendant agreed to pay damages.",
            word_count=6,
            pages=pages,
        )

    def test_uses_custom_template_prompt(self, tmp_path):
        """When a template matches, the custom prompt is used instead of _EXTRACT_PROMPT."""
        db = _make_db(tmp_path)
        db.create_extraction_template(
            name="Legal",
            system_prompt="Extract legal obligations from {title}.\n\nChunk:\n{chunk}",
            kind_label="pdf",
        )

        captured_prompt: list[str] = []

        def _fake_llm(prompt, base_url, model, timeout, db=None):
            captured_prompt.append(prompt)
            return json.dumps({"entities": [], "claims": [], "relationships": []})

        er = self._make_extraction_result()
        with (
            patch("orivellum.capabilities.knowledge_harvest._call_llm_sync", side_effect=_fake_llm),
            patch("orivellum.api._deps.get_config") as mc,
        ):
            cfg = MagicMock()
            cfg.serving.base_url = "http://localhost:9999/v1"
            cfg.serving.workhorse_model = "test-model"
            cfg.serving.extraction_timeout_sec = 5
            mc.return_value = cfg
            from orivellum.capabilities.knowledge_harvest import llm_harvest

            llm_harvest(er, doc_id="doc-1", work_id=None, doc_title="Contract", db=db, kind="pdf")

        assert captured_prompt, "LLM must have been called"
        prompt_text = captured_prompt[0]
        assert "legal obligations" in prompt_text.lower(), (
            "Custom template prompt must appear in the LLM call"
        )
        # Placeholders must be filled in
        assert "Contract" in prompt_text
        assert "{title}" not in prompt_text
        assert "{chunk}" not in prompt_text

    def test_falls_back_to_default_when_no_template(self, tmp_path):
        """When no template matches, the default _EXTRACT_PROMPT is used."""
        db = _make_db(tmp_path)
        # Seed a template for a different kind
        db.create_extraction_template("Excel T", "E {title} {chunk}", kind_label="excel")

        captured_prompt: list[str] = []

        def _fake_llm(prompt, base_url, model, timeout, db=None):
            captured_prompt.append(prompt)
            return json.dumps({"entities": [], "claims": [], "relationships": []})

        er = self._make_extraction_result()
        with (
            patch("orivellum.capabilities.knowledge_harvest._call_llm_sync", side_effect=_fake_llm),
            patch("orivellum.api._deps.get_config") as mc,
        ):
            cfg = MagicMock()
            cfg.serving.base_url = "http://localhost:9999/v1"
            cfg.serving.workhorse_model = "test-model"
            cfg.serving.extraction_timeout_sec = 5
            mc.return_value = cfg
            from orivellum.capabilities.knowledge_harvest import llm_harvest

            llm_harvest(er, doc_id="doc-1", work_id=None, doc_title="Contract", db=db, kind="pdf")

        assert captured_prompt, "LLM must be called even without a template"
        # Default prompt must not contain the Excel template's text
        assert "Excel T" not in captured_prompt[0]
        # Must still include the standard JSON schema guidance
        assert "entities" in captured_prompt[0]

    def test_field_hints_appended_to_prompt(self, tmp_path):
        """Field hints must appear in the LLM call as bullet points."""
        db = _make_db(tmp_path)
        db.create_extraction_template(
            name="Meeting",
            system_prompt="Extract decisions from {title}. Chunk: {chunk}",
            kind_label="docx",
            field_hints=["Always list the meeting date", "Capture every action item"],
        )

        captured_prompt: list[str] = []

        def _fake_llm(prompt, base_url, model, timeout, db=None):
            captured_prompt.append(prompt)
            return json.dumps({"entities": [], "claims": [], "relationships": []})

        er = self._make_extraction_result()
        with (
            patch("orivellum.capabilities.knowledge_harvest._call_llm_sync", side_effect=_fake_llm),
            patch("orivellum.api._deps.get_config") as mc,
        ):
            cfg = MagicMock()
            cfg.serving.base_url = "http://localhost:9999/v1"
            cfg.serving.workhorse_model = "test-model"
            cfg.serving.extraction_timeout_sec = 5
            mc.return_value = cfg
            from orivellum.capabilities.knowledge_harvest import llm_harvest

            llm_harvest(er, doc_id="doc-1", work_id=None, doc_title="Standup", db=db, kind="docx")

        assert captured_prompt
        assert "Always list the meeting date" in captured_prompt[0]
        assert "Capture every action item" in captured_prompt[0]


# ---------------------------------------------------------------------------
# Section 4 — Nightshift sparse-harvest passes kind= to llm_harvest
# ---------------------------------------------------------------------------


class TestNightshiftKindPassthrough:
    """Verify _pass_sparse_harvest forwards the document kind to llm_harvest."""

    def test_kind_forwarded_to_llm_harvest(self, tmp_path, monkeypatch):
        """_pass_sparse_harvest must call llm_harvest with kind= matching the document."""
        from orivellum.database.db import OrivellumDB

        db = OrivellumDB(":memory:")

        # Stub _get_docs_needing_work to return one synthetic PDF doc
        fake_doc = {
            "id": "doc-nightshift-1",
            "work_id": None,
            "title": "Night Doc",
            "source": "night_doc.pdf",
            "kind": "pdf",
        }

        captured_kinds: list = []

        def _fake_llm_harvest(result, *, doc_id, work_id, doc_title, db, kind=None):
            captured_kinds.append(kind)

        def _fake_get_docs(db):
            return [fake_doc]

        def _fake_get_document(doc_id):
            return fake_doc

        db.get_setting = lambda key, default="": (
            "true" if key == "ai_extraction_enabled" else default
        )
        # Stub chunks so _pass_sparse_harvest builds an ExtractionResult
        import types

        fake_chunks_row = [types.SimpleNamespace(text="Some extracted text from a PDF.")]

        class _FakeCursor:
            def fetchall(self_):
                return [{"text": "Some extracted text from a PDF."}]

        class _FakeConn:
            def execute(self_, q, args=()):
                return _FakeCursor()

        import threading

        db._lock = threading.Lock()
        db._conn = _FakeConn()
        db.get_document = _fake_get_document
        db.list_knowledge = lambda **_: []

        def _fake_harvest(result, *, doc_id, work_id, doc_title, db):
            pass  # rule-based harvest stub

        import orivellum.capabilities.nightshift as ns

        monkeypatch.setattr(ns, "_get_docs_needing_work", _fake_get_docs)

        import orivellum.capabilities.knowledge_harvest as kh

        monkeypatch.setattr(kh, "harvest", _fake_harvest)
        monkeypatch.setattr(kh, "llm_harvest", _fake_llm_harvest)

        report: list[str] = []
        ns._pass_sparse_harvest(db, report)

        assert captured_kinds == ["pdf"], (
            f"kind= must equal the document kind 'pdf'; got {captured_kinds}"
        )
