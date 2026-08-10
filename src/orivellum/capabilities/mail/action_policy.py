"""Deterministic action policy for the A-01 Mail Steward.

A model never directly constructs a Graph mutation request.
This module is the only component allowed to do so, and it enforces
IMPLEMENTATION-SPECIFICATION §6 and SECURITY-AND-ACTION-POLICY §Action authority table.

Policy version is embedded in every audit event so the system can
reconstruct which rules were active when an action was applied.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from orivellum.capabilities.mail.models import (
    ACTION_CREATE_DRAFT,
    ACTION_DELETE,
    ACTION_MOVE,
    ACTION_SEND,
    ACTION_UNDO_MOVE,
)

POLICY_VERSION = "1.0.0"
POLICY_HASH = hashlib.sha256(POLICY_VERSION.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    action_type: str


def evaluate(
    action_type: str,
    *,
    explicit_user_approval: bool,
    is_high_risk: bool,
    confidence: float,
    destination_folder_id: str | None = None,
    send_enabled: bool = False,
    delete_enabled: bool = False,
) -> PolicyDecision:
    """Evaluate whether an action is permitted under the current policy.

    Rules (in priority order):
      1. DELETE is always disabled in v1 unless delete_enabled is True.
      2. SEND requires send_enabled AND explicit user approval.
      3. High-risk assessments cannot create drafts without user approval.
      4. Any action requires explicit_user_approval unless it is a sync/read.
      5. Confidence below 0.50 blocks automatic moves but not user-requested moves.
    """
    if action_type == ACTION_DELETE:
        if not delete_enabled:
            return PolicyDecision(False, "Permanent delete is disabled in v1.0", action_type)

    if action_type == ACTION_SEND:
        if not send_enabled:
            return PolicyDecision(
                False, "Send is disabled; enable Mail.Send and set send_enabled", action_type
            )
        if not explicit_user_approval:
            return PolicyDecision(
                False, "Send requires explicit per-item user approval", action_type
            )

    if action_type == ACTION_CREATE_DRAFT:
        if is_high_risk and not explicit_user_approval:
            return PolicyDecision(
                False,
                "High-risk message requires explicit approval before draft creation",
                action_type,
            )

    if action_type in (ACTION_MOVE, ACTION_CREATE_DRAFT, ACTION_SEND):
        if not explicit_user_approval:
            return PolicyDecision(
                False, f"{action_type} requires explicit user approval", action_type
            )

    if action_type == ACTION_UNDO_MOVE:
        if not explicit_user_approval:
            return PolicyDecision(False, "Undo requires explicit user approval", action_type)
        if not destination_folder_id:
            return PolicyDecision(False, "Undo requires the original folder ID", action_type)

    return PolicyDecision(True, "Policy allows this action", action_type)


def is_high_risk(assessment_signals: list[str]) -> bool:
    """Quick deterministic check — any threat-feed hit or injection flag is high risk."""
    risk_keywords = {"threat_feed_hit", "injection_flagged", "spoofed_sender", "attachment_blocked"}
    return bool(risk_keywords.intersection(assessment_signals))


def policy_version_tag() -> str:
    return f"{POLICY_VERSION}+{POLICY_HASH}"


def serialize_for_audit(decision: PolicyDecision) -> dict[str, Any]:
    return {
        "allowed": decision.allowed,
        "reason": decision.reason,
        "action_type": decision.action_type,
        "policy_version": policy_version_tag(),
    }
