"""Web search via SearXNG — real news, research, and general results with LLM synthesis.

SearXNG is an open-source metasearch engine that simultaneously queries Google,
Bing, DuckDuckGo, Wikipedia, and many other engines.  We query its JSON API,
collect the top results, synthesise an answer with the local LLM (so the user
gets a proper cited response), and return the result.

Public SearXNG instances are used by default; a custom instance URL can be
configured via Settings.  Falls back to a direct-search link on total failure.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger("orivellum.websearch")

# ── Public SearXNG instances (tried in order until one responds) ───────────────
_PUBLIC_INSTANCES = [
    "https://searxng.site",
    "https://searx.be",
    "https://paulgo.io",
    "https://search.mdosch.de",
    "https://searx.tiekoetter.com",
]

_MAX_RESULTS = 8
_TIMEOUT     = 15
_HEADERS     = {
    "User-Agent": "Orivellum/1.0 (local AI research assistant)",
    "Accept":     "application/json",
}


def _configured_instance() -> str:
    """Return the user-configured SearXNG base URL, or the first public instance."""
    try:
        from orivellum.api._deps import get_db
        val = get_db().get_setting("searxng_url", "")
        if val and val.startswith("http"):
            return val.rstrip("/")
    except Exception:
        pass
    return _PUBLIC_INSTANCES[0]


def _query_instance(instance: str, query: str, categories: str = "general,news") -> list[dict]:
    """Query one SearXNG instance's JSON endpoint and return raw result dicts."""
    params = urllib.parse.urlencode({
        "q":          query,
        "format":     "json",
        "categories": categories,
        "language":   "en",
    })
    req = urllib.request.Request(f"{instance}/search?{params}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("results", [])[:_MAX_RESULTS]


def _fetch_results(query: str) -> list[dict]:
    """Try the configured instance, then each public fallback, return first success."""
    instances = [_configured_instance()] + [
        i for i in _PUBLIC_INSTANCES if i != _configured_instance()
    ]
    for inst in instances:
        try:
            raw = _query_instance(inst, query)
            if raw:
                logger.info("SearXNG: %d results from %s", len(raw), inst)
                return raw
            logger.debug("SearXNG %s returned 0 results", inst)
        except Exception as exc:
            logger.warning("SearXNG instance %s failed: %s", inst, exc)
    return []


def _format_results_block(query: str, results: list[dict]) -> str:
    """Format search results as a clean numbered markdown block."""
    lines: list[str] = []
    for i, r in enumerate(results, 1):
        title   = (r.get("title") or "").strip()[:200]
        snippet = (r.get("content") or r.get("snippet") or "").strip()[:400]
        url     = (r.get("url") or "").strip()
        pub     = (r.get("publishedDate") or "").strip()[:20]
        if not title or not url:
            continue
        lines.append(f"**[{i}] [{title}]({url})**")
        if pub:
            lines.append(f"*{pub}*")
        if snippet:
            lines.append(snippet)
        lines.append("")
    return "\n".join(lines)


def web_search(query: str) -> str:
    """Search with SearXNG and return a formatted markdown string.

    This is the simple interface used when a full LLM synthesis isn't available.
    For a synthesised conversational answer use web_search_synthesize().
    Never raises.
    """
    try:
        results = _fetch_results(query)
    except Exception as exc:
        logger.error("web_search unexpected error: %s", exc)
        results = []

    if not results:
        direct = f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query)}"
        return (
            f"🌐 **Web Search: {query}**\n\n"
            "No results found — all search engines were unreachable or returned nothing.\n\n"
            f"[Open in browser]({direct})"
        )

    block = _format_results_block(query, results)
    return f"🌐 **Web Search: {query}**\n\n{block}"


def web_search_synthesize(query: str, base_url: str, model: str) -> str:
    """Search with SearXNG, then have the local LLM synthesise a cited answer.

    Returns the synthesised answer with inline [1][2] citations followed by a
    numbered sources list.  Falls back to the plain formatted results if the
    LLM call fails.  Never raises.
    """
    try:
        results = _fetch_results(query)
    except Exception as exc:
        logger.error("web_search_synthesize fetch error: %s", exc)
        results = []

    if not results:
        return web_search(query)  # fallback

    # Build the context block the LLM will read
    context_lines: list[str] = []
    for i, r in enumerate(results, 1):
        title   = (r.get("title")   or "").strip()[:200]
        snippet = (r.get("content") or r.get("snippet") or "").strip()[:500]
        url     = (r.get("url")     or "").strip()
        pub     = (r.get("publishedDate") or "").strip()[:20]
        entry   = f"[{i}] {title}"
        if pub:
            entry += f" ({pub})"
        if snippet:
            entry += f"\n{snippet}"
        entry += f"\nURL: {url}"
        context_lines.append(entry)

    context = "\n\n".join(context_lines)

    synthesis_prompt = (
        f"You are a research assistant synthesising web search results into a clear, "
        f"accurate answer.  Use inline citation numbers like [1] or [2] when referencing "
        f"a source.  Be concise but complete.  Do not invent facts not present in the sources.\n\n"
        f"User query: {query}\n\n"
        f"Search results:\n{context}\n\n"
        f"Write a well-structured answer to the query, citing sources inline."
    )

    try:
        import httpx
        resp = httpx.post(
            f"{base_url}/chat/completions",
            json={
                "model":       model,
                "messages":    [{"role": "user", "content": synthesis_prompt}],
                "max_tokens":  800,
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
                # Build numbered sources footer
                sources: list[str] = []
                for i, r in enumerate(results, 1):
                    title = (r.get("title") or "").strip()[:120]
                    url   = (r.get("url")   or "").strip()
                    sources.append(f"**[{i}]** [{title}]({url})")

                return (
                    f"🌐 **{query}**\n\n"
                    f"{synthesis}\n\n"
                    f"---\n**Sources**\n" + "\n".join(sources)
                )
    except Exception as exc:
        logger.warning("LLM synthesis failed (%s) — returning raw results", exc)

    # Fallback: plain formatted results
    block = _format_results_block(query, results)
    return f"🌐 **Web Search: {query}**\n\n{block}"
