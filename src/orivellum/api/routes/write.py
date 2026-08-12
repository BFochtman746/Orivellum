"""Write Desk — /api/write/*

Full document drafting workspace with AI assistance.  Supports creating,
editing, and managing rich-text documents with AI commands: continue, improve,
expand, summarize, rewrite, fix, shorten, outline, from_knowledge.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from orivellum.api._deps import get_config, get_db, require_auth

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/write", dependencies=[Depends(require_auth)])
# ── Helpers ───────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _count_words(text: str) -> int:
    return len(text.split()) if text.strip() else 0


# ── CRUD models ───────────────────────────────────────────────────────────────


class CreateDocRequest(BaseModel):
    title: str = "Untitled"
    content_json: dict = Field(default_factory=dict)
    content_text: str = ""
    work_id: str | None = None


class UpdateDocRequest(BaseModel):
    title: str | None = None
    content_json: dict | None = None
    content_text: str | None = None
    work_id: str | None = None
    is_pinned: bool | None = None


class AIAssistRequest(BaseModel):
    command: str  # continue|improve|expand|summarize|rewrite|fix|shorten|outline|ask|from_knowledge
    selection: str = ""  # selected text to operate on
    document_text: str = ""  # full doc plain text for context
    instruction: str = ""  # optional custom instruction (for "ask")
    voice: str = "default"  # tone/style hint
    image_b64: str | None = None  # base64-encoded image for vision/analysis
    image_media_type: str = "image/jpeg"


# ── Document CRUD ─────────────────────────────────────────────────────────────


@router.get("/documents")
def list_documents():
    db = get_db()
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, title, word_count, work_id, is_pinned, created_at, updated_at
               FROM write_documents
               ORDER BY is_pinned DESC, updated_at DESC
               LIMIT 200"""
        ).fetchall()
    return {"documents": [dict(r) for r in rows]}


@router.post("/documents")
def create_document(body: CreateDocRequest):
    db = get_db()
    doc_id = _uuid()
    now = _now()
    wc = _count_words(body.content_text)
    with db._lock:
        db._conn.execute(
            """INSERT INTO write_documents
               (id, title, content_json, content_text, word_count, work_id,
                is_pinned, created_at, updated_at)
               VALUES (?,?,?,?,?,?,0,?,?)""",
            (
                doc_id,
                body.title,
                __import__("json").dumps(body.content_json),
                body.content_text,
                wc,
                body.work_id,
                now,
                now,
            ),
        )
        db._conn.commit()
    db.audit(
        "write_document.created",
        object_id=doc_id,
        object_type="write_document",
        actor="user",
        detail=body.title[:120] if body.title else None,
    )
    return get_document(doc_id)


@router.get("/documents/{doc_id}")
def get_document(doc_id: str):
    db = get_db()
    with db._lock:
        row = db._conn.execute("SELECT * FROM write_documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Write document {doc_id!r} not found")
    d = dict(row)
    try:
        d["content_json"] = __import__("json").loads(d["content_json"] or "{}")
    except Exception:
        d["content_json"] = {}
    return d


@router.patch("/documents/{doc_id}")
def update_document(doc_id: str, body: UpdateDocRequest):
    db = get_db()
    with db._lock:
        row = db._conn.execute("SELECT * FROM write_documents WHERE id=?", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Write document {doc_id!r} not found")

    import json as _json

    sets: list[str] = ["updated_at=?"]
    args: list[Any] = [_now()]

    if body.title is not None:
        sets.append("title=?")
        args.append(body.title)
    if body.content_json is not None:
        sets.append("content_json=?")
        args.append(_json.dumps(body.content_json))
    if body.content_text is not None:
        sets.append("content_text=?")
        args.append(body.content_text)
        sets.append("word_count=?")
        args.append(_count_words(body.content_text))
    if body.work_id is not None:
        sets.append("work_id=?")
        args.append(body.work_id if body.work_id != "__none__" else None)
    if body.is_pinned is not None:
        sets.append("is_pinned=?")
        args.append(int(body.is_pinned))

    args.append(doc_id)
    with db._lock:
        cur = db._conn.execute(f"UPDATE write_documents SET {', '.join(sets)} WHERE id=?", args)
        db._conn.commit()
    if cur.rowcount > 0:
        db.audit(
            "write_document.updated",
            object_id=doc_id,
            object_type="write_document",
            actor="user",
            detail=",".join(s.split("=")[0] for s in sets if s != "updated_at=?"),
        )
    return get_document(doc_id)


@router.delete("/documents/{doc_id}")
def delete_document(doc_id: str):
    db = get_db()
    _existed = False
    with db._lock:
        _row = db._conn.execute("SELECT id FROM write_documents WHERE id=?", (doc_id,)).fetchone()
        _existed = _row is not None
        db._conn.execute("DELETE FROM write_documents WHERE id=?", (doc_id,))
        db._conn.commit()
    if _existed:
        db.audit(
            "write_document.deleted", object_id=doc_id, object_type="write_document", actor="user"
        )
    return {"ok": True}


# ── AI Assistance ─────────────────────────────────────────────────────────────

_COMMAND_PROMPTS: dict[str, str] = {
    "continue": (
        "Continue writing from where this text ends.  Match the author's voice, style, "
        "and tone precisely.  Write the next 2–4 paragraphs naturally:\n\n{doc}\n\n"
        "Continue seamlessly from the end of the document above."
    ),
    "improve": (
        "Improve the following text for clarity, flow, and word choice.  "
        "Keep the meaning and length similar.  Return only the improved text, no commentary:\n\n{sel}"
    ),
    "expand": (
        "Expand the following text with more depth, examples, and detail.  "
        "Maintain the author's voice.  Return only the expanded text:\n\n{sel}"
    ),
    "summarize": (
        "Summarize the following text in 2–3 concise sentences that capture the key points:\n\n{sel}"
    ),
    "rewrite": (
        "Rewrite the following text.  Make it clearer and more engaging while preserving "
        "all the original ideas.  Return only the rewritten text:\n\n{sel}"
    ),
    "fix": (
        "Fix all grammar, spelling, and punctuation errors in the following text.  "
        "Do not change wording beyond what is necessary to correct errors.  "
        "Return only the corrected text:\n\n{sel}"
    ),
    "shorten": (
        "Make the following text more concise.  Cut unnecessary words and sentences while "
        "keeping every important idea.  Aim for roughly half the length:\n\n{sel}"
    ),
    "outline": (
        "Create a detailed outline for an essay or document on the following topic.  "
        "Use nested bullet points with Roman numerals for main sections:\n\n{sel}"
    ),
    "makeformal": (
        "Rewrite the following text in a formal, professional tone suitable for a report "
        "or academic paper.  Return only the rewritten text:\n\n{sel}"
    ),
    "makecasual": (
        "Rewrite the following text in a friendly, conversational tone.  "
        "Keep it engaging and approachable.  Return only the rewritten text:\n\n{sel}"
    ),
    "explain": (
        "Explain the following concept or passage as if to an intelligent non-expert.  "
        "Use plain language, analogies where helpful, and be thorough:\n\n{sel}"
    ),
}


@router.post("/documents/{doc_id}/ai")
def ai_assist(doc_id: str, body: AIAssistRequest):
    """Run an AI writing command and stream the response."""
    cfg = get_config()
    db = get_db()

    # Verify document exists (work_id scopes knowledge pulls to this book)
    with db._lock:
        row = db._conn.execute(
            "SELECT title, work_id FROM write_documents WHERE id=?", (doc_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Document not found")
    doc_work_id = row["work_id"]

    cmd = body.command.lower().strip()

    # ── from_knowledge: inject relevant items from the knowledge base ─────────
    if cmd == "from_knowledge":
        query = body.selection or body.instruction or body.document_text[:200]
        # Scope to the document's Work so canon never leaks across books
        # (audit D-10); unlinked documents fall back to the global base.
        # Order by confidence so the strongest facts are offered first.
        where = "k.review_status NOT IN ('rejected','quarantined_reprojection')"
        params: tuple = ()
        if doc_work_id:
            where += " AND k.work_id = ?"
            params = (doc_work_id,)
        with db._lock:
            items = db._conn.execute(
                f"""SELECT k.kind, k.subject, k.text
                    FROM knowledge k
                    WHERE {where}
                    ORDER BY k.confidence DESC, k.created_at DESC
                    LIMIT 20""",
                params,
            ).fetchall()
        if not items:

            def _no_knowledge():
                yield 'data: {"choices":[{"delta":{"content":"No knowledge items found in your knowledge base yet. Upload and extract some documents first."}}]}\n\ndata: [DONE]\n\n'

            return StreamingResponse(_no_knowledge(), media_type="text/event-stream")

        knowledge_block = "\n".join(
            f"- **{r['kind'].upper()}{': ' + r['subject'] if r['subject'] else ''}**: {r['text']}"
            for r in items
        )
        prompt = (
            f"Select the most relevant knowledge items for the following context "
            f"and format them as clean prose that can be inserted into the document.\n\n"
            f"Context: {query}\n\n"
            f"Knowledge base:\n{knowledge_block}\n\n"
            f"Write a paragraph or two inserting the most relevant facts, citing which "
            f"topics they come from."
        )

    # ── ask / custom instruction ───────────────────────────────────────────────
    elif cmd == "ask":
        doc_ctx = (
            f"\n\nDocument context:\n{body.document_text[:3000]}" if body.document_text else ""
        )
        sel_ctx = f"\n\nSelected text:\n{body.selection}" if body.selection else ""
        prompt = f"{body.instruction}{doc_ctx}{sel_ctx}"

    # ── standard commands ─────────────────────────────────────────────────────
    else:
        template = _COMMAND_PROMPTS.get(cmd)
        if not template:
            raise HTTPException(
                400,
                f"Unknown AI command: {cmd!r}. Valid commands: "
                f"{', '.join(_COMMAND_PROMPTS)} ask from_knowledge",
            )

        # Build context: for doc-level commands use full doc; for selection commands use selection
        doc_ctx = body.document_text[:4000] if body.document_text else ""
        sel_ctx = body.selection[:2000] if body.selection else doc_ctx

        prompt = template.format(doc=doc_ctx, sel=sel_ctx or doc_ctx)

        # Append custom instruction if provided
        if body.instruction:
            prompt += f"\n\nAdditional instruction: {body.instruction}"

    # ── Stream the LLM response ───────────────────────────────────────────────
    # This endpoint forwards SSE chunks to the editor, so it keeps its own
    # streaming request loop (llm_call is non-streaming). Telemetry is recorded
    # via record_llm_call() when the stream ends, mirroring the chat stream path.
    import time as _time

    import httpx

    from orivellum.capabilities.llm import record_llm_call

    # Build user message content — plain text or multimodal (with image)
    if body.image_b64:
        _user_content: list | str = [
            {"type": "text", "text": prompt or "Describe and analyze this image."},
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{body.image_media_type};base64,{body.image_b64}",
                },
            },
        ]
    else:
        _user_content = prompt

    def _stream():
        _started = _time.monotonic()
        _ok = True
        _err: str | None = None
        try:
            with httpx.stream(
                "POST",
                f"{cfg.serving.base_url}/chat/completions",
                json={
                    "model": cfg.serving.workhorse_model,
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are an expert writing assistant. Follow instructions precisely. Return only what was requested — no preamble, no meta-commentary.",
                        },
                        {"role": "user", "content": _user_content},
                    ],
                    "stream": True,
                    "max_tokens": 1200,
                    "temperature": 0.7,
                },
                timeout=90,
            ) as resp:
                for line in resp.iter_lines():
                    if line:
                        yield f"data: {line}\n\n"
        except GeneratorExit:
            # Client aborted the SSE stream — classify as a failed call.
            _ok = False
            _err = "client_disconnected"
            raise  # Re-raise so the generator closes properly
        except Exception as exc:
            logger.error("Write AI assist stream error: %s", exc)
            _ok = False
            _err = f"{type(exc).__name__}: {exc}"[:500]
            import json as _j

            yield f"data: {_j.dumps({'error': str(exc)[:500]})}\n\ndata: [DONE]\n\n"
        finally:
            record_llm_call(
                db,
                purpose="write",
                model=cfg.serving.workhorse_model,
                latency_ms=int((_time.monotonic() - _started) * 1000),
                prompt_tokens=None,
                completion_tokens=None,
                ok=_ok,
                error=_err,
            )

    return StreamingResponse(_stream(), media_type="text/event-stream")


# ── Export ─────────────────────────────────────────────────────────────────────


@router.get("/documents/{doc_id}/export/txt")
def export_txt(doc_id: str):
    """Export write document as plain text."""
    import re

    from fastapi.responses import PlainTextResponse

    with get_db()._lock:
        row = (
            get_db()
            ._conn.execute("SELECT title, content_text FROM write_documents WHERE id=?", (doc_id,))
            .fetchone()
        )
    if not row:
        raise HTTPException(404, "Document not found")

    filename = re.sub(r"[^\w\- ]", "_", row["title"])[:60] + ".txt"
    return PlainTextResponse(
        row["content_text"] or "",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
