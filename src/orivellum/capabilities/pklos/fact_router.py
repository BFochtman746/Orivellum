"""Fact router — spec §5.1.

Classifies every request BEFORE the answering model sees it.
ROUTE-REQ-001: the router runs OUTSIDE the answering model.

Seven request classes (exact spec §5.1 terminology):
  deterministically_verifiable — must inspect via adapter; tool use mandatory
  retrieved_private_fact       — must retrieve from library; cite locators
  current_external_fact        — must search authoritative web sources
  user_declared_fact           — record as A7, verify when possible
  derived_fact                 — retrieve inputs, then calculate; expose assumptions
  interpretive_judgment        — evidence-backed reasoning; model may reason
  creative                     — generation/writing; no verification unless it introduces claims

Classification order: deterministic patterns first (sub-ms); LLM fallback available
but NOT used on the hot path — the fast path covers the common cases reliably.

ROUTE-REQ-002: the "does this output sentence contain a checkable claim?" classifier
is the system's leak point and MUST be measured for recall (§9, R4).
"""
from __future__ import annotations

import re
from enum import Enum


class RequestClass(str, Enum):
    """Spec §5.1 request classification vocabulary."""
    DETERMINISTICALLY_VERIFIABLE = "deterministically_verifiable"  # must use adapter
    RETRIEVED_PRIVATE_FACT       = "retrieved_private_fact"         # must search library
    CURRENT_EXTERNAL_FACT        = "current_external_fact"          # must search web
    USER_DECLARED_FACT           = "user_declared_fact"             # A7 capture + verify
    DERIVED_FACT                 = "derived_fact"                   # calc from verified inputs
    INTERPRETIVE_JUDGMENT        = "interpretive_judgment"          # evidence-backed reasoning
    CREATIVE                     = "creative"                       # generation/writing

    # Backward-compatible aliases used in conversations.py
    @classmethod
    def CHECKABLE_FACT(cls) -> RequestClass:
        return cls.DETERMINISTICALLY_VERIFIABLE

    @property
    def requires_adapter(self) -> bool:
        return self == RequestClass.DETERMINISTICALLY_VERIFIABLE

    @property
    def requires_library(self) -> bool:
        return self == RequestClass.RETRIEVED_PRIVATE_FACT

    @property
    def verification_required(self) -> bool:
        return self in (
            RequestClass.DETERMINISTICALLY_VERIFIABLE,
            RequestClass.RETRIEVED_PRIVATE_FACT,
            RequestClass.DERIVED_FACT,
        )

    @property
    def capture_as_a7(self) -> bool:
        """True if the message should trigger A7 claim capture."""
        return self == RequestClass.USER_DECLARED_FACT

    def allowed_to_answer_without_tools(self) -> bool:
        return self in (
            RequestClass.INTERPRETIVE_JUDGMENT,
            RequestClass.CREATIVE,
        )


# ── Deterministically verifiable — questions about the user's specific system ──

_SYSTEM_QUESTION_RE = re.compile(
    r"\b(?:"
    r"how much (?:ram|memory|vram|storage|disk)\b"
    r"|what (?:gpu|cpu|processor|graphics card|video card|ram|memory|ssd|hard drive|"
    r"motherboard|os|operating system|windows|linux|ubuntu|display|monitor|resolution)"
    r"\s+(?:do i|i|my system|my (?:pc|computer|machine|build|rig|laptop|desktop)) "
    r"|what (?:is|are) my (?:specs?|hardware|system|build|rig|setup|config(?:uration)?|"
    r"cpu|gpu|ram|ssd|memory|processor|graphics|os|operating system|display|monitor)\b"
    r"|(?:what|which) (?:cpu|gpu|processor|graphics card|video card|ram|ssd|nvme|"
    r"motherboard|os|operating system) (?:do i|i|am i) (?:have|use|runn?(?:ing)?)\b"
    r"|(?:what|which) (?:cpu|gpu|processor|graphics card|video card|ram|ssd|nvme|"
    r"motherboard|os|operating system) (?:is|are) (?:in|on) my\b"
    r"|my (?:system|pc|computer|machine|rig|build|laptop|desktop) (?:spec|hardware)\b"
    r"|(?:tell me|list|show me) my (?:specs?|hardware|system specs?|build)\b"
    r"|(?:do i have|have i got) (?:enough|a|an|[0-9]+)\s*(?:gb|tb|ram|vram|storage|memory)\b"
    r"|what version (?:of|is) (?:windows|linux|ubuntu|python|node|java)\s+"
    r"(?:do i|am i|i) (?:have|run(?:ning)?|use)\b"
    r"|how many (?:cores?|threads?|gb|tb)\s+(?:do i|does my|does my \w+|i) (?:have|have got)\b"
    r"|(?:what|which) (?:drive|disk|ssd|nvme|storage device) (?:do i|i) (?:have|use)\b"
    r"|what (?:is|are) (?:my|the) (?:system|machine|rig) (?:specs?|config|setup)\b"
    r")",
    re.IGNORECASE,
)

# ── User declaring a fact about themselves ─────────────────────────────────────

_DECLARE_FACT_RE = re.compile(
    r"\b(?:"
    r"i have (?:a |an |[0-9])"
    r"|my (?:machine|system|pc|computer|rig|laptop|desktop|gpu|cpu|ram|ssd|os|build) (?:is|has|are|have|runs?|uses?)\b"
    r"|i(?:'m| am) running\b"
    r"|my specs? (?:are|is|:)"
    r"|i(?:'ve| have) (?:got|installed|configured|upgraded)\b"
    r"|i use [a-z0-9]"
    r"|my setup (?:is|has|:)"
    r")",
    re.IGNORECASE,
)

# ── Retrieved private fact — library lookup ────────────────────────────────────

_LIBRARY_RE = re.compile(
    r"\b(?:"
    r"(?:find|search|look up|retrieve) (?:in|from|inside) (?:my )?(?:library|documents?|notes?|books?|files?)\b"
    r"|what (?:does|do) (?:my|the) (?:document|book|note|file|library) (?:say|contain|mention)\b"
    r"|according to (?:my|the) (?:document|book|note|file)\b"
    r"|(?:in|from) (?:the|my) (?:chapter|section|passage|excerpt)\b"
    r")",
    re.IGNORECASE,
)

# ── Current external fact — web/online lookup ──────────────────────────────────

_EXTERNAL_RE = re.compile(
    r"\b(?:"
    r"latest (?:news|updates?|version|release|price)\b"
    r"|current (?:price|version|release|news)\b"
    r"|today\'?s? (?:news|weather|price)\b"
    r"|search (?:online|the web|for)\b"
    r"|(?:look up|find) online\b"
    r"|what is the (?:current|latest) (?:version|release|price)\b"
    r")",
    re.IGNORECASE,
)

# ── Derived fact — calculation from verified inputs ────────────────────────────

_DERIVED_RE = re.compile(
    r"\b(?:"
    r"can (?:i|my (?:machine|pc|system|computer|rig)|a-?01) (?:run|handle|support|fit)\b"
    r"|(?:will|would) (?:it|my (?:machine|pc|system|computer|rig)) (?:run|handle|fit)\b"
    r"|how much (?:vram|memory|storage) (?:do i need|would i need|is required)\b"
    r"|(?:calculate|compute|estimate|work out) (?:how|if|whether)\b"
    r"|what(?:\'s| is) the (?:maximum|minimum|required) (?:vram|ram|memory|gpu)\b"
    r"|(?:do i have|is there) enough (?:vram|ram|memory|gpu|compute) to\b"
    r")",
    re.IGNORECASE,
)

# ── Creative / generative ──────────────────────────────────────────────────────

_CREATIVE_RE = re.compile(
    r"\b(?:write|draft|generate|create|make|compose|design|draw|imagine|"
    r"describe|outline|summarize|translate|reword|rephrase|edit|improve|"
    r"brainstorm|suggest|give me (?:ideas?|examples?|options?)|"
    r"what (?:should i|could i|would you|do you) (?:write|say|call|name))\b",
    re.IGNORECASE,
)

# ── Interpretive judgment — reasoning/analysis ─────────────────────────────────

_INTERPRETIVE_RE = re.compile(
    r"\b(?:explain|analyze|analyse|compare|contrast|evaluate|assess|review|"
    r"why (?:is|does|did|are)\b|what (?:are|is) the (?:pros?|cons?|benefits?|tradeoffs?)\b|"
    r"should i (?:use|choose|pick|go with)\b|what do you (?:think|recommend|suggest)\b|"
    r"what (?:is|are) (?:the )?(?:best|better|worse|advantages?|disadvantages?)\b)\b",
    re.IGNORECASE,
)

# ── Procedural / how-to ────────────────────────────────────────────────────────

_PROCEDURAL_RE = re.compile(
    r"\b(?:how (?:do i|to|can i|should i)|steps? (?:to|for)|"
    r"guide (?:to|for)|tutorial|walkthrough|setup|install|configure|"
    r"troubleshoot|debug|fix|solve|what is the (?:best|right|proper) way to)\b",
    re.IGNORECASE,
)


class FactRouter:
    """Classifies incoming messages into spec §5.1 request classes.

    Classification order (first match wins):
      1. deterministically_verifiable  — system question patterns
      2. user_declared_fact           — user asserting a fact
      3. retrieved_private_fact       — library lookup patterns
      4. current_external_fact        — web search patterns
      5. derived_fact                 — calculation patterns
      6. creative                     — generation patterns
      7. interpretive_judgment        — reasoning/analysis
      8. creative                     — default (safest; no false abstentions)
    """

    def classify(self, text: str) -> RequestClass:
        if _SYSTEM_QUESTION_RE.search(text):
            return RequestClass.DETERMINISTICALLY_VERIFIABLE
        # DERIVED_FACT before USER_DECLARED_FACT: "can my machine run X" is a
        # capability calculation, not a declaration (spec §6.5 worked example).
        if _DERIVED_RE.search(text):
            return RequestClass.DERIVED_FACT
        if _DECLARE_FACT_RE.search(text):
            return RequestClass.USER_DECLARED_FACT
        if _LIBRARY_RE.search(text):
            return RequestClass.RETRIEVED_PRIVATE_FACT
        if _EXTERNAL_RE.search(text):
            return RequestClass.CURRENT_EXTERNAL_FACT
        if _CREATIVE_RE.search(text):
            return RequestClass.CREATIVE
        if _INTERPRETIVE_RE.search(text):
            return RequestClass.INTERPRETIVE_JUDGMENT
        return RequestClass.CREATIVE  # safe default

    def is_checkable(self, text: str) -> bool:
        return self.classify(text) == RequestClass.DETERMINISTICALLY_VERIFIABLE

    def should_capture_as_a7(self, text: str) -> bool:
        return self.classify(text) == RequestClass.USER_DECLARED_FACT


_router = FactRouter()


def classify(text: str) -> RequestClass:
    return _router.classify(text)


def is_checkable_fact(text: str) -> bool:
    """Backward-compatible alias used by conversations.py and output_validator.py."""
    return _router.is_checkable(text)


def should_capture_as_a7(text: str) -> bool:
    return _router.should_capture_as_a7(text)
