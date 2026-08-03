"""PKLOS — Personal Knowledge and Learning Operating System.

Layer 0 (Phase 1 — Buildable Now):
  authority          — A0-A8 tiers (exact spec), claim status states, TTL, conflict types
  authority_resolver — per-predicate authority policy (preferred sources, min corroboration)
  claim_ledger       — canonical claim CRUD + state machine
  capture_stamp      — boundary provenance stamping + factual assertion detection
  fact_router        — 7-class request classifier (spec §5.1)
  abstention         — policy enforcer: never guess on checkable facts
  claim_verifier     — normalize + corroborate + conflict engine + confidence scoring
  policy_enforcer    — host-side mandatory tool use enforcement (spec §5.3)
  output_validator   — atomic-claim answer checking (spec §5.6, OUT-REQ-001)

Layer 1 (Phase 2 — Source Adapters):
  adapters.base          — AdapterBase interface + AdapterRegistry
  adapters.recollection  — Adapter 4: user recollection / A7 assertion
  adapters.library       — Adapter 2: local library / A4 vault

VER-INV-001: No claim may be presented as fact at a higher authority than
its evidence supports; and where a verification path exists, the system
must take it before asserting.
"""
from .authority import (
    AuthorityTier, ClaimStatus, ConflictType, TTLClass,
    ALLOWED_TRANSITIONS, is_allowed_transition,
    SUBJECT_DEVICE_A01, SUBJECT_USER_SELF, SUBJECT_USER_SYSTEM,
)
from .authority_resolver import AuthorityResolver, AuthorityPolicy, resolve as resolve_authority
from .claim_ledger import ClaimLedger
from .capture_stamp import CaptureStamp, detect_factual_assertions
from .fact_router import FactRouter, RequestClass, classify, is_checkable_fact, should_capture_as_a7
from .abstention import AbstentionPolicy
from .claim_verifier import ClaimVerifier, VerificationResult, normalize_value, verify, verify_assertion
from .policy_enforcer import PolicyEnforcer, EnforcementDecision, PolicyFailure
from .output_validator import OutputValidator, ValidationResult, AnswerContract

__all__ = [
    # authority
    "AuthorityTier", "ClaimStatus", "ConflictType", "TTLClass",
    "ALLOWED_TRANSITIONS", "is_allowed_transition",
    "SUBJECT_DEVICE_A01", "SUBJECT_USER_SELF", "SUBJECT_USER_SYSTEM",
    # authority_resolver
    "AuthorityResolver", "AuthorityPolicy", "resolve_authority",
    # claim_ledger
    "ClaimLedger",
    # capture_stamp
    "CaptureStamp", "detect_factual_assertions",
    # fact_router
    "FactRouter", "RequestClass", "classify", "is_checkable_fact", "should_capture_as_a7",
    # abstention
    "AbstentionPolicy",
    # claim_verifier
    "ClaimVerifier", "VerificationResult", "normalize_value", "verify", "verify_assertion",
    # policy_enforcer
    "PolicyEnforcer", "EnforcementDecision", "PolicyFailure",
    # output_validator
    "OutputValidator", "ValidationResult", "AnswerContract",
]
