"""End-to-end inventory ingestion tests — ledger status and enforcer integration.

Verifies the full pipeline from ingest_inventory() payload →
claim ledger → PolicyEnforcer, without manual status promotion:

1. After valid ingestion the verifier-derived status (VERIFIED /
   PARTIALLY_VERIFIED / RETRIEVED) is actually persisted in the ledger —
   not left as the raw RETRIEVED default from upsert_claim().
2. PolicyEnforcer does NOT abstain on RAM/VRAM questions when the ledger
   holds live claims from inventory (no manual promotion needed).
3. A pre-existing higher-authority claim's status is NOT overwritten by a
   lower-priority ingest (authority guard regression test).
4. AdapterRAM payloads (INV-REQ-001) never produce any VRAM claim.
5. FastAPI route model accepts the full collector payload shape.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from orivellum.capabilities.pklos.adapters.windows_inventory import WindowsInventoryAdapter
from orivellum.capabilities.pklos.authority import SUBJECT_DEVICE_A01
from orivellum.capabilities.pklos.policy_enforcer import PolicyEnforcer

# ── Fixture + payload ──────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path):
    from orivellum.database.db import OrivellumDB

    d = OrivellumDB(str(tmp_path / "test.db"))
    yield d
    try:
        d._conn.close()
    except Exception:
        pass


_FULL_PAYLOAD = {
    "collector_version": "0.1.0",
    "collected_at": "2026-08-05T10:00:00Z",
    "subject": SUBJECT_DEVICE_A01,
    "cpu": {
        "Name": "AMD Ryzen AI MAX+ 395",
        "ProcessorId": "178BFBFF00B40F41",
        "NumberOfCores": 16,
        "NumberOfLogicalProcessors": 32,
        "MaxClockSpeed": 5100,
    },
    "memory": {
        # Two independent CIM sources — both agree → ClaimVerifier produces VERIFIED
        "TotalPhysicalMemory": 137_438_953_472,  # 128 GiB (Win32_ComputerSystem)
        "PhysicalMemoryCapacitySum": 137_438_953_472,  # 128 GiB (Win32_PhysicalMemory sum)
    },
    "gpu": {
        "Name": "AMD Radeon 890M Graphics",
        # AdapterRAM deliberately absent per INV-REQ-001
    },
    "vram": {
        # Single A0 Lemonade source; policy minimum_corroboration=1 → VERIFIED
        "source": "lemonade_api:13305",
        "total_bytes": 103_079_215_104,  # ~96 GiB
        "free_bytes": 90_000_000_000,
    },
    "os": {
        "Caption": "Microsoft Windows 11 Pro",
        "Version": "10.0.22631",
        "BuildNumber": "22631",
    },
    "bios": {
        "Manufacturer": "American Megatrends International, LLC.",
        "SMBIOSBIOSVersion": "3.03",
    },
    "storage": {
        "TotalBytes": 4_000_787_030_016,
    },
    "installed_models": ["llama3.3-70b", "phi4", "qwen2.5-coder-32b"],
}


# ── 1. Verifier-derived status is persisted (no manual promotion) ─────────────


class TestLedgerStatusAfterIngestion:
    """Proves upsert_claim() + update_claim_status() work together so the ledger
    reflects the real verification outcome — not just the RETRIEVED default."""

    def test_ram_with_two_corroborating_a0_sources_is_verified(self, db):
        """Two A0 CIM sources that agree must produce VERIFIED in the ledger.

        Policy: minimum_corroboration=2, minimum_authority=A1.
        Payload has TotalPhysicalMemory AND PhysicalMemoryCapacitySum (both A0).
        Expected verifier outcome: VERIFIED.
        """
        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(_FULL_PAYLOAD)

        ram = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "installed_physical_memory_bytes")
        assert ram is not None, "installed_physical_memory_bytes claim must be stored"
        assert ram["status"] == "VERIFIED", (
            f"Two corroborating A0 CIM sources must produce VERIFIED; got {ram['status']!r}. "
            "If RETRIEVED, the update_claim_status() call was skipped."
        )
        assert int(ram["value"]) == 137_438_953_472, "Value must equal the exact byte count"

    def test_vram_from_lemonade_single_source_is_verified(self, db):
        """Single A0 Lemonade source for VRAM must be VERIFIED.

        Policy for vram_usable_bytes: minimum_corroboration=1, minimum_authority=A0.
        A single A0 source satisfies both → VERIFIED.
        """
        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(_FULL_PAYLOAD)

        vram = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes")
        assert vram is not None, "vram_usable_bytes claim must be stored"
        assert vram["status"] == "VERIFIED", (
            f"Single A0 Lemonade source meets min_corroboration=1 → VERIFIED; "
            f"got {vram['status']!r}"
        )
        assert int(vram["value"]) == 103_079_215_104

    def test_cpu_model_single_source_is_retrieved_or_verified(self, db):
        """CPU model with one A0 source — verifier outcome depends on policy."""
        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(_FULL_PAYLOAD)

        cpu = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "cpu_model")
        assert cpu is not None, "cpu_model claim must be stored"
        # Policy minimum_corroboration=1, minimum_authority=A1.
        # Single A0 source → VERIFIED (meets both).
        assert cpu["status"] in ("VERIFIED", "PARTIALLY_VERIFIED", "RETRIEVED"), (
            f"Unexpected cpu_model status: {cpu['status']!r}"
        )

    def test_status_is_durable_across_reads(self, db):
        """Verifier-derived status must survive a re-fetch (not in-memory only)."""
        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(_FULL_PAYLOAD)

        first = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes")
        assert first is not None
        saved_status = first["status"]

        second = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes")
        assert second["status"] == saved_status, "Status must be durable across reads"


# ── 2. Authority guard: higher-authority claim is never overwritten ────────────


class TestAuthorityGuard:
    """Regression tests for the upsert_claim authority-race guard.

    upsert_claim() returns the existing claim's id WITHOUT writing when the
    incoming tier is lower authority (higher tier number).  update_claim_status()
    must NOT be called in that case — doing so would corrupt an untouched claim.
    """

    def test_existing_verified_a0_claim_not_degraded_by_a7_ingest(self, db):
        """An A7 ingest must not degrade a pre-existing VERIFIED A0 claim."""
        adapter = WindowsInventoryAdapter(db)
        # First: store a VERIFIED A0 RAM claim via full inventory
        adapter.ingest_inventory(_FULL_PAYLOAD)
        before = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "installed_physical_memory_bytes")
        assert before is not None
        assert before["status"] == "VERIFIED"
        assert before["authority_tier"] == "A0"

        # Now manually insert an A7 assertion (lower authority than A0)
        db.upsert_claim(
            SUBJECT_DEVICE_A01,
            "installed_physical_memory_bytes",
            "64000000000",  # wrong value at lower authority
            authority_tier="A7",
        )

        # The A0 VERIFIED claim must be unaffected
        after = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "installed_physical_memory_bytes")
        assert after is not None
        assert after["authority_tier"] == "A0", (
            f"A7 ingest must not overwrite A0 claim; authority_tier is {after['authority_tier']!r}"
        )
        assert after["status"] == "VERIFIED", (
            f"A7 ingest must not degrade VERIFIED status; got {after['status']!r}"
        )
        assert int(after["value"]) == 137_438_953_472, (
            "A7 ingest must not overwrite the A0 byte value"
        )

    def test_second_inventory_ingest_does_not_corrupt_status(self, db):
        """Ingesting the same payload twice must be idempotent — status stays VERIFIED."""
        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(_FULL_PAYLOAD)
        first_ram = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "installed_physical_memory_bytes")
        assert first_ram["status"] == "VERIFIED"

        # Second ingest (same tier, same value) — upsert_claim writes (equal tier),
        # update_claim_status is a no-op (status already VERIFIED).
        adapter.ingest_inventory(_FULL_PAYLOAD)
        second_ram = db.get_claim_by_predicate(
            SUBJECT_DEVICE_A01, "installed_physical_memory_bytes"
        )
        assert second_ram["status"] == "VERIFIED", (
            f"Second identical ingest must leave VERIFIED status intact; got {second_ram['status']!r}"
        )

    def test_higher_authority_ingest_can_update_status(self, db):
        """A0 ingest must be able to promote a pre-existing USER_ASSERTED A7 claim."""
        # Start with a user assertion (A7 → USER_ASSERTED)
        db.upsert_claim(
            SUBJECT_DEVICE_A01,
            "ram_gb",
            "128",
            authority_tier="A7",
        )
        user_claim = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "ram_gb")
        assert user_claim is not None
        assert user_claim["status"] == "USER_ASSERTED"

        # Inventory ingest (A0) should overwrite with a higher-authority value
        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(_FULL_PAYLOAD)

        after = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "ram_gb")
        assert after is not None
        assert after["authority_tier"] == "A0", (
            f"A0 ingest must supersede A7 claim; got authority_tier={after['authority_tier']!r}"
        )


# ── 3. PolicyEnforcer after ingestion (no manual promotion) ───────────────────


class TestEnforcerAfterIngestion:
    """PolicyEnforcer must supply context from real ingested claims.

    No manual status promotion — enforcer behavior must come from actual
    verifier-derived statuses written by ingest_inventory().
    """

    def test_enforcer_does_not_abstain_on_ram_after_ingestion(self, db):
        """After ingestion (two A0 CIM sources → VERIFIED RAM), enforcer must not abstain."""
        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(_FULL_PAYLOAD)

        enforcer = PolicyEnforcer(db)
        decision = enforcer.enforce("how much RAM does my system have")
        assert not decision.must_abstain, (
            f"Enforcer must not abstain when VERIFIED RAM claim exists. "
            f"Abstention reason: {decision.abstention_reason!r}"
        )

    def test_enforcer_supplies_ram_value_in_context(self, db):
        """Context must contain the actual RAM figure from inventory."""
        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(_FULL_PAYLOAD)

        enforcer = PolicyEnforcer(db)
        decision = enforcer.enforce("how much RAM does my system have")
        # 128 GiB = 137438953472 bytes; context should mention either
        assert "128" in decision.verified_context or "137" in decision.verified_context, (
            f"Context must contain the RAM value; got: {decision.verified_context!r}"
        )

    def test_enforcer_does_not_abstain_on_vram_after_ingestion(self, db):
        """After ingestion (A0 Lemonade → VERIFIED VRAM), enforcer must not abstain."""
        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(_FULL_PAYLOAD)

        enforcer = PolicyEnforcer(db)
        decision = enforcer.enforce("how much VRAM does my GPU have")
        assert not decision.must_abstain, (
            f"Enforcer must not abstain when VERIFIED VRAM claim exists. "
            f"Abstention reason: {decision.abstention_reason!r}"
        )

    def test_enforcer_abstains_before_any_ingestion(self, db):
        """Baseline: empty ledger → enforcer still abstains (regression guard)."""
        enforcer = PolicyEnforcer(db)
        decision = enforcer.enforce("how much RAM does my system have")
        assert decision.must_abstain, (
            "Enforcer must abstain when no hardware claims are in the ledger"
        )


# ── 4. INV-REQ-001 E2E: AdapterRAM never produces a VRAM claim ────────────────


class TestAdapterRAMNeverVerified:
    def test_adapterram_payload_produces_no_vram_claim(self, db):
        """A payload containing AdapterRAM but no Lemonade source must produce
        no vram_usable_bytes claim at all."""
        adapter = WindowsInventoryAdapter(db)
        result = adapter.ingest_inventory(
            {
                "subject": SUBJECT_DEVICE_A01,
                "gpu": {"Name": "Fake GPU", "AdapterRAM": 4_294_967_295},
            }
        )
        assert any("INV-REQ-001" in v for v in result["violations"])
        vram = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes")
        assert vram is None, "No vram_usable_bytes claim should be stored from AdapterRAM alone"

    def test_32bit_cap_never_stored_as_vram_value(self, db):
        """The 32-bit cap (4 294 967 295) must never appear as a stored VRAM value."""
        adapter = WindowsInventoryAdapter(db)
        adapter.ingest_inventory(
            {
                "subject": SUBJECT_DEVICE_A01,
                "gpu": {"Name": "AMD GPU", "AdapterRAM": 4_294_967_295},
            }
        )
        vram = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes")
        if vram is not None:
            assert int(vram["value"]) != 4_294_967_295


# ── 5. FastAPI route model ────────────────────────────────────────────────────


class TestInventoryRouteModel:
    def test_inventory_payload_model_accepts_full_collector_json(self):
        from orivellum.api.routes.pklos import InventoryPayload

        model = InventoryPayload(**_FULL_PAYLOAD)
        assert model.subject == SUBJECT_DEVICE_A01
        assert model.memory["TotalPhysicalMemory"] == 137_438_953_472
        assert model.vram["source"] == "lemonade_api:13305"
        assert len(model.installed_models) == 3

    def test_inventory_payload_model_accepts_empty_payload(self):
        from orivellum.api.routes.pklos import InventoryPayload

        model = InventoryPayload()
        assert model.cpu == {}

    def test_ingest_summary_has_required_keys(self, db):
        adapter = WindowsInventoryAdapter(db)
        result = adapter.ingest_inventory(_FULL_PAYLOAD)
        assert {
            "claims_written",
            "claims_verified",
            "claims_conflicted",
            "claims_unavailable",
            "violations",
        }.issubset(result.keys())
        assert result["claims_written"] > 0
        assert isinstance(result["violations"], list)
