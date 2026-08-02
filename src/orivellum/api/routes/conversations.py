"""Conversations and chat endpoints — /api/conversations/*"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

# Max messages to send as history (keeps context window manageable)
_HISTORY_LIMIT = 40

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
# Max knowledge items to inject as context
_CONTEXT_KNOWLEDGE = 12   # max knowledge items injected per turn
_CONTEXT_CHUNKS    = 5    # max raw document passages injected per turn


class ConversationCreate(BaseModel):
    title: str | None = None
    work_id: str | None = None
    model: str | None = None


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


# ──────────────────────────────────────────────────────────────────────────────
# Route handlers
# ──────────────────────────────────────────────────────────────────────────────

@router.get("/conversations")
def list_conversations(archived: bool = False, limit: int = 100):
    db = get_db()
    convs = db.list_conversations(archived=archived, limit=min(limit, 500))
    return {"conversations": convs, "count": len(convs)}


@router.post("/conversations")
def create_conversation(body: ConversationCreate):
    db = get_db()
    conv = db.create_conversation(title=body.title, work_id=body.work_id, model=body.model)
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


@router.delete("/conversations/{conv_id}")
def delete_conversation(conv_id: str):
    db = get_db()
    ok = db.delete_conversation(conv_id)
    if not ok:
        raise HTTPException(404, f"Conversation {conv_id!r} not found")
    return {"ok": True}


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

    # Duplicate-send guard: skip storing user message if an identical one was stored
    # within the last 5 seconds (protects against React StrictMode double-calls,
    # client retries, and accidental double-taps).  We still proceed with the AI
    # response so the user does not see a silent failure.
    with db._lock:
        recent_dup = db._conn.execute(
            """SELECT id FROM messages
               WHERE conv_id=? AND role='user' AND text=?
               AND created_at > datetime('now','-5 seconds')""",
            (conv_id, stored_text)
        ).fetchone()
    if recent_dup:
        logger.debug("Duplicate user message suppressed for conv %s", conv_id)
    else:
        db.add_message(conv_id, "user", stored_text)

    # Background auto-capture: skip when the user explicitly says "remember that…"
    # to avoid a competing write racing against the intent router's _handle_remember.
    import asyncio, threading
    if not _EXPLICIT_REMEMBER_RE.search(body.text):
        threading.Thread(
            target=_maybe_capture_memory,
            args=(db, conv_id, body.text),
            daemon=True,
        ).start()

    if body.stream:
        return StreamingResponse(
            _stream_response(
                db, conv, body.text, deep=body.deep, scope=body.scope,
                image_b64=body.image_b64, image_media_type=body.image_media_type,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming path
    _ns_sources: list = []
    messages = _build_messages(
        db, conv, body.text, scope=body.scope,
        image_b64=body.image_b64, image_media_type=body.image_media_type,
        out_sources=_ns_sources,
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
    tool_result = await _maybe_dispatch_intent(db, body.text, cfg.serving.base_url, model)
    if tool_result is not None:
        tool_text, tool_meta = tool_result
        if ns_sources:
            tool_meta = {**tool_meta, "sources": ns_sources}
        msg = db.add_message(conv_id, "assistant", tool_text, meta=tool_meta)
        _maybe_auto_title(db, conv, body.text)
        return {"message": msg}

    if body.deep:
        import asyncio
        from orivellum.capabilities.cognition import (
            classify, get_clarifying_question, deliberate, update_compass,
        )
        route = await asyncio.to_thread(
            classify, body.text, messages[:-1], cfg.serving.base_url, model
        )
        logger.debug("Cognition gate (non-stream) for conv %s: %s", conv_id, route)

        if route == "clarify":
            question = await asyncio.to_thread(
                get_clarifying_question, body.text, cfg.serving.base_url, model
            )
            clarify_meta: dict = {"model": model, "isClarification": True}
            if ns_sources:
                clarify_meta["sources"] = ns_sources
            msg = db.add_message(conv_id, "assistant", question, meta=clarify_meta)
            _maybe_auto_title(db, conv, body.text)
            return {"message": msg}

        if route == "complex":
            council_reply = await asyncio.to_thread(
                deliberate, messages, cfg.serving.base_url, model
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
                _maybe_auto_title(db, conv, body.text)
                return {"message": msg}
            # Council failed → fall through to direct single call

        # "direct" or council/classify fallback
        reply = await _call_ai(messages, model=model)
    else:
        reply = await _call_ai(messages, model=model)

    ns_meta: dict = {"model": model}
    if ns_sources:
        ns_meta["sources"] = ns_sources
    msg = db.add_message(conv_id, "assistant", reply, meta=ns_meta)
    _maybe_auto_title(db, conv, body.text)
    return {"message": msg}


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

    Priority: conversation.model → config vision_model → workhorse fallback.
    """
    cfg = get_config()
    return conv.get("model") or cfg.serving.vision_model or cfg.serving.workhorse_model


def _build_system_prompt(db: Any, conv: dict, scope: str = "work",
                         user_query: str | None = None,
                         out_sources: list | None = None) -> str:
    """Build a system prompt enriched with relevant knowledge from the database.

    Knowledge retrieval strategy (always global):
      1. If user_query is provided → full-text search across ALL knowledge + document
         chunks, ranked by relevance, grouped by Work/topic.
      2. If no query (e.g. first message) → fall back to most-recent knowledge
         from the linked Work, or across all Works if none is linked.

    Only trusted items (rule-based "auto" + user-approved "approved") are injected.
    Pending AI items ("ai_auto") are excluded until the user approves them.
    """
    base = (
        "You are Orivellum, a capable local-first AI assistant. "
        "Answer any question using your full training knowledge — science, history, "
        "analysis, writing, coding, general facts, explanations, and more. "
        "You are knowledgeable and helpful; never refuse a question just because it "
        "is not in the user's uploaded documents. "
        "The user may also upload documents to a local knowledge base; relevant "
        "excerpts are injected below when they match the query — use them as "
        "additional context when present. "
        "For live internet data (today's breaking news, real-time prices, live events) "
        "you cannot browse the web directly, but a built-in web search tool is "
        "available — the user can ask you to 'search for X' or 'look up X online' "
        "and it will fetch results automatically. "
        "Be concise, precise, and honest. Never fabricate citations or facts."
    )

    # Prepend durable user memory facts
    try:
        with db._lock:
            mem_rows = db._conn.execute(
                "SELECT key, value FROM user_memory ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        if mem_rows:
            mem_block = "MEMORY (durable facts about the user):\n" + "\n".join(
                f"  {r['key']}: {r['value']}" for r in mem_rows
            )
            base = mem_block + "\n\n" + base
    except Exception:
        pass  # user_memory table may not exist yet on old schemas

    _TRUSTED = {"auto", "approved"}
    work_id = conv.get("work_id")

    # ── 1. Query-matched global search (primary path) ──────────────────────────
    if user_query and user_query.strip():
        try:
            # Search knowledge items and raw document chunks across ALL works.
            # Hybrid = keyword FTS + semantic vectors (falls back to FTS-only
            # automatically when the embeddings endpoint is unavailable).
            from orivellum.capabilities.embeddings import hybrid_search_knowledge
            knowledge_hits = hybrid_search_knowledge(user_query, db,
                                                     limit=_CONTEXT_KNOWLEDGE * 2)
            trusted_k = [k for k in knowledge_hits
                         if k.get("review_status") in _TRUSTED][:_CONTEXT_KNOWLEDGE]

            chunk_hits = db.search_chunks(user_query, work_id=None,
                                          limit=_CONTEXT_CHUNKS * 2)
            trusted_c = chunk_hits[:_CONTEXT_CHUNKS]

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
                    "KNOWLEDGE FROM YOUR DATABASE (most relevant to this question):"
                ]
                for wid, group in ordered:
                    context_parts.append(f"\n[Topic: {group['title']}]")
                    for k in group["knowledge"]:
                        text = k.get("text", "").strip()
                        kind = k.get("kind", "note")
                        if text:
                            context_parts.append(f"  [{kind}] {text[:400]}")
                            if out_sources is not None:
                                real_wid = k.get("work_id")
                                out_sources.append({
                                    "id": k.get("id"),
                                    "title": text[:100],
                                    "kind": kind,
                                    "work_id": real_wid,
                                    "work_title": group["title"],
                                    "source_doc_id": k.get("source_doc_id"),
                                    # Legacy fields kept for the existing footer link
                                    "doc_id": k.get("source_doc_id"),
                                    "doc_title": group["title"],
                                })
                    for c in group["chunks"]:
                        text = c.get("text", "").strip()
                        doc  = c.get("doc_title") or "document"
                        if text:
                            context_parts.append(f"  [from '{doc}'] {text[:400]}")
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
                                })

                return f"{base}\n\n" + "\n".join(context_parts)
        except Exception:
            pass  # fall through to recency-based fallback

    # ── 2. Recency fallback (no query or search failed) ────────────────────────
    fallback_wid = work_id  # prefer linked Work; None = all works
    all_knowledge = db.list_knowledge(work_id=fallback_wid,
                                      limit=_CONTEXT_KNOWLEDGE * 4)
    knowledge = [k for k in all_knowledge
                 if k.get("review_status") in _TRUSTED][:_CONTEXT_KNOWLEDGE]

    if not knowledge and fallback_wid:
        # No knowledge in the linked Work — broaden to all works
        all_knowledge = db.list_knowledge(work_id=None, limit=_CONTEXT_KNOWLEDGE * 4)
        knowledge = [k for k in all_knowledge
                     if k.get("review_status") in _TRUSTED][:_CONTEXT_KNOWLEDGE]

    if not knowledge:
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

    return f"{base}\n\n" + "\n".join(context_parts)


def _build_messages(
    db: Any,
    conv: dict,
    new_user_text: str,
    scope: str = "work",
    image_b64: str | None = None,
    image_media_type: str = "image/jpeg",
    out_sources: list | None = None,
) -> list[dict]:
    """Build the full OpenAI-format messages array for this conversation."""
    system_prompt = _build_system_prompt(db, conv, scope=scope,
                                         user_query=new_user_text,
                                         out_sources=out_sources)

    # Fetch recent history (excluding the message we just stored)
    history = db.get_messages(conv["id"], limit=_HISTORY_LIMIT + 1)
    # The last message is the one we just stored — exclude it from history
    # (we add it as the final user turn below)
    prior = [m for m in history if not (m["role"] == "user" and m["text"] == new_user_text)]
    # Keep within context limit
    prior = prior[-_HISTORY_LIMIT:]

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


async def _call_ai(messages: list[dict], model: str) -> str:
    """Call the AI endpoint (Lemonade / Ollama / any OpenAI-compat server)."""
    cfg = get_config()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=cfg.serving.timeout_sec) as client:
            resp = await client.post(
                f"{cfg.serving.base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("AI call failed: %s", exc)
        return _UNAVAILABLE


async def _stream_response(
    db: Any, conv: dict, user_text: str, deep: bool = False, scope: str = "work",
    image_b64: str | None = None, image_media_type: str = "image/jpeg",
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

    cfg = get_config()
    conv_id = conv["id"]
    _sources: list = []
    messages = _build_messages(
        db, conv, user_text, scope=scope,
        image_b64=image_b64, image_media_type=image_media_type,
        out_sources=_sources,
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

    # ── Intent routing — runs before deep mode and normal AI ──────────────────
    tool_result = await _maybe_dispatch_intent(db, user_text, cfg.serving.base_url, model)
    if tool_result is not None:
        tool_text, tool_meta = tool_result
        if sources:
            tool_meta = {**tool_meta, "sources": sources}
        # Persist before streaming (disconnect-safe)
        db.add_message(conv_id, "assistant", tool_text, meta=tool_meta)
        _maybe_auto_title(db, conv, user_text)
        _CHUNK = 40
        for i in range(0, len(tool_text), _CHUNK):
            yield f"data: {json.dumps({'token': tool_text[i:i+_CHUNK], 'intent': tool_meta.get('intent')})}\n\n"
        if sources:
            yield f"data: {json.dumps({'sources': sources})}\n\n"
        yield "data: [DONE]\n\n"
        return

    # ── Deep mode: run the meta-prompt gate ──────────────────────────────────
    if deep:
        from orivellum.capabilities.cognition import (
            classify, get_clarifying_question, deliberate, update_compass,
        )

        route = await asyncio.to_thread(
            classify, user_text, messages[:-1], cfg.serving.base_url, model
        )
        logger.debug("Cognition gate for conv %s: %s", conv_id, route)

        if route == "clarify":
            question = await asyncio.to_thread(
                get_clarifying_question, user_text, cfg.serving.base_url, model
            )
            # Persist the clarifying question so it survives refetch/reload.
            # The isClarification flag lets the frontend render it with the amber bubble style.
            clarify_meta: dict = {"model": model, "isClarification": True}
            if sources:
                clarify_meta["sources"] = sources
            db.add_message(conv_id, "assistant", question, meta=clarify_meta)
            _maybe_auto_title(db, conv, user_text)
            # Also emit a typed SSE event so the frontend can display immediately
            # without waiting for the query invalidation round-trip.
            yield f"data: {json.dumps({'event': 'clarify', 'question': question})}\n\n"
            if sources:
                yield f"data: {json.dumps({'sources': sources})}\n\n"
            yield "data: [DONE]\n\n"
            return

        if route == "complex":
            council_reply = await asyncio.to_thread(
                deliberate, messages, cfg.serving.base_url, model
            )
            if council_reply:
                # ── Disconnect-safe persistence ──────────────────────────────
                # Save the full reply BEFORE yielding any chunks.  This way a
                # GeneratorExit raised during the chunk loop still results in a
                # saved assistant turn — the client just misses the streaming UX.
                council_meta: dict = {"model": model, "council": True}
                if sources:
                    council_meta["sources"] = sources
                db.add_message(conv_id, "assistant", council_reply,
                               meta=council_meta)
                _maybe_auto_title(db, conv, user_text)
                # Update Project Compass (merge — preserves next_step if already set)
                work_id = conv.get("work_id")
                if work_id:
                    await asyncio.to_thread(
                        update_compass, db, work_id,
                        focus=user_text[:200],
                        reasoning=council_reply[:500],
                    )
                # Stream chunks for UI responsiveness (persistence already done above)
                _CHUNK = 30
                for i in range(0, len(council_reply), _CHUNK):
                    yield f"data: {json.dumps({'token': council_reply[i:i+_CHUNK]})}\n\n"
                if sources:
                    yield f"data: {json.dumps({'sources': sources})}\n\n"
                yield "data: [DONE]\n\n"
                return
            # Council failed → fall through to the direct streaming path

    # ── Per-chunk silence timeout ─────────────────────────────────────────────
    # If the AI server sends no new token for this long, treat the stream as
    # stalled and close it cleanly. The timeout is enforced per-chunk (not just
    # for the initial connection) using asyncio.wait_for on each __anext__ call.
    _CHUNK_TIMEOUT_SEC = 30

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
                        delta = d["choices"][0]["delta"]
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
                                            full_reply += flush
                                            yield f"data: {json.dumps({'token': flush})}\n\n"
                                        break
                                    before = _tag_buf[:idx]
                                    if before:
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
        logger.warning("AI stream timed out after %ss of silence", _CHUNK_TIMEOUT_SEC)
        if not full_reply:
            full_reply = _UNAVAILABLE
            yield f"data: {json.dumps({'token': full_reply})}\n\n"

    except GeneratorExit:
        # Client disconnected mid-stream — save whatever tokens arrived so the
        # conversation isn't left with only the user turn and no reply.
        if full_reply:
            try:
                truncated = full_reply + "\n\n*(Response was cut short — re-send to continue.)*"
                _meta: dict = {"model": model, "cut_short": True}
                if thinking_text:
                    _meta["thinking"] = thinking_text
                if sources:
                    _meta["sources"] = sources
                db.add_message(conv_id, "assistant", truncated, meta=_meta)
                _maybe_auto_title(db, conv, user_text)
            except Exception as save_exc:
                logger.warning("Could not persist partial reply: %s", save_exc)
        raise  # Re-raise so the async generator closes properly

    except Exception as exc:
        logger.warning("AI stream failed: %s", exc)
        full_reply = _UNAVAILABLE
        yield f"data: {json.dumps({'token': full_reply})}\n\n"

    # Normal completion path (also reached after AI failure fallback)
    if full_reply:
        meta: dict = {"model": model}
        if thinking_text:
            meta["thinking"] = thinking_text
        if sources:
            meta["sources"] = sources
        db.add_message(conv_id, "assistant", full_reply, meta=meta)
    _maybe_auto_title(db, conv, user_text)
    if sources:
        import json as _json
        yield f"data: {_json.dumps({'sources': sources})}\n\n"
    yield "data: [DONE]\n\n"


# ──────────────────────────────────────────────────────────────────────────────
# Intent routing helpers
# ──────────────────────────────────────────────────────────────────────────────

async def _maybe_dispatch_intent(
    db: Any, user_text: str, base_url: str, model: str,
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
        try:
            from orivellum.capabilities.websearch import web_search_synthesize
            text = await asyncio.to_thread(
                web_search_synthesize, query, base_url, model
            )
        except Exception as exc:
            logger.warning("Web search failed: %s", exc)
            text = f"🌐 **Web Search**\n\nSearch encountered an error: {exc}\nTry rephrasing your query."
        return text, {"intent": "web_search", "query": query}

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

    if intent == "image_gen":
        try:
            text = await asyncio.to_thread(_handle_image_gen, query, base_url, model)
        except Exception as exc:
            logger.warning("Image gen handler failed: %s", exc)
            text = f"Image generation encountered an error: {exc}"
        return text, {"intent": "image_gen", "query": query}

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

        import uuid
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with db._lock:
            db._conn.execute(
                """INSERT INTO user_memory(id, key, value, source_conv_id, created_at)
                   VALUES(?, ?, ?, NULL, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                     created_at=excluded.created_at""",
                (str(uuid.uuid4()), key, value, now),
            )
            db._conn.commit()
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


_MEMORY_PATTERNS = ("remember that", "my name is", "i prefer", "i like", "i dislike",
                     "i always", "i never", "i'm", "i am", "my email", "my phone")


def _maybe_capture_memory(db: Any, conv_id: str, user_text: str) -> None:
    """Extract durable facts from the user's message and upsert into user_memory."""
    lower = user_text.lower().strip()
    if not any(p in lower for p in _MEMORY_PATTERNS):
        return
    try:
        cfg = get_config()
        from orivellum.capabilities.cognition import _call_sync
        prompt = (
            "Extract durable facts from this message that are worth remembering long-term. "
            "Facts must be personal preferences, names, or persistent instructions. "
            "Return ONLY valid JSON: "
            '{"facts": [{"key": "short_key", "value": "fact text"}]} '
            "or {\"facts\": []} if nothing is worth remembering.\n\n"
            f"Message: {user_text[:500]}"
        )
        raw = _call_sync([{"role": "user", "content": prompt}],
                         base_url=cfg.serving.base_url, model=cfg.serving.workhorse_model, timeout=15)
        if not raw:
            return
        parsed = json.loads(raw.strip())
        facts  = parsed.get("facts", [])
        if not facts:
            return
        import uuid
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        _captured_keys: list[str] = []
        with db._lock:
            for fact in facts[:5]:
                key   = str(fact.get("key", ""))[:80]
                value = str(fact.get("value", ""))[:500]
                if not key or not value:
                    continue
                db._conn.execute(
                    """INSERT INTO user_memory(id, key, value, source_conv_id, created_at)
                       VALUES(?, ?, ?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                         source_conv_id=excluded.source_conv_id,
                         created_at=excluded.created_at""",
                    (str(uuid.uuid4()), key, value, conv_id, now),
                )
                _captured_keys.append(key)
            db._conn.commit()
        if _captured_keys:
            db.audit("user_memory.automemory", object_id=None, object_type="user_memory",
                     actor="system", detail=f"{len(_captured_keys)} facts")
        logger.info("Automemory: captured %d fact(s) from conversation %s", len(facts), conv_id)
    except Exception as exc:
        logger.debug("Automemory extraction skipped: %s", exc)


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
