"""Security floor tests for the deterministic mail action policy.

action_policy is the ONLY component allowed to authorise a Graph mutation —
a model never constructs one directly.  These tests pin default-deny for
every mutating action and the specific gate order the docstring declares.
"""

from __future__ import annotations

from orivellum.capabilities.mail import action_policy as policy
from orivellum.capabilities.mail.models import (
    ACTION_CREATE_DRAFT,
    ACTION_DELETE,
    ACTION_MOVE,
    ACTION_SEND,
    ACTION_UNDO_MOVE,
)

_ALL_MUTATIONS = [ACTION_DELETE, ACTION_SEND, ACTION_CREATE_DRAFT, ACTION_MOVE, ACTION_UNDO_MOVE]


def _eval(action, **kw):
    defaults = {"explicit_user_approval": False, "is_high_risk": False, "confidence": 1.0}
    defaults.update(kw)
    return policy.evaluate(action, **defaults)


# ── Default deny ──────────────────────────────────────────────────────────────


def test_every_mutation_is_denied_without_approval():
    """No mutating action may pass with all flags at their defaults."""
    for action in _ALL_MUTATIONS:
        decision = _eval(action)
        assert not decision.allowed, f"{action} allowed with no approval and no enablement"
        assert decision.reason


def test_delete_disabled_in_v1_even_with_approval():
    decision = _eval(ACTION_DELETE, explicit_user_approval=True)
    assert not decision.allowed
    assert "disabled" in decision.reason.lower()


def test_delete_enabled_still_requires_explicit_approval():
    """Regression: delete_enabled alone must never authorise a delete."""
    decision = _eval(ACTION_DELETE, delete_enabled=True)
    assert not decision.allowed
    decision = _eval(ACTION_DELETE, delete_enabled=True, explicit_user_approval=True)
    assert decision.allowed


# ── Send gate ─────────────────────────────────────────────────────────────────


def test_send_requires_enablement_and_approval_independently():
    assert not _eval(ACTION_SEND, explicit_user_approval=True).allowed  # not enabled
    assert not _eval(ACTION_SEND, send_enabled=True).allowed  # no approval
    assert _eval(ACTION_SEND, send_enabled=True, explicit_user_approval=True).allowed


# ── Draft gate ────────────────────────────────────────────────────────────────


def test_high_risk_draft_blocked_without_approval():
    assert not _eval(ACTION_CREATE_DRAFT, is_high_risk=True).allowed
    assert _eval(ACTION_CREATE_DRAFT, is_high_risk=True, explicit_user_approval=True).allowed


def test_normal_draft_still_requires_approval():
    assert not _eval(ACTION_CREATE_DRAFT).allowed
    assert _eval(ACTION_CREATE_DRAFT, explicit_user_approval=True).allowed


# ── Move / undo ───────────────────────────────────────────────────────────────


def test_move_requires_approval():
    assert not _eval(ACTION_MOVE).allowed
    assert _eval(ACTION_MOVE, explicit_user_approval=True).allowed


def test_undo_requires_approval_and_original_folder():
    assert not _eval(ACTION_UNDO_MOVE, explicit_user_approval=True).allowed  # no folder
    assert not _eval(ACTION_UNDO_MOVE, destination_folder_id="f1").allowed  # no approval
    assert _eval(ACTION_UNDO_MOVE, explicit_user_approval=True, destination_folder_id="f1").allowed


# ── Risk classifier & audit serialization ─────────────────────────────────────


def test_is_high_risk_signal_detection():
    assert policy.is_high_risk(["threat_feed_hit"])
    assert policy.is_high_risk(["benign", "injection_flagged"])
    assert policy.is_high_risk(["spoofed_sender"])
    assert policy.is_high_risk(["attachment_blocked"])
    assert not policy.is_high_risk([])
    assert not policy.is_high_risk(["low_confidence", "newsletter"])


def test_audit_serialization_carries_policy_version():
    decision = _eval(ACTION_MOVE)
    payload = policy.serialize_for_audit(decision)
    assert payload["allowed"] is False
    assert payload["action_type"] == ACTION_MOVE
    assert payload["policy_version"] == policy.policy_version_tag()
    assert policy.POLICY_VERSION in payload["policy_version"]
