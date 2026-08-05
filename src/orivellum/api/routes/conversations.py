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

    # Duplicate-send guard: skip storing user message if an identical one was stored
    # within the last 5 seconds (protects against React StrictMode double-calls,
    # client retries, and accidental double-taps).  We still proceed with the AI
    # response so the user does not see a silent failure.
    with db._lock:
        recent_dup = db._conn.execute(
            """SELECT id FROM messages
               WHERE conversation_id=? AND role='user' AND text=?
               AND created_at > datetime('now','-5 seconds')""",
            (conv_id, stored_text)
        ).fetchone()
    if recent_dup:
        logger.debug("Duplicate user message suppressed for conv %s", conv_id)
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
            threading.Thread(
                target=stamp.stamp_and_capture,
                kwargs={
                    "text": body.text,
                    "channel": "chat",
                    "conv_id": conv_id,
                    "base_url": cfg_for_capture.serving.base_url,
                    "model": conv.get("model") or cfg_for_capture.serving.workhorse_model,
                },
                daemon=True,
            ).start()
        except Exception:
            pass  # capture is best-effort; never block the response

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
    _ns_work_id = conv.get("work_id") if conv else None
    tool_result = await _maybe_dispatch_intent(db, body.text, cfg.serving.base_url, model, work_id=_ns_work_id)
    if tool_result is not None:
        tool_text, tool_meta = tool_result
        if ns_sources:
            # Merge knowledge sources with any tool-specific sources (e.g. web search)
            existing = tool_meta.get("sources", [])
            tool_meta = {**tool_meta, "sources": [*existing, *ns_sources]}
        msg = db.add_message(conv_id, "assistant", tool_text, meta=tool_meta)
        _maybe_auto_title(db, conv, body.text)
        threading.Thread(target=_post_reply_background,
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
            _maybe_auto_title(db, conv, body.text)
            threading.Thread(target=_post_reply_background,
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
                _maybe_auto_title(db, conv, body.text)
                threading.Thread(target=_post_reply_background,
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
    msg = db.add_message(conv_id, "assistant", reply, meta=ns_meta)
    _maybe_auto_title(db, conv, body.text)
    # Background: embed exchange + inference memory capture (non-streaming)
    threading.Thread(
        target=_post_reply_background,
        args=(db, conv_id, body.text, reply),
        daemon=True,
    ).start()
    return {"message": msg}


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
    prior = [m for m in history if m.get("id") != cut_short_msg["id"]][-_HISTORY_LIMIT:]
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

    with db._lock:
        db._conn.execute(
            "UPDATE messages SET text=?, meta=? WHERE id=?",
            (new_text, json.dumps(new_meta), cut_short_msg["id"]),
        )
        db._conn.commit()

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
_CHAT_BASE_PROMPT = (
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


_abstention_policy = AbstentionPolicy()


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

    PKLOS Layer 0 — spec §5.3 PolicyEnforcer (P2/P3):
      - Classifies the query (spec §5.1 7-class router).
      - For DETERMINISTICALLY_VERIFIABLE: injects VERIFIED FACTS from the claim
        ledger (USER_ASSERTED + VERIFIED + PARTIALLY_VERIFIED).
      - If no claims: enforces abstention — the model MUST NOT guess.
      - For USER_DECLARED_FACT: logs that the capture path should run.
    """
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

            # Hybrid chunk retrieval (BM25 + cosine, RRF-fused).  Falls back to
            # keyword-only when embeddings are unavailable, and to semantic-only
            # when the query is too short/conceptual for FTS5 to match anything.
            from orivellum.capabilities.embeddings import hybrid_search_chunks
            chunk_hits = hybrid_search_chunks(user_query, db, work_id=None,
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
                        text = c.get("text", "").strip()
                        doc  = c.get("doc_title") or "document"
                        if text:
                            context_parts.append(f"  [from \"{doc}\"] {text[:400]}")
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
                                    "passage": text[:200],
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

    # ── Token-aware history trimming ──────────────────────────────────────────
    # Estimate token counts (4 chars ≈ 1 token) and drop oldest messages first
    # so the combined prompt stays within 80% of the model's context window.
    # This prevents 400 errors from context-length overflows on long chats.
    try:
        _ctx = get_config().serving.context_window
        _budget = int(_ctx * 0.80)
        _CHARS_PER_TOKEN = 4
        # Deduct system prompt and a 256-token margin for the final user turn
        _budget -= len(system_prompt) // _CHARS_PER_TOKEN + 256
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
            logger.warning("AI stream timed out after %ss of silence", _CHUNK_TIMEOUT_SEC)
            _stream_ok = False
            _stream_err = f"stream silent for {_CHUNK_TIMEOUT_SEC}s"
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
            import threading as _threading
            _threading.Thread(
                target=_post_reply_background,
                args=(db, conv_id, user_text, full_reply),
                daemon=True,
            ).start()

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
    prior = [m for m in history if m.get("id") != orig_id][-_HISTORY_LIMIT:]
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

    try:
        import httpx
        async with httpx.AsyncClient(timeout=cfg.serving.timeout_sec) as client:
            async with client.stream(
                "POST",
                f"{cfg.serving.base_url}/chat/completions",
                json={"model": model, "messages": messages, "stream": True},
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
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
    new_meta = {k: v for k, v in meta.items() if k not in ("cut_short", "partial_text")}

    # Mark still cut-short if the provider hit the token limit OR if the stream
    # broke mid-way (tokens arrived but no clean finish_reason).  In both cases
    # the user should be able to press Continue again to get the remainder.
    stream_broke_mid = not _stream_ok and bool(continuation)
    still_cut = stream_broke_mid or (_finish_reason in ("length", "max_tokens"))
    if still_cut:
        new_meta["cut_short"] = True
        new_meta["partial_text"] = new_text
    try:
        with db._lock:
            db._conn.execute(
                "UPDATE messages SET text=?, meta=? WHERE id=?",
                (new_text, json.dumps(new_meta), orig_id),
            )
            db._conn.commit()
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


def _post_reply_background(
    db: Any, conv_id: str, user_text: str, assistant_text: str
) -> None:
    """Background task launched after every assistant reply.

    Runs two lightweight passes:
    1. Embed the exchange as a conversation chunk (enables "where are we on X" recall).
    2. Inference-based memory extraction — no trigger phrase needed.

    Both passes are best-effort; any failure is logged at DEBUG level and never
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
