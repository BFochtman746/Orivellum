"""Conversations and chat endpoints — /api/conversations/*"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config
from orivellum.capabilities.pklos.fact_router import is_checkable_fact, should_capture_as_a7
from orivellum.capabilities.pklos.abstention import AbstentionPolicy
from orivellum.capabilities.pklos.claim_ledger import ClaimLedger
from orivellum.capabilities.pklos.policy_enforcer import PolicyEnforcer
from orivellum.capabilities.pklos.output_validator import OutputValidator
from orivellum.capabilities.pklos.capture_stamp import (
    CaptureStamp, detect_factual_assertions,
)

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

# Max messages to send as history (keeps context window manageable)
_HISTORY_LIMIT = 40

# ── Sliding-window context summarization ────────────────────────────────────
# When a conversation accumulates more than _SUMMARIZE_PAIR_THRESHOLD message
# PAIRS (user+assistant), the oldest _SUMMARIZE_BATCH_PAIRS pairs are collapsed
# into a rolling prose summary stored in conversations.context_summary.
#
# The summary is injected as a [CONVERSATION SUMMARY] block at the top of the
# system prompt so older context is preserved without consuming full token budget.
#
# Design notes:
#   • Summarization runs in a background thread after each reply (non-blocking).
#   • Each run folds new material INTO the existing summary rather than
#     regenerating from scratch (incremental, avoids re-processing old messages).
#   • We track how many leading messages are already captured via a leading-count
#     heuristic: if (total_messages - _HISTORY_LIMIT) > 0 we have candidates,
#     and we skip the most-recent _HISTORY_LIMIT messages so the boundary is
#     always clean.
_SUMMARIZE_PAIR_THRESHOLD: int = 15   # pairs → 30 messages triggers first run
_SUMMARIZE_BATCH_PAIRS:    int = 10   # pairs → 20 messages per summarization batch

# Explicit remember pattern — kept in sync with the "remember" intent fast-path
# in capabilities/intent.py so that every phrase routed to _handle_remember also
# suppresses the background auto-capture thread, ensuring exactly ONE persistence
# path for that turn.
_EXPLICIT_REMEMBER_RE = re.compile(
    r"\b(remember (that|my|i|this)"
    r"|my name is"
    r"|i prefer"
    r"|i like"
    r"|i dislike"
    r"|i always"
    r"|i never"
    r"|my (email|phone|address|birthday))\b",
    re.IGNORECASE,
)
# Max knowledge items to inject as context (count backstop; token budget applied first)
_CONTEXT_KNOWLEDGE = 12   # max knowledge items injected per turn
_CONTEXT_CHUNKS    = 5    # max raw document passages injected per turn

# Token estimation — 4 chars per token heuristic (stdlib-only, never used for billing)
_CHARS_PER_TOKEN: int = 4


def estimate_tokens(text: str) -> int:
    """Estimate token count using the 4-chars-per-token heuristic.

    Pure-Python, zero-dependency, no model-specific tokenizer needed.
    Good enough for context-window budgeting; not suitable for billing
    or model-accurate accounting.
    """
    return max(0, len(text) // _CHARS_PER_TOKEN)


def _get_effective_context_window(db: Any) -> int:
    """Return the effective context-window size (tokens) for this request.

    Priority (highest → lowest):
      1. DB setting ``context_window`` (set via PUT /api/system/settings/context-window)
         — only accepted when the stored value is a valid integer ≥ 512.
      2. ``get_config().serving.context_window`` (YAML / env-var / code default)
      3. ``ServingConfig.context_window`` class default (8 192) as last resort

    This is the single authoritative source for the budget used in
    knowledge injection, history trimming, and continuation-message building.
    All chat-construction code must call this function instead of reading
    ``get_config().serving.context_window`` directly so that runtime overrides
    take effect without a server restart.
    """
    try:
        stored_raw = db.get_setting("context_window", "")
        if stored_raw:
            val = int(stored_raw)
            if val >= 512:
                return val
    except Exception:
        pass
    try:
        return get_config().serving.context_window
    except Exception:
        from orivellum.configuration.config import ServingConfig
        return ServingConfig.context_window


def _trim_history_for_budget(
    prior: list[dict],
    system_prompt: str,
    db: Any,
    *,
    extra_text: str = "",
) -> list[dict]:
    """Trim *prior* (oldest-first history list) so the total prompt fits in budget.

    Budget = 80 % of the effective context window, minus the system-prompt
    token cost, minus *extra_text* (e.g. a partial assistant reply that will be
    appended after the history), minus a 256-token safety margin for the model's
    reply.

    Drops the oldest messages first, preserving the most recent context.
    Falls through with *prior* untrimmed on any exception so a bug here never
    silently breaks the response path.

    Callers should use this instead of duplicating the inline trimming pattern.
    """
    try:
        _ctx = _get_effective_context_window(db)
        _budget = int(_ctx * 0.80)
        _budget -= estimate_tokens(system_prompt) + estimate_tokens(extra_text) + 256
        if _budget <= 0:
            return prior
        trimmed: list[dict] = []
        remain = _budget
        for m in reversed(prior):
            t = estimate_tokens(m.get("text", ""))
            if remain - t < 0:
                break
            trimmed.insert(0, m)
            remain -= t
        if len(trimmed) < len(prior):
            logger.debug(
                "Token budget (continuation): trimmed history %d → %d messages (ctx=%d)",
                len(prior), len(trimmed), _ctx,
            )
        return trimmed
    except Exception:
        return prior


class ConversationCreate(BaseModel):
    title: str | None = None
    work_id: str | None = None
    model: str | None = None
    persona_id: str | None = None   # built-in persona slug; None → "default"


class ConversationUpdate(BaseModel):
    title: str | None = None
    archived: bool | None = None
    model: str | None = None


class MessageSend(BaseModel):
    text: str
    stream: bool = False
    deep: bool = False   # When True, route through cognition council
    scope: str = "work"  # "work" = active work only, "all" = all works
    # Optional base64-encoded image for vision-model chat
    image_b64: str | None = None
    image_media_type: str = "image/jpeg"
    # Stable client-generated idempotency key.  Mobile clients include this
    # when flushing queued offline messages so the server can suppress
    # duplicates if the client retries after a lost response.
    client_msg_id: str | None = None
    # Explicit document IDs to pin into the system-prompt context for this
    # message.  When provided, the extracted text of each listed document is
    # prepended to the knowledge-injection block regardless of semantic score.
    # Allows users to "pin" specific files from the work-files drawer.
    context_doc_ids: list[str] | None = None


class ContinueBody(BaseModel):
    stream: bool = True


# ──────────────────────────────────────────────────────────────────────────────
# Image thumbnail helper
# ──────────────────────────────────────────────────────────────────────────────

# These two constants govern the compact JPEG thumbnail stored in
# message meta (key ``image_thumbnail_b64``) whenever a user attaches
# an image to a chat message.  Change both here — they are referenced by
# _make_thumbnail_b64() and by tests — never inline them elsewhere.
#
# Storage budget per image-bearing message:
#   max longest side ≤ _THUMBNAIL_MAX_PX px, size ≤ _THUMBNAIL_MAX_KB KiB.
#   Worst case: 100 image messages ≈ 100 × 20 KiB = ~2 MB added to the
#   messages table.  Acceptable; revisit if message volumes grow into the
#   thousands per conversation.
_THUMBNAIL_MAX_PX: int = 200   # longest dimension in pixels after resize
_THUMBNAIL_MAX_KB: int = 20    # hard upper-bound on base64-decoded JPEG bytes


def _make_thumbnail_b64(
    image_b64: str,
    image_media_type: str = "image/jpeg",
    max_px: int = _THUMBNAIL_MAX_PX,
    max_kb: int = _THUMBNAIL_MAX_KB,
) -> str | None:
    """Decode *image_b64*, shrink to ≤*max_px* on the longest side, and
    re-encode as a compact JPEG.  Returns a base64 string or None on failure.

    Size is capped at *max_kb* KiB by progressively lowering JPEG quality
    (80 → 60 → 40 → 20).  Stored in message meta as ``image_thumbnail_b64``
    so mobile can render a thumbnail in chat history even after a fresh app
    start when no local URI is available.

    The defaults come from the module-level constants ``_THUMBNAIL_MAX_PX``
    and ``_THUMBNAIL_MAX_KB`` — change those, not the call sites.
    """
    try:
        import base64 as _b64
        import io
        from PIL import Image as _PIL
        raw = _b64.b64decode(image_b64)
        img = _PIL.open(io.BytesIO(raw)).convert("RGB")
        w, h = img.size
        scale = max_px / max(w, h, 1)
        if scale < 1.0:
            nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
            img = img.resize((nw, nh), _PIL.Resampling.LANCZOS)
        raw_out = b""
        for quality in (80, 60, 40, 20):
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            raw_out = buf.getvalue()
            if len(raw_out) <= max_kb * 1024:
                break
        return _b64.b64encode(raw_out).decode()
    except Exception as exc:
        logger.debug("thumbnail generation failed: %s", exc)
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Route handlers
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/conversations/search")
def search_conversations(q: str = "", limit: int = 50):
    """Full-text search across all conversation message content.

    Returns up to `limit` matching messages with a snippet and conversation
    metadata. Requires q >= 2 characters.
    """
    db = get_db()
    if not q or len(q.strip()) < 2:
        return {"results": [], "count": 0}
    hits = db.search_messages(q.strip(), limit=min(limit, 200))
    return {"results": hits, "count": len(hits)}


@router.get("/conversations")
def list_conversations(archived: bool = False, limit: int = 100):
    db = get_db()
    convs = db.list_conversations(archived=archived, limit=min(limit, 500))
    return {"conversations": convs, "count": len(convs)}


@router.post("/conversations")
def create_conversation(body: ConversationCreate):
    db = get_db()
    conv = db.create_conversation(
        title=body.title,
        work_id=body.work_id,
        model=body.model,
        persona_id=body.persona_id,
    )
    return {"conversation": conv}


@router.get("/conversations/{conv_id}")
def get_conversation(conv_id: str):
    db = get_db()
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    messages = db.get_messages(conv_id)
    return {"conversation": conv, "messages": messages}


@router.patch("/conversations/{conv_id}")
def update_conversation(conv_id: str, body: ConversationUpdate):
    db = get_db()
    conv = db.update_conversation(conv_id, title=body.title, archived=body.archived, model=body.model)
    if not conv:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    return {"conversation": conv}


@router.put("/conversations/{conv_id}/web-search")
def toggle_web_search(conv_id: str, body: dict):
    """Enable or disable web search grounding for a conversation.

    Body: ``{"enabled": true | false}``
    Returns the updated conversation dict.
    If the TAVILY_API_KEY environment variable is not set, returns 409 with
    a setup prompt so clients can gate the toggle on configuration status.
    """
    import os
    if not os.environ.get("TAVILY_API_KEY", "").strip():
        raise HTTPException(
            409,
            "Web search requires a TAVILY_API_KEY environment variable. "
            "Add your Tavily API key to continue.",
        )
    enabled = bool(body.get("enabled", False))
    db = get_db()
    conv = db.set_conversation_web_search(conv_id, enabled)
    if not conv:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    return {"conversation": conv, "web_search_enabled": enabled}


@router.get("/conversations/{conv_id}/summary")
def get_conversation_summary(conv_id: str):
    """Return the rolling context summary for a conversation.

    The summary is generated automatically in the background by the
    sliding-window summarizer after replies in long conversations.
    Returns ``{"summary": null}`` when no summary has been generated yet.
    """
    db = get_db()
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    return {
        "conversation_id": conv_id,
        "summary": conv.get("context_summary") or None,
        "has_summary": bool(conv.get("context_summary")),
    }


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str):
    db = get_db()
    ok = db.delete_conversation(conv_id)
    if not ok:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    return {"ok": True}


@router.get("/memory")
async def get_memory() -> dict:
    """Return current (non-superseded) user memory facts, newest first."""
    db = get_db()
    try:
        facts = db.get_current_memory_facts(limit=50)
    except Exception:
        facts = []
    return {"facts": facts, "total": len(facts)}


@router.post("/conversations/{conv_id}/messages")
async def send_message(conv_id: str, body: MessageSend):
    db = get_db()
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")

    # Store user message first so it appears immediately
    stored_text = body.text or "What is in this image?"
    if body.image_b64 and not body.text:
        stored_text = "[Image attached]"
    elif body.image_b64:
        stored_text = f"[Image] {body.text}"

    # Build meta for the user message — include a compact thumbnail so mobile
    # can show the image in history after the session ends (no local URI).
    user_meta: dict = {}
    if body.image_b64:
        thumb = _make_thumbnail_b64(body.image_b64, body.image_media_type)
        if thumb:
            user_meta["image_thumbnail_b64"] = thumb

    # ── Duplicate-send guard ───────────────────────────────────────────────────
    # Two complementary paths:
    #
    # A. client_msg_id path (offline retry / mobile queue flush):
    #    store_user_msg_and_claim() performs two INSERTs atomically under a
    #    single db._lock acquisition so no concurrent request can interleave
    #    between inserting the user message and claiming the generation slot.
    #    Returns one of three actions:
    #      'generate'   — we are the sole claimant; proceed with AI generation
    #      'return'     — a prior request completed; return its assistant reply
    #      'processing' — another request is currently generating; return 409
    #
    # B. Recency text check (React StrictMode / double-tap, no client_msg_id):
    #    Best-effort; narrow 5-second window is acceptable for this path.
    #    The user message is suppressed but AI generation still proceeds.

    # Tracks whether we're the idempotency claimant so we can call
    # complete_idempotency() after every assistant message store.
    _idem_client_msg_id: str | None = None

    if body.client_msg_id:
        _idem_action, _existing_ai_id, _user_msg = db.store_user_msg_and_claim(
            conv_id, stored_text, user_meta or None, body.client_msg_id,
        )

        if _idem_action == "return":
            # A previous request completed — fetch and return the stored reply.
            logger.debug(
                "Idempotent return (client_msg_id=%s) for conv %s — "
                "serving existing assistant reply %s",
                body.client_msg_id, conv_id, _existing_ai_id,
            )
            with db._lock:
                ai_row = db._conn.execute(
                    """SELECT id, conversation_id, role, text, meta, created_at, state
                         FROM messages WHERE id=?""",
                    (_existing_ai_id,),
                ).fetchone()
            if ai_row:
                import json as _json
                return {
                    "message": {
                        "id": ai_row[0],
                        "conversation_id": ai_row[1],
                        "role": ai_row[2],
                        "text": ai_row[3],
                        "meta": _json.loads(ai_row[4] or "{}"),
                        "created_at": ai_row[5],
                        "state": ai_row[6],
                    }
                }
            # Slot said 'completed' but message row is gone — fall through to
            # generate a replacement (treat as crash recovery).
            _idem_client_msg_id = body.client_msg_id

        elif _idem_action == "processing":
            # Another request is currently generating — tell the client to
            # retry later.  The message stays in the mobile outbox.
            logger.debug(
                "Idempotency 409 (client_msg_id=%s) for conv %s — "
                "generation already in progress",
                body.client_msg_id, conv_id,
            )
            from fastapi import Response as _Response
            return _Response(status_code=409, content="Generation in progress — retry later")

        else:
            # _idem_action == 'generate': we are the claimant.
            _idem_client_msg_id = body.client_msg_id

    else:
        # No idempotency key — use the narrow recency-text heuristic.
        with db._lock:
            recent_dup = db._conn.execute(
                """SELECT id FROM messages
                   WHERE conversation_id=? AND role='user' AND text=?
                   AND created_at > datetime('now','-5 seconds')""",
                (conv_id, stored_text)
            ).fetchone()
        if recent_dup:
            logger.debug("Duplicate user message suppressed (recency) for conv %s", conv_id)
        else:
            db.add_message(conv_id, "user", stored_text, meta=user_meta or None)

    import asyncio, threading

    # PKLOS Layer 0 — capture factual assertions about the user's system.
    # Runs only when the fast pattern detects a hardware/system statement.
    # Uses a background thread so it never delays the response.
    if body.text and detect_factual_assertions(body.text):
        try:
            cfg_for_capture = get_config()
            stamp = CaptureStamp(db)
            _kwargs = {
                "text": body.text,
                "channel": "chat",
                "conv_id": conv_id,
                "base_url": cfg_for_capture.serving.base_url,
                "model": conv.get("model") or cfg_for_capture.serving.workhorse_model,
            }
            try:
                from orivellum.api.executor import _tracked_submit as _ts_stamp
                _ts_stamp(stamp.stamp_and_capture, kind="pklos", label="stamp_capture",
                          **_kwargs)
            except Exception as _exc_stamp:
                logger.warning("Executor unavailable for stamp_capture, falling back to thread: %s",
                               _exc_stamp)
                import threading as _thr_stamp
                _thr_stamp.Thread(target=stamp.stamp_and_capture,
                                  kwargs=_kwargs, daemon=True).start()
        except Exception:
            pass  # capture is best-effort; never block the response

    if body.stream:
        return StreamingResponse(
            _stream_response(
                db, conv, body.text, deep=body.deep, scope=body.scope,
                image_b64=body.image_b64, image_media_type=body.image_media_type,
                context_doc_ids=body.context_doc_ids or [],
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming path
    _ns_sources: list = []
    _ns_strategy_meta: dict = {}
    messages = _build_messages(
        db, conv, body.text, scope=body.scope,
        image_b64=body.image_b64, image_media_type=body.image_media_type,
        out_sources=_ns_sources,
        out_meta=_ns_strategy_meta,
        context_doc_ids=body.context_doc_ids or [],
    )
    _seen_ns: set = set()
    ns_sources: list = []
    for s in _ns_sources:
        key = s.get("id") or s.get("source_doc_id") or s.get("title")
        if key and key not in _seen_ns:
            _seen_ns.add(key)
            ns_sources.append(s)
    model = _model_for_vision(conv) if body.image_b64 else _model_for(conv)
    cfg      = get_config()

    # ── Intent routing (non-streaming) ───────────────────────────────────────
    _ns_work_id = conv.get("work_id") if conv else None
    tool_result = await _maybe_dispatch_intent(db, body.text, cfg.serving.base_url, model, work_id=_ns_work_id)
    if tool_result is not None:
        tool_text, tool_meta = tool_result
        if ns_sources:
            # Merge knowledge sources with any tool-specific sources (e.g. web search)
            existing = tool_meta.get("sources", [])
            tool_meta = {**tool_meta, "sources": [*existing, *ns_sources]}
        msg = db.add_message(conv_id, "assistant", tool_text, meta=tool_meta)
        if _idem_client_msg_id:
            db.complete_idempotency(conv_id, _idem_client_msg_id, msg["id"])
        _maybe_auto_title(db, conv, body.text)
        try:
            from orivellum.api.executor import _tracked_submit as _ts_prb1
            _ts_prb1(_post_reply_background, db, conv_id, body.text, tool_text,
                     kind="chat", label="post_reply_bg")
        except Exception as _exc_prb1:
            logger.warning("Executor unavailable for post_reply_bg (tool), falling back to thread: %s",
                           _exc_prb1)
            import threading as _thr_prb1
            _thr_prb1.Thread(target=_post_reply_background,
                             args=(db, conv_id, body.text, tool_text), daemon=True).start()
        return {"message": msg}

    if body.deep:
        import asyncio
        from orivellum.capabilities.cognition import (
            classify, get_clarifying_question, deliberate, update_compass,
        )
        route = await asyncio.to_thread(
            classify, body.text, messages[:-1], cfg.serving.base_url, model, db
        )
        logger.debug("Cognition gate (non-stream) for conv %s: %s", conv_id, route)

        if route == "clarify":
            question = await asyncio.to_thread(
                get_clarifying_question, body.text, cfg.serving.base_url, model, db
            )
            clarify_meta: dict = {"model": model, "isClarification": True}
            if ns_sources:
                clarify_meta["sources"] = ns_sources
            msg = db.add_message(conv_id, "assistant", question, meta=clarify_meta)
            if _idem_client_msg_id:
                db.complete_idempotency(conv_id, _idem_client_msg_id, msg["id"])
            _maybe_auto_title(db, conv, body.text)
            try:
                from orivellum.api.executor import _tracked_submit as _ts_prb2
                _ts_prb2(_post_reply_background, db, conv_id, body.text, question,
                         kind="chat", label="post_reply_bg")
            except Exception as _exc_prb2:
                logger.warning("Executor unavailable for post_reply_bg (clarify), falling back to thread: %s",
                               _exc_prb2)
                import threading as _thr_prb2
                _thr_prb2.Thread(target=_post_reply_background,
                                 args=(db, conv_id, body.text, question), daemon=True).start()
            return {"message": msg}

        if route == "complex":
            council_reply = await asyncio.to_thread(
                deliberate, messages, cfg.serving.base_url, model, db
            )
            if council_reply:
                work_id = conv.get("work_id")
                if work_id:
                    await asyncio.to_thread(
                        update_compass, db, work_id,
                        focus=body.text[:200],
                        reasoning=council_reply[:500],
                    )
                council_meta: dict = {"model": model, "council": True}
                if ns_sources:
                    council_meta["sources"] = ns_sources
                msg = db.add_message(conv_id, "assistant", council_reply,
                                     meta=council_meta)
                if _idem_client_msg_id:
                    db.complete_idempotency(conv_id, _idem_client_msg_id, msg["id"])
                _maybe_auto_title(db, conv, body.text)
                try:
                    from orivellum.api.executor import _tracked_submit as _ts_prb3
                    _ts_prb3(_post_reply_background, db, conv_id, body.text, council_reply,
                             kind="chat", label="post_reply_bg")
                except Exception as _exc_prb3:
                    logger.warning("Executor unavailable for post_reply_bg (council), falling back to thread: %s",
                                   _exc_prb3)
                    import threading as _thr_prb3
                    _thr_prb3.Thread(target=_post_reply_background,
                                     args=(db, conv_id, body.text, council_reply),
                                     daemon=True).start()
                return {"message": msg}
            # Council failed → fall through to direct single call

        # "direct" or council/classify fallback
        _ai_fn = _call_ai_vision if body.image_b64 else _call_ai
        reply = await _ai_fn(messages, model=model, db=db)
    else:
        _ai_fn = _call_ai_vision if body.image_b64 else _call_ai
        reply = await _ai_fn(messages, model=model, db=db)

    # PKLOS output validation (non-streaming path).
    # For checkable hardware/system facts, verify the reply doesn't invent values.
    # Runs only when the query is deterministically verifiable (fast pattern check).
    # Best-effort: any exception falls through to the unmodified reply.
    if body.text and is_checkable_fact(body.text):
        try:
            _ov = OutputValidator(db)
            _ov_claims = db.search_claims_for_context(body.text, limit=15)
            _ov_result = _ov.validate(body.text, reply, verified_claims=_ov_claims)
            if _ov_result.must_regenerate:
                logger.info(
                    "OutputValidator: replacing reply for conv %s (%d hard violations)",
                    conv_id, sum(1 for v in _ov_result.violations if v.startswith("HARD")),
                )
                reply = _ov.build_fallback_answer(body.text, _ov_claims)
        except Exception as _ov_exc:
            logger.debug("OutputValidator skipped (non-fatal): %s", _ov_exc)

    ns_meta: dict = {"model": model}
    if ns_sources:
        ns_meta["sources"] = ns_sources
    if _ns_strategy_meta.get("retrieval_strategy"):
        ns_meta["retrieval_strategy"] = _ns_strategy_meta["retrieval_strategy"]
        ns_meta["query_type"] = _ns_strategy_meta.get("query_type", "")
    msg = db.add_message(conv_id, "assistant", reply, meta=ns_meta)
    if _idem_client_msg_id:
        db.complete_idempotency(conv_id, _idem_client_msg_id, msg["id"])
    _maybe_auto_title(db, conv, body.text)
    # Background: embed exchange + inference memory capture (non-streaming)
    try:
        from orivellum.api.executor import _tracked_submit as _ts_prb4
        _ts_prb4(_post_reply_background, db, conv_id, body.text, reply,
                 kind="chat", label="post_reply_bg")
    except Exception as _exc_prb4:
        logger.warning("Executor unavailable for post_reply_bg (direct), falling back to thread: %s",
                       _exc_prb4)
        import threading as _thr_prb4
        _thr_prb4.Thread(target=_post_reply_background,
                         args=(db, conv_id, body.text, reply), daemon=True).start()
    return {"message": msg}


class PredictBody(BaseModel):
    draft: str


@router.post("/conversations/{conv_id}/predict")
async def predict_completion(conv_id: str, body: PredictBody):
    """Ghost-text completion + up to 3 source chips for the predictive composer.

    Fast path (≤ 5 s budget, called after 800 ms debounce on each draft change):
      1. Hybrid-search knowledge items by draft text → top 3 source chips
      2. LLM call with tight instruction + knowledge context, max_tokens=80
      3. Returns ``{"ghost": str, "sources": [{id, title, kind, work_id, ...}]}``

    Returns ``{"ghost":"","sources":[]}`` when the draft is too short (< 8 chars),
    the AI server is unreachable, or no relevant knowledge is found.
    """
    import asyncio as _aio
    db = get_db()
    cfg = get_config()

    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")

    draft = body.draft.strip()
    if len(draft) < 8:
        return {"ghost": "", "sources": []}

    work_id: str | None = conv.get("work_id")

    # ── Knowledge retrieval ────────────────────────────────────────────────────
    k_hits: list[dict] = []
    try:
        from orivellum.capabilities.embeddings import hybrid_search_knowledge
        k_hits = hybrid_search_knowledge(draft, db, limit=8, work_id=work_id)
    except Exception:
        try:
            k_hits = db.search_knowledge(draft, work_id=work_id, limit=8)
        except Exception:
            pass

    # Build sources list — top 3, deduplicated by id
    sources: list[dict] = []
    seen_ids: set[str] = set()
    for hit in k_hits:
        hit_id = str(hit.get("id") or "")
        if not hit_id or hit_id in seen_ids:
            continue
        seen_ids.add(hit_id)
        content = hit.get("content") or hit.get("text") or ""
        sources.append({
            "id":            hit_id,
            "title":         hit.get("title") or hit.get("doc_title") or "Knowledge",
            "kind":          "knowledge",
            "work_id":       hit.get("work_id"),
            "work_title":    hit.get("work_title"),
            "source_doc_id": hit.get("source_doc_id") or hit.get("doc_id"),
            "passage":       content[:150] if content else None,
        })
        if len(sources) == 3:
            break

    # ── LLM ghost-text generation ──────────────────────────────────────────────
    context_lines = [
        (hit.get("content") or hit.get("text") or "").strip()
        for hit in k_hits[:5]
    ]
    context_str = "\n".join(
        f"• {c[:300]}" for c in context_lines if c
    ) or "(no relevant context)"

    messages = [
        {
            "role": "system",
            "content": (
                "You are a concise writing assistant. Complete the user's in-progress "
                "message naturally. Rules:\n"
                "- Output ONLY the completion — not what they already typed.\n"
                "- Maximum 15 words. Stop at a natural phrase boundary.\n"
                "- Prefer grounding in the CONTEXT when relevant.\n"
                "- No quotes, explanation, or preamble.\n\n"
                f"CONTEXT FROM KNOWLEDGE BASE:\n{context_str}"
            ),
        },
        {
            "role": "user",
            "content": f"Complete this message: {draft}",
        },
    ]

    ghost = ""
    try:
        from orivellum.capabilities.llm import llm_call
        result = await _aio.to_thread(
            llm_call, messages,
            cfg=cfg, db=db,
            purpose="predict",
            timeout=5.0,
            temperature=0.35,
            max_tokens=80,
        )
        if result.ok and result.text:
            raw = result.text.strip()
            # Strip accidental re-echo of the last few typed chars
            if len(draft) >= 5 and raw.lower().startswith(draft[-5:].lower()):
                raw = raw[5:].lstrip()
            ghost = raw
    except Exception:
        pass

    return {"ghost": ghost, "sources": sources}


@router.post("/conversations/{conv_id}/continue")
async def continue_message(conv_id: str, body: ContinueBody):
    """Continue a cut-short assistant reply instead of regenerating from scratch.

    Finds the last assistant message with ``meta.cut_short == True``, prepends
    its partial text as an assistant turn, and streams (or returns) only the
    continuation.  The original message is updated in-place so history is clean.
    """
    db = get_db()
    conv = db.get_conversation(conv_id)
    if not conv:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")

    # Find the last cut-short assistant message
    messages_list = db.get_messages(conv_id)
    cut_short_msg: dict | None = None
    for m in reversed(messages_list):
        if m.get("role") != "assistant":
            continue
        raw_meta = m.get("meta")
        if isinstance(raw_meta, str):
            try:
                raw_meta = json.loads(raw_meta)
            except Exception:
                raw_meta = {}
        if (raw_meta or {}).get("cut_short"):
            cut_short_msg = m
            cut_short_msg = {**m, "meta": raw_meta}
        break  # only look at the last assistant message

    if not cut_short_msg:
        raise HTTPException(409, "No cut-short message found to continue")

    if body.stream:
        return StreamingResponse(
            _stream_continuation(db, conv, cut_short_msg),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # Non-streaming path (mobile)
    cfg = get_config()
    model = _model_for(conv)
    meta = cut_short_msg.get("meta") or {}
    partial_text: str = meta.get("partial_text") or cut_short_msg.get("text", "")
    partial_text = partial_text.removesuffix(
        "\n\n*(Response was cut short — re-send to continue.)*"
    )

    system_prompt = _build_system_prompt(db, conv, scope="work", user_query=None)
    history = db.get_messages(conv_id, limit=_HISTORY_LIMIT + 5)
    raw_prior = [m for m in history if m.get("id") != cut_short_msg["id"]][-_HISTORY_LIMIT:]
    prior = _trim_history_for_budget(raw_prior, system_prompt, db, extra_text=partial_text)
    msgs: list[dict] = [{"role": "system", "content": system_prompt}]
    for m in prior:
        role = m["role"] if m["role"] in ("user", "assistant") else "user"
        msgs.append({"role": role, "content": m.get("text") or ""})
    msgs.append({"role": "assistant", "content": partial_text})

    from starlette.concurrency import run_in_threadpool
    from orivellum.capabilities.llm import llm_call
    result = await run_in_threadpool(
        llm_call, msgs,
        base_url=cfg.serving.base_url, model=model,
        timeout=cfg.serving.timeout_sec, purpose="chat.continue", db=db,
    )
    continuation = result.text or ""
    if not result.ok or not continuation:
        # LLM call failed or returned nothing — preserve the cut-short state so
        # the client can retry.  Do NOT clear partial_text or cut_short.
        raise HTTPException(502, "Continuation failed — model returned no content; please try again")

    new_text = partial_text + continuation
    new_meta = {k: v for k, v in meta.items() if k not in ("cut_short", "partial_text")}
    still_cut = result.finish_reason in ("length", "max_tokens")
    if still_cut:
        new_meta["cut_short"] = True
        new_meta["partial_text"] = new_text

    msg_id = cut_short_msg["id"]
    with db._lock:
        db._conn.execute(
            "UPDATE messages SET text=?, meta=? WHERE id=?",
            (new_text, json.dumps(new_meta), msg_id),
        )
        db._conn.commit()
    # Keep FTS index in sync — the UPDATE above bypasses finalize_message()
    db.sync_message_fts(
        msg_id, new_text,
        conv_id=cut_short_msg.get("conversation_id", conv_id),
        role=cut_short_msg.get("role", "assistant"),
    )

    updated_msg = {**cut_short_msg, "text": new_text, "meta": new_meta}
    return {"message": updated_msg, "cut_short": still_cut}


# ──────────────────────────────────────────────────────────────────────────────
# Message construction
# ──────────────────────────────────────────────────────────────────────────────

def _model_for(conv: dict) -> str:
    """Return the model to use for this conversation.

    Priority: conversation.model → config workhorse default.
    """
    cfg = get_config()
    return conv.get("model") or cfg.serving.workhorse_model


def _model_for_vision(conv: dict) -> str:
    """Return the vision model for this conversation.

    Priority: conversation.model → DB vision_model setting
              → config vision_model → workhorse fallback.
    The DB setting (editable from System Settings) overrides the YAML config.
    """
    cfg = get_config()
    db  = get_db()
    db_vision = db.get_setting("vision_model", "")
    return conv.get("model") or db_vision or cfg.serving.vision_model or cfg.serving.workhorse_model


# Hardcoded fallback for the chat base persona.  The MCOS prompt registry
# (slot 'chat.base') is seeded from this exact string; if the registry is
# missing/empty or a lookup raises, chat falls back to this constant.
#
# Style principles encoded here are drawn from master-level prose craft:
# lead with the answer, be concrete and specific, earn every word, vary
# sentence length for rhythm, use active voice and strong verbs, and be
# precise about uncertainty rather than hedging.
_CHAT_BASE_PROMPT = """\
You are Orivellum, a local-first AI assistant built for research, analysis, and writing.

RESPONSE STANDARDS — apply to every reply without exception:
• Lead with the answer. State the thing first, then support it. Never open \
with pleasantries, preamble, or throat-clearing.
• Be concrete. Prefer the specific, countable, and named over the vague and \
general. A detail earns its place by revealing something — not by padding length.
• Earn every word. Strip out: "certainly", "of course", "great question", \
"I'd be happy to", "As an AI", "It's worth noting that", "Absolutely", \
"Sure!", and all similar filler. These phrases carry no meaning.
• Vary sentence length deliberately. Short sentences land hard after long ones. \
Use the short sentence to close the point.
• Active voice, strong verbs. "The committee rejected the proposal" — \
not "the proposal was not approved by the committee."
• Be precise about uncertainty. "I don't know" is stronger than \
"it's complicated" or "there are many perspectives." Say what you know; \
mark plainly what you don't.
• Never fabricate citations, statistics, or direct quotes. When uncertain, say so.

CAPABILITIES:
• Answer any question from your training knowledge — science, history, \
analysis, writing, code, research, general facts.
• The user may have uploaded documents to the knowledge base; relevant excerpts \
are injected below when they match the query — treat them as authoritative context.
• A built-in web search tool is available for live internet data — when the user \
asks to search or look something up, it fetches current results automatically.\
"""


_abstention_policy = AbstentionPolicy()


def _strip_filter_phrases(text: str) -> str:
    """Remove recognised temporal and document-kind filter phrases from *text*.

    Returns the residual content query suitable for passing to FTS5.
    Returns an empty string when the entire query is consumed by filter phrases
    (i.e. the query is *purely* temporal/source-filter such as "what did I add
    last week?" or "summarize my PDFs").

    Callers should pass the result to DB filtered-search methods; when the
    return value is empty those methods issue a date-only / kind-only scan
    (plain SELECT, no FTS MATCH) so they return all qualifying items regardless
    of whether the items' text contains the filter wording.
    """
    import re as _re

    t = text

    # ── Remove temporal phrases (longest first to avoid partial removal) ──────
    _temporal_pats = [
        r'\bin\s+the\s+(?:past|last)\s+\d+\s+(?:days?|weeks?|months?)\b',
        r'\b(?:past|last)\s+\d+\s+(?:days?|weeks?|months?)\b',
        r'\b(?:last|past)\s+(?:week|month|year)\b',
        r'\bthis\s+(?:week|month|year)\b',
        r'\byesterday\b',
        r'\btoday\b',
    ]
    for pat in _temporal_pats:
        t = _re.sub(pat, ' ', t, flags=_re.IGNORECASE)

    # ── Remove document-kind filter phrases ───────────────────────────────────
    _kind_group = (
        r'pdf[s]?'
        r'|word\s+docs?'
        r'|docx?'
        r'|excel'
        r'|spreadsheets?'
        r'|xlsx?'
        r'|csv'
        r'|markdown'
        r'|md\s+files?'
        # Code — Python, JS/TS family, Java, Go, Rust, Ruby, PHP, C/C++
        r'|python'
        r'|javascript'
        r'|typescript'
        r'|\.js\b'
        r'|\.ts\b'
        r'|\.jsx\b'
        r'|\.tsx\b'
        r'|java\b'
        r'|golang'
        r'|rust\b'
        r'|ruby\b'
        r'|php\b'
        r'|c\+\+'
        r'|cpp\b'
        r'|code\s+files?'
        r'|scripts?'
        r'|source\s+files?'
        r'|audio'
        r'|recordings?'
        r'|podcasts?'
        r'|mp3'
        r'|wav'
        r'|images?'
        r'|photos?'
        r'|pictures?'
        r'|screenshots?'
        r'|powerpoints?'
        r'|slides?'
        r'|pptx?'
        r'|txt'
        r'|text\s+files?'
        r'|plain\s+text'
    )
    # Match with optional preceding "from (my|the)" or "my"
    # and optional trailing "file(s)" so "PDF files" and "audio files" are
    # fully consumed by this pass rather than leaving a dangling "files" token.
    t = _re.sub(
        rf'\b(?:from\s+(?:my|the)\s+|my\s+)?(?:{_kind_group})(?:\s+files?)?\b',
        ' ', t, flags=_re.IGNORECASE,
    )

    # ── Strip hollow stop-words left behind after phrase removal ─────────────
    _stop = (
        r'\b(?:what|when|where|who|how|did|do|does|i|we|have|has|been|was|were'
        r'|add|added|import|imported|show|me|tell|give|find|get|list'
        r'|summarize|summarise|summarized|summarising|everything|anything|something'
        r'|all|the|a|an|of|and|or'
        r'|from|in|at|to|on|for|with|about|by|my|during|over|since|between'
        r'|any|is|are|there|been|had)\b'
    )
    residual = _re.sub(_stop, ' ', t, flags=_re.IGNORECASE)
    residual = _re.sub(r'\s+', ' ', residual).strip(' .,?!:;')

    return residual


def _detect_query_filters(
    text: str,
    now: "datetime | None" = None,
) -> "dict | None":
    """Detect temporal and document-kind filters from a user query.

    Returns a dict with keys:
        after_date  — ISO-format lower bound (inclusive), or None
        before_date — ISO-format upper bound (exclusive), or None
        description — human-readable label, e.g. "last week" or "last week from pdf files"
        doc_kinds   — list of document-kind strings to filter, e.g. ["pdf", "audio"]

    Returns None when no temporal or source-kind filter is found, so the
    caller falls through to the default hybrid search.

    Supported temporal expressions:
        today / yesterday / this week / last week / past week /
        this month / last month / past month /
        this year / last year / past year /
        past N days / last N days / in the last N days /
        past N weeks / last N weeks /
        past N months / last N months

    Supported source-kind expressions (matched against the query):
        PDFs · Word docs / DOCX · Excel / XLSX / spreadsheets · CSV ·
        Markdown / MD files · Python / code files / scripts ·
        audio / recordings / podcasts / MP3 / WAV ·
        images / photos / screenshots · PowerPoint / slides / PPTX ·
        plain text / TXT files
    """
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    import re as _re

    if now is None:
        now = _dt.now(_tz.utc)

    t = text.lower()

    # ── Temporal detection ───────────────────────────────────────────────────
    after: "_dt | None" = None
    before: "_dt | None" = None
    time_desc: "str | None" = None

    def _day_start(d: "_dt") -> "_dt":
        return d.replace(hour=0, minute=0, second=0, microsecond=0)

    if _re.search(r'\btoday\b', t):
        after = _day_start(now)
        time_desc = "today"

    elif _re.search(r'\byesterday\b', t):
        after = _day_start(now - _td(days=1))
        before = _day_start(now)
        time_desc = "yesterday"

    elif _re.search(r'\b(last|past)\s+week\b', t):
        # Sunday→Monday depends on locale; use ISO Monday-based week
        monday = _day_start(now - _td(days=now.weekday()))
        after = monday - _td(weeks=1)
        before = monday
        time_desc = "last week"

    elif _re.search(r'\bthis\s+week\b', t):
        after = _day_start(now - _td(days=now.weekday()))
        time_desc = "this week"

    elif _re.search(r'\b(last|past)\s+month\b', t):
        first_this = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if first_this.month == 1:
            first_last = first_this.replace(year=first_this.year - 1, month=12)
        else:
            first_last = first_this.replace(month=first_this.month - 1)
        after = first_last
        before = first_this
        time_desc = "last month"

    elif _re.search(r'\bthis\s+month\b', t):
        after = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        time_desc = "this month"

    elif _re.search(r'\b(last|past)\s+year\b', t):
        jan1 = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        after = jan1.replace(year=jan1.year - 1)
        before = jan1
        time_desc = "last year"

    elif _re.search(r'\bthis\s+year\b', t):
        after = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        time_desc = "this year"

    else:
        # "past/last N days/weeks/months"
        m = _re.search(
            r'\b(?:(?:in\s+the\s+)?(?:past|last))\s+(\d+)\s+(days?|weeks?|months?)\b', t
        )
        if m:
            n = int(m.group(1))
            unit = m.group(2).rstrip("s")
            if unit == "day":
                after = now - _td(days=n)
            elif unit == "week":
                after = now - _td(weeks=n)
            else:   # month (approximate)
                after = now - _td(days=n * 30)
            time_desc = f"past {n} {m.group(2)}"

    # ── Source-kind detection ────────────────────────────────────────────────
    _KIND_PATTERNS: list[tuple[str, str]] = [
        (r'\bpdf[s]?\b',                                   'pdf'),
        (r'\bword\s+docs?\b|\bdocx?\b',                    'docx'),
        (r'\bexcel\b|\bspreadsheets?\b|\bxlsx?\b',         'excel'),
        (r'\bcsv\b',                                        'csv'),
        (r'\bmarkdown\b|\bmd\s+files?\b',                  'markdown'),
        # Code — Python, JS/TS/JSX/TSX, Java, Go/Golang, Rust, Ruby, PHP, C/C++
        (
            r'\bpython\b'
            r'|\bjavascript\b'
            r'|\btypescript\b'
            r'|\.js\b'
            r'|\.ts\b'
            r'|\.jsx\b'
            r'|\.tsx\b'
            r'|\bjava\b'
            r'|\bgolang\b'
            r'|\brust\b'
            r'|\bruby\b'
            r'|\bphp\b'
            r'|\bc\+\+'
            r'|\bcpp\b'
            r'|\bcode\s+files?\b'
            r'|\bscripts?\b'
            r'|\bsource\s+files?\b',
            'code',
        ),
        (r'\baudio\b|\brecordings?\b|\bpodcasts?\b|\bmp3\b|\bwav\b', 'audio'),
        (r'\bimages?\b|\bphotos?\b|\bpictures?\b|\bscreenshots?\b',  'image'),
        (r'\bpowerpoints?\b|\bslides?\b|\bpptx?\b',        'pptx'),
        (r'\btxt\b|\btext\s+files?\b|\bplain\s+text\b',    'text'),
    ]
    doc_kinds: list[str] = [
        kind for pattern, kind in _KIND_PATTERNS if _re.search(pattern, t)
    ]

    if not time_desc and not doc_kinds:
        return None

    parts: list[str] = []
    if time_desc:
        parts.append(time_desc)
    if doc_kinds:
        parts.append(f"from {'/'.join(doc_kinds)} files")

    return {
        "after_date":  after.isoformat() if after else None,
        "before_date": before.isoformat() if before else None,
        "description": " ".join(parts),
        "doc_kinds":   doc_kinds,
    }


def _build_system_prompt(db: Any, conv: dict, scope: str = "work",
                         user_query: str | None = None,
                         out_sources: list | None = None,
                         out_meta: dict | None = None,
                         context_doc_ids: list[str] | None = None) -> str:
    """Build a system prompt enriched with relevant knowledge from the database.

    Knowledge retrieval strategy (always global):
      1. If user_query is provided → full-text search across ALL knowledge + document
         chunks, ranked by relevance, grouped by Work/topic.
      2. If no query (e.g. first message) → fall back to most-recent knowledge
         from the linked Work, or across all Works if none is linked.

    Only trusted items (rule-based "auto" + user-approved "approved") are injected.
    Pending AI items ("ai_auto") are excluded until the user approves them.

    PKLOS Layer 0 — spec §5.3 PolicyEnforcer (P2/P3):
      - Classifies the query (spec §5.1 7-class router).
      - For DETERMINISTICALLY_VERIFIABLE: injects VERIFIED FACTS from the claim
        ledger (USER_ASSERTED + VERIFIED + PARTIALLY_VERIFIED).
      - If no claims: enforces abstention — the model MUST NOT guess.
      - For USER_DECLARED_FACT: logs that the capture path should run.
    """
    # Built-in conversation personas.  Each slug maps to a short directive that
    # is appended to the base prompt so the model adopts a distinct tone/focus.
    _PERSONAS: dict[str, str] = {
        "default": "",
        "story_partner": (
            "ROLE: You are a creative story partner. Spark imagination with 'what if' questions, "
            "explore narrative possibilities with enthusiasm, celebrate ideas before critiquing them, "
            "and keep the creative energy high. Prioritise generative thinking over correctness."
        ),
        "technical_editor": (
            "ROLE: You are a precise technical editor. Flag inconsistencies, ambiguities, and structural "
            "weaknesses. Suggest clarity improvements with concrete alternatives. Stay factual, concise, "
            "and direct — praise sparingly; focus on what can be made sharper."
        ),
        "research_assistant": (
            "ROLE: You are a thorough research assistant. Cite sources when you have them, provide context "
            "and background, and ask one clarifying question before diving into a long answer. Organise "
            "findings clearly and flag uncertainty explicitly."
        ),
        "devils_advocate": (
            "ROLE: You are a devil's advocate. Challenge assumptions, surface counterarguments, and push "
            "the user to stress-test their reasoning. You are not trying to win — you are trying to make "
            "their thinking stronger. Be rigorous but constructive."
        ),
    }
    _COMM_STYLE_DIRECTIVES: dict[str, str] = {
        "casual":    "Communicate in a relaxed, conversational tone — contractions welcome, no stiff formality.",
        "direct":    "Be direct and concise. Lead with the answer, skip preamble.",
        "socratic":  "Use the Socratic method — ask questions to guide thinking rather than giving answers outright.",
        "formal":    "Maintain a formal, professional register throughout.",
        "technical": "Use precise technical language; assume domain familiarity; skip basic explanations.",
    }

    # Base persona comes from the MCOS prompt registry (slot 'chat.base') so it
    # can be A/B-benchmarked and swapped without a code change.  Never let this
    # break chat — fall back to the hardcoded constant on any failure.
    base = _CHAT_BASE_PROMPT
    try:
        active = db.get_active_prompt("chat.base")
        if active:
            base = active
    except Exception:
        base = _CHAT_BASE_PROMPT

    # ── User profile + communication style injection ───────────────────────────
    # Reads user_name / user_bio / communication_style from the settings table
    # and prepends a compact "About the user" block at the top of the prompt.
    # Falls through silently on old schemas / missing keys (zero regression).
    try:
        _uname  = db.get_setting("user_name", "").strip()
        _ubio   = db.get_setting("user_bio", "").strip()
        _ustyle = db.get_setting("communication_style", "").strip().lower()
        _profile_lines: list[str] = []
        if _uname:
            _profile_lines.append(f"  Name: {_uname}")
        if _ubio:
            _profile_lines.append(f"  About: {_ubio}")
        if _profile_lines:
            base = "ABOUT THE USER:\n" + "\n".join(_profile_lines) + "\n\n" + base
        # Communication style directive
        if _ustyle and _ustyle in _COMM_STYLE_DIRECTIVES:
            base = base + "\n\nCOMMUNICATION STYLE: " + _COMM_STYLE_DIRECTIVES[_ustyle]
    except Exception:
        pass  # settings table unavailable on old schemas

    # ── Conversation persona directive ─────────────────────────────────────────
    try:
        _pid = (conv.get("persona_id") or "default").lower()
        _pdirective = _PERSONAS.get(_pid, "")
        if _pdirective:
            base = base + "\n\n" + _pdirective
    except Exception:
        pass

    # ── PKLOS §5.3: host-side policy enforcement ──────────────────────────────
    # PolicyEnforcer classifies the query and either:
    #   (a) injects verified claims + use-them instruction, or
    #   (b) injects abstention instruction (model must not guess)
    # This runs BEFORE the knowledge search so the claim block is as high as
    # possible in the prompt, giving it maximum authority.
    claim_block = ""
    verification_instruction = ""
    if user_query:
        try:
            enforcer = PolicyEnforcer(db)
            ctx, instr = enforcer.build_system_prompt_additions(user_query)
            claim_block = ctx
            verification_instruction = instr
        except Exception:
            # Claim ledger unavailable (old schema) — degrade gracefully to
            # legacy abstention policy
            try:
                checkable = is_checkable_fact(user_query)
                if checkable:
                    ledger = ClaimLedger(db)
                    relevant_claims = ledger.search_for_context(user_query, limit=15)
                    has_claims = bool(relevant_claims)
                    claim_block = ledger.format_for_prompt(relevant_claims)
                    verification_instruction = _abstention_policy.get_instruction(
                        is_checkable=True,
                        has_verified_claims=has_claims,
                    )
            except Exception:
                pass

    # Prepend durable user memory facts (with temporal history for changed facts)
    try:
        mem_rows = db.get_current_memory_facts(limit=20)
        if mem_rows:
            fact_lines = []
            for r in mem_rows:
                line = f"  {r['key']}: {r['value']}"
                if r.get("prev_value"):
                    line += f"  [previously: {r['prev_value']}]"
                fact_lines.append(line)
            mem_block = "MEMORY (durable facts about the user):\n" + "\n".join(fact_lines)
            base = mem_block + "\n\n" + base
    except Exception:
        pass  # user_memory table may not exist yet on old schemas

    # ── D-5: Active workspace status block ─────────────────────────────────────
    # Injects a concise "ACTIVE WORKS" summary so the model can answer
    # status questions ("what are we working on?") from authoritative state
    # rather than abstaining. Kept short — one line per work.
    try:
        works = db.list_works(limit=5)
        if works:
            work_lines = ["ACTIVE WORKS IN YOUR WORKSPACE:"]
            for w in works:
                title = w.get("title") or "Untitled"
                wtype = w.get("work_type") or "work"
                doc_count = w.get("doc_count") or 0
                kn_count = w.get("knowledge_count") or 0
                pending = w.get("pending_tasks") or 0
                line = f"  • {title} ({wtype}) — {doc_count} docs, {kn_count} knowledge items"
                if pending:
                    line += f", {pending} pending tasks"
                work_lines.append(line)
            base = "\n".join(work_lines) + "\n\n" + base
    except Exception:
        pass  # degraded gracefully when works table unavailable

    _TRUSTED = {"auto", "approved"}
    work_id = conv.get("work_id")

    # ── Chapter-scoped context injection ───────────────────────────────────────
    # Detection priority (highest first):
    #   1. "chapter N" / "chapter five" — numeric or word-number match on seq.
    #   2. Chapter title match — any chapter whose title appears verbatim in the
    #      query (case-insensitive, ≥ 4 chars, longest title wins on ties).
    # Whichever path matches first injects that chapter's extracted text +
    # its chapter-tagged knowledge items so the model has precise grounding.
    _chapter_block = ""
    if user_query and work_id:
        try:
            import re as _re

            _CH_RE = _re.compile(
                r"\bchapter\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten"
                r"|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen"
                r"|nineteen|twenty)\b",
                _re.IGNORECASE,
            )
            _ORD = {
                "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
                "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
                "nineteen": 19, "twenty": 20,
            }

            _ch_row = None
            _cm = _CH_RE.search(user_query)
            if _cm:
                # Path 1 — "chapter N" / "chapter five"
                _raw = _cm.group(1)
                _ch_num = int(_raw) if _raw.isdigit() else _ORD.get(_raw.lower(), 0)
                if _ch_num > 0:
                    # Chapters stored 0-indexed (seq=0 is chapter 1)
                    with db._lock:
                        _ch_row = db._conn.execute(
                            """SELECT bc.id, bc.title, bc.text
                               FROM book_chapters bc
                               JOIN documents d ON d.id = bc.source_doc_id
                               WHERE d.work_id = ? AND bc.seq = ?
                               ORDER BY bc.created_at LIMIT 1""",
                            (work_id, _ch_num - 1),
                        ).fetchone()

            if _ch_row is None:
                # Path 2 — chapter title mention.
                # Fetch all titles for this work, find the longest one that
                # appears verbatim in the query (longest-wins to be specific).
                with db._lock:
                    _all_titles = db._conn.execute(
                        """SELECT bc.id, bc.title, bc.text
                           FROM book_chapters bc
                           JOIN documents d ON d.id = bc.source_doc_id
                           WHERE d.work_id = ? AND bc.title IS NOT NULL
                             AND length(bc.title) >= 4
                           ORDER BY bc.seq""",
                        (work_id,),
                    ).fetchall()
                _uq = user_query.lower()
                _best: tuple[int, Any] = (0, None)   # (title_len, row)
                for _tr in _all_titles:
                    _t = (_tr["title"] or "").strip()
                    if len(_t) >= 4 and _t.lower() in _uq:
                        if len(_t) > _best[0]:
                            _best = (len(_t), _tr)
                if _best[1] is not None:
                    _ch_row = _best[1]

            if _ch_row:
                _ch_title = _ch_row["title"] or "Chapter"
                _ch_text  = (_ch_row["text"] or "")[:3_000]
                _ch_id    = _ch_row["id"]
                # Chapter-tagged knowledge items
                with db._lock:
                    _ch_k = db._conn.execute(
                        """SELECT text FROM knowledge
                           WHERE chapter_id = ?
                             AND review_status IN ('auto','approved','ai_auto')
                           ORDER BY confidence DESC LIMIT 12""",
                        (_ch_id,),
                    ).fetchall()
                _k_lines = [f"  • {r['text']}" for r in _ch_k]
                _chapter_block = f"CHAPTER CONTEXT — {_ch_title}:\n{_ch_text}"
                if _k_lines:
                    _chapter_block += (
                        "\n\nKNOWLEDGE FROM THIS CHAPTER:\n"
                        + "\n".join(_k_lines)
                    )
                _chapter_block = _chapter_block.strip()
        except Exception:
            _chapter_block = ""

    if _chapter_block:
        base = base + "\n\n" + _chapter_block

    # ── Pinned document injection ──────────────────────────────────────────────
    # When context_doc_ids are provided (user pinned specific files via the
    # mobile "Context" drawer), inject those documents' extracted text verbatim
    # before regular knowledge retrieval so the model always sees them.
    #
    # Authorization / scope policy (enforced server-side; never trust client):
    #
    #   scope="work"  — All pins MUST belong to the conversation's linked Work.
    #                   If the conversation has no linked Work, ALL pins are
    #                   rejected outright (no work context = no work docs).
    #                   Any doc whose work_id differs from the linked Work is
    #                   dropped individually, mirroring existing work-scoped
    #                   knowledge-retrieval isolation.
    #
    #   scope="all"   — Pins may belong to any Work, but each ID must resolve
    #                   to a real document in the database; fabricated IDs are
    #                   silently ignored.
    #
    #   Any other scope value — treated as "work" (allowlist; safest default).
    #
    # Capped at 5 documents × 2 000 chars to stay within context budgets.
    _scope_normalized = scope if scope in ("work", "all") else "work"
    if context_doc_ids:
        _pinned_blocks: list[str] = []
        # scope="work" with no linked Work → reject all pins immediately
        if _scope_normalized == "work" and not work_id:
            logger.debug(
                "_build_system_prompt: context_doc_ids ignored — scope='work' "
                "but conversation has no linked Work"
            )
        else:
            for _pin_id in (context_doc_ids or [])[:5]:
                try:
                    with db._lock:
                        _pin_row = db._conn.execute(
                            "SELECT title, extracted_text, work_id "
                            "FROM documents WHERE id = ?",
                            (_pin_id,),
                        ).fetchone()
                    if not _pin_row or not _pin_row["extracted_text"]:
                        continue  # fabricated / empty — skip
                    # Work-boundary check for work scope
                    if _scope_normalized == "work":
                        # work_id is non-None here (guarded above)
                        if _pin_row["work_id"] != work_id:
                            logger.warning(
                                "_build_system_prompt: pinned doc %s rejected — "
                                "belongs to work %s, conversation work is %s",
                                _pin_id, _pin_row["work_id"], work_id,
                            )
                            continue  # cross-Work doc silently dropped
                    _pin_title = (_pin_row["title"] or _pin_id).strip()
                    _pin_text  = str(_pin_row["extracted_text"])[:2000].strip()
                    _pinned_blocks.append(
                        f"PINNED DOCUMENT — {_pin_title}:\n{_pin_text}"
                    )
                except Exception:
                    pass
        if _pinned_blocks:
            base = base + "\n\n" + "\n\n".join(_pinned_blocks)

    # ── 1. Query-matched global search (primary path) ──────────────────────────
    if user_query and user_query.strip():

        # ── 1a. Temporal / source-filter path ─────────────────────────────────
        # Detects "last week", "past 30 days", "from my PDFs", etc. and routes
        # to targeted filtered DB queries instead of the default hybrid search.
        # This runs BEFORE the hybrid search; if filters are found we always
        # return from here (either with results or with a "nothing found" guard),
        # so the hybrid search only runs for unfiltered queries.
        _qfilter = _detect_query_filters(user_query)
        # scope="work" with no linked Work: the existing recency fallback (step 2)
        # returns base-only to prevent cross-Work exposure.  Mirror that guarantee
        # here — skip the filtered path and fall through to hybrid search or step 2.
        if _qfilter and (scope != "work" or work_id):
            try:
                # Strip temporal/source phrases so FTS only sees content terms.
                # Empty residual → pure date-/kind-only scan (no FTS MATCH).
                _content_q = _strip_filter_phrases(user_query)

                # Scope: when the conversation is linked to a Work, restrict
                # filtered search to that Work so other Works cannot leak in.
                _f_work_ids: list[str] | None = (
                    [work_id] if work_id and scope == "work" else None
                )

                _fk = db.search_knowledge_filtered(
                    _content_q,
                    after_date=_qfilter.get("after_date"),
                    before_date=_qfilter.get("before_date"),
                    doc_kinds=_qfilter.get("doc_kinds") or None,
                    work_ids=_f_work_ids,
                    limit=_CONTEXT_KNOWLEDGE * 3,
                )
                _fc = db.search_chunks_filtered(
                    _content_q,
                    after_date=_qfilter.get("after_date"),
                    before_date=_qfilter.get("before_date"),
                    doc_kinds=_qfilter.get("doc_kinds") or None,
                    work_ids=_f_work_ids,
                    limit=_CONTEXT_CHUNKS * 3,
                )

                # Token budget: 30% of context_window for injected knowledge
                # (same as the hybrid path) so filtered queries cannot crowd
                # out instructions or exceed small model contexts.
                _f_budget = int(_get_effective_context_window(db) * 0.30)
                _f_k_used = 0
                _trusted_fk: list[dict] = []
                for _fki_raw in _fk:
                    if _fki_raw.get("review_status") not in _TRUSTED:
                        continue
                    _fki_text = _fki_raw.get("text", "")
                    _ft_cost = estimate_tokens(_fki_text)
                    if len(_trusted_fk) >= _CONTEXT_KNOWLEDGE:
                        break
                    if _f_k_used + _ft_cost > _f_budget:
                        if not _trusted_fk:
                            # First item alone exceeds the budget — truncate it to fit
                            # rather than skipping it entirely (an empty context leads
                            # to silent 400 context-overflow errors on small windows).
                            _fki_raw = {**_fki_raw,
                                        "text": _fki_text[: _f_budget * _CHARS_PER_TOKEN]}
                            _ft_cost = _f_budget
                        else:
                            break
                    _trusted_fk.append(_fki_raw)
                    _f_k_used += _ft_cost

                _f_c_budget = max(0, _f_budget - _f_k_used)
                _f_c_used = 0
                _trusted_fc: list[dict] = []
                for _fci_raw in _fc:
                    _ft_cost = estimate_tokens(_fci_raw.get("text", ""))
                    if len(_trusted_fc) >= _CONTEXT_CHUNKS or _f_c_used + _ft_cost > _f_c_budget:
                        break
                    _trusted_fc.append(_fci_raw)
                    _f_c_used += _ft_cost

                _fdesc = _qfilter["description"]

                if _trusted_fk or _trusted_fc:
                    _synthesis = (
                        f"FILTER ACTIVE — content retrieved specifically {_fdesc}.\n"
                        "Synthesize and summarize across the items below. "
                        "State how many items were found, what time range or source type "
                        "they cover, and cite source titles where available."
                    )
                    _fparts = [
                        f"FILTERED KNOWLEDGE ({_fdesc.upper()}):\n{_synthesis}"
                    ]

                    # Doc-title cache (same pattern as the hybrid path)
                    _fdoc_cache: dict[str, str] = {}
                    for _fki in _trusted_fk:
                        _dsid = _fki.get("source_doc_id")
                        if _dsid and _dsid not in _fdoc_cache:
                            try:
                                _fd = db.get_document(_dsid)
                                if _fd:
                                    _raw = _fd.get("title") or _fd.get("source", "")
                                    _fdoc_cache[_dsid] = (
                                        _raw.split("/")[-1] if "/" in _raw else _raw
                                    ) or "Document"
                            except Exception:
                                pass

                    for _fki in _trusted_fk:
                        _ft = _fki.get("text", "").strip()
                        _fkind = _fki.get("kind", "note")
                        _dsid = _fki.get("source_doc_id")
                        _fdoc_title = _fdoc_cache.get(_dsid, "") if _dsid else ""
                        _fdate = (_fki.get("created_at") or "")[:10]
                        _fcite = f" | source: \"{_fdoc_title}\"" if _fdoc_title else ""
                        _fdate_tag = f" | {_fdate}" if _fdate else ""
                        if _ft:
                            _fparts.append(
                                f"  [{_fkind}{_fcite}{_fdate_tag}] {_ft[:400]}"
                            )
                        if out_sources is not None:
                            out_sources.append({
                                "id": _fki.get("id"),
                                "title": _fdoc_title or _ft[:100],
                                "kind": _fkind,
                                "work_id": _fki.get("work_id"),
                                "source_doc_id": _dsid,
                                "doc_id": _dsid,
                                "doc_title": _fdoc_title,
                                "passage": _ft[:200],
                                "filter": _fdesc,
                            })

                    for _fci in _trusted_fc:
                        _fraw_text = _fci.get("text", "").strip()
                        _fpfx = (_fci.get("context_prefix") or "").strip()
                        # Surface context prefix + raw text so the model gets the
                        # enriched representation that matches the stored vector.
                        _ft = ((_fpfx + "\n\n" + _fraw_text) if _fpfx else _fraw_text)
                        _fdoc = _fci.get("doc_title") or "document"
                        _fdate = (_fci.get("created_at") or "")[:10]
                        _fdate_tag = f" | {_fdate}" if _fdate else ""
                        if _ft:
                            _fparts.append(
                                f"  [from \"{_fdoc}\"{_fdate_tag}] {_ft[:500]}"
                            )
                        if out_sources is not None:
                            out_sources.append({
                                "id": _fci.get("id"),
                                "title": _fdoc,
                                "kind": "document",
                                "work_id": _fci.get("work_id"),
                                "source_doc_id": _fci.get("doc_id"),
                                "doc_id": _fci.get("doc_id"),
                                "doc_title": _fdoc,
                                "passage": _fraw_text[:200],
                                "filter": _fdesc,
                            })

                    _fknowledge_section = "\n".join(_fparts)
                    _fout = [base]
                    if claim_block:
                        _fout.append(claim_block)
                    if verification_instruction:
                        _fout.append(verification_instruction)
                    _fout.append(_fknowledge_section)
                    return "\n\n".join(p for p in _fout if p.strip())

                else:
                    # Filters matched but no items exist for that range/kind
                    _no_results = (
                        f"FILTER ACTIVE: The library was searched {_fdesc} "
                        f"but no matching items were found. "
                        "Tell the user exactly that — no items found for that time "
                        "range or file type — and suggest broadening the search or "
                        "checking which documents have been imported."
                    )
                    _fout = [base]
                    if claim_block:
                        _fout.append(claim_block)
                    if verification_instruction:
                        _fout.append(verification_instruction)
                    _fout.append(_no_results)
                    return "\n\n".join(p for p in _fout if p.strip())

            except Exception:
                pass  # fall through to hybrid search on any error

        try:
            # ── Adaptive retrieval: classify query → tuned strategy ─────────────
            # The classifier returns one of four types (FACTUAL / SYNTHESIS /
            # COMPARISON / CONVERSATIONAL).  Each type maps to a RetrievalConfig
            # that controls top-k limits and the FTS vs semantic weight split in
            # the hybrid-search RRF fusion.  The classification is cached by
            # message hash so retries never re-run the regex pass.
            from orivellum.capabilities.retrieval import (
                classify_query as _classify_query,
                get_retrieval_config as _get_retrieval_config,
                extract_comparison_entities as _extract_entities,
            )
            _query_type = _classify_query(user_query, db)
            _ret_cfg = _get_retrieval_config(_query_type)

            # Log strategy: INFO line (visible in server logs + system diagnostics)
            # and a row in conversation_events (visible on the diagnostics page).
            logger.info(
                "adaptive_retrieval conv=%s query_type=%s label=%s top_k_k=%d top_k_c=%d",
                (conv.get("id") or "?")[:8], _query_type.value, _ret_cfg.label,
                _ret_cfg.top_k_knowledge, _ret_cfg.top_k_chunks,
            )
            try:
                db.log_conversation_event(
                    conversation_id=conv.get("id") or "",
                    event_type="retrieval_strategy",
                    detail={
                        "query_type": _query_type.value,
                        "label": _ret_cfg.label,
                        "top_k_knowledge": _ret_cfg.top_k_knowledge,
                        "top_k_chunks": _ret_cfg.top_k_chunks,
                        "fts_weight": _ret_cfg.fts_weight,
                        "semantic_weight": _ret_cfg.semantic_weight,
                    },
                )
            except Exception:
                pass  # non-fatal — event table may not exist on old schemas

            # Expose query_type to the caller via out_meta so it can be stored
            # on the message record for client-side diagnostics.
            if out_meta is not None:
                out_meta["query_type"] = _query_type.value
                out_meta["retrieval_strategy"] = _ret_cfg.label

            # Search knowledge items and raw document chunks across ALL works.
            # Hybrid = keyword FTS + semantic vectors (falls back to FTS-only
            # automatically when the embeddings endpoint is unavailable).
            from orivellum.capabilities.embeddings import (
                hybrid_search_knowledge, hybrid_search_chunks,
            )

            # ── COMPARISON: per-entity sub-queries ─────────────────────────────
            # Extract the two (or more) named subjects from the query and run a
            # separate hybrid search for each.  Results are merged by insertion
            # order (first entity first) with de-duplication by id so each
            # subject is fairly represented in the final context window.
            if _ret_cfg.multi_entity:
                _entities = _extract_entities(user_query)
                _k_seen: set = set()
                knowledge_hits: list[dict] = []
                _c_seen: set = set()
                _raw_chunk_hits: list[dict] = []
                _per_entity_k = max(4, _ret_cfg.top_k_knowledge // max(len(_entities), 1))
                _per_entity_c = max(2, _ret_cfg.top_k_chunks // max(len(_entities), 1))
                for _entity in _entities:
                    for _eh in hybrid_search_knowledge(
                        _entity, db,
                        limit=_per_entity_k * 2,
                        fts_weight=_ret_cfg.fts_weight,
                        semantic_weight=_ret_cfg.semantic_weight,
                    ):
                        if _eh.get("id") not in _k_seen:
                            _k_seen.add(_eh["id"])
                            knowledge_hits.append(_eh)
                    for _ec in hybrid_search_chunks(
                        _entity, db, work_id=None,
                        limit=_per_entity_c * 2,
                        fts_weight=_ret_cfg.fts_weight,
                        semantic_weight=_ret_cfg.semantic_weight,
                    ):
                        if _ec.get("id") not in _c_seen:
                            _c_seen.add(_ec["id"])
                            _raw_chunk_hits.append(_ec)
            else:
                knowledge_hits = hybrid_search_knowledge(
                    user_query, db,
                    limit=_ret_cfg.top_k_knowledge * 2,
                    fts_weight=_ret_cfg.fts_weight,
                    semantic_weight=_ret_cfg.semantic_weight,
                )
                _raw_chunk_hits = hybrid_search_chunks(
                    user_query, db, work_id=None,
                    limit=_ret_cfg.top_k_chunks * 2,
                    fts_weight=_ret_cfg.fts_weight,
                    semantic_weight=_ret_cfg.semantic_weight,
                )

            # Re-rank knowledge hits by BM25 (always) + optional LLM listwise
            # (gated by ai_reranking_enabled setting).  Re-ranking bubbles the
            # most query-relevant items to the top before token-budget filtering
            # so the 30% budget is used on the best candidates, not arbitrary ones.
            try:
                from orivellum.capabilities.rerank import rerank_candidates as _rerank
                knowledge_hits = _rerank(user_query, knowledge_hits, db)
            except Exception as _rk_exc:
                logger.debug("Knowledge re-rank skipped (non-fatal): %s", _rk_exc)

            # Token budget: 30% of context_window for all injected knowledge.
            # Count cap (top_k_knowledge / top_k_chunks from strategy config) is a backstop.
            _k_budget = int(_get_effective_context_window(db) * 0.30)

            trusted_k: list[dict] = []
            _k_used = 0
            for _ki in knowledge_hits:
                if _ki.get("review_status") not in _TRUSTED:
                    continue
                _ki_text = _ki.get("text", "")
                _t = estimate_tokens(_ki_text)
                if len(trusted_k) >= _ret_cfg.top_k_knowledge:
                    break
                if _k_used + _t > _k_budget:
                    if not trusted_k:
                        # First item alone exceeds the budget — truncate it to fit
                        # rather than skipping it entirely (an empty context leads
                        # to silent 400 context-overflow errors on small windows).
                        _ki = {**_ki, "text": _ki_text[: _k_budget * _CHARS_PER_TOKEN]}
                        _t = _k_budget
                    else:
                        break
                trusted_k.append(_ki)
                _k_used += _t

            # Re-rank chunk hits by the same pipeline as knowledge hits.
            try:
                from orivellum.capabilities.rerank import rerank_candidates as _rerank_c
                chunk_hits = _rerank_c(user_query, _raw_chunk_hits, db)
            except Exception as _rk_c_exc:
                logger.debug("Chunk re-rank skipped (non-fatal): %s", _rk_c_exc)
                chunk_hits = _raw_chunk_hits

            # Chunks share what remains of the 30% budget after knowledge items
            _c_budget = max(0, _k_budget - _k_used)
            trusted_c: list[dict] = []
            _c_used = 0
            for _ci in chunk_hits:
                _t = estimate_tokens(_ci.get("text", ""))
                if len(trusted_c) >= _ret_cfg.top_k_chunks or _c_used + _t > _c_budget:
                    break
                trusted_c.append(_ci)
                _c_used += _t

            if trusted_k or trusted_c:
                # Group by work so the AI sees topics clearly
                by_work: dict[str, dict] = {}
                work_title_cache: dict[str, str] = {}

                def _work_title(wid: str | None) -> str:
                    if not wid:
                        return "General"
                    if wid not in work_title_cache:
                        w = db.get_work(wid)
                        work_title_cache[wid] = (w.get("title") or wid) if w else wid
                    return work_title_cache[wid]

                # Batch-fetch document titles for knowledge items with source_doc_id
                _doc_title_cache: dict[str, str] = {}
                _doc_ids = {k.get("source_doc_id") for k in trusted_k if k.get("source_doc_id")}
                for _doc_id in _doc_ids:
                    try:
                        _doc = db.get_document(_doc_id)
                        if _doc:
                            _raw = _doc.get("title") or _doc.get("source", "")
                            _doc_title_cache[_doc_id] = (
                                _raw.split("/")[-1] if "/" in _raw else _raw
                            ) or "Document"
                    except Exception:
                        pass

                for k in trusted_k:
                    wid = k.get("work_id") or "__general__"
                    by_work.setdefault(wid, {"title": _work_title(k.get("work_id")),
                                             "knowledge": [], "chunks": []})
                    by_work[wid]["knowledge"].append(k)

                for c in trusted_c:
                    wid = c.get("work_id") or "__general__"
                    by_work.setdefault(wid, {"title": _work_title(c.get("work_id")),
                                             "knowledge": [], "chunks": []})
                    by_work[wid]["chunks"].append(c)

                # Boost the linked Work to the top if present
                ordered = sorted(
                    by_work.items(),
                    key=lambda kv: (0 if kv[0] == work_id else 1, kv[1]["title"])
                )

                context_parts = [
                    "KNOWLEDGE FROM YOUR DATABASE (most relevant to this question):\n"
                    "When referencing facts from this section, cite the source document "
                    "title in your answer so the user knows where the information comes from."
                ]
                for wid, group in ordered:
                    context_parts.append(f"\n[Topic: {group['title']}]")
                    for k in group["knowledge"]:
                        text = k.get("text", "").strip()
                        kind = k.get("kind", "note")
                        src_doc_id = k.get("source_doc_id")
                        doc_title = _doc_title_cache.get(src_doc_id, "") if src_doc_id else ""
                        if text:
                            cite = f" | source: \"{doc_title}\"" if doc_title else ""
                            context_parts.append(f"  [{kind}{cite}] {text[:400]}")
                            if out_sources is not None:
                                real_wid = k.get("work_id")
                                out_sources.append({
                                    "id": k.get("id"),
                                    "title": doc_title or text[:100],
                                    "kind": kind,
                                    "work_id": real_wid,
                                    "work_title": group["title"],
                                    "source_doc_id": src_doc_id,
                                    # Legacy fields kept for the existing footer link
                                    "doc_id": src_doc_id,
                                    "doc_title": doc_title or group["title"],
                                    "passage": text[:200],
                                })
                    for c in group["chunks"]:
                        raw_text = c.get("text", "").strip()
                        prefix = (c.get("context_prefix") or "").strip()
                        # Prepend AI-generated context prefix when present so the
                        # model receives the enriched chunk that matches the vector.
                        text = (prefix + "\n\n" + raw_text) if prefix else raw_text
                        doc  = c.get("doc_title") or "document"
                        if text:
                            context_parts.append(f"  [from \"{doc}\"] {text[:500]}")
                            if out_sources is not None:
                                real_wid = c.get("work_id")
                                out_sources.append({
                                    "id": c.get("id"),
                                    "title": doc,
                                    "kind": "document",
                                    "work_id": real_wid,
                                    "work_title": group["title"],
                                    "source_doc_id": c.get("doc_id"),
                                    # Legacy fields kept for the existing footer link
                                    "doc_id": c.get("doc_id"),
                                    "doc_title": doc,
                                    "passage": raw_text[:200],
                                })

                # ── Prepend claim block; append verification instruction ──
                knowledge_section = "\n".join(context_parts)
                parts = [base]
                if claim_block:
                    parts.append(claim_block)
                if verification_instruction:
                    parts.append(verification_instruction)
                parts.append(knowledge_section)
                return "\n\n".join(p for p in parts if p.strip())

            else:
                # No knowledge found — inject a corpus abstention guard so the
                # model doesn't fabricate document content.
                abstention_guard = (
                    "CORPUS SEARCH: Your library was searched but no relevant "
                    "information was found for this query. "
                    "If the user is asking about specific content from their uploaded "
                    "documents, respond with: "
                    "\"I don't have that information in your library\" "
                    "— do not invent document content or fabricate citations."
                )
                parts = [base]
                if claim_block:
                    parts.append(claim_block)
                if verification_instruction:
                    parts.append(verification_instruction)
                parts.append(abstention_guard)
                return "\n\n".join(p for p in parts if p.strip())

        except Exception:
            pass  # fall through to recency-based fallback

    # ── 2. Recency fallback (no query or search failed) ────────────────────────
    # scope="work" with no linked Work → never inject knowledge from other Works.
    if scope == "work" and not work_id:
        if claim_block or verification_instruction:
            parts = [base]
            if claim_block:
                parts.append(claim_block)
            if verification_instruction:
                parts.append(verification_instruction)
            return "\n\n".join(p for p in parts if p.strip())
        return base

    fallback_wid = work_id  # prefer linked Work; None = all works

    # Token budget for the recency fallback: 30% of context_window (same as primary path)
    _fb_budget = int(_get_effective_context_window(db) * 0.30)

    def _trim_by_budget(candidates: list[dict]) -> list[dict]:
        """Return trusted knowledge items within the token budget.

        If the very first trusted item is larger than the entire budget it is
        truncated to fit rather than skipped, so the model always receives at
        least one item's worth of context (prevents silent 400 overflow errors
        when a single item is larger than 30 % of the context window).
        """
        result: list[dict] = []
        used = 0
        for _k in candidates:
            if _k.get("review_status") not in _TRUSTED:
                continue
            _k_text = _k.get("text", "")
            _t = estimate_tokens(_k_text)
            if len(result) >= _CONTEXT_KNOWLEDGE:
                break
            if used + _t > _fb_budget:
                if not result:
                    # First item alone exceeds the budget — truncate to fit.
                    _k = {**_k, "text": _k_text[: _fb_budget * _CHARS_PER_TOKEN]}
                    _t = _fb_budget
                else:
                    break
            result.append(_k)
            used += _t
        return result

    all_knowledge = db.list_knowledge(work_id=fallback_wid,
                                      limit=_CONTEXT_KNOWLEDGE * 4)
    knowledge = _trim_by_budget(all_knowledge)

    if not knowledge and fallback_wid:
        # No knowledge in the linked Work — broaden to all works
        all_knowledge = db.list_knowledge(work_id=None, limit=_CONTEXT_KNOWLEDGE * 4)
        knowledge = _trim_by_budget(all_knowledge)

    if not knowledge:
        # Still inject claim block + verification instruction even with no knowledge
        if claim_block or verification_instruction:
            parts = [base]
            if claim_block:
                parts.append(claim_block)
            if verification_instruction:
                parts.append(verification_instruction)
            return "\n\n".join(p for p in parts if p.strip())
        return base

    header = (
        f"You are assisting with the work \"{db.get_work(work_id).get('title', '')}\". "
        if work_id and db.get_work(work_id) else
        "Knowledge from your database:"
    )
    context_parts = [header]
    for k in knowledge:
        kind = k.get("kind", "note")
        text = k.get("text", "").strip()
        if text:
            context_parts.append(f"  [{kind}] {text[:400]}")

    knowledge_section = "\n".join(context_parts)
    parts = [base]
    if claim_block:
        parts.append(claim_block)
    if verification_instruction:
        parts.append(verification_instruction)
    parts.append(knowledge_section)
    return "\n\n".join(p for p in parts if p.strip())


def _build_messages(
    db: Any,
    conv: dict,
    new_user_text: str,
    scope: str = "work",
    image_b64: str | None = None,
    image_media_type: str = "image/jpeg",
    out_sources: list | None = None,
    out_meta: dict | None = None,
    context_doc_ids: list[str] | None = None,
) -> list[dict]:
    """Build the full OpenAI-format messages array for this conversation."""
    system_prompt = _build_system_prompt(db, conv, scope=scope,
                                         user_query=new_user_text,
                                         out_sources=out_sources,
                                         out_meta=out_meta,
                                         context_doc_ids=context_doc_ids or [])

    # ── Web search grounding ──────────────────────────────────────────────────
    # When the conversation has web_search_enabled=1, fetch live Tavily results
    # and append them to the system prompt so the model can answer from current
    # web material alongside local library knowledge.  Always runs after local
    # knowledge injection (local knowledge has priority).
    # Never raises — any Tavily failure is silently swallowed.
    if conv.get("web_search_enabled") and new_user_text and new_user_text.strip():
        try:
            from orivellum.capabilities.websearch import fetch_web_context
            web_results = fetch_web_context(new_user_text.strip(), max_results=3, timeout=5)
            if web_results:
                web_lines = [
                    "WEB SOURCES (live web results — use these to answer current/recent questions, "
                    "cite URLs inline when referencing them):"
                ]
                for i, r in enumerate(web_results, 1):
                    title   = r.get("title", "").strip()
                    url     = r.get("url", "").strip()
                    content = r.get("content", "").strip()[:600]
                    entry = f"[{i}] {title}"
                    if content:
                        entry += f"\n{content}"
                    entry += f"\nURL: {url}"
                    web_lines.append(entry)
                system_prompt = system_prompt + "\n\n" + "\n\n".join(web_lines)
                if out_sources is not None:
                    _seen_web_urls: set[str] = set()
                    for r in web_results:
                        url   = r.get("url", "").strip()
                        title = r.get("title", "").strip() or url or "Web"
                        if url and url not in _seen_web_urls:
                            _seen_web_urls.add(url)
                            out_sources.append({
                                "id":    url,
                                "title": title,
                                "kind":  "web",
                                "url":   url,
                                "isWeb": True,
                            })
        except Exception as _ws_exc:
            logger.debug("web search grounding failed (non-fatal): %s", _ws_exc)

    # ── Context summary injection (BEFORE token budget trim) ─────────────────
    # When the conversation has a rolling context_summary (generated by the
    # background sliding-window summarizer), prepend it to the system prompt as
    # a clearly-labelled block.  This MUST happen before the token-budget trim
    # so the summary's token cost is already reflected in the budget deduction,
    # preventing the summary from silently pushing the prompt beyond the model's
    # context window.
    _context_summary: str | None = conv.get("context_summary") or None
    if _context_summary:
        summary_block = (
            "\n\n[EARLIER CONVERSATION SUMMARY — this condenses earlier exchanges "
            "that are no longer in the message history]\n"
            f"{_context_summary.strip()}\n"
            "[END OF SUMMARY]"
        )
        system_prompt = system_prompt + summary_block

    # Fetch the most-recent history (excluding the message we just stored).
    # get_recent_messages() uses ORDER BY created_at DESC LIMIT ? then reverses,
    # so we always receive the true NEWEST messages — not the oldest ones that
    # an ASC-with-LIMIT query would return.
    history = db.get_recent_messages(conv["id"], limit=_HISTORY_LIMIT + 1)
    # The last message is the one we just stored — exclude it from history
    # (we add it as the final user turn below)
    prior = [m for m in history if not (m["role"] == "user" and m["text"] == new_user_text)]
    # Keep within context limit (already bounded by the fetch limit; trim for safety)
    prior = prior[-_HISTORY_LIMIT:]

    # ── Token-aware history trimming ──────────────────────────────────────────
    # Estimate token counts (4 chars ≈ 1 token) and drop oldest messages first
    # so the combined prompt stays within 80% of the model's context window.
    # The system_prompt cost already includes the context_summary block (added
    # above) so the summary tokens are correctly deducted here.
    try:
        _ctx = _get_effective_context_window(db)
        _budget = int(_ctx * 0.80)
        # Deduct system prompt (incl. summary) and a 256-token margin for reply
        _budget -= estimate_tokens(system_prompt) + 256
        if _budget > 0:
            _trimmed: list[dict] = []
            _remain = _budget
            for _m in reversed(prior):
                _t = len(_m.get("text", "")) // _CHARS_PER_TOKEN
                if _remain - _t < 0:
                    break
                _trimmed.insert(0, _m)
                _remain -= _t
            if len(_trimmed) < len(prior):
                logger.debug(
                    "Token budget: trimmed history from %d → %d messages (ctx=%d)",
                    len(prior), len(_trimmed), _ctx,
                )
            prior = _trimmed
    except Exception:
        pass  # trimming is best-effort — fall through with untrimmed history

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for m in prior:
        role = m["role"] if m["role"] in ("user", "assistant") else "user"
        messages.append({"role": role, "content": m["text"]})

    # Final user turn — multipart content when an image is attached
    if image_b64:
        messages.append({"role": "user", "content": [
            {"type": "text", "text": new_user_text or "What is in this image?"},
            {"type": "image_url", "image_url": {
                "url": f"data:{image_media_type};base64,{image_b64}",
            }},
        ]})
    else:
        messages.append({"role": "user", "content": new_user_text})
    return messages


# ──────────────────────────────────────────────────────────────────────────────
# AI calls
# ──────────────────────────────────────────────────────────────────────────────

_UNAVAILABLE = (
    "The local AI service is currently unavailable. "
    "Start Lemonade (default port 13305) or Ollama (`ollama serve`) "
    "and configure `ORIVELLUM_AI_URL` if needed. Your message has been saved."
)


async def _call_ai(messages: list[dict], model: str, db: Any = None) -> str:
    """Call the AI endpoint (Lemonade / Ollama / any OpenAI-compat server).

    Routes through the central ``llm_call`` gateway (run in a threadpool so
    this async call site keeps its non-blocking character).  Returns the
    reply text, or the unavailable message on any failure.
    """
    from starlette.concurrency import run_in_threadpool
    from orivellum.capabilities.llm import llm_call

    cfg = get_config()
    result = await run_in_threadpool(
        llm_call, messages,
        base_url=cfg.serving.base_url, model=model,
        timeout=cfg.serving.timeout_sec, purpose="chat", db=db,
    )
    if not result.ok or result.text is None:
        return _UNAVAILABLE
    return result.text


# Keywords that local model servers (Ollama, LM Studio, llama.cpp) include in
# their error payloads when a non-vision model receives an image message.
_VISION_ERROR_HINTS = (
    "does not support image", "not multimodal", "multimodal not",
    "vision not", "not support vision", "image input", "image_url",
    "does not support vision", "images are not", "image is not",
    "unsupported content type", "unsupported message content",
)


def _is_vision_error(error: str | None) -> bool:
    """Return True when an LLM error string indicates vision is unsupported."""
    if not error:
        return False
    low = error.lower()
    # 4xx HTTP errors from the model server while an image was in the payload
    # are almost always "model doesn't support vision"; capture them too.
    if "400" in low or "422" in low or "unsupported" in low:
        return True
    return any(hint in low for hint in _VISION_ERROR_HINTS)


async def _call_ai_vision(messages: list[dict], model: str, db: Any = None) -> str:
    """Like _call_ai but raises HTTP 422 when the model rejects image input.

    Used only for the non-streaming path when the request contains an image.
    The 422 lets clients (mobile, web) detect the failure reliably and show
    an actionable message rather than the generic "AI unavailable" text.
    """
    from starlette.concurrency import run_in_threadpool
    from orivellum.capabilities.llm import llm_call

    cfg = get_config()
    result = await run_in_threadpool(
        llm_call, messages,
        base_url=cfg.serving.base_url, model=model,
        timeout=cfg.serving.timeout_sec, purpose="chat", db=db,
    )
    if not result.ok or result.text is None:
        if _is_vision_error(result.error):
            raise HTTPException(
                422,
                detail=(
                    "VISION_NOT_SUPPORTED: The configured model does not support "
                    "image input. Set a vision-capable model in System Settings "
                    f"(e.g. llava, qwen2-vl, llama3.2-vision). Model: {model}"
                ),
            )
        return _UNAVAILABLE
    return result.text


async def _stream_response(
    db: Any, conv: dict, user_text: str, deep: bool = False, scope: str = "work",
    image_b64: str | None = None, image_media_type: str = "image/jpeg",
    context_doc_ids: list[str] | None = None,
):
    """SSE generator — streams tokens, stores final reply, auto-titles.

    When deep=True the cognition gate runs first:
      - "clarify"  → persist + emit clarify SSE event with amber-bubble metadata, return early
      - "complex"  → run Author→Critic→Synthesizer council; persist BEFORE streaming chunks
                     so a mid-stream disconnect never loses the reply
      - "direct"   → fall through to the normal single-call streaming path

    Handles client disconnect (GeneratorExit) by persisting whatever tokens
    arrived before the connection dropped, so the conversation is never left
    with a missing assistant turn.
    """
    import asyncio
    import time as _time
    from orivellum.capabilities.llm import record_llm_call

    cfg = get_config()
    conv_id = conv["id"]
    _sources: list = []
    _stream_strategy_meta: dict = {}
    messages = _build_messages(
        db, conv, user_text, scope=scope,
        image_b64=image_b64, image_media_type=image_media_type,
        out_sources=_sources,
        out_meta=_stream_strategy_meta,
        context_doc_ids=context_doc_ids or [],
    )
    # Deduplicate sources by doc_id
    _seen: set = set()
    sources: list = []
    for s in _sources:
        key = s.get("id") or s.get("source_doc_id") or s.get("title")
        if key and key not in _seen:
            _seen.add(key)
            sources.append(s)
    full_reply = ""
    thinking_text = ""   # accumulated <think> / reasoning_content text
    _in_think = False    # True while inside a <think>…</think> block
    _tag_buf = ""        # partial-tag detection buffer (handles cross-token tags)
    model = _model_for_vision(conv) if image_b64 else _model_for(conv)

    # ── Telemetry: time the whole generator and record ONCE in the finally ────
    # This covers every terminal path — intent/tool short-circuit, clarify,
    # council success, direct stream, timeout, error, and client disconnect
    # (GeneratorExit). purpose reflects which branch produced the reply.
    _stream_started = _time.monotonic()
    _stream_ok = True
    _stream_err: str | None = None
    _stream_purpose = "chat.stream"
    # Initialise before the try block so the GeneratorExit handler can always
    # reference _assist_id even when an early-return path (intent/clarify/council)
    # fires before the stub is created.
    _assist_id: str = ""
    try:
        # ── Intent routing — runs before deep mode and normal AI ──────────────
        _stream_work_id = conv.get("work_id")
        tool_result = await _maybe_dispatch_intent(db, user_text, cfg.serving.base_url, model, work_id=_stream_work_id)
        if tool_result is not None:
            tool_text, tool_meta = tool_result
            if sources:
                # Merge knowledge sources with any tool-specific sources (e.g. web search)
                existing = tool_meta.get("sources", [])
                tool_meta = {**tool_meta, "sources": [*existing, *sources]}
            # Persist before streaming (disconnect-safe)
            db.add_message(conv_id, "assistant", tool_text, meta=tool_meta)
            _maybe_auto_title(db, conv, user_text)
            _stream_purpose = "chat.intent"
            # Background: embed + infer memory (intent path)
            try:
                from orivellum.api.executor import _tracked_submit as _ts_intent
                _ts_intent(_post_reply_background, db, conv_id, user_text, tool_text,
                           kind="chat", label="post_reply_bg")
            except Exception as _exc_intent:
                logger.warning("Executor unavailable for post_reply_bg (stream intent), falling back to thread: %s",
                               _exc_intent)
                import threading as _t
                _t.Thread(target=_post_reply_background,
                          args=(db, conv_id, user_text, tool_text), daemon=True).start()
            _CHUNK = 40
            for i in range(0, len(tool_text), _CHUNK):
                yield f"data: {json.dumps({'token': tool_text[i:i+_CHUNK], 'intent': tool_meta.get('intent')})}\n\n"
            # Emit the merged source list (web + knowledge) via the SSE sources sentinel
            all_sources = tool_meta.get("sources", [])
            if all_sources:
                yield f"data: {json.dumps({'sources': all_sources})}\n\n"
            yield "data: [DONE]\n\n"
            return

        # ── Deep mode: run the meta-prompt gate ────────────────────────────────
        if deep:
            from orivellum.capabilities.cognition import (
                classify, get_clarifying_question, deliberate, update_compass,
            )

            route = await asyncio.to_thread(
                classify, user_text, messages[:-1], cfg.serving.base_url, model, db
            )
            logger.debug("Cognition gate for conv %s: %s", conv_id, route)

            if route == "clarify":
                question = await asyncio.to_thread(
                    get_clarifying_question, user_text, cfg.serving.base_url, model, db
                )
                # Persist the clarifying question so it survives refetch/reload.
                # The isClarification flag lets the frontend render it with the amber bubble style.
                clarify_meta: dict = {"model": model, "isClarification": True}
                if sources:
                    clarify_meta["sources"] = sources
                db.add_message(conv_id, "assistant", question, meta=clarify_meta)
                _maybe_auto_title(db, conv, user_text)
                _stream_purpose = "chat.clarify"
                # Background: embed + infer memory (clarify path)
                try:
                    from orivellum.api.executor import _tracked_submit as _ts_clarify
                    _ts_clarify(_post_reply_background, db, conv_id, user_text, question,
                                kind="chat", label="post_reply_bg")
                except Exception as _exc_clarify:
                    logger.warning("Executor unavailable for post_reply_bg (stream clarify), falling back to thread: %s",
                                   _exc_clarify)
                    import threading as _t2
                    _t2.Thread(target=_post_reply_background,
                               args=(db, conv_id, user_text, question), daemon=True).start()
                # Also emit a typed SSE event so the frontend can display immediately
                # without waiting for the query invalidation round-trip.
                yield f"data: {json.dumps({'event': 'clarify', 'question': question})}\n\n"
                if sources:
                    yield f"data: {json.dumps({'sources': sources})}\n\n"
                yield "data: [DONE]\n\n"
                return

            if route == "complex":
                council_reply = await asyncio.to_thread(
                    deliberate, messages, cfg.serving.base_url, model, db
                )
                if council_reply:
                    # ── Disconnect-safe persistence ──────────────────────────
                    # Save the full reply BEFORE yielding any chunks.  This way a
                    # GeneratorExit raised during the chunk loop still results in
                    # a saved assistant turn — the client just misses the UX.
                    council_meta: dict = {"model": model, "council": True}
                    if sources:
                        council_meta["sources"] = sources
                    db.add_message(conv_id, "assistant", council_reply,
                                   meta=council_meta)
                    _maybe_auto_title(db, conv, user_text)
                    _stream_purpose = "chat.council"
                    # Background: embed + infer memory (council path)
                    try:
                        from orivellum.api.executor import _tracked_submit as _ts_council
                        _ts_council(_post_reply_background, db, conv_id, user_text, council_reply,
                                    kind="chat", label="post_reply_bg")
                    except Exception as _exc_council_s:
                        logger.warning("Executor unavailable for post_reply_bg (stream council), falling back to thread: %s",
                                       _exc_council_s)
                        import threading as _t3
                        _t3.Thread(target=_post_reply_background,
                                   args=(db, conv_id, user_text, council_reply),
                                   daemon=True).start()
                    # Update Project Compass (merge — preserves next_step if set)
                    work_id = conv.get("work_id")
                    if work_id:
                        await asyncio.to_thread(
                            update_compass, db, work_id,
                            focus=user_text[:200],
                            reasoning=council_reply[:500],
                        )
                    # Stream chunks for UI responsiveness (persistence done above)
                    _CHUNK = 30
                    for i in range(0, len(council_reply), _CHUNK):
                        yield f"data: {json.dumps({'token': council_reply[i:i+_CHUNK]})}\n\n"
                    if sources:
                        yield f"data: {json.dumps({'sources': sources})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                # Council failed → fall through to the direct streaming path

        # ── Pre-create assistant message stub (state=queued) ─────────────────
        # Creating the stub before any AI call means the message is already
        # in the database even if the client disconnects during streaming.
        # The message_id is emitted immediately so the client can associate
        # incoming token SSE events with the right DB row.
        _assist_meta: dict = {"model": model}
        if _stream_strategy_meta.get("retrieval_strategy"):
            _assist_meta["retrieval_strategy"] = _stream_strategy_meta["retrieval_strategy"]
            _assist_meta["query_type"] = _stream_strategy_meta.get("query_type", "")
        _assist_stub = db.add_message(conv_id, "assistant", "", state="queued",
                                      meta=_assist_meta)
        _assist_id = _assist_stub["id"]
        yield f"data: {json.dumps({'message_id': _assist_id, 'state': 'queued'})}\n\n"
        _finish_reason: str | None = None

        # ── Transition to 'running' — AI call is about to begin ───────────────
        try:
            db.transition_message(_assist_id, "running")
        except Exception:
            pass  # non-fatal if transition fails; streaming still proceeds

        # ── Per-chunk silence timeout ─────────────────────────────────────────
        # If the AI server sends no new token for this long, treat the stream as
        # stalled and close it cleanly. The timeout is enforced per-chunk (not
        # just for the initial connection) using asyncio.wait_for per __anext__.
        _CHUNK_TIMEOUT_SEC = 30
        _first_token_received = False
        _timed_out = False  # set True on asyncio.TimeoutError to mark message incomplete

        try:
            import httpx
            async with httpx.AsyncClient(timeout=cfg.serving.timeout_sec) as client:
                async with client.stream(
                    "POST",
                    f"{cfg.serving.base_url}/chat/completions",
                    json={
                        "model": model,
                        "messages": messages,
                        "stream": True,
                    },
                ) as resp:
                    resp.raise_for_status()
                    _line_iter = resp.aiter_lines().__aiter__()
                    while True:
                        try:
                            line = await asyncio.wait_for(
                                _line_iter.__anext__(), timeout=_CHUNK_TIMEOUT_SEC
                            )
                        except StopAsyncIteration:
                            break
                        if not line.startswith("data: "):
                            continue
                        chunk = line[6:]
                        if chunk.strip() == "[DONE]":
                            break
                        try:
                            d = json.loads(chunk)
                            choice0 = d["choices"][0]
                            # Capture finish_reason — set on the final chunk
                            fr = choice0.get("finish_reason")
                            if fr:
                                _finish_reason = fr
                            delta = choice0["delta"]
                            # Some providers (e.g. DeepSeek via OpenRouter) emit
                            # reasoning in a separate field rather than inside content.
                            reasoning = delta.get("reasoning_content") or ""
                            if reasoning:
                                thinking_text += reasoning
                                yield f"data: {json.dumps({'thinking': reasoning})}\n\n"
                            raw = delta.get("content") or ""
                            if raw:
                                _tag_buf += raw
                                # Process buffer, splitting on <think> / </think> tags.
                                # The while-loop drains _tag_buf until a partial tag
                                # (or empty buffer) remains.
                                while _tag_buf:
                                    if not _in_think:
                                        idx = _tag_buf.find("<think>")
                                        if idx == -1:
                                            # No complete open tag — flush everything
                                            # except a possible partial suffix.
                                            partial = 0
                                            for l in range(min(7, len(_tag_buf)), 0, -1):
                                                if _tag_buf[-l:] == "<think>"[:l]:
                                                    partial = l
                                                    break
                                            flush = _tag_buf[: len(_tag_buf) - partial]
                                            _tag_buf = _tag_buf[len(_tag_buf) - partial :]
                                            if flush:
                                                # First real content token — advance to 'streaming'
                                                if not _first_token_received:
                                                    _first_token_received = True
                                                    try:
                                                        db.transition_message(_assist_id, "streaming")
                                                    except Exception:
                                                        pass
                                                full_reply += flush
                                                yield f"data: {json.dumps({'token': flush})}\n\n"
                                            break
                                        before = _tag_buf[:idx]
                                        if before:
                                            if not _first_token_received:
                                                _first_token_received = True
                                                try:
                                                    db.transition_message(_assist_id, "streaming")
                                                except Exception:
                                                    pass
                                            full_reply += before
                                            yield f"data: {json.dumps({'token': before})}\n\n"
                                        _in_think = True
                                        _tag_buf = _tag_buf[idx + 7 :]
                                    else:
                                        idx = _tag_buf.find("</think>")
                                        if idx == -1:
                                            partial = 0
                                            for l in range(min(8, len(_tag_buf)), 0, -1):
                                                if _tag_buf[-l:] == "</think>"[:l]:
                                                    partial = l
                                                    break
                                            flush = _tag_buf[: len(_tag_buf) - partial]
                                            _tag_buf = _tag_buf[len(_tag_buf) - partial :]
                                            if flush:
                                                thinking_text += flush
                                                yield f"data: {json.dumps({'thinking': flush})}\n\n"
                                            break
                                        think_chunk = _tag_buf[:idx]
                                        if think_chunk:
                                            thinking_text += think_chunk
                                            yield f"data: {json.dumps({'thinking': think_chunk})}\n\n"
                                        _in_think = False
                                        _tag_buf = _tag_buf[idx + 8 :]
                        except Exception:
                            pass
        except asyncio.TimeoutError:
            logger.warning("AI stream timed out after %ss of silence (conv=%s)",
                           _CHUNK_TIMEOUT_SEC, conv_id)
            _stream_ok = False
            _timed_out = True
            _stream_err = f"stream silent for {_CHUNK_TIMEOUT_SEC}s"
            # Emit a [TIMEOUT] sentinel so the client can show the re-send affordance
            # immediately without waiting for the [DONE] event.
            yield f"data: {json.dumps({'timeout': True, 'message_id': _assist_id})}\n\n"
            if not full_reply:
                full_reply = _UNAVAILABLE
                yield f"data: {json.dumps({'token': full_reply})}\n\n"

        except Exception as exc:
            logger.warning("AI stream failed: %s", exc)
            _stream_ok = False
            _stream_err = f"{type(exc).__name__}: {exc}"[:500]
            full_reply = _UNAVAILABLE
            yield f"data: {json.dumps({'token': full_reply})}\n\n"

        # Normal completion path (also reached after AI failure fallback)
        # Use finalize_message instead of add_message — the stub already exists.
        _final_state = "done" if _stream_ok else "failed"
        # Detect token-limit truncation from the provider's finish_reason.
        # "length" = OpenAI/Ollama, "max_tokens" = some Anthropic-compat servers.
        _cut_short = _stream_ok and _finish_reason in ("length", "max_tokens")
        meta: dict = {"model": model}
        if thinking_text:
            meta["thinking"] = thinking_text
        if sources:
            meta["sources"] = sources
        if _cut_short:
            meta["cut_short"] = True
            meta["partial_text"] = full_reply  # clean text; no suffix appended
        # Treat timed-out messages the same as token-limit truncations so the
        # continuation selector and the re-send button both pick them up.
        # incomplete=True is the new flag; cut_short=True preserves backward
        # compatibility with UI code that only watches for cut_short.
        if _timed_out:
            meta["incomplete"] = True
            meta["cut_short"] = True
            meta["partial_text"] = full_reply  # preserve whatever arrived before stall
        if full_reply:
            # Update text + metadata on the stub, then set terminal state
            with db._lock:
                db._conn.execute(
                    "UPDATE messages SET meta=? WHERE id=?",
                    (json.dumps(meta), _assist_id),
                )
                db._conn.commit()
            db.finalize_message(_assist_id, full_reply, _final_state)
        else:
            # No reply at all — just mark the stub as failed
            try:
                db.transition_message(_assist_id, "failed")
            except Exception:
                pass
        _maybe_auto_title(db, conv, user_text)
        # Background: embed exchange + inference memory capture (streaming)
        if full_reply and _stream_ok:
            try:
                from orivellum.api.executor import _tracked_submit as _ts_prb5
                _ts_prb5(_post_reply_background, db, conv_id, user_text, full_reply,
                         kind="chat", label="post_reply_bg")
            except Exception as _exc_prb5:
                logger.warning("Executor unavailable for post_reply_bg (stream direct), falling back to thread: %s",
                               _exc_prb5)
                import threading as _thr_prb5
                _thr_prb5.Thread(target=_post_reply_background,
                                 args=(db, conv_id, user_text, full_reply),
                                 daemon=True).start()

        # PKLOS output validation (streaming path).
        # After the full reply is accumulated and persisted, check it against the
        # claim ledger.  If a hard violation is found, replace the stored message
        # with the safe fallback and emit a pklos_correction SSE event so the
        # client can update the rendered bubble without a full reload.
        if full_reply and _stream_ok and is_checkable_fact(user_text):
            try:
                _ov = OutputValidator(db)
                _ov_claims = db.search_claims_for_context(user_text, limit=15)
                _ov_result = _ov.validate(user_text, full_reply, verified_claims=_ov_claims)
                if _ov_result.must_regenerate:
                    _correction = _ov.build_fallback_answer(user_text, _ov_claims)
                    db.finalize_message(_assist_id, _correction, "done")
                    logger.info(
                        "OutputValidator (stream): corrected reply for conv %s (%d violations)",
                        conv_id, sum(1 for v in _ov_result.violations if v.startswith("HARD")),
                    )
                    yield f"data: {json.dumps({'pklos_correction': _correction, 'message_id': _assist_id})}\n\n"
            except Exception as _ov_exc:
                logger.debug("OutputValidator (stream) skipped (non-fatal): %s", _ov_exc)

        if sources:
            import json as _json
            yield f"data: {_json.dumps({'sources': sources})}\n\n"
        if _cut_short:
            yield f"data: {json.dumps({'cut_short': True, 'message_id': _assist_id})}\n\n"
        yield "data: [DONE]\n\n"

    except GeneratorExit:
        # Client disconnected mid-stream — save whatever tokens arrived so the
        # conversation isn't left with only the user turn and no reply.
        _stream_ok = False
        _stream_err = "client_disconnected"
        if _assist_id:
            # Stub was created — finalize it with whatever arrived before disconnect.
            try:
                if full_reply:
                    truncated = full_reply + "\n\n*(Response was cut short — re-send to continue.)*"
                    _meta: dict = {
                        "model": model,
                        "cut_short": True,
                        # Store clean partial so /continue can resume without re-parsing the suffix
                        "partial_text": full_reply,
                    }
                    if thinking_text:
                        _meta["thinking"] = thinking_text
                    if sources:
                        _meta["sources"] = sources
                    with db._lock:
                        db._conn.execute(
                            "UPDATE messages SET meta=? WHERE id=?",
                            (json.dumps(_meta), _assist_id),
                        )
                        db._conn.commit()
                    db.finalize_message(_assist_id, truncated, "failed")
                else:
                    db.transition_message(_assist_id, "failed")
                _maybe_auto_title(db, conv, user_text)
            except Exception as save_exc:
                logger.warning("Could not persist partial reply on disconnect: %s", save_exc)
        raise  # Re-raise so the async generator closes properly

    finally:
        # Single telemetry record covering EVERY terminal path — early returns
        # (intent/clarify/council), normal completion, timeout, error, and
        # client disconnect. Tokens are unavailable in the streaming path.
        record_llm_call(
            db, purpose=_stream_purpose, model=model,
            latency_ms=int((_time.monotonic() - _stream_started) * 1000),
            prompt_tokens=None, completion_tokens=None,
            ok=_stream_ok, error=_stream_err,
        )


# ──────────────────────────────────────────────────────────────────────────────
# Continuation streaming generator
# ──────────────────────────────────────────────────────────────────────────────

async def _stream_continuation(db: Any, conv: dict, cut_short_msg: dict):
    """SSE generator that continues a cut-short assistant message.

    Prepends the partial reply as an assistant turn so the model resumes
    exactly where it stopped.  Tokens are appended to the original message
    in-place so conversation history stays clean.
    """
    import asyncio
    import time as _time
    from orivellum.capabilities.llm import record_llm_call

    cfg = get_config()
    conv_id = conv["id"]
    orig_id = cut_short_msg["id"]
    meta = cut_short_msg.get("meta") or {}
    model = _model_for(conv)

    # Recover the clean partial text (stored without the UI truncation suffix)
    partial_text: str = meta.get("partial_text") or cut_short_msg.get("text", "")
    partial_text = partial_text.removesuffix(
        "\n\n*(Response was cut short — re-send to continue.)*"
    )

    # Tell the client which message bubble to append to
    yield f"data: {json.dumps({'continue_message_id': orig_id})}\n\n"

    # Build messages: system + history (excluding cut-short stub) + partial as assistant turn
    system_prompt = _build_system_prompt(db, conv, scope="work", user_query=None)
    history = db.get_messages(conv_id, limit=_HISTORY_LIMIT + 5)
    raw_prior = [m for m in history if m.get("id") != orig_id][-_HISTORY_LIMIT:]
    prior = _trim_history_for_budget(raw_prior, system_prompt, db, extra_text=partial_text)
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for m in prior:
        role = m["role"] if m["role"] in ("user", "assistant") else "user"
        messages.append({"role": role, "content": m.get("text") or ""})
    # Append partial as the last assistant turn — model continues from here
    messages.append({"role": "assistant", "content": partial_text})

    continuation = ""
    _finish_reason: str | None = None
    _stream_started = _time.monotonic()
    _stream_ok = True
    _stream_err: str | None = None

    # Per-chunk wall-clock timeout — same policy as _stream_response.
    _CONT_TIMEOUT_SEC = 30
    _cont_timed_out = False
    try:
        import httpx
        async with httpx.AsyncClient(timeout=cfg.serving.timeout_sec) as client:
            async with client.stream(
                "POST",
                f"{cfg.serving.base_url}/chat/completions",
                json={"model": model, "messages": messages, "stream": True},
            ) as resp:
                resp.raise_for_status()
                _cont_iter = resp.aiter_lines().__aiter__()
                while True:
                    try:
                        line = await asyncio.wait_for(
                            _cont_iter.__anext__(), timeout=_CONT_TIMEOUT_SEC
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        logger.warning(
                            "Continuation stream timed out after %ss of silence (conv=%s)",
                            _CONT_TIMEOUT_SEC, conv_id,
                        )
                        _stream_ok = False
                        _cont_timed_out = True
                        _stream_err = f"continuation silent for {_CONT_TIMEOUT_SEC}s"
                        yield f"data: {json.dumps({'timeout': True, 'message_id': orig_id})}\n\n"
                        break
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:]
                    if chunk.strip() == "[DONE]":
                        break
                    try:
                        d = json.loads(chunk)
                        choice0 = d["choices"][0]
                        fr = choice0.get("finish_reason")
                        if fr:
                            _finish_reason = fr
                        raw = (choice0.get("delta") or {}).get("content") or ""
                        if raw:
                            continuation += raw
                            yield f"data: {json.dumps({'token': raw})}\n\n"
                    except Exception:
                        pass
    except asyncio.TimeoutError:
        # Catch timeout bubbling out of the outer httpx context manager
        logger.warning("Continuation stream outer timeout (conv=%s)", conv_id)
        _stream_ok = False
        _cont_timed_out = True
        _stream_err = f"continuation silent for {_CONT_TIMEOUT_SEC}s"
        yield f"data: {json.dumps({'timeout': True, 'message_id': orig_id})}\n\n"
    except Exception as exc:
        _stream_ok = False
        _stream_err = f"{type(exc).__name__}: {exc}"[:500]
        logger.warning("Continuation stream failed: %s", _stream_err)

    record_llm_call(
        db, purpose="chat.continue", model=model,
        latency_ms=int((_time.monotonic() - _stream_started) * 1000),
        prompt_tokens=None, completion_tokens=None,
        ok=_stream_ok, error=_stream_err,
    )

    if not continuation:
        # Nothing was produced (transport/model failure, zero tokens).
        # Preserve the original cut-short state so the client retains the
        # Continue affordance for retry — do not touch the DB.
        yield f"data: {json.dumps({'error': 'continuation_failed', 'cut_short': True})}\n\n"
        yield "data: [DONE]\n\n"
        return

    # Update the original message: full text = partial + continuation
    new_text = partial_text + continuation
    # Strip ALL truncation flags — successful continuation clears cut_short, partial_text,
    # and incomplete so the message doesn't stay permanently marked resumable.
    new_meta = {k: v for k, v in meta.items()
                if k not in ("cut_short", "partial_text", "incomplete")}

    # Mark still cut-short if the provider hit the token limit OR if the stream
    # broke mid-way (tokens arrived but no clean finish_reason).  In both cases
    # the user should be able to press Continue again to get the remainder.
    stream_broke_mid = not _stream_ok and bool(continuation)
    still_cut = stream_broke_mid or (_finish_reason in ("length", "max_tokens"))
    if still_cut:
        new_meta["cut_short"] = True
        new_meta["partial_text"] = new_text
    # If this continuation itself timed out, mark the updated message incomplete so
    # the re-send affordance remains visible after the client reloads from the server.
    if _cont_timed_out:
        new_meta["incomplete"] = True
        new_meta["cut_short"] = True  # redundant with still_cut but ensures compat
        new_meta["partial_text"] = new_text
    try:
        with db._lock:
            db._conn.execute(
                "UPDATE messages SET text=?, meta=? WHERE id=?",
                (new_text, json.dumps(new_meta), orig_id),
            )
            db._conn.commit()
        # Keep FTS index in sync — this UPDATE bypasses finalize_message()
        db.sync_message_fts(
            orig_id, new_text,
            conv_id=cut_short_msg.get("conversation_id", conv_id),
            role=cut_short_msg.get("role", "assistant"),
        )
    except Exception as exc:
        logger.warning("Could not persist continuation: %s", exc)

    if stream_broke_mid:
        # Distinguish a mid-stream break from a clean token-limit truncation so
        # the client can show an appropriate message.
        yield f"data: {json.dumps({'error': 'continuation_failed', 'cut_short': True})}\n\n"
    elif still_cut:
        yield f"data: {json.dumps({'cut_short': True})}\n\n"
    yield "data: [DONE]\n\n"


# ──────────────────────────────────────────────────────────────────────────────
# Intent routing helpers
# ──────────────────────────────────────────────────────────────────────────────

async def _maybe_dispatch_intent(
    db: Any, user_text: str, base_url: str, model: str,
    work_id: str | None = None,
) -> tuple[str, dict] | None:
    """Classify intent and dispatch to the appropriate tool.

    Returns (reply_text, meta_dict) when a tool handles the request,
    or None when the intent is "chat" (caller falls through to the AI).
    """
    import asyncio
    try:
        from orivellum.capabilities.intent import classify_intent
        classification = await asyncio.to_thread(
            classify_intent, user_text, base_url, model
        )
    except Exception as exc:
        logger.debug("Intent classification error: %s — falling back to chat", exc)
        return None

    intent   = classification.get("intent", "chat")
    query    = classification.get("query", user_text)
    location = classification.get("location")

    if intent == "chat":
        return None

    logger.debug("Intent routing: %s (query=%r, location=%r)", intent, query[:60], location)

    if intent == "web_search":
        web_sources: list = []
        try:
            from orivellum.capabilities.websearch import web_search_synthesize
            text, web_sources = await asyncio.to_thread(
                web_search_synthesize, query, base_url, model, db
            )
        except Exception as exc:
            logger.warning("Web search failed: %s", exc)
            text = f"🌐 **Web Search**\n\nSearch encountered an error: {exc}\nTry rephrasing your query."
        ws_meta: dict = {"intent": "web_search", "query": query}
        if web_sources:
            ws_meta["sources"] = web_sources
        return text, ws_meta

    if intent == "weather":
        try:
            from orivellum.capabilities.weather import get_weather
            loc = location or query
            text = await asyncio.to_thread(get_weather, loc)
        except Exception as exc:
            logger.warning("Weather fetch failed: %s", exc)
            text = f"📍 **Weather**\n\nCould not retrieve weather data: {exc}"
        return text, {"intent": "weather", "location": location or query}

    if intent == "remember":
        try:
            text = await asyncio.to_thread(_handle_remember, db, user_text, base_url, model)
        except Exception as exc:
            logger.warning("Remember handler failed: %s", exc)
            text = "I couldn't save that right now — try again in a moment."
        return text, {"intent": "remember"}

    if intent == "recall":
        try:
            text, recall_meta = await asyncio.to_thread(
                _handle_recall_query, db, user_text, base_url, model
            )
        except Exception as exc:
            logger.warning("Recall handler failed: %s", exc)
            text = "I couldn't search past conversations right now — try again in a moment."
            recall_meta = {"intent": "recall", "query": query}
        return text, recall_meta

    if intent == "image_gen":
        try:
            text = await asyncio.to_thread(_handle_image_gen, query, base_url, model)
        except Exception as exc:
            logger.warning("Image gen handler failed: %s", exc)
            text = f"Image generation encountered an error: {exc}"
        return text, {"intent": "image_gen", "query": query}

    if intent == "recall_output":
        try:
            text, ro_meta = await asyncio.to_thread(
                _handle_recall_output, db, query, work_id
            )
        except Exception as exc:
            logger.warning("Recall output handler failed: %s", exc)
            text = "I couldn't search your outputs right now — try again in a moment."
            ro_meta = {"intent": "recall_output", "query": query}
        return text, ro_meta

    if intent == "action":
        action_name = classification.get("action_name") or ""
        action_inputs = dict(classification.get("action_inputs") or {})
        # Inject the conversation's work_id so work-scoped actions can execute
        if work_id and "work_id" not in action_inputs:
            action_inputs["work_id"] = work_id
        return await asyncio.to_thread(
            _handle_action_preview, action_name, action_inputs
        )

    return None


def _handle_action_preview(action_name: str, action_inputs: dict) -> tuple[str, dict] | None:
    """Build an action confirmation card for display in chat.

    Returns (reply_text, meta) where meta carries the action card payload.
    The frontend renders a 'Run' button; the user's click calls the execute endpoint.
    The model never executes directly — the user must confirm.
    """
    try:
        from orivellum.capabilities.actions import get_registry
        registry = get_registry()
        action = registry.get(action_name)
        if not action:
            names = ", ".join(registry.keys())
            return (
                f"I recognise that as an **{action_name}** action, but I couldn't find "
                f"that action in the registry. Available actions: {names}.",
                {"intent": "action", "action_name": action_name, "action_error": "not_found"},
            )
        confirm_msg = action.confirm_message(action_inputs)
        reply = (
            f"I can do that for you. Here's what this action will do:\n\n"
            f"{confirm_msg}\n\n"
            f"Click **Run Action** below to proceed, or ignore this message to cancel."
        )
        return reply, {
            "intent": "action",
            "action_name": action_name,
            "action_inputs": action_inputs,
            "action_description": action.description,
            "action_confirm": confirm_msg,
            "needs_confirm": True,
        }
    except Exception as exc:
        logger.warning("Action preview failed for %r: %s", action_name, exc)
        return None


def _handle_remember(db: Any, user_text: str, base_url: str, model: str) -> str:
    """Synchronously extract and store a durable fact, then return a confirmation.

    Only acknowledges success after a committed database write.
    Returns a clear failure message when extraction or storage does not succeed.
    """
    try:
        from orivellum.capabilities.cognition import _call_sync
        prompt = (
            "Extract the single most important durable fact from this message. "
            "Return ONLY valid JSON (no code fences): "
            "{\"key\": \"short_snake_case_key\", \"value\": \"fact text\"} "
            "or {\"key\": null, \"value\": null} if nothing is worth storing.\n\n"
            f"Message: {user_text[:400]}"
        )
        raw = _call_sync(
            [{"role": "user", "content": prompt}],
            base_url=base_url, model=model, timeout=12,
        )
        if not raw:
            raise ValueError("Empty response from LLM extractor")

        # Strip optional code fences before parsing
        raw_clean = raw.strip().strip("`").strip()
        if raw_clean.startswith("json"):
            raw_clean = raw_clean[4:].strip()
        parsed = json.loads(raw_clean)
        key   = str(parsed.get("key") or "").strip()[:80]
        value = str(parsed.get("value") or "").strip()[:500]

        if not key or not value:
            return (
                "📌 **Nothing stored**\n\n"
                "I couldn't identify a specific fact worth saving from that message. "
                "Try phrasing it more explicitly, "
                "e.g. *\"remember that I prefer APA citations\"*."
            )

        db.upsert_memory_fact(key, value, source_conv_id=None)
        db.audit("user_memory.upserted", object_id=None, object_type="user_memory",
                 actor="user", detail=key[:80])

        return (
            f"📌 **Remembered**\n\n"
            f"Stored: **{key.replace('_', ' ').title()}** → {value}\n\n"
            "*This will be included in all future conversations.*"
        )

    except Exception as exc:
        logger.warning("Remember extraction/storage failed: %s", exc)
        return (
            "📌 **Could not save**\n\n"
            "Something went wrong while trying to store that fact "
            f"({type(exc).__name__}). Please try again."
        )


def _handle_image_gen(query: str, base_url: str, model: str) -> str:
    """Attempt image generation via the AI backend; return a markdown response."""
    try:
        import urllib.request, urllib.error, json as _json
        cfg = get_config()
        payload = json.dumps({
            "model": model,
            "prompt": query,
            "n": 1,
            "size": "512x512",
        }).encode()
        req = urllib.request.Request(
            f"{cfg.serving.base_url}/images/generations",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = _json.loads(resp.read())
        item = data["data"][0]
        if item.get("url") and item["url"].startswith("http"):
            return f"🎨 **Image generated**\n\n![{query}]({item['url']})"
        if item.get("b64_json"):
            # Full base64 payload — valid data URL the browser can render directly
            b64 = item["b64_json"]
            return f"🎨 **Image generated**\n\n![{query}](data:image/png;base64,{b64})"
    except Exception as exc:
        logger.debug("Image generation unavailable: %s", exc)
    return (
        "🎨 **Image Generation**\n\n"
        "Image generation isn't available on this Orivellum instance. "
        "It requires an AI backend that supports the `/images/generations` endpoint "
        "(e.g., a DALL·E-compatible server). Your local Ollama/Lemonade model handles text only."
    )


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _deep_response(messages: list[dict], model: str) -> str:
    """Run the cognition council synchronously (called via asyncio.to_thread)."""
    try:
        from orivellum.capabilities.cognition import deliberate
        cfg = get_config()
        result = deliberate(messages, base_url=cfg.serving.base_url, model=model)
        return result or _UNAVAILABLE
    except Exception as exc:
        logger.warning("Cognition council failed: %s", exc)
        return _UNAVAILABLE


def _summarize_early_context(
    earliest_messages: list[dict],
    existing_summary: str | None,
    db: Any,
) -> str | None:
    """Call the LLM to build/update the rolling context summary.

    When *existing_summary* is provided the model is asked to FOLD the new
    earliest_messages into it rather than starting fresh — this keeps the output
    compact and avoids re-summarising material that is already captured.

    Returns the updated summary string, or None on any failure so the caller
    can skip the DB write and retry later.
    """
    if not earliest_messages:
        return None
    try:
        cfg = get_config()
        exchange_lines: list[str] = []
        for m in earliest_messages:
            role = "User" if m.get("role") == "user" else "Assistant"
            text = (m.get("text") or "").strip()
            if text:
                exchange_lines.append(f"{role}: {text[:800]}")

        exchanges_block = "\n\n".join(exchange_lines)

        if existing_summary:
            prompt = (
                "You are a conversation archivist. Below is an EXISTING SUMMARY of early "
                "conversation turns, followed by ADDITIONAL EARLIER EXCHANGES that must now "
                "be folded into that summary.\n\n"
                "Rules:\n"
                "  • Produce a single, updated prose summary (200–400 words).\n"
                "  • Preserve all key facts, decisions, preferences, and conclusions.\n"
                "  • Write in third person (e.g. 'The user explained…', 'The assistant noted…').\n"
                "  • Do NOT include anything from the current or recent conversation.\n"
                "  • Output ONLY the summary — no preamble, no labels.\n\n"
                f"EXISTING SUMMARY:\n{existing_summary.strip()}\n\n"
                f"ADDITIONAL EXCHANGES:\n{exchanges_block}"
            )
        else:
            prompt = (
                "You are a conversation archivist. Summarise the following early conversation "
                "exchanges into a compact prose summary (150–350 words) that captures:\n"
                "  • Key topics discussed\n"
                "  • Important facts, decisions, or preferences the user stated\n"
                "  • Conclusions or outcomes the assistant reached\n\n"
                "Rules:\n"
                "  • Write in third person (e.g. 'The user explained…', 'The assistant noted…').\n"
                "  • Do NOT editorialize — only report what was actually said.\n"
                "  • Output ONLY the summary — no preamble, no labels.\n\n"
                f"EXCHANGES:\n{exchanges_block}"
            )

        # _call_sync is the synchronous wrapper around the LLM gateway; safe
        # to call from background threads (does not require an asyncio event loop).
        from orivellum.capabilities.cognition import _call_sync
        summary = _call_sync(
            [{"role": "user", "content": prompt}],
            base_url=cfg.serving.base_url,
            model=cfg.serving.workhorse_model,
            timeout=30,
        )
        return summary.strip() if summary and summary.strip() else None
    except Exception as exc:
        logger.debug("Context summarization skipped: %s", exc)
        return None


def _maybe_summarize(db: Any, conv_id: str) -> None:
    """Check if the conversation is long enough to warrant (incremental) summarization.

    Called from the background thread after every reply.  All work is
    best-effort — any exception is caught and logged at DEBUG level so it
    never impacts in-flight responses.

    Algorithm (cursor-based, COUNT + OFFSET so accuracy is independent of any
    LIMIT cap on get_messages):
      1. Use COUNT(*) for the true total — never truncated by a LIMIT.
      2. If total < 2 * _SUMMARIZE_PAIR_THRESHOLD, skip.
      3. verbatim_start = total - _HISTORY_LIMIT.
         Messages with index < verbatim_start are outside the verbatim window.
      4. Resolve the coverage cursor via get_message_position():
         - If summary_cursor_id resolves to position P, cursor_pos = P + 1.
         - If summary_cursor_id is not found (message deleted), cursor_pos is
           reset to 0 so the summarizer rebuilds cleanly from the start,
           folding the existing summary in as old material.
      5. new_batch = get_messages_range(offset=cursor_pos,
                                        limit=2*_SUMMARIZE_BATCH_PAIRS)
         clamped so it does not reach into the verbatim window.
         Skip if len(new_batch) < 4 (< 2 pairs).
      6. Summarize new_batch, fold into existing_summary when present.
      7. Persist updated summary + new cursor_id atomically.
    """
    try:
        conv = db.get_conversation(conv_id)
        if not conv:
            return

        # Use COUNT(*) — accurate for any conversation length
        true_total = db.count_messages(conv_id)

        if true_total < 2 * _SUMMARIZE_PAIR_THRESHOLD:
            return

        verbatim_start = max(0, true_total - _HISTORY_LIMIT)
        if verbatim_start < 4:
            return

        existing_summary: str | None = conv.get("context_summary") or None
        cursor_id: str | None = conv.get("summary_cursor_id") or None

        # Resolve cursor position via DB query (correct even for >2 000 messages)
        cursor_pos = 0
        if cursor_id:
            pos = db.get_message_position(conv_id, cursor_id)
            if pos is None:
                # Cursor message was deleted — rebuild from scratch.
                # existing_summary is passed as old material so the LLM folds it in.
                cursor_pos = 0
            else:
                cursor_pos = pos + 1  # first message NOT yet covered

        # Nothing new to cover since the last run
        if cursor_pos >= verbatim_start:
            return

        # Fetch exactly the next batch, clamped to the verbatim boundary
        batch_limit = min(2 * _SUMMARIZE_BATCH_PAIRS, verbatim_start - cursor_pos)
        new_batch = db.get_messages_range(conv_id, offset=cursor_pos, limit=batch_limit)
        if len(new_batch) < 4:   # < 2 pairs — skip
            return

        new_summary = _summarize_early_context(new_batch, existing_summary, db)
        if new_summary:
            new_cursor_id = new_batch[-1]["id"]
            db.update_conversation_summary(conv_id, new_summary, cursor_id=new_cursor_id)
            logger.debug(
                "Context summarized for conv %s: batch [%d..%d] of %d total messages "
                "(%d chars summary, cursor→%s)",
                conv_id[:8], cursor_pos, cursor_pos + len(new_batch) - 1,
                true_total, len(new_summary), new_cursor_id[:8],
            )
    except Exception as exc:
        logger.debug("_maybe_summarize failed for conv %s: %s", conv_id[:8], exc)


def _post_reply_background(
    db: Any, conv_id: str, user_text: str, assistant_text: str
) -> None:
    """Background task launched after every assistant reply.

    Runs three lightweight passes:
    1. Embed the exchange as a conversation chunk (enables "where are we on X" recall).
    2. Inference-based memory extraction — no trigger phrase needed.
    3. Sliding-window summarization — condenses old messages into a rolling summary
       when the conversation exceeds the threshold length.

    All passes are best-effort; any failure is logged at DEBUG level and never
    blocks the response that's already been sent to the user.
    """
    # 1. Embed exchange → conversation_chunks + vectors
    try:
        from orivellum.capabilities.embeddings import embed_conversation_exchange
        embed_conversation_exchange(conv_id, user_text, assistant_text, db)
    except Exception as exc:
        logger.debug("Conv embedding skipped: %s", exc)

    # 2. Inference-based fact extraction
    _infer_memory_facts(db, conv_id, user_text, assistant_text)

    # 3. Sliding-window context summarization (non-blocking; best-effort)
    _maybe_summarize(db, conv_id)


def _infer_memory_facts(
    db: Any, conv_id: str, user_text: str, assistant_text: str
) -> None:
    """Extract and store durable facts from a full exchange using LLM inference.

    Unlike the legacy trigger-phrase approach, this runs on every substantive
    exchange.  A quality gate (confidence ≥ 0.75) keeps noise out.  Changed
    facts are versioned rather than overwritten — the old value is archived
    with a superseded_at timestamp.
    """
    # Skip trivially short exchanges that won't contain storable facts
    if len(user_text) < 15:
        return
    try:
        cfg = get_config()
        from orivellum.capabilities.cognition import _call_sync
        exchange = (
            f"User: {user_text[:600].strip()}\n\n"
            f"Assistant: {assistant_text[:400].strip()}"
        )
        prompt = (
            "Review this conversation exchange. Extract ONLY facts that are:\n"
            "  (a) Specific and concrete — not vague or situational.\n"
            "  (b) About the USER's identity, preferences, goals, or explicit decisions.\n"
            "  (c) Durable — worth knowing in future conversations weeks from now.\n"
            "Do NOT extract: general knowledge, temporary context, what the AI said,\n"
            "or anything the user only implied rather than stated.\n\n"
            "Return ONLY valid JSON (no code fences):\n"
            '{"facts": [{"key": "snake_case_key", "value": "fact text", "confidence": 0.0}]}\n'
            "Include only facts with confidence ≥ 0.75. Max 3 facts.\n"
            'Return {"facts": []} if nothing qualifies.\n\n'
            f"Exchange:\n{exchange}"
        )
        raw = _call_sync(
            [{"role": "user", "content": prompt}],
            base_url=cfg.serving.base_url,
            model=cfg.serving.workhorse_model,
            timeout=15,
        )
        if not raw:
            return
        clean = raw.strip().strip("`").strip()
        if clean.startswith("json"):
            clean = clean[4:].strip()
        parsed = json.loads(clean)
        facts = parsed.get("facts", [])
        written = 0
        for fact in facts[:3]:
            key   = str(fact.get("key") or "").strip()[:80]
            value = str(fact.get("value") or "").strip()[:500]
            try:
                confidence = float(fact.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0
            if not key or not value or confidence < 0.75 or len(key) < 3:
                continue
            if db.upsert_memory_fact(key, value, conv_id):
                written += 1
        if written:
            db.audit("user_memory.inferred", object_id=None, object_type="user_memory",
                     actor="system", detail=f"{written} fact(s) from conv {conv_id[:8]}")
            logger.info("Inference memory: wrote %d fact(s) from conv %s", written, conv_id[:8])
    except Exception as exc:
        logger.debug("Inference memory extraction skipped: %s", exc)


def _handle_recall_output(
    db: Any,
    query: str,
    work_id: str | None = None,
) -> tuple[str, dict]:
    """Search object_provenance for user-created outputs matching *query*.

    Returns a markdown reply with a bulleted list of matching documents plus
    a metadata dict so the frontend can build clickable result cards.
    """
    # Strip intent phrases, keep only meaningful content keywords.
    _STOP = frozenset({
        "find", "show", "get", "retrieve", "locate", "where", "give", "list",
        "the", "my", "a", "an", "that", "which", "what", "i", "we", "me",
        "you", "us", "made", "created", "generated", "uploaded", "wrote",
        "built", "produced", "have", "did", "about", "for", "on", "is", "are",
        "was", "been", "files", "file", "outputs", "output", "all", "any",
        "some", "latest", "recent", "new",
    })
    terms = " ".join(
        w for w in query.lower().split()
        if w.isalnum() and w not in _STOP and len(w) > 2
    )

    results = db.search_provenance(terms, work_id=work_id, limit=15)

    meta: dict = {"intent": "recall_output", "query": query, "count": len(results)}

    if not results:
        msg = "🔍 **No matching outputs found**\n\nI couldn't find any files, documents, or outputs"
        if terms:
            msg += f" matching **{terms}**"
        msg += ".\n\nTry a different keyword, or open the Library to browse all your documents."
        return msg, meta

    _SOURCE_LABEL: dict[str, str] = {
        "upload":       "uploaded",
        "generation":   "generated",
        "studio":       "created in Studio",
        "chat":         "created in chat",
        "zip_extract":  "extracted from archive",
        "intake":       "researched",
    }

    lines = ["📁 **Outputs matching your search**\n"]
    for item in results[:10]:
        title      = item.get("title") or "Untitled"
        kind       = item.get("kind") or "file"
        source     = item.get("source") or "upload"
        doc_id     = item.get("id") or ""
        prov_date  = (item.get("prov_created_at") or "")[:10]

        label = _SOURCE_LABEL.get(source, source)
        date_part = f" · {prov_date}" if prov_date else ""
        lines.append(
            f"- **{title}** (`{kind}`, {label}{date_part})"
            f" — [Open in Library](/library/{doc_id})"
        )

    if len(results) > 10:
        lines.append(f"\n…and {len(results) - 10} more. Visit the Library to see all.")

    meta["results"] = [
        {"id": r.get("id"), "title": r.get("title"), "kind": r.get("kind")}
        for r in results[:10]
    ]
    return "\n".join(lines), meta


def _handle_recall_query(
    db: Any, user_text: str, base_url: str, model: str
) -> tuple[str, dict]:
    """Handle a recall intent — semantic search + synthesis with source citations.

    Searches:
      1. Conversation chunks (semantic, with FTS fallback)
      2. Current user memory facts
      3. Knowledge items

    Returns (reply_text, meta_dict) where meta contains a ``sources`` list
    with clickable conversation links.
    """
    from orivellum.capabilities.embeddings import semantic_search_conversations
    from orivellum.capabilities.embeddings import hybrid_search_knowledge

    # ── 1. Search conversation chunks (semantic AND keyword, always combined) ──
    # Both paths run every time so that chunks without vectors (stored during an
    # embedding-endpoint outage) are still surfaced via keyword match even when
    # semantic results exist.  Results are deduplicated by chunk id.
    sem_hits: list[dict] = []
    try:
        sem_hits = semantic_search_conversations(user_text, db, limit=5)
    except Exception:
        pass
    kw_hits: list[dict] = db.search_conversation_chunks(user_text, limit=5)
    seen_chunk_ids: set[str] = set()
    conv_hits: list[dict] = []
    for h in sem_hits + kw_hits:
        cid = h.get("id") or ""
        if cid and cid not in seen_chunk_ids:
            seen_chunk_ids.add(cid)
            conv_hits.append(h)
    conv_hits = conv_hits[:5]

    # ── 2. Memory facts that overlap with the query topic ─────────────────────
    all_facts = db.get_current_memory_facts(limit=20)
    q_words = {w for w in user_text.lower().split() if len(w) > 3}
    fact_hits = [
        f for f in all_facts
        if any(w in f["value"].lower() or w in f["key"].lower() for w in q_words)
    ]

    # ── 3. Knowledge items ────────────────────────────────────────────────────
    kn_hits: list[dict] = []
    try:
        kn_hits = hybrid_search_knowledge(user_text, db, limit=4)
    except Exception:
        pass

    # ── Build answer if nothing found ─────────────────────────────────────────
    if not conv_hits and not fact_hits and not kn_hits:
        return (
            f'📭 **Nothing found for "{user_text[:80]}"**\n\n'
            "I don't have stored memory or past conversations on this topic yet. "
            "As we discuss it, I'll start capturing relevant facts automatically.",
            {"intent": "recall", "query": user_text},
        )

    # ── Assemble context for synthesis ────────────────────────────────────────
    sections: list[str] = []
    sources: list[dict] = []

    if fact_hits:
        fact_lines = []
        for f in fact_hits[:5]:
            line = f"• {f['key']}: {f['value']}"
            if f.get("prev_value"):
                line += f"  *(previously: {f['prev_value']})*"
            fact_lines.append(line)
        sections.append("**Stored memory:**\n" + "\n".join(fact_lines))

    if kn_hits:
        kn_lines = [f"• {h.get('text','')[:200]}" for h in kn_hits[:3]]
        sections.append("**Knowledge:**\n" + "\n".join(kn_lines))

    if conv_hits:
        conv_lines: list[str] = []
        seen_convs: set[str] = set()
        for h in conv_hits[:4]:
            title    = (h.get("conv_title") or "Untitled conversation").strip()
            created  = (h.get("created_at") or "")[:10]
            conv_id  = h.get("conv_id") or ""
            excerpt  = h.get("text", "")[:400].strip()
            conv_lines.append(f"[{title} / {created}]\n{excerpt}")
            if conv_id and conv_id not in seen_convs:
                seen_convs.add(conv_id)
                sources.append({
                    "type": "conversation",
                    "title": title,
                    "id": conv_id,
                    "created_at": h.get("created_at"),
                })
        sections.append("**Past conversations:**\n" + "\n---\n".join(conv_lines))

    context_block = "\n\n".join(sections)
    synth_prompt = (
        f'Answer this recall question: "{user_text[:250]}"\n\n'
        f"Using only the context below, write a concise grounded answer "
        f"(2–4 paragraphs). Cite conversation sources as [title / date]. "
        f"If a fact changed (previously vs now), mention both. "
        f"If evidence is incomplete, say so explicitly.\n\n"
        f"{context_block}"
    )

    from orivellum.capabilities.cognition import _call_sync
    try:
        reply = _call_sync(
            [{"role": "user", "content": synth_prompt}],
            base_url=base_url, model=model, timeout=25,
        ) or _UNAVAILABLE
    except Exception:
        reply = _UNAVAILABLE

    meta: dict = {"intent": "recall", "query": user_text}
    if sources:
        meta["sources"] = sources
    return reply, meta


def _maybe_auto_title(db: Any, conv: dict, first_user_text: str) -> None:
    """Set a conversation title from the first user message if still default."""
    current_title = conv.get("title") or ""
    if current_title and current_title not in ("New Conversation", "Untitled", ""):
        return
    # Use the first ~60 chars of the user's message as the title
    snippet = first_user_text.strip().replace("\n", " ")
    title = snippet[:60] + ("…" if len(snippet) > 60 else "")
    if title:
        try:
            db.update_conversation(conv["id"], title=title)
        except Exception:
            pass
