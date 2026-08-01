"""Intent classifier — routes chat messages to specialized tools before the AI.

Classification order:
  1. Fast pattern match (no LLM needed, sub-millisecond)
  2. LLM classify call (JSON schema, ~1 s, fallback to chat on error)

Recognized intents:
  - web_search  — "search for X", "find recent papers on X"
  - weather     — "what's the weather in X", "temperature in X"
  - image_gen   — "generate an image of X", "draw X"
  - remember    — "remember that X", "my name is X"
  - chat        — everything else
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger("orivellum.intent")

# ─── Pattern-based fast path ──────────────────────────────────────────────────

# Weather pattern has a named group to extract location directly in the fast path
_WEATHER_RE = re.compile(
    r"\b(?:weather (?:in|for|at)|what.?s the weather (?:in|for|at)|"
    r"temperature (?:in|for|at)|forecast (?:in|for|at)|weather forecast (?:in|for|at))"
    r"\s+(?P<location>[A-Za-z][A-Za-z ,'\-]{1,60}?)(?:[?.,!]|$)",
    re.IGNORECASE,
)
# Simpler fallback: "is it raining/snowing/cold/hot in <location>"
_WEATHER_RE2 = re.compile(
    r"\bis it (?:raining|snowing|cold|hot|warm|sunny|cloudy) (?:in|at)\s+(?P<location>[A-Za-z][A-Za-z ,'\-]{1,60}?)(?:[?.,!]|$)",
    re.IGNORECASE,
)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # remember
    (re.compile(
        r"\b(remember (that|my|i|this)|my name is|i prefer|i like|i dislike"
        r"|i always|i never|my (email|phone|address|birthday))\b",
        re.IGNORECASE,
    ), "remember"),
    # weather — handled separately below for location extraction
    (re.compile(
        r"\b(weather (in|for|at)|what.?s the weather|temperature in|is it (raining|snowing|cold|hot|warm|sunny)"
        r"|forecast for|weather forecast)\b",
        re.IGNORECASE,
    ), "weather"),
    # image generation
    (re.compile(
        r"\b(generate (an? )?image|create (an? )?image|draw (an? |a picture of )?"
        r"|make (an? )?image|show me (an? )?image|paint (an? )?|illustrate)\b",
        re.IGNORECASE,
    ), "image_gen"),
    # web search — broad pattern covering "research online", "look up", news queries, etc.
    (re.compile(
        r"\b(search (for|the web|online)|find (information|papers|articles|news) (about|on)"
        r"|look up( online)?|recent (papers|news|articles|research) (on|about)"
        r"|what (are|is) (the latest|recent)|google|bing|search online"
        r"|research online|find online|latest (news|updates|info) (about|on|for)"
        r"|news (about|on|for)|top news|current (news|events|updates)"
        r"|what.?s happening|what.?s new (with|at|in)"
        r"|can you (research|find|look up|search))\b",
        re.IGNORECASE,
    ), "web_search"),
]


def _extract_weather_location(text: str) -> str | None:
    """Try to extract a city/location from a weather question without using an LLM."""
    for pat in (_WEATHER_RE, _WEATHER_RE2):
        m = pat.search(text)
        if m:
            loc = m.group("location").strip().rstrip("?.,!")
            if loc:
                return loc
    return None

# ─── LLM classify prompt ──────────────────────────────────────────────────────

_CLASSIFY_PROMPT = """Classify the user's message into exactly one intent.

Intents:
- "web_search"  — wants information from the web, recent news, research papers, or facts the AI may not know
- "weather"     — wants current weather, temperature, or forecast for a location
- "image_gen"   — wants an image generated or drawn
- "remember"    — explicitly wants to store a personal fact/preference for later recall
- "chat"        — everything else: questions, analysis, writing, research assistance

Respond ONLY with valid JSON (no code fences):
{"intent": "<one of the five intents>", "query": "<extracted search query or key phrase>", "location": "<city/region for weather, else null>"}

Message: {message}"""


def classify_intent(
    user_text: str,
    base_url: str,
    model: str,
    history: list[dict] | None = None,
) -> dict[str, Any]:
    """Classify the intent of *user_text*.

    Returns a dict with keys:
      - intent: str (one of web_search / weather / image_gen / remember / chat)
      - query: str (extracted search query or key phrase)
      - location: str | None (for weather)

    Never raises — falls back to {"intent": "chat", "query": user_text, "location": None}.
    """
    # Fast pattern path — no LLM needed
    lower = user_text.strip().lower()
    for pattern, intent in _PATTERNS:
        if pattern.search(lower):
            logger.debug("Intent fast-path: %s for %r", intent, user_text[:60])
            location = None
            if intent == "weather":
                # Extract location directly from the text so the geocoder
                # receives "London" not "what's the weather in London?"
                location = _extract_weather_location(user_text)
            return {"intent": intent, "query": user_text, "location": location}

    # LLM classify
    try:
        from orivellum.capabilities.cognition import _call_sync
        prompt = _CLASSIFY_PROMPT.format(message=user_text[:400])
        raw = _call_sync(
            [{"role": "user", "content": prompt}],
            base_url=base_url,
            model=model,
            timeout=10,
        )
        if not raw:
            return {"intent": "chat", "query": user_text, "location": None}
        parsed = json.loads(raw.strip())
        intent = parsed.get("intent", "chat")
        if intent not in ("web_search", "weather", "image_gen", "remember", "chat"):
            intent = "chat"
        return {
            "intent": intent,
            "query": parsed.get("query", user_text),
            "location": parsed.get("location"),
        }
    except Exception as exc:
        logger.debug("Intent LLM classify failed: %s — falling back to chat", exc)
        return {"intent": "chat", "query": user_text, "location": None}
