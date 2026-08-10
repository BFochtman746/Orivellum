"""Shared data contracts for the A-01 Mail Steward capability."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class MailStewardError(RuntimeError):
    """Raised for all Mail Steward capability errors."""


# ── Lifecycle states (matches IMPLEMENTATION-SPECIFICATION §4) ─────────────────

LIFECYCLE_STATES = (
    "DISCOVERED",
    "METADATA_SYNCED",
    "CONTENT_FETCHED",
    "SIGNALLED",
    "ASSESSED",
    "ACTION_RECOMMENDED",
    "USER_APPROVED",
    "USER_REJECTED",
    "DEFERRED",
    "ACTION_APPLIED",
    "VERIFIED",
)

# ── Action types ───────────────────────────────────────────────────────────────

ACTION_CREATE_DRAFT = "CREATE_DRAFT"
ACTION_MOVE = "MOVE"
ACTION_UNDO_MOVE = "UNDO_MOVE"
ACTION_SEND = "SEND"
ACTION_DELETE = "DELETE"  # always disabled in v1

# ── Assessment attention levels ────────────────────────────────────────────────

ATTENTION_HIGH = "high"
ATTENTION_MEDIUM = "medium"
ATTENTION_LOW = "low"


@dataclass
class ThreatEvidence:
    feed: str
    indicator: str
    indicator_type: str
    confidence: float = 0.8
    description: str = ""


@dataclass
class Assessment:
    attention_level: str  # high | medium | low
    needs_reply: bool
    rationale: str
    suggested_reply: str | None
    recommended_action: str  # CREATE_DRAFT | MOVE | DEFER | NONE
    confidence: float  # 0.0–1.0
    is_high_risk: bool
    injection_flagged: bool
    model_id: str = ""
    signals: list[str] = field(default_factory=list)


@dataclass
class MailRecord:
    id: str
    graph_message_id: str  # plaintext in memory, encrypted in DB
    graph_change_key: str
    graph_folder_id: str
    conversation_id: str
    subject: str
    sender_name: str
    sender_domain: str
    received_at: str
    has_attachments: bool
    attachment_count: int
    importance: str
    is_read: bool
    lifecycle_state: str
    assessment_id: str | None
    action_request_id: str | None
    created_at: str
    updated_at: str
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActionRequest:
    id: str
    mail_record_id: str
    assessment_id: str | None
    action_type: str
    destination_folder_id: str | None
    graph_draft_id: str | None
    nonce: str
    status: str  # PENDING|APPROVED|APPLIED|REVERSED|REJECTED
    result_message_id: str | None
    original_folder_id: str | None
    actor: str
    created_at: str
    applied_at: str | None


@dataclass
class AuditEvent:
    id: str
    mail_record_id: str | None
    action_request_id: str | None
    at: str
    actor: str
    event_type: str
    policy_version: str
    model_id: str
    signals: list[str]
    before: dict[str, Any]
    after: dict[str, Any]
    result: str  # SUCCESS|REJECTED|FAILED
