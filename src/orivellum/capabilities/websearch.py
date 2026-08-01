"""Web search via Tavily — AI-optimised results with full page content + LLM synthesis.

Tavily is built specifically for AI agents: it returns extracted page content
(not just snippets), relevance scores, and optionally a pre-generated answer.
We take the raw results, feed them to the local LLM for a cited synthesis, and
return a clean markdown response.

Requires the TAVILY_API_KEY environment variable.  Falls back to a DuckDuckGo
link if the key is absent or the API is unreachable.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request

logger = logging.getLogger("orivellum.websearch")

_TAVILY_URL  = "https://api.tavily.com/search"
_MAX_RESULTS = 8
_TIMEOUT     = 20


def _api_key() -> str:
    """Return the Tavily API key from the environment."""
    return os.environ.get("TAVILY_API_KEY", "").strip()


def _fetch_results(query: str, search_depth: str = "basic") -> list[dict]:
    """Call the Tavily Search API and return raw result dicts.

    search_depth: "basic" (fast, free-tier) or "advanced" (deeper, costs 2 credits).
    """
    key = _api_key()
    if not key:
        raise RuntimeError("TAVILY_API_KEY is not set")

    payload = json.dumps({
        "query":        query,
        "search_depth": search_depth,
        "max_results":  _MAX_RESULTS,
        "include_answer": False,   # we synthesise our own with the local LLM
    }).encode("utf-8")

    req = urllib.request.Request(
        _TAVILY_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results = data.get("results", [])
    logger.info("Tavily: %d results for %r", len(results), query)
    return results


def _format_results_block(query: str, results: list[dict]) -> str:
    """Format Tavily results as a clean numbered markdown block (fallback display)."""
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        title   = (r.get("title")          or "").strip()[:200]
        content = (r.get("content")        or "").strip()[:400]
        url     = (r.get("url")            or "").strip()
        pub     = (r.get("published_date") or "").strip()[:20]
        if not title or not url:
            continue
        lines.append(f"**[{i}] [{title}]({url})**")
        if pub:
            lines.append(f"*{pub}*")
        if content:
            lines.append(content)
        lines.append("")
    return "\n".join(lines)


def web_search(query: str) -> str:
    """Search with Tavily and return a formatted markdown string (no LLM synthesis).

    Used as the simple interface when a full synthesis isn't available.
    Never raises.
    """
    try:
        results = _fetch_results(query)
    except Exception as exc:
        logger.error("web_search error: %s", exc)
        results = []

    if not results:
        direct = f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query)}"
        return (
            f"🌐 **Web Search: {query}**\n\n"
            "No results found — Tavily API key may be missing or the service is unreachable.\n\n"
            f"[Search on DuckDuckGo]({direct})"
        )

    block = _format_results_block(query, results)
    return f"🌐 **Web Search: {query}**\n\n{block}"


def web_search_synthesize(query: str, base_url: str, model: str) -> str:
    """Search with Tavily, then have the local LLM synthesise a cited answer.

    Tavily returns the full extracted content of each page — far more than snippets —
    so the LLM has rich material to work from.  Returns a synthesised answer with
    inline [1][2] citations followed by a numbered sources list.

    Falls back to plain formatted results if the LLM call fails.  Never raises.
    """
    try:
        results = _fetch_results(query)
    except Exception as exc:
        logger.error("web_search_synthesize fetch error: %s", exc)
        results = []

    if not results:
        return web_search(query)

    # Build the context block the LLM will read.
    # Tavily's `content` field is full extracted page text — truncate to 600 chars
    # per result to stay within a reasonable prompt budget.
    context_lines: list[str] = []
    for i, r in enumerate(results, 1):
        title   = (r.get("title")          or "").strip()[:200]
        content = (r.get("content")        or "").strip()[:600]
        url     = (r.get("url")            or "").strip()
        pub     = (r.get("published_date") or "").strip()[:20]
        score   = r.get("score", 0)

        entry = f"[{i}] {title}"
        if pub:
            entry += f" ({pub})"
        if content:
            entry += f"\n{content}"
        entry += f"\nURL: {url}"
        context_lines.append(entry)

    context = "\n\n".join(context_lines)

    synthesis_prompt = (
        "You are a research assistant synthesising web search results into a clear, "
        "accurate answer. Use inline citation numbers like [1] or [2] when referencing "
        "a source. Be concise but complete. Do not invent facts not present in the sources.\n\n"
        f"User query: {query}\n\n"
        f"Search results:\n{context}\n\n"
        "Write a well-structured answer to the query, citing sources inline."
    )

    try:
        import httpx
        resp = httpx.post(
            f"{base_url}/chat/completions",
            json={
                "model":       model,
                "messages":    [{"role": "user", "content": synthesis_prompt}],
                "max_tokens":  900,
                "temperature": 0.2,
            },
            timeout=45,
        )
        if resp.status_code == 200:
            synthesis = (
                resp.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )
            if synthesis:
                sources: list[str] = []
                for i, r in enumerate(results, 1):
                    title = (r.get("title") or "").strip()[:120]
                    url   = (r.get("url")   or "").strip()
                    sources.append(f"**[{i}]** [{title}]({url})")

                return (
                    f"🌐 **{query}**\n\n"
                    f"{synthesis}\n\n"
                    "---\n**Sources**\n" + "\n".join(sources)
                )
    except Exception as exc:
        logger.warning("LLM synthesis failed (%s) — returning raw results", exc)

    # Fallback: plain formatted results
    block = _format_results_block(query, results)
    return f"🌐 **Web Search: {query}**\n\n{block}"
