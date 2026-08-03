"""Policy enforcer — spec §5.3.

The host decides whether an answer is permitted.
The model never receives the option to guess a checkable fact.

ENF-REQ-001: the host application decides whether an answer is permitted.
ENF-REQ-002: adapters expose narrow typed operations, never 'run_any_command'.
ABST-REQ-001: controlled abstention is always preferred to an unverified assertion.

Usage:
    enforcer = PolicyEnforcer(db)
    decision = enforcer.enforce(user_query)
    if decision.must_abstain:
        return abstention_response(decision.abstention_reason)
    # build system prompt using decision.verified_context
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .authority import AuthorityTier, ClaimStatus
from .authority_resolver import AuthorityResolver
from .claim_verifier import ClaimVerifier, VerificationResult
from .fact_router import FactRouter, RequestClass

logger = logging.getLogger("orivellum.pklos.policy_enforcer")

_resolver = AuthorityResolver()
_verifier = ClaimVerifier()
_router   = FactRouter()


class PolicyFailure(Exception):
    """Raised when enforcement cannot proceed (e.g. no verification path)."""


@dataclass
class EnforcementDecision:
    """The outcome of running enforcement for a single user request."""
    request_class: RequestClass
    must_abstain: bool
    abstention_reason: str
    # Claims retrieved and verified for this request
    verified_claims: list[dict] = field(default_factory=list)
    unverified_claims: list[dict] = field(default_factory=list)
    # Pre-formatted context block to inject into the system prompt
    verified_context: str = ""
    # Instruction to append to the system prompt
    policy_instruction: str = ""


# ── System-prompt instruction templates ────────────────────────────────────────

_ABSTAIN_INSTRUCTION = (
    "\n[POLICY ENFORCEMENT — MANDATORY]\n"
    "This request asks about a checkable fact. The claim ledger has NO verified "
    "data for the specific values requested.\n"
    "You MUST respond with:\n"
    "  'I don\\'t have verified information about that. You can tell me the value "
    "and I will record it as a self-reported claim, or connect an adapter to "
    "read it directly from your system.'\n"
    "Do NOT guess, estimate, infer from product family, or use training knowledge "
    "as a substitute for a verified measurement. A wrong specific answer is worse "
    "than an honest abstention.\n"
)

_USE_VERIFIED_INSTRUCTION = (
    "\n[POLICY ENFORCEMENT — MANDATORY]\n"
    "The VERIFIED FACTS block above contains values from the claim ledger. "
    "Use those exact values. Do NOT override or contradict them with different "
    "values from your training data. If a ledger value differs from what your "
    "training suggests, the ledger value is correct for this user's system.\n"
)

_USER_ASSERTED_INSTRUCTION = (
    "\n[POLICY ENFORCEMENT — NOTE]\n"
    "The VERIFIED FACTS block contains self-reported values (USER_ASSERTED, A7 tier). "
    "Use them as the user's stated configuration, but note they have not been "
    "independently verified. Qualify your answer if precision matters: "
    "'Based on what you\\'ve told me, ...'.\n"
)


class PolicyEnforcer:
    """Spec §5.3 — the host-side enforcement gate.

    For every user request:
      1. Classify with the fact router
      2. If checkable: consult claim ledger
      3. If verified claims exist: inject and enforce their use
      4. If no claims: enforce abstention
      5. For non-checkable requests: no enforcement (pass through)
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    def enforce(self, query: str) -> EnforcementDecision:
        """Run enforcement for a user query.  Returns an EnforcementDecision.

        Always returns a valid decision — never raises (errors → abstention).
        """
        try:
            return self._enforce_inner(query)
        except Exception as exc:
            logger.warning("Policy enforcement error (safe abstention): %s", exc)
            return EnforcementDecision(
                request_class=RequestClass.CHECKABLE_FACT,
                must_abstain=True,
                abstention_reason=f"enforcement error: {exc}",
                policy_instruction=_ABSTAIN_INSTRUCTION,
            )

    def _enforce_inner(self, query: str) -> EnforcementDecision:
        req_class = _router.classify(query)

        if req_class != RequestClass.DETERMINISTICALLY_VERIFIABLE:
            # Non-checkable: no enforcement needed
            return EnforcementDecision(
                request_class=req_class,
                must_abstain=False,
                abstention_reason="",
            )

        # Checkable fact: consult claim ledger
        from .claim_ledger import ClaimLedger
        ledger = ClaimLedger(self._db)
        claims = ledger.search_for_context(query, limit=15)

        # Split verified vs asserted
        verified = [c for c in claims if c.get("status") == ClaimStatus.VERIFIED.value]
        asserted = [c for c in claims if c.get("status") == ClaimStatus.USER_ASSERTED.value]
        usable   = verified + asserted

        if not usable:
            return EnforcementDecision(
                request_class=req_class,
                must_abstain=True,
                abstention_reason="no verified or asserted claims found for this query",
                policy_instruction=_ABSTAIN_INSTRUCTION,
            )

        # Build the verified context block
        context_lines = [
            "VERIFIED FACTS (from claim ledger — enforce these values):"
        ]
        for c in usable:
            status   = c.get("status", "")
            pred     = c.get("predicate", "")
            value    = c.get("value", "")
            unit     = c.get("unit") or ""
            tier     = c.get("authority_tier", "A7")
            display  = f"{value} {unit}".strip()
            qual     = ""
            if status == ClaimStatus.USER_ASSERTED.value:
                qual = " [self-reported, A7, not independently verified]"
            elif status == ClaimStatus.VERIFIED.value:
                qual = f" [verified, {tier}]"
            elif status == ClaimStatus.STALE.value:
                qual = " [may be stale — revalidation recommended]"
            context_lines.append(f"  • {pred} = {display}{qual}")
        context_block = "\n".join(context_lines)

        instruction = _USE_VERIFIED_INSTRUCTION if verified else _USER_ASSERTED_INSTRUCTION

        return EnforcementDecision(
            request_class=req_class,
            must_abstain=False,
            abstention_reason="",
            verified_claims=verified,
            unverified_claims=asserted,
            verified_context=context_block,
            policy_instruction=instruction,
        )

    def build_system_prompt_additions(self, query: str) -> tuple[str, str]:
        """Return (context_block, instruction) to inject into the system prompt.

        Both may be empty strings if the request is not checkable.
        This is the interface used by conversations.py's _build_system_prompt().
        """
        decision = self.enforce(query)
        if decision.must_abstain:
            return "", decision.policy_instruction
        return decision.verified_context, decision.policy_instruction
