"""Authority tiers, claim status states, and TTL classes — PKLOS Layer 0.

Spec §3.1 — Authority hierarchy (claim-specific precedence, not universal truth):
  A0  Direct measurement or system API (live inspection)
  A1  Authoritative local configuration / signed inventory
  A2  Authoritative vendor / official documentation
  A3  User-supplied original artifact (invoice, order, source file)
  A4  Governed internal knowledge base (a PKLOS vault)
  A5  Independently corroborated reputable external sources (>= 2)
  A6  Single unverified external source
  A7  User recollection / conversational assertion
  A8  Model inference — NEVER present as verified fact; always abstain

Spec §3.3 — Claim-state machine:
  UNOBSERVED → USER_ASSERTED → RETRIEVED → PARTIALLY_VERIFIED → VERIFIED
                                        ↘ CONFLICTED
  VERIFIED → STALE → (revalidate) → VERIFIED | INVALIDATED
  any → UNAVAILABLE   (verification path failed)

VER-INV-001: No claim may be presented as fact at a higher authority than
its evidence supports; and where a verification path exists, the system
must take it before asserting.
"""
from __future__ import annotations

from enum import Enum


class AuthorityTier(str, Enum):
    A0 = "A0"  # Direct measurement or system API (live inspection)
    A1 = "A1"  # Authoritative local configuration / signed inventory
    A2 = "A2"  # Authoritative vendor / official documentation
    A3 = "A3"  # User-supplied original artifact (invoice, order, source file)
    A4 = "A4"  # Governed internal knowledge base (PKLOS vault)
    A5 = "A5"  # Independently corroborated reputable external sources (≥2)
    A6 = "A6"  # Single unverified external source
    A7 = "A7"  # User recollection / conversational assertion
    A8 = "A8"  # Model inference — NEVER surface as verified fact

    @property
    def label(self) -> str:
        return {
            "A0": "Direct measurement / system API",
            "A1": "Authoritative local configuration / signed inventory",
            "A2": "Authoritative vendor documentation",
            "A3": "User-supplied original artifact",
            "A4": "Governed internal knowledge base",
            "A5": "Corroborated external sources (≥2)",
            "A6": "Single unverified external source",
            "A7": "User recollection / assertion",
            "A8": "Model inference — unverified",
        }[self.value]

    @property
    def numeric(self) -> int:
        """Lower = higher authority. Used for tier comparison."""
        return int(self.value[1:])

    @property
    def can_surface_as_fact(self) -> bool:
        """A8 is NEVER surfaced as verified fact — always abstain instead."""
        return self != AuthorityTier.A8

    @property
    def requires_verification(self) -> bool:
        """A7 and A8 should be verified against a higher tier when possible."""
        return self in (AuthorityTier.A7, AuthorityTier.A8)

    def outranks(self, other: AuthorityTier) -> bool:
        """True if this tier has higher authority (lower numeric) than other."""
        return self.numeric < other.numeric


class ClaimStatus(str, Enum):
    """Spec §3.3 claim-state machine states (exact spec terminology)."""
    UNOBSERVED        = "UNOBSERVED"         # No evidence gathered yet
    USER_ASSERTED     = "USER_ASSERTED"      # User stated it; not yet verified
    RETRIEVED         = "RETRIEVED"          # Fetched from a source; not yet corroborated
    PARTIALLY_VERIFIED= "PARTIALLY_VERIFIED" # Some corroboration; below minimum threshold
    VERIFIED          = "VERIFIED"           # Meets minimum authority + corroboration
    CONFLICTED        = "CONFLICTED"         # Two or more sources materially contradict
    STALE             = "STALE"              # Beyond TTL — revalidation required
    INVALIDATED       = "INVALIDATED"        # Was verified; subsequent evidence disproved it
    UNAVAILABLE       = "UNAVAILABLE"        # Verification path failed (timeout/error)
    ABSTAINED         = "ABSTAINED"          # System chose not to answer (policy decision)

    # Spec CLAIM-REQ-002: these statuses MUST NOT be presented as confirmed fact
    # without a visible qualifier.
    MUST_QUALIFY: frozenset  # defined below

    @property
    def is_presentable(self) -> bool:
        """True if this claim can be shown to the user with a verified label."""
        return self == ClaimStatus.VERIFIED

    @property
    def requires_qualifier(self) -> bool:
        """CLAIM-REQ-002: must show a qualifier when presenting this status."""
        return self in (
            ClaimStatus.USER_ASSERTED,
            ClaimStatus.CONFLICTED,
            ClaimStatus.STALE,
            ClaimStatus.PARTIALLY_VERIFIED,
        )

    @property
    def triggers_abstention(self) -> bool:
        """ABST-REQ-001: UNAVAILABLE always triggers abstention."""
        return self == ClaimStatus.UNAVAILABLE

    @property
    def qualifier_text(self) -> str:
        """Short label to append when presenting a non-verified claim."""
        return {
            "UNOBSERVED": "(not yet checked)",
            "USER_ASSERTED": "(self-reported, not yet verified)",
            "RETRIEVED": "(retrieved, awaiting corroboration)",
            "PARTIALLY_VERIFIED": "(partially verified)",
            "VERIFIED": "",
            "CONFLICTED": "(conflicting sources — needs resolution)",
            "STALE": "(may be out of date)",
            "INVALIDATED": "(previously verified, now invalidated)",
            "UNAVAILABLE": "(unavailable)",
            "ABSTAINED": "(system abstained)",
        }.get(self.value, "")


# Allowed status transitions (deterministic enforcement)
ALLOWED_TRANSITIONS: dict[ClaimStatus, frozenset[ClaimStatus]] = {
    ClaimStatus.UNOBSERVED:         frozenset({ClaimStatus.USER_ASSERTED, ClaimStatus.RETRIEVED, ClaimStatus.UNAVAILABLE}),
    ClaimStatus.USER_ASSERTED:      frozenset({ClaimStatus.RETRIEVED, ClaimStatus.PARTIALLY_VERIFIED, ClaimStatus.VERIFIED, ClaimStatus.CONFLICTED, ClaimStatus.UNAVAILABLE}),
    ClaimStatus.RETRIEVED:          frozenset({ClaimStatus.PARTIALLY_VERIFIED, ClaimStatus.VERIFIED, ClaimStatus.CONFLICTED, ClaimStatus.UNAVAILABLE}),
    ClaimStatus.PARTIALLY_VERIFIED: frozenset({ClaimStatus.VERIFIED, ClaimStatus.CONFLICTED, ClaimStatus.STALE, ClaimStatus.UNAVAILABLE}),
    ClaimStatus.VERIFIED:           frozenset({ClaimStatus.STALE, ClaimStatus.CONFLICTED, ClaimStatus.INVALIDATED}),
    ClaimStatus.CONFLICTED:         frozenset({ClaimStatus.VERIFIED, ClaimStatus.PARTIALLY_VERIFIED, ClaimStatus.INVALIDATED, ClaimStatus.UNAVAILABLE}),
    ClaimStatus.STALE:              frozenset({ClaimStatus.VERIFIED, ClaimStatus.INVALIDATED, ClaimStatus.UNAVAILABLE}),
    ClaimStatus.INVALIDATED:        frozenset({ClaimStatus.RETRIEVED}),  # can be re-observed
    ClaimStatus.UNAVAILABLE:        frozenset({ClaimStatus.RETRIEVED, ClaimStatus.USER_ASSERTED}),  # retry
    ClaimStatus.ABSTAINED:          frozenset({ClaimStatus.RETRIEVED}),
}


def is_allowed_transition(from_status: ClaimStatus, to_status: ClaimStatus) -> bool:
    allowed = ALLOWED_TRANSITIONS.get(from_status, frozenset())
    return to_status in allowed


class ConflictType(str, Enum):
    """Spec §3.5 conflict taxonomy."""
    EXACT_AGREEMENT        = "exact_agreement"
    NORMALIZED_AGREEMENT   = "normalized_agreement"
    COMPATIBLE_DIFFERENCE  = "compatible_difference"
    TEMPORAL_DIFFERENCE    = "temporal_difference"
    SOURCE_SCOPE_DIFFERENCE= "source_scope_difference"
    MATERIAL_CONTRADICTION = "material_contradiction"   # CONF-REQ-001: report, never auto-reconcile
    UNRESOLVABLE_AMBIGUITY = "unresolvable_ambiguity"


class TTLClass(str, Enum):
    """Spec §3.6 freshness classes — each carries a revalidation rule."""
    CPU_IDENTITY      = "cpu_identity"       # hardware-change event or monthly
    INSTALLED_RAM     = "installed_ram"      # on boot / hardware-change event
    OS_BUILD          = "os_build"           # daily or on update
    GPU_DRIVER        = "gpu_driver"         # daily or device-change event
    FREE_MEMORY       = "free_memory"        # every request
    RUNNING_SERVERS   = "running_servers"    # every request
    SERVICE_HEALTH    = "service_health"     # every request
    INSTALLED_MODELS  = "installed_models"   # event + daily reconcile
    BENCHMARK_RESULT  = "benchmark_result"   # after relevant hw/sw change
    # Generic classes
    PERMANENT         = "PERMANENT"          # constitutional facts, birth year — never expire
    DURABLE           = "DURABLE"            # months/years
    VOLATILE          = "VOLATILE"           # weeks
    EPHEMERAL         = "EPHEMERAL"          # hours

    @property
    def ttl_hours(self) -> int | None:
        return {
            "cpu_identity": 24 * 30,
            "installed_ram": 24 * 30,
            "os_build": 24,
            "gpu_driver": 24,
            "free_memory": 0,        # always refresh
            "running_servers": 0,
            "service_health": 0,
            "installed_models": 24,
            "benchmark_result": None,  # event-driven
            "PERMANENT": None,
            "DURABLE": 24 * 365,
            "VOLATILE": 24 * 14,
            "EPHEMERAL": 4,
        }.get(self.value)


# Canonical subject identifiers
SUBJECT_DEVICE_A01  = "device:a01"    # this machine
SUBJECT_USER_SELF   = "user:self"
SUBJECT_USER_SYSTEM = "device:a01"    # alias for backward compat
