"""Tests for context_doc_ids pinning in _build_system_prompt / _build_messages.

Covers:
  - Non-streaming path passes context_doc_ids to _build_messages.
  - Streaming path signature accepts context_doc_ids (static check).
  - Pinned docs from the correct Work appear in the built prompt.
  - Pinned docs from a different Work are rejected when scope="work".
  - Pinned docs from any Work are accepted when scope="all".
  - Non-existent / fabricated doc IDs are silently ignored.
  - The 5-document cap is enforced.
  - The 2 000-character per-doc truncation is enforced.
"""
import inspect
import pytest

from orivellum.api.routes.conversations import (
    _build_system_prompt,
    _build_messages,
    _stream_response,
    MessageSend,
)


# ── Minimal in-memory DB stub ─────────────────────────────────────────────────

class _Lock:
    def __enter__(self): return self
    def __exit__(self, *a): pass


class _FakeDB:
    """Minimal stub that _build_system_prompt can call without a real DB."""

    def __init__(self, docs: dict, work_id: str | None = None):
        """docs: {doc_id: {title, extracted_text, work_id}} mapping."""
        self._docs = docs
        self._work_id = work_id
        self._lock = _Lock()
        self._conn = _FakeConn(docs)

    # ── Methods called by _build_system_prompt ──────────────────────────────
    def get_work(self, wid):
        if wid and wid == self._work_id:
            return {"id": wid, "title": "Test Work"}
        return None

    def get_active_prompt(self, slot):
        return None  # fall back to hardcoded constant

    def get_setting(self, key, default=""):
        return default

    def get_user_profile(self):
        return {}

    def list_knowledge(self, work_id=None, limit=100):
        return []

    def search_knowledge(self, *a, **kw):
        return []

    def search_chunks(self, *a, **kw):
        return []

    def get_work_summary(self, *a, **kw):
        return None


class _FakeRow(dict):
    def __getattr__(self, name):
        return self[name]

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    """Only handles the single-row SELECT used for pinned-doc lookup."""

    def __init__(self, docs: dict):
        self._docs = docs

    def execute(self, sql: str, params: tuple = ()):
        # Match the pinned-doc lookup:
        # SELECT title, extracted_text, work_id FROM documents WHERE id = ?
        if "FROM documents WHERE id" in sql and len(params) == 1:
            doc_id = params[0]
            doc = self._docs.get(doc_id)
            if doc is None:
                return _FakeCursor(None)
            row = _FakeRow({
                "title": doc.get("title", ""),
                "extracted_text": doc.get("extracted_text", ""),
                "work_id": doc.get("work_id"),
            })
            return _FakeCursor(row)
        # Any other query → empty cursor
        return _FakeCursor(None)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_conv(work_id: str | None = None):
    return {
        "id": "conv-1",
        "work_id": work_id,
        "persona_id": "default",
        "web_search_enabled": False,
        "model": None,
    }


def _build(docs, conv_work_id, context_doc_ids, scope="work"):
    db = _FakeDB(docs, work_id=conv_work_id)
    conv = _make_conv(work_id=conv_work_id)
    return _build_system_prompt(
        db, conv,
        scope=scope,
        user_query=None,
        context_doc_ids=context_doc_ids,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestContextDocIds:

    def test_pinned_doc_same_work_appears_in_prompt(self):
        """A doc from the linked Work should be injected into the prompt."""
        docs = {
            "doc-1": {
                "title": "My Chapter",
                "extracted_text": "Once upon a time there were three little pigs.",
                "work_id": "work-A",
            }
        }
        prompt = _build(docs, "work-A", ["doc-1"])
        assert "PINNED DOCUMENT" in prompt
        assert "My Chapter" in prompt
        assert "three little pigs" in prompt

    def test_cross_work_doc_rejected_in_work_scope(self):
        """A doc from a different Work must be dropped when scope='work'."""
        docs = {
            "doc-evil": {
                "title": "Secret Chapter",
                "extracted_text": "Top secret content from another work.",
                "work_id": "work-OTHER",  # different from conversation's work
            }
        }
        prompt = _build(docs, "work-A", ["doc-evil"], scope="work")
        assert "Secret Chapter" not in prompt
        assert "Top secret content" not in prompt

    def test_cross_work_doc_accepted_in_all_scope(self):
        """When scope='all', docs from any Work should pass the boundary check."""
        docs = {
            "doc-other": {
                "title": "Other Work Doc",
                "extracted_text": "Cross-work content that is allowed in all-scope.",
                "work_id": "work-OTHER",
            }
        }
        prompt = _build(docs, "work-A", ["doc-other"], scope="all")
        assert "Other Work Doc" in prompt
        assert "Cross-work content" in prompt

    def test_nonexistent_doc_id_is_ignored(self):
        """A fabricated / non-existent doc ID must produce no output, no crash."""
        docs = {}  # empty — no real docs
        prompt = _build(docs, "work-A", ["fake-id-123"])
        assert "PINNED DOCUMENT" not in prompt
        assert "fake-id-123" not in prompt

    def test_five_doc_cap_enforced(self):
        """Only the first 5 docs in the list may be injected."""
        docs = {
            f"doc-{i}": {
                "title": f"Doc {i}",
                "extracted_text": f"Content number {i}",
                "work_id": "work-A",
            }
            for i in range(8)  # 8 docs supplied, only 5 should appear
        }
        prompt = _build(docs, "work-A", [f"doc-{i}" for i in range(8)], scope="work")
        # Docs 0–4 must appear; docs 5–7 must not
        for i in range(5):
            assert f"Doc {i}" in prompt, f"Expected Doc {i} to appear in prompt"
        for i in range(5, 8):
            assert f"Doc {i}" not in prompt, f"Doc {i} should be beyond the 5-doc cap"

    def test_two_thousand_char_truncation(self):
        """Each pinned doc's text is capped at 2 000 characters."""
        long_text = "A" * 5000
        docs = {
            "doc-long": {
                "title": "Long Doc",
                "extracted_text": long_text,
                "work_id": "work-A",
            }
        }
        prompt = _build(docs, "work-A", ["doc-long"])
        # 2 000 × 'A' appears; 2 001st 'A' does not
        assert "A" * 2000 in prompt
        assert "A" * 2001 not in prompt

    def test_empty_context_doc_ids_no_pinned_block(self):
        """An empty list produces no PINNED DOCUMENT block."""
        docs = {
            "doc-1": {
                "title": "Real Doc",
                "extracted_text": "Real content.",
                "work_id": "work-A",
            }
        }
        prompt = _build(docs, "work-A", [])
        assert "PINNED DOCUMENT" not in prompt

    def test_none_context_doc_ids_no_pinned_block(self):
        """None is treated as 'no pinned docs'."""
        docs = {
            "doc-1": {
                "title": "Real Doc",
                "extracted_text": "Real content.",
                "work_id": "work-A",
            }
        }
        prompt = _build(docs, "work-A", None)
        assert "PINNED DOCUMENT" not in prompt


class TestBoundaryEdgeCases:
    """Additional boundary cases flagged by security review."""

    def test_unlinked_conv_work_scope_rejects_all_pins(self):
        """With scope='work' and no linked work, ALL pins must be rejected."""
        docs = {
            "doc-1": {
                "title": "Doc in some work",
                "extracted_text": "Should never appear.",
                "work_id": "work-B",
            }
        }
        # conv has no work_id (unlinked conversation)
        db = _FakeDB(docs, work_id=None)
        conv = _make_conv(work_id=None)
        prompt = _build_system_prompt(
            db, conv, scope="work", user_query=None, context_doc_ids=["doc-1"]
        )
        assert "Should never appear" not in prompt
        assert "PINNED DOCUMENT" not in prompt

    def test_invalid_scope_treated_as_work(self):
        """An arbitrary scope value falls back to 'work' (safest default)."""
        docs = {
            "doc-evil": {
                "title": "Evil Doc",
                "extracted_text": "Content from another work.",
                "work_id": "work-OTHER",
            }
        }
        # scope='admin' is not a valid value — must behave like 'work'
        prompt = _build(docs, "work-A", ["doc-evil"], scope="admin")
        assert "Content from another work" not in prompt

    def test_mixed_valid_invalid_pins_only_valid_appear(self):
        """With mixed valid/invalid doc IDs, only valid same-work docs appear."""
        docs = {
            "doc-good": {
                "title": "Good Doc",
                "extracted_text": "Valid content from correct work.",
                "work_id": "work-A",
            },
            "doc-bad": {
                "title": "Bad Doc",
                "extracted_text": "Content from wrong work.",
                "work_id": "work-WRONG",
            },
        }
        prompt = _build(
            docs, "work-A", ["doc-good", "doc-bad", "nonexistent-id"], scope="work"
        )
        assert "Valid content from correct work" in prompt
        assert "Content from wrong work" not in prompt
        assert "nonexistent-id" not in prompt

    def test_scope_all_unlinked_conv_accepts_any_work_doc(self):
        """scope='all' on an unlinked conversation should still accept real docs."""
        docs = {
            "doc-any": {
                "title": "Any Work Doc",
                "extracted_text": "Content that should appear under all scope.",
                "work_id": "work-ANYWHERE",
            }
        }
        db = _FakeDB(docs, work_id=None)
        conv = _make_conv(work_id=None)
        prompt = _build_system_prompt(
            db, conv, scope="all", user_query=None, context_doc_ids=["doc-any"]
        )
        assert "Content that should appear under all scope" in prompt


class TestStreamSignatureIncludesContextDocIds:
    """Static checks that both the streaming and non-streaming paths accept
    context_doc_ids so the API surface is consistent."""

    def test_stream_response_has_context_doc_ids_param(self):
        sig = inspect.signature(_stream_response)
        assert "context_doc_ids" in sig.parameters, (
            "_stream_response must accept context_doc_ids so the streaming path "
            "can forward pinned IDs to _build_messages"
        )

    def test_build_messages_has_context_doc_ids_param(self):
        sig = inspect.signature(_build_messages)
        assert "context_doc_ids" in sig.parameters, (
            "_build_messages must accept context_doc_ids"
        )

    def test_message_send_model_has_context_doc_ids_field(self):
        """MessageSend Pydantic model must declare context_doc_ids."""
        fields = MessageSend.model_fields
        assert "context_doc_ids" in fields, (
            "MessageSend must declare context_doc_ids so clients can send it"
        )

    def test_message_send_context_doc_ids_defaults_to_none(self):
        """When omitted, context_doc_ids defaults to None (not required)."""
        msg = MessageSend(text="hello")
        assert msg.context_doc_ids is None
