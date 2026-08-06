"""
Tests: sliding-window context summariser in conversations.py

Verified behaviours:
  1. Threshold: _maybe_summarize writes context_summary once the conversation
     is long enough for the verbatim window to leave messages uncovered
     (requires total >= _HISTORY_LIMIT + 4 = 44 messages so verbatim_start >= 4).
     Stays silent for short conversations.
  2. Injection: _build_messages prepends the [EARLIER CONVERSATION SUMMARY]
     block to the system message content when context_summary is set on the
     conversation record.
  3. Fact preservation: a fact stated in an early message (message #0) that
     falls outside the verbatim window appears in the batch passed to
     _summarize_early_context — confirming it cannot be silently lost.
"""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from orivellum.database.db import OrivellumDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> OrivellumDB:
    """Return a fresh in-memory OrivellumDB with all migrations applied."""
    return OrivellumDB(":memory:")


def _seed_messages(db: OrivellumDB, conv_id: str, n_pairs: int) -> list[str]:
    """Insert n_pairs of user+assistant messages; return all message IDs."""
    ids: list[str] = []
    for i in range(n_pairs):
        u = db.add_message(conv_id, "user",
                           f"User message {i}: tell me about topic {i}.")
        ids.append(u["id"])
        a = db.add_message(conv_id, "assistant",
                           f"Assistant reply {i}: here is information on topic {i}.")
        ids.append(a["id"])
    return ids


# ---------------------------------------------------------------------------
# Constants mirrored from conversations.py (for clarity in comments only)
# ---------------------------------------------------------------------------
#   _SUMMARIZE_PAIR_THRESHOLD = 15  → need total >= 30 for the count check
#   _HISTORY_LIMIT             = 40 → verbatim_start = max(0, total - 40)
#   Need verbatim_start >= 4        → total >= 44 before a batch is processed


# ---------------------------------------------------------------------------
# Test 1 — threshold trigger
# ---------------------------------------------------------------------------

def test_maybe_summarize_triggers_when_conversation_is_long_enough():
    """_maybe_summarize writes context_summary once the conversation has
    enough messages for some to fall outside the verbatim window.

    With _HISTORY_LIMIT=40, total >= 44 gives verbatim_start >= 4 which is
    the real prerequisite for the first batch to be processed.
    """
    from orivellum.api.routes.conversations import _maybe_summarize

    db = _make_db()
    conv = db.create_conversation(title="Long conv")
    conv_id = conv["id"]

    # 22 pairs (44 messages) — verbatim_start = 44 - 40 = 4, exactly at threshold
    _seed_messages(db, conv_id, 22)

    fake_summary = "The user explored topics 0 through 21 in early exchanges."

    with patch(
        "orivellum.api.routes.conversations._summarize_early_context",
        return_value=fake_summary,
    ):
        _maybe_summarize(db, conv_id)

    conv_after = db.get_conversation(conv_id)
    assert conv_after["context_summary"] is not None, (
        "context_summary should be set after _maybe_summarize on a long conversation"
    )
    assert conv_after["context_summary"] == fake_summary, (
        "context_summary should equal the value returned by _summarize_early_context"
    )


def test_maybe_summarize_skips_short_conversation():
    """_maybe_summarize must not set context_summary when total < 30 messages."""
    from orivellum.api.routes.conversations import _maybe_summarize

    db = _make_db()
    conv = db.create_conversation(title="Short conv")
    conv_id = conv["id"]

    # 10 pairs (20 messages) — well below the 30-message count threshold
    _seed_messages(db, conv_id, 10)

    with patch(
        "orivellum.api.routes.conversations._summarize_early_context",
        return_value="Should never be stored",
    ) as mock_summarize:
        _maybe_summarize(db, conv_id)
        mock_summarize.assert_not_called()

    conv_after = db.get_conversation(conv_id)
    assert conv_after.get("context_summary") is None, (
        "context_summary must remain None for a conversation below the threshold"
    )


# ---------------------------------------------------------------------------
# Test 2 — summary block injected into LLM messages
# ---------------------------------------------------------------------------

def test_build_messages_injects_summary_block():
    """When context_summary is set on a conversation, _build_messages must
    include the [EARLIER CONVERSATION SUMMARY] marker and the summary text
    in the system message that is passed to the LLM."""
    from orivellum.api.routes.conversations import _build_messages

    db = _make_db()
    conv = db.create_conversation(title="Conv with summary")
    conv_id = conv["id"]

    # Inject a pre-existing summary directly into the DB
    summary_text = "The user stated their favourite colour is cerulean blue."
    db.update_conversation_summary(conv_id, summary_text)
    conv = db.get_conversation(conv_id)  # reload so context_summary is present

    # Patch _build_system_prompt so we don't need a live AI endpoint or
    # populated knowledge base — we only care about what _build_messages
    # does with the context_summary AFTER the system prompt is built.
    with patch(
        "orivellum.api.routes.conversations._build_system_prompt",
        return_value="BASE SYSTEM PROMPT",
    ):
        messages = _build_messages(db, conv, "What is my favourite colour?")

    system_msgs = [m for m in messages if m.get("role") == "system"]
    assert system_msgs, "_build_messages returned no system message"

    system_content = system_msgs[0].get("content", "")
    assert "[EARLIER CONVERSATION SUMMARY" in system_content, (
        "Expected '[EARLIER CONVERSATION SUMMARY' marker in the system message.\n"
        f"System content (first 600 chars):\n{system_content[:600]}"
    )
    assert summary_text in system_content, (
        "The literal summary text must appear inside the [EARLIER CONVERSATION SUMMARY] block.\n"
        f"System content (first 600 chars):\n{system_content[:600]}"
    )


def test_build_messages_no_summary_block_when_summary_absent():
    """When context_summary is None, _build_messages must NOT inject a
    [EARLIER CONVERSATION SUMMARY] block — the marker must not appear."""
    from orivellum.api.routes.conversations import _build_messages

    db = _make_db()
    conv = db.create_conversation(title="Conv without summary")
    conv = db.get_conversation(conv["id"])

    with patch(
        "orivellum.api.routes.conversations._build_system_prompt",
        return_value="BASE SYSTEM PROMPT",
    ):
        messages = _build_messages(db, conv, "Hello?")

    system_msgs = [m for m in messages if m.get("role") == "system"]
    assert system_msgs, "_build_messages returned no system message"
    system_content = system_msgs[0].get("content", "")
    assert "[EARLIER CONVERSATION SUMMARY" not in system_content, (
        "The summary block must not appear when context_summary is None.\n"
        f"System content:\n{system_content[:400]}"
    )


# ---------------------------------------------------------------------------
# Test 3 — early fact present in the summarisation batch
# ---------------------------------------------------------------------------

def test_early_fact_present_in_summarization_batch():
    """A fact stated in the very first message must appear in the batch passed
    to _summarize_early_context when the conversation is long enough for that
    message to fall outside the verbatim window.

    Setup:
      - Message #0 (user): carries EARLY_FACT
      - Message #1 (assistant): acknowledgement
      - Messages #2–#43: filler pairs to reach 44 total (verbatim_start = 4)
      - Message #44: final user query

    The first batch covers messages [0 .. verbatim_start-1] = [0..3], so
    message #0 (the one with EARLY_FACT) must be in that batch.
    """
    from orivellum.api.routes.conversations import _maybe_summarize

    db = _make_db()
    conv = db.create_conversation(title="Fact recall conv")
    conv_id = conv["id"]

    EARLY_FACT = "My secret codeword is PROMETHEUS."

    # Messages #0 and #1
    db.add_message(conv_id, "user", EARLY_FACT)
    db.add_message(conv_id, "assistant", "Understood, I have noted your codeword.")

    # Messages #2–#43: 21 more pairs (42 messages) → total so far = 44
    _seed_messages(db, conv_id, 21)

    # Message #44: total = 45 → verbatim_start = 5, batch covers messages [0..4]
    db.add_message(conv_id, "user", "What was my codeword?")

    captured_batches: list[list[dict]] = []

    def _capture(batch, existing_summary, db_arg):
        captured_batches.append(list(batch))
        return "Summary that preserves the codeword PROMETHEUS."

    with patch(
        "orivellum.api.routes.conversations._summarize_early_context",
        side_effect=_capture,
    ):
        _maybe_summarize(db, conv_id)

    assert captured_batches, (
        "_summarize_early_context was never called — "
        "the message count may not exceed the verbatim-start threshold. "
        "Check that total >= _HISTORY_LIMIT + 4 (= 44)."
    )

    all_texts = [
        m.get("text", "")
        for batch in captured_batches
        for m in batch
    ]
    assert any(EARLY_FACT in t for t in all_texts), (
        f"The early fact {EARLY_FACT!r} was not found in any summarization batch.\n"
        f"Texts passed to _summarize_early_context: {all_texts[:10]}"
    )
