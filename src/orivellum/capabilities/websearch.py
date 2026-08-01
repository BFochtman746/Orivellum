"""Web search tool — returns markdown-formatted citations from DuckDuckGo.

Uses DuckDuckGo's Instant Answers JSON API (no API key, no scraping required).
This API returns structured abstract text, related topics, and definition results.
It works reliably from server-side contexts unlike the HTML Lite endpoint, which
requires a real browser session and CAPTCHA-solving for automated access.

Falls back to a "search directly" link on total failure.  Never raises.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request

logger = logging.getLogger("orivellum.websearch")

_DDG_API = "https://api.duckduckgo.com/"
_HEADERS  = {
    "User-Agent": "Orivellum/1.0 (local AI research assistant; +https://orivellum.app)",
    "Accept": "application/json",
}
_MAX_RESULTS = 5
_TIMEOUT     = 12


def _ddg_search(query: str) -> list[dict[str, str]]:
    """Query the DuckDuckGo Instant Answers JSON API.

    Returns a list of {title, snippet, url} dicts (may be empty for queries
    outside DuckDuckGo's answer knowledge base).
    """
    params = urllib.parse.urlencode({
        "q":           query,
        "format":      "json",
        "no_html":     "1",
        "skip_disambig": "1",
        "no_redirect": "1",
    })
    req = urllib.request.Request(f"{_DDG_API}?{params}", headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    results: list[dict[str, str]] = []

    # 1. Abstract (best single authoritative answer)
    if data.get("AbstractText") and data.get("AbstractURL"):
        results.append({
            "title":   data.get("Heading", query)[:120],
            "snippet": data["AbstractText"][:350],
            "url":     data["AbstractURL"],
        })

    # 2. Answer (calculator, conversions, etc.)
    if data.get("Answer") and data.get("AnswerType"):
        results.append({
            "title":   f"{data['AnswerType'].title()} answer",
            "snippet": str(data["Answer"])[:250],
            "url":     f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query)}",
        })

    # 3. Definition
    if data.get("Definition") and data.get("DefinitionURL"):
        results.append({
            "title":   f"Definition: {data.get('Heading', query)}",
            "snippet": data["Definition"][:300],
            "url":     data["DefinitionURL"],
        })

    # 4. Related topics
    for topic in data.get("RelatedTopics", []):
        if len(results) >= _MAX_RESULTS:
            break
        # Flat topic
        if isinstance(topic, dict) and topic.get("Text") and topic.get("FirstURL"):
            results.append({
                "title":   topic["Text"][:80],
                "snippet": topic["Text"][:250],
                "url":     topic["FirstURL"],
            })
            continue
        # Nested sub-topics
        for sub in topic.get("Topics", []):
            if len(results) >= _MAX_RESULTS:
                break
            if sub.get("Text") and sub.get("FirstURL"):
                results.append({
                    "title":   sub["Text"][:80],
                    "snippet": sub["Text"][:250],
                    "url":     sub["FirstURL"],
                })

    return results[:_MAX_RESULTS]


def web_search(query: str) -> str:
    """Search for *query* and return a markdown-formatted result.

    Uses DuckDuckGo Instant Answers API (reliable, no CAPTCHA, no key).
    For queries outside DDG's answer database the tool provides a direct
    search link so the user can open it in their browser.

    Never raises.
    """
    direct_url = f"https://duckduckgo.com/?q={urllib.parse.quote_plus(query)}"

    results: list[dict[str, str]] = []
    try:
        results = _ddg_search(query)
    except Exception as exc:
        logger.warning("DuckDuckGo Instant Answers search failed: %s", exc)

    if not results:
        return (
            f"🌐 **Web Search: {query}**\n\n"
            "DuckDuckGo's Instant Answers database doesn't have a structured result "
            "for this query (common for open-ended research topics). "
            "To get full web results, open the search directly in your browser:\n\n"
            f"[Search on DuckDuckGo]({direct_url})"
        )

    lines = [f"🌐 **Web Search: {query}**\n"]
    for i, r in enumerate(results, 1):
        title   = r["title"].strip()
        snippet = r["snippet"].strip()
        url     = r["url"]
        # Avoid duplicating title text in snippet
        if snippet.lower().startswith(title.lower()):
            snippet = snippet[len(title):].lstrip(" —-:")
        lines.append(f"**{i}. [{title}]({url})**")
        if snippet:
            lines.append(f"   {snippet}")
        lines.append("")

    lines.append(f"---\n*Powered by DuckDuckGo Instant Answers — [open full results]({direct_url})*")
    return "\n".join(lines)
