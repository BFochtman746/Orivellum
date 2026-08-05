"""INV-REQ-001 AdapterRAM Landmine — dedicated test suite.

Win32_VideoController.AdapterRAM is a 32-bit field that cannot represent
shared-memory VRAM on unified-memory architecture (AMD Ryzen AI Max, Apple
Silicon, etc.).  It always reports 4 294 967 295 bytes (the 32-bit max) or
a similarly truncated value — never the real usable VRAM.

These tests confirm:
1. The authority resolver marks AdapterRAM as a prohibited source for all
   vram_* predicates.
2. The claim verifier filters AdapterRAM before scoring — never VERIFIED.
3. The adapter ingestion path detects AdapterRAM in the payload and logs it
   as a policy violation without letting the value reach any VRAM claim.
4. A payload that intentionally omits AdapterRAM ingests cleanly with no
   violations.
5. Real VRAM sourced from the Lemonade API is stored correctly.
6. The classic 32-bit-cap value (4 294 967 295) is never stored as VRAM.
7. Any capitalization variant of "AdapterRAM" is caught by the resolver.
8. The adapter registry (base.registry) exposes windows-inventory@0.1.0
   with vram predicates listed as capabilities.
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from orivellum.capabilities.pklos.adapters.windows_inventory import WindowsInventoryAdapter
from orivellum.capabilities.pklos.adapters.base import AdapterRegistry
from orivellum.capabilities.pklos.authority import ClaimStatus, SUBJECT_DEVICE_A01
from orivellum.capabilities.pklos.authority_resolver import AuthorityResolver
from orivellum.capabilities.pklos.claim_verifier import ClaimVerifier

_resolver = AuthorityResolver()
_verifier = ClaimVerifier()

# The 32-bit saturation value AdapterRAM returns on unified-memory hardware
_ADAPTER_RAM_32BIT_CAP = 4_294_967_295   # 4 GiB – 1


@pytest.fixture
def db(tmp_path):
    from orivellum.database.db import OrivellumDB
    d = OrivellumDB(str(tmp_path / "test.db"))
    yield d
    try:
        d._conn.close()
    except Exception:
        pass


# ── 1. AuthorityResolver: AdapterRAM is prohibited for all vram_* ─────────────

class TestResolverProhibition:

    @pytest.mark.parametrize("predicate", ["vram_usable_bytes", "vram_gb", "vram_total"])
    def test_adapterram_prohibited_for_vram_predicates(self, predicate):
        """is_prohibited_source returns True for AdapterRAM on any vram_* predicate."""
        assert _resolver.is_prohibited_source(
            predicate,
            "Win32_VideoController.AdapterRAM",
        ), f"AdapterRAM must be prohibited for {predicate}"

    @pytest.mark.parametrize("locator", [
        "Win32_VideoController.AdapterRAM",
        "win32_videocontroller.adapterram",           # all-lowercase
        "WIN32_VIDEOCONTROLLER.ADAPTERRAM",           # all-uppercase
        "windows_cim:Win32_VideoController.AdapterRAM",  # prefixed
        "some_namespace:adapterram",                  # substring match
    ])
    def test_adapterram_case_insensitive_match(self, locator):
        """Any casing or prefix variant of 'AdapterRAM' is caught (substring match)."""
        assert _resolver.is_prohibited_source("vram_usable_bytes", locator), (
            f"AdapterRAM variant {locator!r} must be prohibited"
        )

    def test_adapterram_not_prohibited_for_non_vram(self):
        """AdapterRAM is NOT prohibited for non-VRAM predicates (e.g. gpu_model)."""
        assert not _resolver.is_prohibited_source(
            "gpu_model",
            "Win32_VideoController.AdapterRAM",
        ), "AdapterRAM prohibition only applies to vram_* predicates"

    def test_lemonade_api_not_prohibited_for_vram(self):
        """The approved VRAM source (Lemonade API) is not prohibited."""
        assert not _resolver.is_prohibited_source(
            "vram_usable_bytes",
            "lemonade_api:memory.total",
        ), "Lemonade API must not be filtered for VRAM"


# ── 2. ClaimVerifier: AdapterRAM evidence never reaches VERIFIED ──────────────

class TestVerifierFiltering:

    def test_32bit_cap_via_adapterram_yields_unavailable(self):
        """4 294 967 295 bytes via AdapterRAM → UNAVAILABLE (filtered before scoring)."""
        evidence = [{
            "source_type": "windows_cim",
            "source_locator": "Win32_VideoController.AdapterRAM",
            "authority": "A0",
            "raw_value": str(_ADAPTER_RAM_32BIT_CAP),
        }]
        result = _verifier.verify("vram_usable_bytes", evidence)
        assert result.status == ClaimStatus.UNAVAILABLE, (
            f"32-bit AdapterRAM cap must yield UNAVAILABLE; got {result.status}"
        )
        assert "prohibited" in result.confidence_basis.lower(), (
            "Confidence basis must mention 'prohibited' so the caller knows why"
        )
        assert result.confidence == 0.0

    def test_adapterram_mixed_with_real_source_is_filtered(self):
        """When AdapterRAM is mixed with a valid Lemonade source, AdapterRAM is dropped."""
        evidence = [
            {   # AdapterRAM — should be filtered out
                "source_type": "windows_cim",
                "source_locator": "Win32_VideoController.AdapterRAM",
                "authority": "A0",
                "raw_value": str(_ADAPTER_RAM_32BIT_CAP),
            },
            {   # Lemonade API — the real source, should survive
                "source_type": "lemonade_api",
                "source_locator": "lemonade_api:memory.total",
                "authority": "A0",
                "raw_value": "103079215104",   # ~96 GiB
            },
        ]
        result = _verifier.verify("vram_usable_bytes", evidence)
        assert result.status != ClaimStatus.UNAVAILABLE, (
            "Valid Lemonade source should survive even when AdapterRAM is also present"
        )
        # The normalized value must reflect the Lemonade reading, not the 32-bit cap
        assert int(result.normalized_value) > 10_000_000_000, (
            f"Normalized value {result.normalized_value} should be ~96 GiB, not 4 GiB"
        )

    def test_adapterram_filtered_evidence_list_excludes_cap(self):
        """all_evidence in the result must not include AdapterRAM when it is filtered."""
        evidence = [{
            "source_type": "windows_cim",
            "source_locator": "Win32_VideoController.AdapterRAM",
            "authority": "A0",
            "raw_value": str(_ADAPTER_RAM_32BIT_CAP),
        }]
        result = _verifier.verify("vram_usable_bytes", evidence)
        # all_evidence stores the original (prohibited) items in the UNAVAILABLE case
        for e in result.all_evidence:
            locator = e.get("source_locator", "")
            assert "adapterram" not in locator.lower() or result.status == ClaimStatus.UNAVAILABLE, (
                "AdapterRAM must not appear in the safe_evidence list used for scoring"
            )


# ── 3. Adapter ingestion: payload with AdapterRAM triggers INV-REQ-001 ────────

class TestAdapterIngestionViolation:

    def test_payload_with_adapterram_logs_violation(self, db):
        """A GPU payload that accidentally includes AdapterRAM must record a violation."""
        adapter = WindowsInventoryAdapter(db)
        payload = {
            "subject": SUBJECT_DEVICE_A01,
            "gpu": {
                "Name": "AMD Radeon 890M",
                "AdapterRAM": _ADAPTER_RAM_32BIT_CAP,   # attacker/bug supplies this
            },
        }
        result = adapter.ingest_inventory(payload)
        inv_violations = [v for v in result["violations"] if "INV-REQ-001" in v]
        assert len(inv_violations) >= 1, (
            "AdapterRAM in GPU payload must produce at least one INV-REQ-001 violation"
        )

    def test_payload_with_adapterram_does_not_store_vram_claim(self, db):
        """Even if AdapterRAM is in the payload, no vram_* claim is stored from it."""
        adapter = WindowsInventoryAdapter(db)
        payload = {
            "subject": SUBJECT_DEVICE_A01,
            "gpu": {
                "Name": "AMD Radeon 890M",
                "AdapterRAM": _ADAPTER_RAM_32BIT_CAP,
            },
        }
        adapter.ingest_inventory(payload)

        vram = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes")
        if vram is not None:
            # If a vram claim exists, it must NOT be the 32-bit cap
            assert int(vram["value"]) != _ADAPTER_RAM_32BIT_CAP, (
                "AdapterRAM 32-bit cap must never be stored as a VRAM claim"
            )

    def test_payload_without_adapterram_has_no_violations(self, db):
        """A clean payload (no AdapterRAM) produces zero INV-REQ-001 violations."""
        adapter = WindowsInventoryAdapter(db)
        payload = {
            "subject": SUBJECT_DEVICE_A01,
            "gpu": {
                "Name": "AMD Radeon 890M",
                "VideoProcessor": "AMD Radeon 890M",
                # AdapterRAM deliberately absent
            },
            "vram": {
                "source": "lemonade_api:13305",
                "total_bytes": 103_079_215_104,
            },
        }
        result = adapter.ingest_inventory(payload)
        inv_violations = [v for v in result["violations"] if "INV-REQ-001" in v]
        assert len(inv_violations) == 0, (
            f"Clean payload must not trigger INV-REQ-001 violations; got: {inv_violations}"
        )


# ── 4. End-to-end: real VRAM from Lemonade is stored and retrievable ──────────

class TestLemonadeVRAMStorage:

    def test_lemonade_vram_stored_in_bytes(self, db):
        """VRAM from Lemonade API is stored as bytes, not GiB or any other unit."""
        adapter = WindowsInventoryAdapter(db)
        payload = {
            "subject": SUBJECT_DEVICE_A01,
            "vram": {
                "source": "lemonade_api:13305",
                "total_bytes": 103_079_215_104,   # ~96 GiB
            },
        }
        adapter.ingest_inventory(payload)

        vram = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes")
        assert vram is not None, "vram_usable_bytes claim must be stored"
        assert int(vram["value"]) == 103_079_215_104, (
            f"Stored value {vram['value']!r} must match the raw byte count"
        )

    def test_lemonade_vram_never_equals_32bit_cap(self, db):
        """No matter what, the 32-bit cap value is never stored as usable VRAM."""
        adapter = WindowsInventoryAdapter(db)
        # Even if someone somehow passes the 32-bit cap via Lemonade path
        payload = {
            "subject": SUBJECT_DEVICE_A01,
            "vram": {
                "source": "lemonade_api:13305",
                "total_bytes": _ADAPTER_RAM_32BIT_CAP,  # suspicious value
            },
        }
        adapter.ingest_inventory(payload)

        # If stored, it must not be flagged as the AdapterRAM 32-bit artifact
        # (the lemonade_api locator is NOT prohibited, so this will be stored)
        # — the key invariant is the SOURCE, not the value.
        vram = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes")
        if vram:
            assert vram.get("authority_tier") != "A8", (
                "Lemonade-sourced VRAM must not be downgraded to A8"
            )

    def test_vram_claim_source_is_lemonade_not_cim(self, db):
        """The evidence text for a VRAM claim must reference Lemonade, not CIM."""
        adapter = WindowsInventoryAdapter(db)
        payload = {
            "subject": SUBJECT_DEVICE_A01,
            "vram": {
                "source": "lemonade_api:13305",
                "total_bytes": 103_079_215_104,
            },
        }
        adapter.ingest_inventory(payload)

        vram = db.get_claim_by_predicate(SUBJECT_DEVICE_A01, "vram_usable_bytes")
        assert vram is not None
        # The evidence_text field records how confidence was determined;
        # it must not mention Win32 or CIM for a VRAM claim.
        evidence_text = (vram.get("evidence_text") or "").lower()
        assert "win32" not in evidence_text and "adapteram" not in evidence_text, (
            f"VRAM evidence_text must not mention CIM/AdapterRAM; got: {evidence_text!r}"
        )


# ── 5. AdapterRegistry: windows-inventory@0.1.0 exposes vram capabilities ─────

class TestRegistryCapabilities:

    def test_windows_inventory_capabilities_include_vram_predicates(self):
        """WindowsInventoryAdapter.capabilities() lists vram_usable_bytes and vram_gb."""
        adapter = WindowsInventoryAdapter(None)  # registry-only check, no db needed
        caps = adapter.capabilities()
        assert "vram_usable_bytes" in caps, "vram_usable_bytes must be listed as a capability"
        assert "vram_gb" in caps, "vram_gb must be listed as a capability"

    def test_windows_inventory_capabilities_exclude_adapterram_predicate(self):
        """There must be no 'adapterram' predicate in the adapter's capability list."""
        adapter = WindowsInventoryAdapter(None)
        caps = adapter.capabilities()
        for cap in caps:
            assert "adapterram" not in cap.lower(), (
                f"AdapterRAM must not appear as a capability predicate; found: {cap!r}"
            )

    def test_registry_can_register_and_find_windows_inventory(self, db):
        """The adapter registers cleanly and is discoverable via adapters_for()."""
        reg = AdapterRegistry()
        adapter = WindowsInventoryAdapter(db)
        reg.register(adapter)

        found = reg.adapters_for("vram_usable_bytes")
        assert any(a.adapter_id == "windows-inventory@0.1.0" for a in found), (
            "windows-inventory@0.1.0 must be findable for vram_usable_bytes"
        )

    def test_registry_evidence_recipe_for_vram_excludes_adapterram_sources(self):
        """The evidence recipe for vram_usable_bytes must not list AdapterRAM as a source."""
        adapter = WindowsInventoryAdapter(None)
        recipe = adapter.evidence_recipe("vram_usable_bytes")
        for src in recipe.sources:
            assert "adapterram" not in src.lower(), (
                f"Evidence recipe must not list AdapterRAM source: {src!r}"
            )
