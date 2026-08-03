"""Output validator — spec §5.6 + OUT-REQ-001.

The answer contract: the responder receives only verified claims, labelled
unresolved claims, allowed reasoning operations, and a required output schema.

OUT-REQ-001: validation is whole-answer-decomposed to atomic claims, not
paragraph-level. The output validator decomposes the draft answer into atomic
propositions and rejects/regenerates if:
  - any factual sentence lacks a claim reference
  - an inference is presented as observed fact
  - a conflicted claim is unqualified
  - the answer introduces an entity absent from evidence
  - units mismatch
  - the answer contradicts the ledger

This is the hard backstop. The system-prompt ABSTENTION_INSTRUCTION is the
soft enforcement; this catches leaks.

ROUTE-REQ-002: the "does this output sentence contain a checkable claim?"
classifier MUST be measured for recall (the leak point). A missed claim is
a silent guarantee failure.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .authority import ClaimStatus
from .fact_router import is_checkable_fact

logger = logging.getLogger("orivellum.pklos.output_validator")


# ── Answer contract schema ─────────────────────────────────────────────────────

@dataclass
class ClaimRef:
    claim_id: str
    statement: str
    status: str             # "verified" | "partial" | "conflicted" | "asserted"
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class InferenceRef:
    statement: str
    derived_from_claim_ids: list[str]
    assumptions: list[str] = field(default_factory=list)


@dataclass
class AnswerContract:
    """The structured answer the responder must produce for checkable-fact questions.

    Spec §5.6.
    """
    answer: str
    claims_used: list[ClaimRef] = field(default_factory=list)
    inferences: list[InferenceRef] = field(default_factory=list)
    unverified_statements: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass
class ValidationResult:
    passed: bool
    violations: list[str]
    answer: str                 # possibly revised answer
    must_regenerate: bool = False


# ── Atomic claim detector ──────────────────────────────────────────────────────

# Patterns that indicate a specific factual assertion about the user's system
_SPECIFIC_FACT_PATTERNS = [
    # "X has/have N GB/TB/etc."
    re.compile(r"\b(?:has|have|contains?|with)\s+\d+\s*(?:gb|tb|mhz|ghz|cores?|threads?|vram)\b", re.IGNORECASE),
    # "N GB of RAM/VRAM/memory/storage"
    re.compile(r"\b\d+\s*(?:gb|tb|mib|gib|tib)\s+(?:of\s+)?(?:ram|memory|vram|storage|ssd|nvme)\b", re.IGNORECASE),
    # "your CPU/GPU/RAM is X"
    re.compile(r"\byour\s+(?:cpu|gpu|processor|graphics|ram|memory|os|operating system|ssd|nvme|vram|system)\s+(?:is|are|has|have)\b", re.IGNORECASE),
    # "running Windows/Linux/Ubuntu X"
    re.compile(r"\brunning\s+(?:windows|linux|ubuntu|macos)\s+\d", re.IGNORECASE),
    # "X GB installed"
    re.compile(r"\b\d+\s*(?:gb|tb)\s+(?:installed|available|total|of\s+ram)\b", re.IGNORECASE),
]

# Hedged-language patterns — these indicate the model is uncertain
_HEDGE_PATTERNS = [
    re.compile(r"\b(?:i think|i believe|i assume|probably|likely|might be|could be|appears to be|seems to be|typically|usually)\b", re.IGNORECASE),
    re.compile(r"\b(?:based on your earlier|you mentioned|you said|you told me)\b", re.IGNORECASE),
    re.compile(r"\b(?:don\'t have verified|no verified|i don\'t have|cannot verify|unable to verify)\b", re.IGNORECASE),
]

# Abstention patterns — the model correctly said it doesn't know
_ABSTENTION_PATTERNS = [
    re.compile(r"\bdon\'t have verified (?:information|data)\b", re.IGNORECASE),
    re.compile(r"\bcannot verify\b", re.IGNORECASE),
    re.compile(r"\bno verified data\b", re.IGNORECASE),
    re.compile(r"\bi don\'t have (?:that information|verified)\b", re.IGNORECASE),
    re.compile(r"\bnot in (?:my|the) (?:ledger|memory|database|records)\b", re.IGNORECASE),
]


def _has_specific_fact_claim(sentence: str) -> bool:
    """Return True if the sentence makes a specific factual claim about the user's system."""
    return any(p.search(sentence) for p in _SPECIFIC_FACT_PATTERNS)


def _is_hedged(sentence: str) -> bool:
    """Return True if the sentence is properly hedged."""
    return any(p.search(sentence) for p in _HEDGE_PATTERNS)


def _is_abstention(text: str) -> bool:
    """Return True if the answer correctly abstains."""
    return any(p.search(text) for p in _ABSTENTION_PATTERNS)


def _split_to_sentences(text: str) -> list[str]:
    """Split text into sentences for per-sentence validation."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in sentences if s.strip()]


# ── Validator ──────────────────────────────────────────────────────────────────

class OutputValidator:
    """Spec §5.6 — validates the model's answer against the claim ledger.

    Phase 1 implementation: soft validation (pattern-based).
    Phase 2 will add LLM-based atomic decomposition with measured recall (R4).

    Usage:
        validator = OutputValidator(db)
        result = validator.validate(
            query=query,
            answer=draft_answer,
            verified_claims=claims_from_ledger,
        )
        if result.must_regenerate:
            # get a new answer
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    def validate(
        self,
        query: str,
        answer: str,
        verified_claims: list[dict] | None = None,
    ) -> ValidationResult:
        """Validate a draft answer against the claim ledger.

        Returns ValidationResult with violations list.
        must_regenerate = True if a hard violation is found.
        """
        violations: list[str] = []
        verified_claims = verified_claims or []

        # Non-checkable queries pass without validation
        if not is_checkable_fact(query):
            return ValidationResult(passed=True, violations=[], answer=answer)

        # If the model correctly abstained, pass
        if _is_abstention(answer):
            return ValidationResult(passed=True, violations=[], answer=answer)

        # Split into sentences for atomic validation
        sentences = _split_to_sentences(answer)

        # Build a set of known-good values from verified claims
        known_values: set[str] = set()
        for c in verified_claims:
            val = str(c.get("value") or "").strip().lower()
            if val:
                known_values.add(val)

        hard_violations: list[str] = []

        for sentence in sentences:
            if not _has_specific_fact_claim(sentence):
                continue

            # Sentence makes a specific factual claim about the user's system.
            # Is it hedged?
            if _is_hedged(sentence):
                if not verified_claims:
                    # No evidence at all: hedging doesn't save a specific value guess.
                    # "probably 16 GB" is still a fabricated number (HARD violation).
                    hard_violations.append(
                        f"HARD: hedged specific-value claim with no ledger evidence "
                        f"(hedging does not permit guessing): {sentence[:80]!r}"
                    )
                else:
                    # Has evidence; model hedged appropriately about partial coverage.
                    violations.append(
                        f"SOFT: factual sentence is hedged (acceptable but note): {sentence[:80]!r}"
                    )
                continue

            # Is there a verified claim that supports this sentence?
            sentence_lower = sentence.lower()
            supported = any(val in sentence_lower for val in known_values if val)

            if not supported and verified_claims:
                # Has verified claims but sentence doesn't reference them
                hard_violations.append(
                    f"HARD: factual claim not supported by any verified ledger entry: {sentence[:80]!r}"
                )
            elif not supported and not verified_claims:
                # No verified claims at all — model is guessing
                hard_violations.append(
                    f"HARD: factual claim with no ledger evidence: {sentence[:80]!r}"
                )

        violations.extend(hard_violations)
        passed = len(hard_violations) == 0
        must_regenerate = len(hard_violations) > 0

        if must_regenerate:
            logger.warning(
                "Output validator: %d hard violations in answer for query %r",
                len(hard_violations), query[:60]
            )

        return ValidationResult(
            passed=passed,
            violations=violations,
            answer=answer,
            must_regenerate=must_regenerate,
        )

    def build_fallback_answer(
        self,
        query: str,
        verified_claims: list[dict],
    ) -> str:
        """Build a safe fallback answer when validation fails.

        If we have verified claims, summarize them cleanly.
        If not, return the canonical abstention text.
        """
        from .abstention import ABSTENTION_RESPONSE

        if not verified_claims:
            return ABSTENTION_RESPONSE

        # Build a direct answer from verified claims
        lines = ["Based on your verified system data:"]
        for c in verified_claims[:8]:
            pred  = c.get("predicate", "")
            value = c.get("value", "")
            unit  = c.get("unit") or ""
            tier  = c.get("authority_tier", "A7")
            status = c.get("status", "")
            display = f"{value} {unit}".strip()
            qual = " (self-reported)" if status == "USER_ASSERTED" else ""
            lines.append(f"  • {pred}: {display}{qual}")

        lines.append(
            "\nIf you need values not listed above, I don't have verified "
            "information for those — you can tell me and I'll record it."
        )
        return "\n".join(lines)
