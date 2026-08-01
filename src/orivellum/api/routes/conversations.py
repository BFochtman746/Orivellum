"""Conversations and chat endpoints — /api/conversations/*"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from orivellum.api._deps import get_db, get_config

router = APIRouter(prefix="/api")


class ConversationCreate(BaseModel):
    title: str | None = None
    work_id: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    archived: bool | None = None


class MessageSend(BaseModel):
    text: str
    stream: bool = False


@router.get("/conversations")
def list_conversations(archived: bool = False, limit: int = 100):
    db = get_db()
    convs = db.list_conversations(archived=archived, limit=min(limit, 500))
    return {"conversations": convs, "count": len(convs)}


@router.post("/conversations")
def create_conversation(body: ConversationCreate):
    db = get_db()
    conv = db.create_conversation(title=body.title, work_id=body.work_id)
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
    conv = db.update_conversation(conv_id, title=body.title, archived=body.archived)
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

    # Store user message
    db.add_message(conv_id, "user", body.text)

    if body.stream:
        return StreamingResponse(
            _stream_response(db, conv_id, body.text),
            media_type="text/event-stream",
        )

    # Non-streaming: call AI or return degraded response
    reply = await _get_ai_reply(body.text, conv_id)
    msg = db.add_message(conv_id, "assistant", reply)
    return {"message": msg}


async def _get_ai_reply(user_text: str, conv_id: str) -> str:
    """Call Lemonade AI; degrade gracefully if unavailable."""
    cfg = get_config()
    try:
        import httpx
        async with httpx.AsyncClient(timeout=cfg.serving.timeout_sec) as client:
            resp = await client.post(
                f"{cfg.serving.base_url}/chat/completions",
                json={
                    "model": cfg.serving.workhorse_model,
                    "messages": [{"role": "user", "content": user_text}],
                    "stream": False,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                return data["choices"][0]["message"]["content"]
    except Exception:
        pass
    return (
        "AI service is currently unavailable (Lemonade not connected). "
        "Your message has been saved."
    )


async def _stream_response(db: Any, conv_id: str, user_text: str):
    """SSE stream — yields tokens, stores final reply."""
    cfg = get_config()
    full_reply = ""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=cfg.serving.timeout_sec) as client:
            async with client.stream(
                "POST",
                f"{cfg.serving.base_url}/chat/completions",
                json={
                    "model": cfg.serving.workhorse_model,
                    "messages": [{"role": "user", "content": user_text}],
                    "stream": True,
                },
            ) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        chunk = line[6:]
                        if chunk == "[DONE]":
                            break
                        try:
                            import json
                            d = json.loads(chunk)
                            token = d["choices"][0]["delta"].get("content", "")
                            if token:
                                full_reply += token
                                yield f"data: {json.dumps({'token': token})}\n\n"
                        except Exception:
                            pass
    except Exception:
        full_reply = (
            "AI service is currently unavailable (Lemonade not connected). "
            "Your message has been saved."
        )
        import json
        yield f"data: {json.dumps({'token': full_reply})}\n\n"

    if full_reply:
        db.add_message(conv_id, "assistant", full_reply)
    yield "data: [DONE]\n\n"
