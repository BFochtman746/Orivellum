"""Conversations and chat endpoints — /api/conversations/*"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config

router = APIRouter(prefix="/api")
logger = logging.getLogger(__name__)

# Max messages to send as history (keeps context window manageable)
_HISTORY_LIMIT = 40
# Max knowledge items to inject as context
_CONTEXT_KNOWLEDGE = 8


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
    db.add_message(conv_id, "user", body.text)

    # Capture durable user memory facts from this turn (background, non-blocking)
    import asyncio, threading
    threading.Thread(
        target=_maybe_capture_memory,
        args=(db, conv_id, body.text),
        daemon=True,
    ).start()

    if body.stream:
        return StreamingResponse(
            _stream_response(db, conv, body.text, deep=body.deep, scope=body.scope),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # Non-streaming path
    messages = _build_messages(db, conv, body.text, scope=body.scope)
    model    = _model_for(conv)

    if body.deep:
        # Cognition council — run in thread to avoid blocking event loop
        import asyncio
        reply = await asyncio.to_thread(_deep_response, messages, model)
    else:
        reply = await _call_ai(messages, model=model)

    msg = db.add_message(conv_id, "assistant", reply, meta={"model": model})
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


def _build_system_prompt(db: Any, conv: dict, scope: str = "work") -> str:
    """Build a system prompt, optionally enriched with work knowledge and user memory.

    scope="work"  → inject knowledge from the conversation's Work only (default)
    scope="all"   → inject top knowledge across ALL works
    """
    base = (
        "You are Orivellum, a sovereign local-first AI assistant. "
        "You help the user think through their research, synthesise documents, "
        "generate ideas, and manage knowledge. Be concise, precise, and honest. "
        "Never fabricate citations or facts."
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

    work_id = conv.get("work_id")

    if scope == "all":
        # Inject knowledge across ALL works (no work_id filter)
        all_knowledge = db.list_knowledge(work_id=None, limit=_CONTEXT_KNOWLEDGE * 4)
        knowledge = [
            k for k in all_knowledge
            if k.get("review_status") not in ("rejected",)
        ][:_CONTEXT_KNOWLEDGE]
        if knowledge:
            context_parts = ["Relevant knowledge from all your works:"]
            for k in knowledge:
                kind = k.get("kind", "note")
                text = k.get("text", "").strip()
                if text:
                    context_parts.append(f"  [{kind}] {text[:300]}")
            return f"{base}\n\n" + "\n".join(context_parts)
        return base

    if not work_id:
        return base

    # Inject work title and top knowledge items as context
    work = db.get_work(work_id)
    if not work:
        return base

    work_title = work.get("title", "")
    all_knowledge = db.list_knowledge(work_id=work_id, limit=_CONTEXT_KNOWLEDGE * 4)
    # Exclude dismissed items — only inject auto (rule-based) and approved items
    knowledge = [
        k for k in all_knowledge
        if k.get("review_status") not in ("rejected",)
    ][:_CONTEXT_KNOWLEDGE]

    context_parts = [f"You are assisting with a research work titled \"{work_title}\"."]
    if knowledge:
        context_parts.append("Relevant knowledge from the user's documents:")
        for k in knowledge:
            kind = k.get("kind", "note")
            text = k.get("text", "").strip()
            if text:
                context_parts.append(f"  [{kind}] {text[:300]}")

    return f"{base}\n\n" + "\n".join(context_parts)


def _build_messages(db: Any, conv: dict, new_user_text: str, scope: str = "work") -> list[dict]:
    """Build the full OpenAI-format messages array for this conversation."""
    system_prompt = _build_system_prompt(db, conv, scope=scope)

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


async def _stream_response(db: Any, conv: dict, user_text: str, deep: bool = False, scope: str = "work"):
    """SSE generator — streams tokens, stores final reply, auto-titles.

    Handles client disconnect (GeneratorExit) by persisting whatever tokens
    arrived before the connection dropped, so the conversation is never left
    with a missing assistant turn.
    """
    cfg = get_config()
    conv_id = conv["id"]
    messages = _build_messages(db, conv, user_text, scope=scope)
    full_reply = ""

    model = _model_for(conv)
    # Per-chunk silence timeout: if the AI server sends no new token for this
    # long, we treat the stream as stalled and close it cleanly.
    _CHUNK_TIMEOUT_SEC = 30

    try:
        import asyncio
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
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:]
                    if chunk.strip() == "[DONE]":
                        break
                    try:
                        d = json.loads(chunk)
                        token = d["choices"][0]["delta"].get("content", "")
                        if token:
                            full_reply += token
                            yield f"data: {json.dumps({'token': token})}\n\n"
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
                db.add_message(conv_id, "assistant", truncated, meta={"model": model})
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
        db.add_message(conv_id, "assistant", full_reply, meta={"model": model})
    _maybe_auto_title(db, conv, user_text)
    yield "data: [DONE]\n\n"


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
            db._conn.commit()
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
