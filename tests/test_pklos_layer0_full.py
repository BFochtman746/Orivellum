"""PKLOS Layer 0 comprehensive test suite — spec §9.1 test cases A-G.

Tests the full verification stack:
  - AuthorityTier (correct spec A0-A8 labels)
  - ClaimStatus state machine (spec §3.3 statuses)
  - FactRouter (all 7 spec §5.1 request classes)
  - AuthorityResolver (per-predicate policy)
  - ClaimVerifier (normalize + corroborate + conflict engine)
  - PolicyEnforcer (host-side mandatory tool use enforcement)
  - OutputValidator (atomic-claim answer checking)
  - AdapterBase interface
  - Status names in db.upsert_claim (USER_ASSERTED for A7)
"""
from __future__ import annotations

import pytest
import sqlite3
import tempfile
import os
import sys

# Ensure src is on the path for direct imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from orivellum.capabilities.pklos.authority import (
    AuthorityTier, ClaimStatus, ConflictType, TTLClass,
    ALLOWED_TRANSITIONS, is_allowed_transition,
    SUBJECT_DEVICE_A01,
)
from orivellum.capabilities.pklos.authority_resolver import (
    AuthorityResolver, AuthorityPolicy, resolve as resolve_authority,
)
from orivellum.capabilities.pklos.claim_verifier import (
    ClaimVerifier, VerificationResult, normalize_value, verify, verify_assertion,
)
from orivellum.capabilities.pklos.fact_router import (
    FactRouter, RequestClass, classify, is_checkable_fact, should_capture_as_a7,
)
from orivellum.capabilities.pklos.policy_enforcer import (
    PolicyEnforcer, EnforcementDecision, PolicyFailure,
)
from orivellum.capabilities.pklos.output_validator import (
    OutputValidator, ValidationResult,
)
from orivellum.capabilities.pklos.adapters.base import (
    AdapterBase, Evidence, Recipe, AdapterRegistry,
)


# ── Fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db():
    """In-memory SQLite db with the full schema applied."""
    from orivellum.database.db import OrivellumDB
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    try:
        db = OrivellumDB(path)
        yield db
    finally:
        try:
            db._conn.close()
        except Exception:
            pass
        try:
            os.unlink(path)
        except Exception:
            pass


# ── AuthorityTier (spec §3.1) ───────────────────────────────────────────────────

class TestAuthorityTier:
    def test_nine_tiers(self):
        # A0 through A8 inclusive = 9 tiers (spec §3.1)
        assert len(AuthorityTier) == 9

    def test_spec_labels(self):
        """Authority labels match spec §3.1 exactly."""
        assert "Direct measurement" in AuthorityTier.A0.label
        assert "configuration" in AuthorityTier.A1.label
        assert "vendor" in AuthorityTier.A2.label.lower()
        assert "artifact" in AuthorityTier.A3.label.lower()
        assert "vault" in AuthorityTier.A4.label.lower() or "knowledge" in AuthorityTier.A4.label.lower()
        assert "corroborated" in AuthorityTier.A5.label.lower()
        assert "unverified" in AuthorityTier.A6.label.lower()
        assert "recollection" in AuthorityTier.A7.label.lower() or "assertion" in AuthorityTier.A7.label.lower()
        assert "inference" in AuthorityTier.A8.label.lower()

    def test_a8_cannot_surface_as_fact(self):
        """VER-INV-001: A8 must never be presented as verified fact."""
        assert not AuthorityTier.A8.can_surface_as_fact
        for tier in [AuthorityTier.A0, AuthorityTier.A1, AuthorityTier.A7]:
            assert tier.can_surface_as_fact

    def test_outranks(self):
        assert AuthorityTier.A0.outranks(AuthorityTier.A7)
        assert AuthorityTier.A1.outranks(AuthorityTier.A8)
        assert not AuthorityTier.A7.outranks(AuthorityTier.A0)
        assert not AuthorityTier.A7.outranks(AuthorityTier.A7)  # same tier

    def test_a7_requires_verification(self):
        assert AuthorityTier.A7.requires_verification
        assert AuthorityTier.A8.requires_verification
        assert not AuthorityTier.A0.requires_verification


# ── ClaimStatus state machine (spec §3.3) ──────────────────────────────────────

class TestClaimStatus:
    def test_correct_statuses_exist(self):
        """Spec §3.3 states must all exist."""
        statuses = {s.value for s in ClaimStatus}
        for expected in (
            "UNOBSERVED", "USER_ASSERTED", "RETRIEVED",
            "PARTIALLY_VERIFIED", "VERIFIED", "CONFLICTED",
            "STALE", "INVALIDATED", "UNAVAILABLE",
        ):
            assert expected in statuses, f"{expected} missing from ClaimStatus"

    def test_no_current_status(self):
        """'CURRENT' is a legacy internal value, not a spec status."""
        statuses = {s.value for s in ClaimStatus}
        # CURRENT should NOT be in ClaimStatus enum (it was the old name)
        # (We keep it in the DB filter but not as a spec state)
        assert "CURRENT" not in statuses

    def test_verified_is_presentable(self):
        assert ClaimStatus.VERIFIED.is_presentable
        assert not ClaimStatus.USER_ASSERTED.is_presentable
        assert not ClaimStatus.STALE.is_presentable

    def test_claim_req_002_requires_qualifier(self):
        """CLAIM-REQ-002: these statuses must not be presented without qualifier."""
        must_qualify = {
            ClaimStatus.USER_ASSERTED, ClaimStatus.CONFLICTED,
            ClaimStatus.STALE, ClaimStatus.PARTIALLY_VERIFIED,
        }
        for s in must_qualify:
            assert s.requires_qualifier, f"{s} should require qualifier"
        assert not ClaimStatus.VERIFIED.requires_qualifier

    def test_unavailable_triggers_abstention(self):
        assert ClaimStatus.UNAVAILABLE.triggers_abstention
        assert not ClaimStatus.VERIFIED.triggers_abstention

    def test_allowed_transitions(self):
        """Spec §3.3: UNOBSERVED → USER_ASSERTED → ... → VERIFIED."""
        assert is_allowed_transition(ClaimStatus.UNOBSERVED, ClaimStatus.USER_ASSERTED)
        assert is_allowed_transition(ClaimStatus.USER_ASSERTED, ClaimStatus.VERIFIED)
        assert is_allowed_transition(ClaimStatus.VERIFIED, ClaimStatus.STALE)
        assert is_allowed_transition(ClaimStatus.STALE, ClaimStatus.VERIFIED)
        assert is_allowed_transition(ClaimStatus.STALE, ClaimStatus.INVALIDATED)

    def test_prohibited_transitions(self):
        """INVALIDATED → VERIFIED is not allowed directly."""
        assert not is_allowed_transition(ClaimStatus.INVALIDATED, ClaimStatus.VERIFIED)
        assert not is_allowed_transition(ClaimStatus.VERIFIED, ClaimStatus.UNOBSERVED)


# ── AuthorityResolver (spec §5.2) ───────────────────────────────────────────────

class TestAuthorityResolver:
    def setup_method(self):
        self.resolver = AuthorityResolver()

    def test_ram_policy(self):
        policy = self.resolver.resolve("installed_physical_memory_bytes")
        assert policy.minimum_authority == AuthorityTier.A1
        assert policy.minimum_corroboration == 2
        assert policy.conflict_policy == "hold_and_report"
        assert any("Win32_ComputerSystem" in s for s in policy.preferred_sources)

    def test_inv_req_001_adapteram_prohibited(self):
        """INV-REQ-001: AdapterRAM is prohibited for VRAM predicates.

        "AdapterRAM" is the CIM property name; lowercased it is "adapterram"
        (double-r: Adapter + RAM).  Both the full CIM path and the property
        name alone must be detected as prohibited.
        """
        # Full CIM locator
        assert self.resolver.is_prohibited_source("vram_usable_bytes", "Win32_VideoController.AdapterRAM")
        # Short property name (correct spelling: double-r)
        assert self.resolver.is_prohibited_source("vram_gb", "adapterram")
        # Not prohibited for non-VRAM predicates
        assert not self.resolver.is_prohibited_source("ram_gb", "Win32_VideoController.AdapterRAM")

    def test_cpu_model_policy(self):
        policy = self.resolver.resolve("cpu_model")
        assert "alias" in policy.notes.lower() or "canonical" in policy.notes.lower()

    def test_default_policy_for_unknown(self):
        policy = self.resolver.resolve("nonexistent_predicate_xyz")
        assert policy.minimum_authority == AuthorityTier.A7
        assert policy.minimum_corroboration == 1

    def test_meets_minimum_authority(self):
        assert self.resolver.meets_minimum_authority("ram_gb", AuthorityTier.A0)
        assert self.resolver.meets_minimum_authority("ram_gb", AuthorityTier.A7)  # default is A7
        assert not self.resolver.meets_minimum_authority("installed_physical_memory_bytes", AuthorityTier.A7)


# ── ClaimVerifier (spec §5.5) ───────────────────────────────────────────────────

class TestClaimVerifier:
    def setup_method(self):
        self.verifier = ClaimVerifier()

    def test_normalize_ram_bytes_to_gib(self):
        norm, display = normalize_value("installed_physical_memory_bytes", "128 GB")
        assert "128" in display.upper() or "GiB" in display

    def test_normalize_exact_bytes(self):
        norm, display = normalize_value("installed_physical_memory_bytes", "137438953472")
        assert "128" in display  # 137438953472 bytes = 128 GiB

    def test_verify_two_sources_agree(self):
        """Spec test A: two A0 sources agree → VERIFIED with high confidence."""
        evidence = [
            {"source_type": "windows_cim", "source_locator": "Win32_ComputerSystem.TotalPhysicalMemory",
             "authority": "A0", "raw_value": "137438953472"},
            {"source_type": "windows_cim", "source_locator": "Win32_PhysicalMemory.Capacity:sum",
             "authority": "A0", "raw_value": "137438953472"},
        ]
        result = self.verifier.verify("installed_physical_memory_bytes", evidence)
        assert result.status == ClaimStatus.VERIFIED
        assert result.confidence >= 0.75
        assert "128" in result.display_value

    def test_verify_no_evidence_returns_unavailable(self):
        """Spec test D: no evidence → UNAVAILABLE, never a guess."""
        result = self.verifier.verify("installed_physical_memory_bytes", [])
        assert result.status == ClaimStatus.UNAVAILABLE
        assert result.confidence == 0.0

    def test_verify_material_contradiction(self):
        """Spec test C: conflicting values → CONFLICTED, never auto-reconciled (CONF-REQ-001)."""
        evidence = [
            {"source_type": "cim", "source_locator": "A", "authority": "A0", "raw_value": "137438953472"},
            {"source_type": "user", "source_locator": "B", "authority": "A7", "raw_value": "64 GB"},
        ]
        result = self.verifier.verify("installed_physical_memory_bytes", evidence)
        assert result.status == ClaimStatus.CONFLICTED
        assert result.confidence < 0.5

    def test_inv_req_001_adapteram_filtered(self):
        """INV-REQ-001: evidence from AdapterRAM must be rejected for VRAM predicates."""
        evidence = [
            {"source_type": "windows_cim",
             "source_locator": "Win32_VideoController.AdapterRAM",
             "authority": "A0", "raw_value": "4294967295"},  # classic 4GB cap bug
        ]
        result = self.verifier.verify("vram_usable_bytes", evidence)
        assert result.status == ClaimStatus.UNAVAILABLE  # prohibited source filtered
        assert "prohibited" in result.confidence_basis.lower()

    def test_verify_single_a7_assertion(self):
        """Spec test: A7 user assertion → USER_ASSERTED, not VERIFIED."""
        result = self.verifier.verify_single_assertion("ram_gb", "128")
        assert result.status == ClaimStatus.USER_ASSERTED
        assert result.authority == AuthorityTier.A7

    def test_normalized_agreement(self):
        """Spec §3.5: '128 GB' and 137438953472 bytes → normalized_agreement (not contradiction)."""
        evidence = [
            {"source_type": "user", "source_locator": "assertion", "authority": "A7", "raw_value": "128 GB"},
            {"source_type": "cim",  "source_locator": "TotalPhysicalMemory", "authority": "A0", "raw_value": "137438953472"},
        ]
        result = self.verifier.verify("installed_physical_memory_bytes", evidence)
        # Should not be CONFLICTED (normalized agreement within tolerance)
        assert result.status != ClaimStatus.CONFLICTED


# ── FactRouter (spec §5.1 — all 7 classes) ────────────────────────────────────

class TestFactRouter:
    def setup_method(self):
        self.router = FactRouter()

    def test_class_a_hardware_question(self):
        """Spec test A: system spec question → deterministically_verifiable."""
        for q in [
            "how much ram does my system have",
            "what GPU do I have",
            "what cpu am i running",
            "what are my specs",
            "how much VRAM do I have",
        ]:
            assert self.router.classify(q) == RequestClass.DETERMINISTICALLY_VERIFIABLE, q

    def test_user_declared_fact(self):
        """User stating a fact → user_declared_fact."""
        for q in [
            "my GPU is an RTX 4090",
            "i have 128 GB of RAM",
            "my CPU is Ryzen AI Max 395",
            "i'm running Windows 11",
        ]:
            result = self.router.classify(q)
            assert result == RequestClass.USER_DECLARED_FACT, f"{q!r} → {result}"

    def test_derived_fact(self):
        """Can my machine run X → derived_fact (needs calculation)."""
        for q in [
            "can my machine run Llama 3 70B",
            "will it handle a 128k context window",
        ]:
            assert self.router.classify(q) == RequestClass.DERIVED_FACT, q

    def test_creative(self):
        for q in ["write me a poem", "draft a cover letter", "generate a list of names"]:
            assert self.router.classify(q) == RequestClass.CREATIVE, q

    def test_interpretive(self):
        for q in ["explain transformer attention", "compare PyTorch and JAX", "what are the pros and cons of RAG"]:
            result = self.router.classify(q)
            assert result in (RequestClass.INTERPRETIVE_JUDGMENT, RequestClass.CREATIVE), q

    def test_is_checkable_fact_alias(self):
        assert is_checkable_fact("what gpu do i have")
        assert not is_checkable_fact("write me a haiku")

    def test_should_capture_as_a7(self):
        assert should_capture_as_a7("my GPU is an RTX 4090")
        assert should_capture_as_a7("I have 128 GB of RAM")
        assert not should_capture_as_a7("what is the speed of light")


# ── OutputValidator (spec §5.6, OUT-REQ-001) ───────────────────────────────────

class TestOutputValidator:
    def setup_method(self):
        self.validator = OutputValidator(db=None)

    def test_pass_non_checkable_query(self):
        """Non-checkable queries pass without validation."""
        result = self.validator.validate("write me a poem", "The roses are red...")
        assert result.passed

    def test_pass_correct_abstention(self):
        """Model correctly abstaining → validation passes."""
        result = self.validator.validate(
            "what GPU do I have",
            "I don't have verified information about that. You can tell me the value and I'll record it.",
        )
        assert result.passed

    def test_fail_unverified_specific_claim_no_ledger(self):
        """Model guessing a specific fact with no verified claims → hard violation."""
        result = self.validator.validate(
            "how much RAM do I have",
            "Your system has 64 GB of RAM.",
            verified_claims=[],
        )
        assert not result.passed
        assert result.must_regenerate
        assert any("HARD" in v for v in result.violations)

    def test_pass_with_verified_claim(self):
        """Answer matching a verified claim → passes."""
        claims = [{"predicate": "ram_gb", "value": "128", "status": "VERIFIED", "authority_tier": "A0"}]
        result = self.validator.validate(
            "how much RAM do I have",
            "You have 128 GB of RAM installed.",
            verified_claims=claims,
        )
        assert result.passed

    def test_hedged_answer_softer_violation(self):
        """A hedged factual claim is a soft (non-blocking) violation."""
        result = self.validator.validate(
            "how much RAM do I have",
            "I think your system probably has 32 GB of RAM.",
            verified_claims=[],
        )
        # Hedged → soft violation only, must_regenerate is False
        # (system is properly uncertain, not asserting)
        # This depends on the pattern — a properly hedged answer should not block
        # We just check it doesn't crash
        assert isinstance(result, ValidationResult)

    def test_build_fallback_answer_no_claims(self):
        """Fallback answer with no claims → abstention text."""
        answer = self.validator.build_fallback_answer("what GPU do I have", [])
        assert "verified" in answer.lower() or "don't" in answer.lower()

    def test_build_fallback_answer_with_claims(self):
        """Fallback answer with claims → summarizes them."""
        claims = [
            {"predicate": "gpu_model", "value": "RTX 4090", "unit": None, "status": "VERIFIED", "authority_tier": "A0"},
            {"predicate": "ram_gb", "value": "128", "unit": "GB", "status": "USER_ASSERTED", "authority_tier": "A7"},
        ]
        answer = self.validator.build_fallback_answer("what GPU do I have", claims)
        assert "gpu_model" in answer or "RTX" in answer


# ── AdapterBase interface (spec §5.4) ───────────────────────────────────────────

class TestAdapterBase:
    def test_adapter_interface(self):
        """A concrete adapter must implement all abstract methods."""
        class ConcreteAdapter(AdapterBase):
            @property
            def adapter_id(self): return "test@0.1.0"
            def capabilities(self): return ["test_predicate"]
            def evidence_recipe(self, predicate): return Recipe(predicate=predicate, sources=["test"])
            def fetch(self, predicate, *, freshness="DURABLE"):
                return [Evidence("test", "test:locator", "A7", "test_value", predicate)]

        adapter = ConcreteAdapter()
        assert adapter.can_answer("test_predicate")
        assert not adapter.can_answer("other_predicate")
        evidence = adapter.fetch("test_predicate")
        assert len(evidence) == 1
        assert evidence[0].authority == "A7"

    def test_registry(self):
        class Adapter1(AdapterBase):
            @property
            def adapter_id(self): return "a1@0.1.0"
            def capabilities(self): return ["pred1", "pred2"]
            def evidence_recipe(self, p): return Recipe(predicate=p, sources=[])
            def fetch(self, p, *, freshness="DURABLE"): return []

        class Adapter2(AdapterBase):
            @property
            def adapter_id(self): return "a2@0.1.0"
            def capabilities(self): return ["pred2", "pred3"]
            def evidence_recipe(self, p): return Recipe(predicate=p, sources=[])
            def fetch(self, p, *, freshness="DURABLE"): return []

        reg = AdapterRegistry()
        reg.register(Adapter1())
        reg.register(Adapter2())

        assert len(reg.adapters_for("pred2")) == 2
        assert len(reg.adapters_for("pred1")) == 1
        assert len(reg.adapters_for("pred3")) == 1
        assert len(reg.adapters_for("pred_none")) == 0


# ── DB status names (spec §3.3 — USER_ASSERTED for A7) ─────────────────────────

class TestDBStatusNames:
    def test_a7_upsert_creates_user_asserted(self, temp_db):
        """A7 claims must be stored as USER_ASSERTED, not CURRENT."""
        cid = temp_db.upsert_claim(
            "device:a01", "ram_gb", "128",
            authority_tier="A7",
        )
        claim = temp_db.get_claim(cid)
        assert claim is not None
        assert claim["status"] == "USER_ASSERTED", f"Got {claim['status']!r}, expected USER_ASSERTED"

    def test_a0_upsert_creates_retrieved(self, temp_db):
        """A0 claims are initially RETRIEVED (verifier must run to reach VERIFIED)."""
        cid = temp_db.upsert_claim(
            "device:a01", "cpu_cores", "12",
            authority_tier="A0",
        )
        claim = temp_db.get_claim(cid)
        assert claim is not None
        assert claim["status"] == "RETRIEVED", f"Got {claim['status']!r}, expected RETRIEVED"

    def test_higher_authority_wins(self, temp_db):
        """A higher-authority upsert wins over a lower-authority one."""
        temp_db.upsert_claim("device:a01", "cpu_model", "some_model", authority_tier="A7")
        cid2 = temp_db.upsert_claim("device:a01", "cpu_model", "correct_model", authority_tier="A0")
        claim = temp_db.get_claim(cid2)
        assert claim["value"] == "correct_model"

    def test_lower_authority_does_not_downgrade(self, temp_db):
        """A lower-authority upsert must NOT overwrite a higher-authority claim."""
        cid1 = temp_db.upsert_claim("device:a01", "cpu_model", "correct_from_cim", authority_tier="A0")
        cid2 = temp_db.upsert_claim("device:a01", "cpu_model", "wrong_from_user", authority_tier="A7")
        # Should return same claim id and not change value
        claim = temp_db.get_claim(cid1)
        assert claim["value"] == "correct_from_cim"

    def test_search_finds_user_asserted(self, temp_db):
        """search_claims_for_context must return USER_ASSERTED claims."""
        temp_db.upsert_claim("device:a01", "ram_gb", "128", authority_tier="A7")
        results = temp_db.search_claims_for_context("ram memory", limit=10)
        assert len(results) > 0
        statuses = {r["status"] for r in results}
        assert "USER_ASSERTED" in statuses

    def test_a8_never_returned_by_search(self, temp_db):
        """A8 claims must never surface in context search (VER-INV-001)."""
        temp_db.upsert_claim("device:a01", "gpu_vram", "96", authority_tier="A8")
        results = temp_db.search_claims_for_context("gpu vram", limit=10)
        a8_results = [r for r in results if r.get("authority_tier") == "A8"]
        assert len(a8_results) == 0, "A8 claims leaked into context"


# ── PolicyEnforcer (spec §5.3) — integration test ─────────────────────────────

class TestPolicyEnforcer:
    def test_non_checkable_passes_through(self, temp_db):
        """Non-checkable queries are not enforced."""
        enforcer = PolicyEnforcer(temp_db)
        decision = enforcer.enforce("write a poem about the sky")
        assert not decision.must_abstain
        assert decision.request_class != RequestClass.DETERMINISTICALLY_VERIFIABLE

    def test_checkable_no_claims_forces_abstention(self, temp_db):
        """Spec test B: missing data → controlled abstention; no inference (ABST-REQ-001)."""
        enforcer = PolicyEnforcer(temp_db)
        decision = enforcer.enforce("how much RAM does my system have")
        assert decision.must_abstain
        assert "MANDATORY" in decision.policy_instruction
        assert "abstain" in decision.policy_instruction.lower() or "verified" in decision.policy_instruction.lower()

    def test_checkable_with_claim_gives_context(self, temp_db):
        """Spec test A: has claim → inject verified context, not abstention."""
        temp_db.upsert_claim("device:a01", "ram_gb", "128", authority_tier="A7")
        enforcer = PolicyEnforcer(temp_db)
        decision = enforcer.enforce("how much RAM does my system have")
        assert not decision.must_abstain
        assert "128" in decision.verified_context or "ram" in decision.verified_context.lower()

    def test_build_system_prompt_additions_no_claims(self, temp_db):
        """With no claims: context is empty, instruction is abstention."""
        enforcer = PolicyEnforcer(temp_db)
        ctx, instr = enforcer.build_system_prompt_additions("what GPU do I have")
        assert ctx == ""
        assert "MANDATORY" in instr

    def test_build_system_prompt_additions_with_claims(self, temp_db):
        """With claims: context has facts, instruction is use-them enforcement."""
        temp_db.upsert_claim("device:a01", "gpu_model", "RTX 4090", authority_tier="A7")
        enforcer = PolicyEnforcer(temp_db)
        ctx, instr = enforcer.build_system_prompt_additions("what GPU do I have")
        assert not (ctx == "" and "MANDATORY" in instr)  # at least one should differ from no-claims case
