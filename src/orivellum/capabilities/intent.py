"""Intent classifier — routes chat messages to specialized tools before the AI.

Classification order:
  1. Fast pattern match (no LLM needed, sub-millisecond)
  2. LLM classify call (JSON schema, ~1 s, fallback to chat on error)

Recognized intents:
  - web_search  — "search for X", "find recent papers on X"
  - weather     — "what's the weather in X", "temperature in X"
  - image_gen   — "generate an image of X", "draw X"
  - remember    — "remember that X", "my name is X"
  - recall      — "where are we on X", "what did we decide about X"
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

_ACTION_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # tax_package
    (
        re.compile(
            r"\b(build|create|make|generate|prepare|assemble|put together|compile)\b.{0,40}"
            r"\b(tax package|expense package|expense report|receipt bundle|tax bundle)\b"
            r"|\bfile my taxes\b|\bprepare my taxes\b"
            r"|\btax (package|export|summary|report) for \d{4}\b",
            re.IGNORECASE,
        ),
        "action",
        "tax_package",
    ),
    # book_export
    (
        re.compile(
            r"\b(export|assemble|compile|build|create|download)\b.{0,30}"
            r"\b(manuscript|book|chapters).{0,20}\b(docx|word|file|document)?\b"
            r"|\bexport (the )?book\b|\bexport (the )?manuscript\b|\bcompile (my )?chapters\b",
            re.IGNORECASE,
        ),
        "action",
        "book_export",
    ),
    # report_assembler
    (
        re.compile(
            r"\b(build|create|generate|assemble|compile|export)\b.{0,30}"
            r"\b(report|package|summary doc|research report|work report)\b"
            r"|\bcompile this work\b|\bassemble (a |this )?report\b|\bgenerate (a )?report\b",
            re.IGNORECASE,
        ),
        "action",
        "report_assembler",
    ),
    # study_plan
    (
        re.compile(
            r"\b(create|generate|build|make|prepare)\b.{0,30}"
            r"\b(study plan|learning plan|study schedule|learning schedule|learning path)\b"
            r"|\bstudy plan for\b|\blearning plan\b",
            re.IGNORECASE,
        ),
        "action",
        "study_plan",
    ),
    # template_fill
    (
        re.compile(
            r"\b(fill|complete|populate|render)\b.{0,30}\btemplate\b"
            r"|\bfill (in |out )?(the |this |a )?template\b"
            r"|\btemplate fill\b",
            re.IGNORECASE,
        ),
        "action",
        "template_fill",
    ),
]

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # recall_output — "find the report I made", "show me the TTS clip I generated"
    (
        re.compile(
            r"\b(find|show|get|retrieve|locate|where is)\b.{0,50}"
            r"\b(i (made|created|generated|uploaded|wrote|built|produced)"
            r"|my (report|tts|clip|image|document|pdf|docx|manuscript|file|output|summary|transcript))\b"
            r"|\bfind what i (made|created|generated|produced|uploaded) (about|on|for|regarding)\b"
            r"|\b(show|find|get|list) my (generated|created|uploaded|recent) (files?|outputs?|documents?|reports?|clips?|images?|tts)\b"
            r"|\bwhat (files?|outputs?|documents?|reports?|clips?|images?) (did i|have i) (made?|created?|generated?|uploaded?)\b",
            re.IGNORECASE,
        ),
        "recall_output",
    ),
    # recall — "where are we on X", "what did we decide", "what's our progress"
    (
        re.compile(
            r"\b(where (are|were) we (on|with|regarding|about)"
            r"|what('s| is| was| did| have)? (our|the)? ?(status|progress|decision|outcome|conclusion|summary|update|position) (on|of|about|regarding|for)"
            r"|what (did we|have we) (decide|concluded|agreed|discussed|settled|resolved|determine)"
            r"|where did we (land|end up|settle|get to) (on|with)"
            r"|what'?s? (our|the) current status (on|of|for|about)"
            r"|summarize (our |the )?(work|progress|decisions|discussion) (on|about|regarding)"
            r"|recall (what|where|when|how|our|the)"
            r"|what do (i|we) (know|remember) about)\b",
            re.IGNORECASE,
        ),
        "recall",
    ),
    # remember
    (
        re.compile(
            r"\b(remember (that|my|i|this)|my name is|i prefer|i like|i dislike"
            r"|i always|i never|my (email|phone|address|birthday))\b",
            re.IGNORECASE,
        ),
        "remember",
    ),
    # weather — handled separately below for location extraction
    (
        re.compile(
            r"\b(weather (in|for|at)|what.?s the weather|temperature in|is it (raining|snowing|cold|hot|warm|sunny)"
            r"|forecast for|weather forecast)\b",
            re.IGNORECASE,
        ),
        "weather",
    ),
    # image generation
    (
        re.compile(
            r"\b(generate (an? )?image|create (an? )?image|draw (an? |a picture of )?"
            r"|make (an? )?image|show me (an? )?image|paint (an? )?|illustrate)\b",
            re.IGNORECASE,
        ),
        "image_gen",
    ),
    # web search — broad pattern covering "research online", "look up", news queries, etc.
    (
        re.compile(
            r"\b(search (for|the web|online)|find (information|papers|articles|news) (about|on)"
            r"|look up( online)?|recent (papers|news|articles|research) (on|about)"
            r"|what (are|is) (the latest|recent)|google|bing|search online"
            r"|research online|find online|latest (news|updates|info) (about|on|for)"
            r"|news (about|on|for)|top news|current (news|events|updates)"
            r"|what.?s happening|what.?s new (with|at|in)"
            r"|can you (research|find|look up|search))\b",
            re.IGNORECASE,
        ),
        "web_search",
    ),
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
- "web_search"     — wants information from the web, recent news, research papers, or facts the AI may not know
- "weather"        — wants current weather, temperature, or forecast for a location
- "image_gen"      — wants an image generated or drawn
- "remember"       — explicitly wants to store a personal fact/preference for later recall
- "recall"         — asking about past conversations, decisions, or progress: "where are we on X", "what did we decide about Y", "what's our status on Z"
- "recall_output"  — wants to find or retrieve a specific file, document, report, TTS clip, image, or other output they previously created, uploaded, or generated: "find the report I made about X", "show me the TTS clip from last week", "where is the PDF I uploaded"
- "action"         — wants to execute a specific action: build/create a tax package or expense report, export a book/manuscript, compile/assemble a report, create a study/learning plan, fill a template
- "chat"           — everything else: questions, analysis, writing, research assistance

For "action" intent also include "action_name" (one of: tax_package, book_export, report_assembler, study_plan, template_fill) and "action_inputs" (object with year for tax_package, else {}).

Respond ONLY with valid JSON (no code fences):
{"intent": "<intent>", "query": "<key phrase>", "location": "<city/region or null>", "action_name": "<action name or null>", "action_inputs": {}}

Message: {message}"""


def _match_action_patterns(text: str) -> dict | None:
    """Fast-path action intent detection without LLM.

    Returns a dict with intent/action_name/action_inputs or None.
    """
    import re as _re

    for pattern, intent, action_name in _ACTION_PATTERNS:
        if pattern.search(text):
            # Try to extract year for tax_package
            action_inputs: dict = {}
            if action_name == "tax_package":
                m = _re.search(r"\b(20\d{2})\b", text)
                if m:
                    action_inputs["year"] = int(m.group(1))
                else:
                    # Default to the current calendar year so the action
                    # can always execute without requiring an explicit year.
                    from datetime import datetime as _dt

                    action_inputs["year"] = _dt.now().year
            return {
                "intent": intent,
                "action_name": action_name,
                "action_inputs": action_inputs,
                "query": text[:80],
            }
    return None


def classify_intent(
    user_text: str,
    base_url: str,
    model: str,
    history: list[dict] | None = None,
) -> dict[str, Any]:
    """Classify the intent of *user_text*.

    Returns a dict with keys:
      - intent: str (one of web_search / weather / image_gen / remember / action / chat)
      - query: str (extracted search query or key phrase)
      - location: str | None (for weather)
      - action_name: str | None (for action intent)
      - action_inputs: dict (for action intent)

    Never raises — falls back to {"intent": "chat", "query": user_text, "location": None}.
    """
    # Fast pattern path — no LLM needed
    lower = user_text.strip().lower()

    # Action patterns checked first (they are more specific than generic chat)
    action_match = _match_action_patterns(user_text)
    if action_match:
        logger.debug(
            "Intent fast-path (action): %s for %r", action_match["action_name"], user_text[:60]
        )
        return action_match

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
        if intent not in (
            "web_search",
            "weather",
            "image_gen",
            "remember",
            "recall",
            "recall_output",
            "chat",
            "action",
        ):
            intent = "chat"
        result: dict = {
            "intent": intent,
            "query": parsed.get("query", user_text),
            "location": parsed.get("location"),
        }
        if intent == "action":
            result["action_name"] = parsed.get("action_name", "")
            result["action_inputs"] = parsed.get("action_inputs") or {}
        return result
    except Exception as exc:
        logger.debug("Intent LLM classify failed: %s — falling back to chat", exc)
        return {"intent": "chat", "query": user_text, "location": None}
