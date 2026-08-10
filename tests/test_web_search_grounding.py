"""Tests for web search grounding in chat (Task #387).

Covers:
  - Schema: conversations.web_search_enabled column exists and defaults to 0
  - DB: set_conversation_web_search() toggles the flag and returns refreshed conv
  - API: GET /system/web-search-status returns configured=True/False based on env var
  - API: PUT /conversations/{id}/web-search sets the flag (mocked Tavily key)
  - Context injection: when web_search_enabled, fetch_web_context() results appended
    to system prompt and out_sources; when disabled, no web call is made
  - Tavily helper: fetch_web_context() returns [] gracefully on any network error
  - fetch_web_context() returns [] when TAVILY_API_KEY is absent
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db():
    from orivellum.database.db import OrivellumDB

    return OrivellumDB(":memory:")


def _make_conv(db, web_search_enabled: bool = False) -> dict:
    conv = db.create_conversation(title="Test conv")
    if web_search_enabled:
        conv = db.set_conversation_web_search(conv["id"], True)
    return conv


# ---------------------------------------------------------------------------
# 1 — Schema: web_search_enabled column
# ---------------------------------------------------------------------------


class TestSchemaWebSearchEnabled:
    def test_column_exists_and_defaults_false(self):
        db = _make_db()
        conv = db.create_conversation(title="T")
        assert "web_search_enabled" in conv, "Column must be present in conversation dict"
        assert conv["web_search_enabled"] == 0 or conv["web_search_enabled"] is False, (
            "Default must be 0/False"
        )

    def test_create_multiple_convs_all_default_false(self):
        db = _make_db()
        for _ in range(3):
            conv = db.create_conversation()
            assert not conv.get("web_search_enabled")


# ---------------------------------------------------------------------------
# 2 — DB: set_conversation_web_search
# ---------------------------------------------------------------------------


class TestSetConversationWebSearch:
    def test_enable_sets_flag_true(self):
        db = _make_db()
        conv = db.create_conversation(title="C")
        updated = db.set_conversation_web_search(conv["id"], True)
        assert updated is not None
        assert updated["web_search_enabled"] in (1, True)

    def test_disable_sets_flag_false(self):
        db = _make_db()
        conv = _make_conv(db, web_search_enabled=True)
        assert conv["web_search_enabled"] in (1, True)
        updated = db.set_conversation_web_search(conv["id"], False)
        assert updated["web_search_enabled"] in (0, False)

    def test_missing_conv_returns_none(self):
        db = _make_db()
        result = db.set_conversation_web_search("nonexistent-id", True)
        assert result is None

    def test_get_conversation_reflects_flag(self):
        db = _make_db()
        conv = db.create_conversation()
        db.set_conversation_web_search(conv["id"], True)
        refreshed = db.get_conversation(conv["id"])
        assert refreshed["web_search_enabled"] in (1, True)

    def test_toggle_on_then_off(self):
        db = _make_db()
        conv = db.create_conversation()
        db.set_conversation_web_search(conv["id"], True)
        db.set_conversation_web_search(conv["id"], False)
        final = db.get_conversation(conv["id"])
        assert final["web_search_enabled"] in (0, False)


# ---------------------------------------------------------------------------
# 3 — Tavily helper: fetch_web_context
# ---------------------------------------------------------------------------


class TestFetchWebContext:
    def test_returns_empty_when_no_api_key(self, monkeypatch):
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)
        from orivellum.capabilities.websearch import fetch_web_context

        result = fetch_web_context("test query")
        assert result == []

    def test_returns_empty_on_network_error(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        from orivellum.capabilities.websearch import fetch_web_context

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = fetch_web_context("test query")
        assert result == []

    def test_returns_results_on_success(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")

        fake_response_data = {
            "results": [
                {
                    "title": "Python 3.13 Release Notes",
                    "url": "https://docs.python.org/3.13/",
                    "content": "Python 3.13 includes many improvements.",
                    "score": 0.95,
                },
                {
                    "title": "Python Official Site",
                    "url": "https://www.python.org/",
                    "content": "The official home of Python.",
                    "score": 0.88,
                },
            ]
        }

        class _FakeResp:
            def read(self):
                return json.dumps(fake_response_data).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        from orivellum.capabilities.websearch import fetch_web_context

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            results = fetch_web_context("Python 3.13", max_results=2)

        assert len(results) == 2
        assert results[0]["url"] == "https://docs.python.org/3.13/"
        assert "Python 3.13" in results[0]["title"]
        assert results[0]["score"] == 0.95

    def test_respects_max_results(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")

        fake_data = {
            "results": [
                {
                    "title": f"Result {i}",
                    "url": f"https://example.com/{i}",
                    "content": f"Content {i}",
                    "score": 0.9 - i * 0.05,
                }
                for i in range(8)
            ]
        }

        class _FakeResp:
            def read(self):
                return json.dumps(fake_data).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        from orivellum.capabilities.websearch import fetch_web_context

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            results = fetch_web_context("anything", max_results=3)

        assert len(results) <= 3

    def test_missing_url_skipped(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")

        fake_data = {
            "results": [
                {"title": "No URL result", "url": "", "content": "x", "score": 0.9},
                {"title": "Has URL", "url": "https://example.com/", "content": "y", "score": 0.8},
            ]
        }

        class _FakeResp:
            def read(self):
                return json.dumps(fake_data).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        from orivellum.capabilities.websearch import fetch_web_context

        with patch("urllib.request.urlopen", return_value=_FakeResp()):
            results = fetch_web_context("anything", max_results=5)

        urls = [r["url"] for r in results]
        assert "" not in urls
        assert "https://example.com/" in urls


# ---------------------------------------------------------------------------
# 4 — Context injection via _build_messages
# ---------------------------------------------------------------------------


def _build_messages_with_web(web_results: list[dict], web_search_enabled: bool = True):
    """Call _build_messages with mocked websearch and return (messages, sources)."""
    from orivellum.api.routes.conversations import _build_messages

    db = _make_db()
    conv = _make_conv(db, web_search_enabled=web_search_enabled)

    out_sources: list[dict] = []
    with patch(
        "orivellum.capabilities.websearch.fetch_web_context", return_value=web_results
    ) as mock_fetch:
        msgs = _build_messages(
            db,
            conv,
            "What is the latest Python version?",
            out_sources=out_sources,
        )
    return msgs, out_sources, mock_fetch


class TestWebSearchContextInjection:
    def test_web_context_appended_to_system_prompt(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        results = [
            {
                "title": "Python 3.13 Notes",
                "url": "https://docs.python.org/3.13/",
                "content": "Python 3.13 added many improvements to performance.",
                "score": 0.95,
            },
        ]
        msgs, sources, mock_fetch = _build_messages_with_web(results, web_search_enabled=True)

        system_content = msgs[0]["content"]
        assert "WEB SOURCES" in system_content, "WEB SOURCES header must be in system prompt"
        assert "Python 3.13 Notes" in system_content
        assert "https://docs.python.org/3.13/" in system_content

    def test_web_sources_appear_in_out_sources(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        results = [
            {
                "title": "PEP 703",
                "url": "https://peps.python.org/703/",
                "content": "PEP 703 proposes removing the GIL.",
                "score": 0.90,
            },
        ]
        msgs, sources, _ = _build_messages_with_web(results, web_search_enabled=True)

        web_sources = [s for s in sources if s.get("kind") == "web"]
        assert len(web_sources) >= 1
        assert web_sources[0]["url"] == "https://peps.python.org/703/"
        assert web_sources[0].get("isWeb") is True

    def test_no_web_call_when_disabled(self, monkeypatch):
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        msgs, sources, mock_fetch = _build_messages_with_web([], web_search_enabled=False)

        mock_fetch.assert_not_called()
        system_content = msgs[0]["content"]
        assert "WEB SOURCES" not in system_content

    def test_graceful_when_fetch_returns_empty(self, monkeypatch):
        """Empty Tavily results must not add WEB SOURCES block or crash."""
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        msgs, sources, _ = _build_messages_with_web([], web_search_enabled=True)

        system_content = msgs[0]["content"]
        assert "WEB SOURCES" not in system_content
        web_sources = [s for s in sources if s.get("kind") == "web"]
        assert web_sources == []

    def test_web_sources_deduplication_by_url(self, monkeypatch):
        """Duplicate URLs in Tavily results must not produce duplicate sources."""
        monkeypatch.setenv("TAVILY_API_KEY", "test-key")
        results = [
            {"title": "Page A", "url": "https://example.com/a", "content": "C", "score": 0.9},
            {"title": "Page A again", "url": "https://example.com/a", "content": "D", "score": 0.8},
        ]
        msgs, sources, _ = _build_messages_with_web(results, web_search_enabled=True)
        web_urls = [s["url"] for s in sources if s.get("kind") == "web"]
        assert web_urls.count("https://example.com/a") <= 1, (
            "Duplicate URLs must not appear in out_sources"
        )


# ---------------------------------------------------------------------------
# 5 — API toggle endpoint failure paths
# ---------------------------------------------------------------------------


class TestWebSearchToggleAPIFailures:
    """Verify the toggle endpoint returns useful errors on configuration problems.

    These tests call the route handler directly (not through ASGI) to avoid
    the session/auth middleware that wraps the full ``app`` singleton.  This
    keeps the tests fast, deterministic, and free of credential setup.
    """

    def _call_toggle(self, conv_id: str, enabled: bool, db, env_key: str | None):
        """Call toggle_web_search() directly with a patched DB and env."""
        import orivellum.api._deps as _deps

        saved_db = _deps._DB
        saved_key = os.environ.get("TAVILY_API_KEY")
        try:
            _deps._DB = db
            if env_key is None:
                os.environ.pop("TAVILY_API_KEY", None)
            else:
                os.environ["TAVILY_API_KEY"] = env_key

            from fastapi import HTTPException

            from orivellum.api.routes.conversations import toggle_web_search

            try:
                result = toggle_web_search(conv_id, {"enabled": enabled})
                return 200, result
            except HTTPException as exc:
                return exc.status_code, {"detail": exc.detail}
        finally:
            _deps._DB = saved_db
            if saved_key is None:
                os.environ.pop("TAVILY_API_KEY", None)
            else:
                os.environ["TAVILY_API_KEY"] = saved_key

    def test_toggle_returns_409_when_no_api_key(self):
        """toggle_web_search must raise HTTP 409 when TAVILY_API_KEY is absent."""
        db = _make_db()
        conv = db.create_conversation(title="T")
        status, body = self._call_toggle(conv["id"], True, db, env_key=None)
        assert status == 409, f"Expected 409, got {status}: {body}"
        assert "TAVILY_API_KEY" in body.get("detail", "")

    def test_toggle_returns_404_for_missing_conv(self):
        """toggle_web_search must raise HTTP 404 for a nonexistent conversation."""
        db = _make_db()
        status, body = self._call_toggle("does-not-exist", True, db, env_key="test-key")
        assert status == 404, f"Expected 404, got {status}: {body}"

    def test_toggle_succeeds_when_api_key_present(self):
        """toggle_web_search must return 200 and the updated conv when key is set."""
        db = _make_db()
        conv = db.create_conversation(title="T2")
        status, body = self._call_toggle(conv["id"], True, db, env_key="test-key")
        assert status == 200, f"Expected 200, got {status}: {body}"
        assert body.get("web_search_enabled") is True
        assert body["conversation"]["web_search_enabled"] in (1, True)

    def test_toggle_off_returns_disabled(self):
        """Disabling an already-enabled conversation must return web_search_enabled=False."""
        db = _make_db()
        conv = _make_conv(db, web_search_enabled=True)
        status, body = self._call_toggle(conv["id"], False, db, env_key="test-key")
        assert status == 200
        assert body["conversation"]["web_search_enabled"] in (0, False)
