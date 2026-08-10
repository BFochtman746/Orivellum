"""Model Context Protocol (MCP) server for Orivellum knowledge base.

Exposes the knowledge base as an MCP-compatible HTTP endpoint so external
AI agents (Claude Desktop, Cursor, custom agents) can query it.

Supported transport: streamable HTTP POST (MCP 2025-03-26 spec).

Available tools:
  - search_knowledge(query, limit?, work_id?) → knowledge items + chunks
  - get_document(doc_id) → full document metadata + extracted text
  - list_works() → all Works (books/projects)
  - list_documents(work_id?, limit?) → documents in a Work

Reference: https://modelcontextprotocol.io/specification
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from orivellum.api._deps import get_db

logger = logging.getLogger("orivellum.mcp")

router = APIRouter()

# ── MCP helpers ───────────────────────────────────────────────────────────────

_SERVER_INFO = {
    "name": "orivellum",
    "version": "1.0.0",
}

_TOOLS: list[dict] = [
    {
        "name": "search_knowledge",
        "description": (
            "Search the knowledge base for items matching a query. "
            "Returns knowledge items (facts, concepts, decisions) and text chunks. "
            "Optionally filter by Work (book/project) using work_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results (default 10, max 50)", "default": 10},
                "work_id": {"type": "string", "description": "Filter by Work ID (optional)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_document",
        "description": "Retrieve full metadata and extracted text for a library document by its ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "doc_id": {"type": "string", "description": "Document ID"},
            },
            "required": ["doc_id"],
        },
    },
    {
        "name": "list_works",
        "description": "List all Works (books, research projects) in the knowledge base.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_documents",
        "description": "List documents in the library, optionally filtered by Work.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "work_id": {"type": "string", "description": "Filter by Work ID (optional)"},
                "limit": {"type": "integer", "description": "Max results (default 50)", "default": 50},
            },
        },
    },
]


def _tool_result(content: Any) -> dict:
    """Wrap a tool result in MCP format."""
    return {
        "content": [{"type": "text", "text": json.dumps(content, default=str, indent=2)}],
        "isError": False,
    }


def _tool_error(msg: str) -> dict:
    return {
        "content": [{"type": "text", "text": msg}],
        "isError": True,
    }


def _call_tool(name: str, arguments: dict) -> dict:
    """Execute a tool and return MCP-formatted result."""
    db = get_db()

    if name == "search_knowledge":
        query = str(arguments.get("query", "")).strip()
        if not query:
            return _tool_error("query is required")
        limit = min(int(arguments.get("limit", 10)), 50)
        work_id = arguments.get("work_id")

        # Knowledge items
        knowledge = db.search_knowledge(query, work_id=work_id, limit=limit)

        # Text chunks
        chunks = db.search_chunks(query, work_id=work_id, limit=limit)

        return _tool_result({
            "knowledge_items": knowledge,
            "text_chunks": [
                {"doc_id": c.get("doc_id"), "text": c.get("text", "")[:600],
                 "doc_title": c.get("doc_title"), "score": c.get("score")}
                for c in chunks
            ],
            "count": len(knowledge) + len(chunks),
        })

    elif name == "get_document":
        doc_id = str(arguments.get("doc_id", "")).strip()
        if not doc_id:
            return _tool_error("doc_id is required")
        doc = db.get_document(doc_id)
        if not doc:
            return _tool_error(f"Document {doc_id!r} not found")
        return _tool_result(doc)

    elif name == "list_works":
        works = db.list_works()
        return _tool_result({"works": works, "count": len(works)})

    elif name == "list_documents":
        work_id = arguments.get("work_id")
        limit = min(int(arguments.get("limit", 50)), 200)
        docs = db.list_documents(work_id=work_id, limit=limit)
        return _tool_result({"documents": docs, "count": len(docs)})

    else:
        return _tool_error(f"Unknown tool: {name!r}")


# ── MCP HTTP endpoint ─────────────────────────────────────────────────────────

@router.post("/mcp")
async def mcp_endpoint(request: Request):
    """Handle MCP requests over HTTP POST.

    Supports the MCP 2025-03-26 streamable HTTP transport.
    Each request is a JSON-RPC 2.0 message (object or batch array).
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None},
            status_code=400,
        )

    # Handle batch requests
    if isinstance(body, list):
        responses = [_handle_jsonrpc(msg) for msg in body]
        return JSONResponse([r for r in responses if r is not None])

    response = _handle_jsonrpc(body)
    if response is None:
        # Notification — no response expected
        return JSONResponse(None, status_code=204)
    return JSONResponse(response)


def _handle_jsonrpc(msg: dict) -> dict | None:
    """Process one JSON-RPC 2.0 message and return a response (or None for notifications)."""
    req_id = msg.get("id")
    method = msg.get("method", "")
    params = msg.get("params") or {}

    def ok(result: Any) -> dict:
        return {"jsonrpc": "2.0", "result": result, "id": req_id}

    def err(code: int, message: str, data: Any = None) -> dict:
        e: dict = {"code": code, "message": message}
        if data is not None:
            e["data"] = data
        return {"jsonrpc": "2.0", "error": e, "id": req_id}

    try:
        # ── Standard MCP methods ────────────────────────────────────────────
        if method == "initialize":
            return ok({
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": _SERVER_INFO,
                "instructions": (
                    "Orivellum knowledge base. Use search_knowledge to find relevant information, "
                    "list_works to browse projects, and get_document for full document content."
                ),
            })

        elif method == "initialized":
            return None  # notification — no response

        elif method == "tools/list":
            return ok({"tools": _TOOLS})

        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            try:
                result = _call_tool(tool_name, arguments)
            except Exception as exc:
                logger.exception("MCP tool call error: %s(%r)", tool_name, arguments)
                return err(-32603, f"Tool execution error: {exc}")
            return ok(result)

        elif method == "ping":
            return ok({})

        else:
            return err(-32601, f"Method not found: {method!r}")

    except Exception as exc:
        logger.exception("MCP JSON-RPC error")
        return err(-32603, f"Internal error: {exc}")


@router.get("/mcp")
async def mcp_info():
    """Return a human-readable summary of the MCP server capabilities."""
    return {
        "name": "Orivellum MCP Server",
        "version": "1.0.0",
        "protocol": "MCP 2024-11-05",
        "transport": "HTTP POST /mcp",
        "tools": [t["name"] for t in _TOOLS],
        "usage": (
            "Send JSON-RPC 2.0 POST requests to /mcp. "
            "Start with method='initialize', then 'tools/list', then 'tools/call'."
        ),
    }
