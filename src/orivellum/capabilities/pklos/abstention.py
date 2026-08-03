"""Abstention policy — ABST-REQ-001: never guess on checkable facts.

When a question is classified CHECKABLE_FACT and the claim ledger has no
CURRENT claim for the relevant predicates, the system must abstain rather
than guess.  This module:

  1. Decides whether to abstain (claim_count == 0 after ledger check)
  2. Generates the abstention instruction injected into the system prompt
  3. Provides the abstention response text when the policy fires hard

The policy fires at two levels:
  SOFT (preferred) — the system prompt instructs the model to abstain.
                     The model may still slip through; OUT-REQ-001 (output
                     validator, Phase 2) is the hard backstop.
  HARD — reserved for Phase 2 output validator.  Not implemented here.
"""
from __future__ import annotations


# Instruction injected into the system prompt when CHECKABLE_FACT is detected.
# This is the soft enforcement layer.
ABSTENTION_INSTRUCTION = (
    "\nVERIFICATION RULE (MUST FOLLOW):\n"
    "If the user is asking about their specific system, hardware, installed "
    "software, measurements, or any other personal/specific fact that you "
    "cannot answer from general knowledge alone:\n"
    "  1. Check the VERIFIED FACTS block above first.\n"
    "  2. If the answer is there — use it exactly as stated. Do NOT modify, "
    "round, or embellish the value.\n"
    "  3. If the answer is NOT there — say clearly: "
    "'I don\\'t have verified information about that in your claim ledger. "
    "You can tell me and I\\'ll remember it, or connect an adapter to read "
    "it directly from your system.'\n"
    "  4. NEVER invent, estimate, or guess a specific value (RAM amount, CPU "
    "model, GPU model, OS version, etc.) when it is not in the VERIFIED FACTS "
    "block. A wrong specific answer is worse than an honest 'I don\\'t know'.\n"
)

# Instruction injected when there ARE verified facts — reinforces using them.
VERIFIED_FACTS_INSTRUCTION = (
    "\nVERIFICATION RULE: The VERIFIED FACTS block above contains the user's "
    "actual system data from their claim ledger. Use those exact values when "
    "answering questions about their system. Do NOT override them with "
    "different values from your training data.\n"
)

# Soft abstention response text (used when the model should say it doesn't know).
# Not injected directly — used by the output validator in Phase 2.
ABSTENTION_RESPONSE = (
    "I don't have verified information about that in your claim ledger. "
    "You can tell me (e.g. 'I have 128 GB RAM') and I'll remember it for "
    "future questions, or you can connect the Windows inventory adapter to "
    "have it read directly from your system."
)


class AbstentionPolicy:
    """Decides what to inject into the system prompt for a given request class + claims."""

    def get_instruction(
        self,
        is_checkable: bool,
        has_verified_claims: bool,
    ) -> str:
        """Return the instruction string to append to the system prompt.

        Args:
            is_checkable:       True if the fact router classified the message
                                as CHECKABLE_FACT.
            has_verified_claims: True if the claim ledger returned ≥1 CURRENT
                                 claim for this query.
        Returns:
            The instruction string to inject, or empty string if not needed.
        """
        if not is_checkable:
            return ""
        if has_verified_claims:
            return VERIFIED_FACTS_INSTRUCTION
        return ABSTENTION_INSTRUCTION

    def should_abstain(self, is_checkable: bool, has_verified_claims: bool) -> bool:
        """True when the system should abstain rather than guess."""
        return is_checkable and not has_verified_claims
