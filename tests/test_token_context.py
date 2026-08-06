"""Tests for the token-aware chat context builder (task #375).

Covers:
  1. estimate_tokens() heuristic
  2. Knowledge injection stops at 30%-of-context_window token budget
  3. History trimming respects the 80%-of-context_window budget
  4. context_window loaded from YAML / env-var override via load_config()
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp: str):
    from orivellum.database.db import OrivellumDB
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.api import _deps

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return db, cfg


def _knowledge_hit(text: str, work_id: str = "w1") -> dict:
    return {
        "id": "k-" + text[:8],
        "text": text,
        "kind": "fact",
        "work_id": work_id,
        "source_doc_id": "d1",
        "review_status": "approved",
    }


# ---------------------------------------------------------------------------
# 1. estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_string(self):
        from orivellum.api.routes.conversations import estimate_tokens
        assert estimate_tokens("") == 0

    def test_heuristic(self):
        """4 characters → 1 token."""
        from orivellum.api.routes.conversations import estimate_tokens
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("abcdefgh") == 2

    def test_partial_chars(self):
        """Floor division: 5 chars → 1 token (not 1.25)."""
        from orivellum.api.routes.conversations import estimate_tokens
        assert estimate_tokens("abcde") == 1

    def test_large_text(self):
        from orivellum.api.routes.conversations import estimate_tokens
        text = "x" * 4000
        assert estimate_tokens(text) == 1000

    def test_never_negative(self):
        """Return value is always >= 0, even for empty input."""
        from orivellum.api.routes.conversations import estimate_tokens
        assert estimate_tokens("") >= 0


# ---------------------------------------------------------------------------
# 2. Knowledge injection token budget
# ---------------------------------------------------------------------------

class TestKnowledgeTokenBudget:
    def test_token_budget_stops_injection(self):
        """When knowledge items exceed 30% of context_window, injection stops."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            conv = db.create_conversation(title="Test")

            from orivellum.api.routes import conversations as C

            # 8 items of 100 chars each ≈ 25 tokens each → 200 total tokens
            # 30% of 256 window ≈ 76 tokens — only ~3 items should fit
            big_texts = [_knowledge_hit("A" * 100, "w1") for _ in range(8)]

            out_sources: list = []
            with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                       return_value=big_texts), \
                 patch("orivellum.capabilities.embeddings.hybrid_search_chunks",
                       return_value=[]):
                # Use a tiny context window so the token budget is hit quickly
                with patch.object(C.get_config().serving, "context_window", 256,
                                  create=False):
                    # get_config() is called each time — patch the returned obj
                    mock_cfg = type("C", (), {"serving": type("S", (), {
                        "context_window": 256,
                    })()})()
                    with patch.object(C, "get_config", return_value=mock_cfg):
                        prompt = C._build_system_prompt(
                            db, conv, user_query="test query",
                            out_sources=out_sources,
                        )

            # 30% of 256 = 76 tokens → 76 * 4 = 304 chars budget.
            # Each item is 100 chars = 25 tokens, so max 3 items (75 tokens ≤ 76).
            assert len(out_sources) <= 4, (
                f"Expected ≤4 sources but got {len(out_sources)} "
                "(token budget should have stopped injection earlier)"
            )
            db.close()

    def test_count_cap_still_applies(self):
        """_CONTEXT_KNOWLEDGE (12) is the count backstop even within budget."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            conv = db.create_conversation(title="Test")

            from orivellum.api.routes import conversations as C

            # 20 tiny items (1 char each ≈ 0 tokens) — all fit in any budget
            tiny_hits = [_knowledge_hit("X", "w1") for _ in range(20)]

            out_sources: list = []
            with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                       return_value=tiny_hits), \
                 patch("orivellum.capabilities.embeddings.hybrid_search_chunks",
                       return_value=[]):
                mock_cfg = type("C", (), {"serving": type("S", (), {
                    "context_window": 1_000_000,  # huge — budget never binding
                })()})()
                with patch.object(C, "get_config", return_value=mock_cfg):
                    C._build_system_prompt(
                        db, conv, user_query="test",
                        out_sources=out_sources,
                    )

            assert len(out_sources) <= C._CONTEXT_KNOWLEDGE, (
                f"Count cap not enforced: got {len(out_sources)} sources"
            )
            db.close()

    def test_trusted_filter_still_applied(self):
        """ai_auto items are excluded even if they fit in the token budget."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            conv = db.create_conversation(title="Test")

            from orivellum.api.routes import conversations as C

            untrusted = [
                {**_knowledge_hit("Secret", "w1"), "review_status": "ai_auto"},
            ]
            out_sources: list = []
            with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                       return_value=untrusted), \
                 patch("orivellum.capabilities.embeddings.hybrid_search_chunks",
                       return_value=[]):
                mock_cfg = type("C", (), {"serving": type("S", (), {
                    "context_window": 1_000_000,
                })()})()
                with patch.object(C, "get_config", return_value=mock_cfg):
                    C._build_system_prompt(
                        db, conv, user_query="test",
                        out_sources=out_sources,
                    )

            assert out_sources == [], "ai_auto items must not be injected"
            db.close()


# ---------------------------------------------------------------------------
# 3. History trimming (existing logic — regression guard)
# ---------------------------------------------------------------------------

class TestHistoryTrimming:
    def test_long_history_trimmed(self):
        """_build_messages trims history so estimated tokens fit in 80% of budget."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            conv = db.create_conversation(title="Long conv")

            # Add 30 messages of 200 chars each ≈ 50 tokens each → 1500 tokens
            for i in range(15):
                db.add_message(conv["id"], "user", "U" * 200)
                db.add_message(conv["id"], "assistant", "A" * 200)

            from orivellum.api.routes import conversations as C

            # Window=2000 tokens → 80% = 1600-token history budget.
            # System prompt (base persona + corpus guard) ≈ 200 tokens + 256 margin
            # leaves ≈ 1140 tokens for history.
            # Each message is 200 chars ≈ 50 tokens → only ~22 of 30 fit.
            # Total expected: system(1) + ~22 history + user(1) = ~24  <  32.
            mock_cfg = type("C", (), {"serving": type("S", (), {
                "context_window": 2000,
                "base_url": "http://localhost",
                "workhorse_model": "test",
                "vision_model": "",
            })()})()
            with patch.object(C, "get_config", return_value=mock_cfg), \
                 patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                       return_value=[]), \
                 patch("orivellum.capabilities.embeddings.hybrid_search_chunks",
                       return_value=[]):
                messages = C._build_messages(db, conv, "hello?")

            # system + trimmed history + final user turn = ~24 (< 32 = full set)
            assert len(messages) < 32, (
                f"Expected history to be trimmed; got {len(messages)} messages "
                f"(32 = system + all 30 history msgs + user turn = untrimmed)"
            )
            db.close()

    def test_short_history_unchanged(self):
        """Short conversations are not trimmed even with a moderate window."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            conv = db.create_conversation(title="Short conv")
            db.add_message(conv["id"], "user", "Hi")
            db.add_message(conv["id"], "assistant", "Hello!")

            from orivellum.api.routes import conversations as C

            with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                       return_value=[]), \
                 patch("orivellum.capabilities.embeddings.hybrid_search_chunks",
                       return_value=[]):
                messages = C._build_messages(db, conv, "How are you?")

            # system + 2 history + final user turn = 4 minimum
            assert len(messages) >= 4, "Short conversation should not be trimmed"
            db.close()


# ---------------------------------------------------------------------------
# 4. Config loading
# ---------------------------------------------------------------------------

class TestContextWindowConfig:
    def test_default_value(self):
        """Default context_window is 8192."""
        from orivellum.configuration.config import ServingConfig
        assert ServingConfig.context_window == 8192

    def test_yaml_override(self):
        """context_window in YAML is picked up by load_config."""
        with tempfile.TemporaryDirectory() as tmp:
            cfg_file = Path(tmp) / "config.yaml"
            cfg_file.write_text("serving:\n  context_window: 16384\n")

            from orivellum.configuration.config import load_config
            cfg = load_config(str(cfg_file))
            assert cfg.serving.context_window == 16384

    def test_env_var_override(self):
        """ORIVELLUM_CONTEXT_WINDOW env var overrides the default."""
        from orivellum.configuration.config import load_config
        with patch.dict(os.environ, {"ORIVELLUM_CONTEXT_WINDOW": "4096"}):
            cfg = load_config()
        assert cfg.serving.context_window == 4096


# ---------------------------------------------------------------------------
# 5. _get_effective_context_window — DB override takes priority
# ---------------------------------------------------------------------------

class TestGetEffectiveContextWindow:
    def test_db_override_takes_priority(self):
        """A valid DB-stored context_window beats the config value."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            db.set_setting("context_window", "4096", actor="user")

            from orivellum.api.routes.conversations import _get_effective_context_window
            result = _get_effective_context_window(db)
            assert result == 4096
            db.close()

    def test_falls_back_to_config_when_no_db_setting(self):
        """Without a DB setting, the config value is returned."""
        with tempfile.TemporaryDirectory() as tmp:
            db, cfg = _make_db(tmp)
            # No DB setting — use config default

            from orivellum.api.routes.conversations import _get_effective_context_window
            result = _get_effective_context_window(db)
            assert result == cfg.serving.context_window
            db.close()

    def test_invalid_db_value_falls_back_to_config(self):
        """A non-integer or sub-512 DB setting is ignored."""
        with tempfile.TemporaryDirectory() as tmp:
            db, cfg = _make_db(tmp)
            db.set_setting("context_window", "not-a-number", actor="user")

            from orivellum.api.routes.conversations import _get_effective_context_window
            result = _get_effective_context_window(db)
            assert result == cfg.serving.context_window
            db.close()

    def test_sub_512_db_value_falls_back_to_config(self):
        """A stored value below 512 tokens is treated as invalid."""
        with tempfile.TemporaryDirectory() as tmp:
            db, cfg = _make_db(tmp)
            db.set_setting("context_window", "256", actor="user")

            from orivellum.api.routes.conversations import _get_effective_context_window
            result = _get_effective_context_window(db)
            assert result == cfg.serving.context_window
            db.close()


# ---------------------------------------------------------------------------
# 6. End-to-end: DB override flows through to chat construction
# ---------------------------------------------------------------------------

class TestDbOverrideEndToEnd:
    def test_knowledge_budget_uses_db_override(self):
        """A DB-stored context_window shrinks the knowledge budget applied during
        _build_system_prompt — not just what GET /system/settings/context-window reports."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            conv = db.create_conversation(title="Test")

            # Store a very small window via the DB (simulating a PUT call)
            db.set_setting("context_window", "800", actor="user")
            # 30% of 800 = 240 tokens → 240 * 4 = 960 chars budget
            # Each item is 200 chars = 50 tokens → at most 4 items (200 tokens ≤ 240)

            items = [_knowledge_hit("B" * 200, "w1") for _ in range(10)]

            from orivellum.api.routes import conversations as C

            out_sources: list = []
            with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                       return_value=items), \
                 patch("orivellum.capabilities.embeddings.hybrid_search_chunks",
                       return_value=[]):
                C._build_system_prompt(
                    db, conv, user_query="test query",
                    out_sources=out_sources,
                )

            # 30% of 800 = 240 tokens; each item = 50 tokens → max 4 items fit
            assert len(out_sources) <= 5, (
                f"DB-stored context_window=800 should limit injection to ≤5 items; "
                f"got {len(out_sources)}"
            )
            db.close()

    def test_history_trimming_uses_db_override(self):
        """A DB-stored context_window is applied to history trimming in _build_messages."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            conv = db.create_conversation(title="History test")

            # Add 30 messages of 200 chars each
            for _ in range(15):
                db.add_message(conv["id"], "user", "U" * 200)
                db.add_message(conv["id"], "assistant", "A" * 200)

            # Store a 2000-token window in DB (same logic as TestHistoryTrimming)
            db.set_setting("context_window", "2000", actor="user")

            from orivellum.api.routes import conversations as C

            with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                       return_value=[]), \
                 patch("orivellum.capabilities.embeddings.hybrid_search_chunks",
                       return_value=[]):
                messages = C._build_messages(db, conv, "hello?")

            # Full set = system(1) + 30 history + user(1) = 32; trimming should
            # reduce history so total is less than 32.
            assert len(messages) < 32, (
                f"DB context_window=2000 should trigger trimming; "
                f"got {len(messages)} messages (32 = untrimmed)"
            )
            db.close()

    def test_default_used_when_no_db_setting(self):
        """Without a DB override, the config default still governs trimming."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            conv = db.create_conversation(title="Default test")
            # No DB setting — only 2 messages, should never be trimmed
            db.add_message(conv["id"], "user", "Hi")
            db.add_message(conv["id"], "assistant", "Hello!")

            from orivellum.api.routes import conversations as C

            with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                       return_value=[]), \
                 patch("orivellum.capabilities.embeddings.hybrid_search_chunks",
                       return_value=[]):
                messages = C._build_messages(db, conv, "How are you?")

            assert len(messages) >= 4  # system + 2 history + user
            db.close()


# ---------------------------------------------------------------------------
# 7. GET /system/settings/context-window validation consistency
# ---------------------------------------------------------------------------

class TestGetContextWindowEndpoint:
    def test_sub_512_db_value_not_reported_as_effective(self):
        """GET must not report a sub-512 stored value as effective —
        it should fall back to the config default, matching _get_effective_context_window."""
        with tempfile.TemporaryDirectory() as tmp:
            db, cfg = _make_db(tmp)
            # Store an invalid sub-512 value directly
            db.set_setting("context_window", "256", actor="user")

            from orivellum.api.routes.system import get_context_window_setting
            from orivellum.api import _deps
            _deps.init(db=db, cfg=cfg)

            result = get_context_window_setting()
            # stored should be None (invalid), effective should be config default
            assert result["stored"] is None, "sub-512 value must not be reported as stored"
            assert result["context_window"] == cfg.serving.context_window
            db.close()

    def test_non_integer_db_value_not_reported_as_effective(self):
        """GET falls back gracefully when the stored value is not an integer."""
        with tempfile.TemporaryDirectory() as tmp:
            db, cfg = _make_db(tmp)
            db.set_setting("context_window", "garbage", actor="user")

            from orivellum.api.routes.system import get_context_window_setting
            from orivellum.api import _deps
            _deps.init(db=db, cfg=cfg)

            result = get_context_window_setting()
            assert result["stored"] is None
            assert result["context_window"] == cfg.serving.context_window
            db.close()

    def test_valid_db_value_reported_as_effective(self):
        """A valid DB-stored value (≥512) is reported as both stored and effective."""
        with tempfile.TemporaryDirectory() as tmp:
            db, cfg = _make_db(tmp)
            db.set_setting("context_window", "16384", actor="user")

            from orivellum.api.routes.system import get_context_window_setting
            from orivellum.api import _deps
            _deps.init(db=db, cfg=cfg)

            result = get_context_window_setting()
            assert result["stored"] == 16384
            assert result["context_window"] == 16384
            assert result["config_default"] == cfg.serving.context_window
            db.close()


# ---------------------------------------------------------------------------
# 8. Continuation paths respect DB-stored context_window
# ---------------------------------------------------------------------------

class TestContinuationBudget:
    def test_trim_history_for_budget_drops_oldest_messages(self):
        """_trim_history_for_budget returns fewer messages when budget is tight."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            # Store a tiny window via DB
            db.set_setting("context_window", "800", actor="user")

            from orivellum.api.routes.conversations import _trim_history_for_budget

            # 20 messages at 200 chars each = 50 tokens each
            prior = [{"role": "user" if i % 2 == 0 else "assistant",
                      "text": "X" * 200, "id": str(i)} for i in range(20)]
            system_prompt = "You are a helpful assistant."  # ~7 tokens
            partial_text = "I was saying..."               # ~4 tokens

            trimmed = _trim_history_for_budget(prior, system_prompt, db,
                                               extra_text=partial_text)

            # Budget = 800*0.80 - 7 - 4 - 256 = 373 tokens
            # Each msg = 50 tokens → at most 7 messages fit
            assert len(trimmed) < len(prior), "history should be trimmed"
            assert len(trimmed) <= 8
            db.close()

    def test_trim_history_preserves_newest_messages(self):
        """The most recent messages survive trimming; oldest are dropped."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            db.set_setting("context_window", "800", actor="user")

            from orivellum.api.routes.conversations import _trim_history_for_budget

            prior = [{"role": "user", "text": f"msg-{i}", "id": str(i)}
                     for i in range(20)]
            trimmed = _trim_history_for_budget(prior, "sys", db)

            # The last message (msg-19) must be present
            assert trimmed[-1]["id"] == "19", "newest message must survive trimming"
            db.close()

    def test_trim_history_falls_through_on_exception(self):
        """If get_setting fails, _get_effective_context_window falls back to config
        and trimming proceeds normally — small history is preserved intact."""
        from orivellum.api.routes.conversations import _trim_history_for_budget

        class BrokenDB:
            def get_setting(self, *a, **kw):
                raise RuntimeError("DB unavailable")

        prior = [{"role": "user", "text": "Hello", "id": "1"}]
        result = _trim_history_for_budget(prior, "sys", BrokenDB())
        # DB error → _get_effective_context_window falls back to config default.
        # One tiny message fits easily, so content is returned unchanged.
        assert result == prior, "history content must be preserved when DB fails"


# ---------------------------------------------------------------------------
# 9. Oversized single-item truncation (task #417)
# ---------------------------------------------------------------------------

class TestOversizedItemTruncation:
    """When a single knowledge item exceeds the 30% knowledge budget it must be
    truncated to fit, not skipped.  Skipping causes an empty context which
    leads to silent 400 context-overflow errors on small context windows."""

    def test_single_oversized_item_is_truncated_not_skipped(self):
        """An item larger than the entire budget is truncated and injected."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            conv = db.create_conversation(title="Overflow test")

            from orivellum.api.routes import conversations as C

            # Context window = 512 tokens → budget = 153 tokens → 612 chars max.
            # Item is 2000 chars (500 tokens) — well over budget.
            giant_text = "G" * 2000
            oversized = [_knowledge_hit(giant_text, "w1")]

            out_sources: list = []
            with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                       return_value=oversized), \
                 patch("orivellum.capabilities.embeddings.hybrid_search_chunks",
                       return_value=[]):
                mock_cfg = type("C", (), {"serving": type("S", (), {
                    "context_window": 512,
                    "base_url": "http://localhost",
                    "workhorse_model": "test",
                })()})()
                with patch.object(C, "get_config", return_value=mock_cfg):
                    prompt = C._build_system_prompt(
                        db, conv, user_query="test query",
                        out_sources=out_sources,
                    )

            # The item must be injected (not skipped) — out_sources is populated
            assert len(out_sources) == 1, (
                "Oversized item must be truncated and injected, not skipped; "
                f"got {len(out_sources)} sources"
            )
            # The injected text must be shorter than the original
            injected_passage = out_sources[0]["passage"]
            assert len(injected_passage) < len(giant_text), (
                "Injected passage must be shorter than the original oversized text"
            )
            # And the prompt must contain part of the item text
            assert "G" * 10 in prompt, "Prompt must include content from the truncated item"
            db.close()

    def test_item_exactly_at_budget_is_accepted(self):
        """An item whose token count exactly equals the budget is accepted in full."""
        with tempfile.TemporaryDirectory() as tmp:
            db, _cfg = _make_db(tmp)
            conv = db.create_conversation(title="Exact budget test")

            from orivellum.api.routes import conversations as C
            from orivellum.api.routes.conversations import _CHARS_PER_TOKEN

            # Context window = 512 → budget = int(512 * 0.30) = 153 tokens
            # Item text = exactly 153 * 4 = 612 chars → exactly at budget
            _ctx = 512
            _budget_tokens = int(_ctx * 0.30)          # 153
            exact_chars = _budget_tokens * _CHARS_PER_TOKEN  # 612
            exact_text = "E" * exact_chars
            exact_item = [_knowledge_hit(exact_text, "w1")]

            out_sources: list = []
            with patch("orivellum.capabilities.embeddings.hybrid_search_knowledge",
                       return_value=exact_item), \
                 patch("orivellum.capabilities.embeddings.hybrid_search_chunks",
                       return_value=[]):
                mock_cfg = type("C", (), {"serving": type("S", (), {
                    "context_window": _ctx,
                    "base_url": "http://localhost",
                    "workhorse_model": "test",
                })()})()
                with patch.object(C, "get_config", return_value=mock_cfg):
                    C._build_system_prompt(
                        db, conv, user_query="test query",
                        out_sources=out_sources,
                    )

            assert len(out_sources) == 1, (
                f"Item exactly at budget ({_budget_tokens} tokens) must be accepted; "
                f"got {len(out_sources)} sources"
            )
            db.close()
