"""Adapter 1 — Windows System Inventory (spec §6.1).

Uses the structured JSON emitted by scripts/inventory_collector.ps1.
The PowerShell script runs on the Windows machine and POSTs the result to
POST /api/pklos/inventory.  This adapter ingests that payload, runs every
claim through the ClaimVerifier, and stores results in the claim ledger.

Authority: A0 (direct CIM measurement) / A1 (signed inventory).

CRITICAL — INV-REQ-001: Win32_VideoController.AdapterRAM misreports VRAM on
unified-memory architecture (32-bit field, no shared-memory concept).
This adapter NEVER reads AdapterRAM for VRAM claims.  Usable VRAM is sourced
only from the Lemonade/ROCm runtime API or vendor architecture docs.

BUILDABLE-NOW — spec §6.1.
"""
from __future__ import annotations

import logging
from typing import Any

from ..authority import AuthorityTier, ClaimStatus, SUBJECT_DEVICE_A01
from ..authority_resolver import AuthorityResolver
from ..claim_verifier import ClaimVerifier
from .base import AdapterBase, Evidence, Recipe

logger = logging.getLogger("orivellum.pklos.adapters.windows_inventory")

_resolver = AuthorityResolver()
_verifier = ClaimVerifier()

_CAPABILITIES = [
    "installed_physical_memory_bytes", "ram_gb",
    "cpu_model", "cpu_cores", "cpu_threads", "cpu_base_clock_mhz",
    "gpu_model",
    "vram_usable_bytes", "vram_gb",           # NOTE: never from AdapterRAM
    "os_name", "os_version", "os_build",
    "bios_version", "bios_manufacturer",
    "storage_total_bytes",
    "installed_models",
]


class WindowsInventoryAdapter(AdapterBase):
    """Adapter 1: Windows System Inventory via CIM (Get-CimInstance).

    Two modes:
      ingest_inventory(db, payload) — called from POST /api/pklos/inventory;
                                      parses the collector JSON and writes claims.
      fetch(predicate)              — reads current claims from the ledger;
                                      does NOT re-run PowerShell live.
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    @property
    def adapter_id(self) -> str:
        return "windows-inventory@0.1.0"

    def capabilities(self) -> list[str]:
        return _CAPABILITIES

    def evidence_recipe(self, predicate: str) -> Recipe:
        policy = _resolver.resolve(predicate)
        return Recipe(
            predicate=predicate,
            sources=policy.preferred_sources,
            minimum_authority=policy.minimum_authority.value,
            minimum_corroboration=policy.minimum_corroboration,
            notes=policy.notes,
        )

    def fetch(self, predicate: str, *, freshness: str = "DURABLE") -> list[Evidence]:
        """Read the most recent inventory claim for predicate from the ledger."""
        try:
            claim = self._db.get_claim_by_predicate(SUBJECT_DEVICE_A01, predicate)
            if not claim:
                return []
            return [Evidence(
                source_type="windows_inventory_cache",
                source_locator=f"claims:{claim['id'][:12]}",
                authority=claim.get("authority_tier", "A0"),
                raw_value=claim.get("value", ""),
                predicate=predicate,
                subject=SUBJECT_DEVICE_A01,
                captured_at=claim.get("updated_at", ""),
                meta={"claim_id": claim["id"], "status": claim.get("status")},
            )]
        except Exception as exc:
            logger.debug("WindowsInventoryAdapter.fetch failed: %s", exc)
            return []

    # ── Main ingestion path ────────────────────────────────────────────────────

    def ingest_inventory(self, payload: dict) -> dict:
        """Parse a collector JSON payload and write verified claims to the ledger.

        Returns a summary dict:
          {
            claims_written: int,
            claims_verified: int,
            claims_conflicted: int,
            claims_unavailable: int,
            violations: [str],  # INV-REQ-001 and other policy violations
          }
        """
        subject = payload.get("subject", SUBJECT_DEVICE_A01)
        collected_at = payload.get("collected_at", "")
        violations: list[str] = []
        claims_written = 0
        claims_verified = 0
        claims_conflicted = 0
        claims_unavailable = 0

        def _write(predicate: str, evidence_items: list[dict]) -> None:
            nonlocal claims_written, claims_verified, claims_conflicted, claims_unavailable
            # Check for prohibited sources first (INV-REQ-001)
            safe = []
            for e in evidence_items:
                locator = e.get("source_locator", "")
                if _resolver.is_prohibited_source(predicate, locator):
                    violations.append(
                        f"INV-REQ-001: prohibited source {locator!r} excluded from {predicate}"
                    )
                    continue
                safe.append(e)
            if not safe:
                claims_unavailable += 1
                return

            result = _verifier.verify(predicate, safe, subject=subject)

            # Choose the display value to store
            store_value = result.normalized_value or (safe[0].get("raw_value", "") if safe else "")

            self._db.upsert_claim(
                subject, predicate, store_value,
                authority_tier=result.authority.value,
                ttl_class=_resolver.resolve(predicate).ttl_class,
                evidence_text=result.confidence_basis,
                meta={
                    "normalized_display_value": result.display_value,
                    "confidence": result.confidence,
                    "confidence_basis": result.confidence_basis,
                    "observed_at": collected_at,
                    "conflict_type": result.conflict_type.value if result.conflict_type else None,
                    "all_evidence_count": len(safe),
                },
            )
            claims_written += 1

            if result.status == ClaimStatus.VERIFIED:
                claims_verified += 1
            elif result.status == ClaimStatus.CONFLICTED:
                claims_conflicted += 1
            elif result.status == ClaimStatus.UNAVAILABLE:
                claims_unavailable += 1

        # ── CPU ───────────────────────────────────────────────────────────────
        cpu = payload.get("cpu") or {}
        if cpu.get("Name"):
            _write("cpu_model", [
                {"source_type": "windows_cim", "source_locator": "Win32_Processor.Name",
                 "authority": "A0", "raw_value": cpu["Name"]},
            ])
        if cpu.get("NumberOfCores") is not None:
            _write("cpu_cores", [
                {"source_type": "windows_cim", "source_locator": "Win32_Processor.NumberOfCores",
                 "authority": "A0", "raw_value": str(cpu["NumberOfCores"])},
            ])
        if cpu.get("NumberOfLogicalProcessors") is not None:
            _write("cpu_threads", [
                {"source_type": "windows_cim", "source_locator": "Win32_Processor.NumberOfLogicalProcessors",
                 "authority": "A0", "raw_value": str(cpu["NumberOfLogicalProcessors"])},
            ])

        # ── RAM — A0: two independent CIM sources required (spec §5.2) ────────
        mem = payload.get("memory") or {}
        mem_evidence: list[dict] = []
        if mem.get("TotalPhysicalMemory") is not None:
            mem_evidence.append({
                "source_type": "windows_cim",
                "source_locator": "Win32_ComputerSystem.TotalPhysicalMemory",
                "authority": "A0",
                "raw_value": str(mem["TotalPhysicalMemory"]),
            })
        if mem.get("PhysicalMemoryCapacitySum") is not None:
            mem_evidence.append({
                "source_type": "windows_cim",
                "source_locator": "Win32_PhysicalMemory.Capacity:sum",
                "authority": "A0",
                "raw_value": str(mem["PhysicalMemoryCapacitySum"]),
            })
        if mem_evidence:
            _write("installed_physical_memory_bytes", mem_evidence)
            # Also store as ram_gb (derived display predicate)
            if mem.get("TotalPhysicalMemory"):
                gib = mem["TotalPhysicalMemory"] / 1_073_741_824
                _write("ram_gb", [{
                    "source_type": "windows_cim",
                    "source_locator": "Win32_ComputerSystem.TotalPhysicalMemory",
                    "authority": "A0",
                    "raw_value": f"{gib:.0f} GiB",
                }])

        # ── GPU — NOTE: no AdapterRAM (INV-REQ-001) ──────────────────────────
        gpu = payload.get("gpu") or {}
        if gpu.get("Name"):
            evidence = [{"source_type": "windows_cim", "source_locator": "Win32_VideoController.Name",
                         "authority": "A0", "raw_value": gpu["Name"]}]
            if gpu.get("VideoProcessor"):
                evidence.append({"source_type": "windows_cim",
                                  "source_locator": "Win32_VideoController.VideoProcessor",
                                  "authority": "A0", "raw_value": gpu["VideoProcessor"]})
            _write("gpu_model", evidence)

        # AdapterRAM MUST NOT be used for VRAM (INV-REQ-001)
        # Explicitly log if the collector accidentally included it
        if gpu.get("AdapterRAM") is not None:
            violations.append(
                "INV-REQ-001: Win32_VideoController.AdapterRAM present in payload — "
                "excluded from vram_* claims (misreports on unified-memory architecture)"
            )

        # ── VRAM — sourced from Lemonade/ROCm runtime ONLY ───────────────────
        vram = payload.get("vram") or {}
        vram_source = vram.get("source", "unavailable")
        vram_bytes = vram.get("total_bytes")
        if vram_source != "unavailable" and vram_bytes:
            _write("vram_usable_bytes", [{
                "source_type": "lemonade_api",
                "source_locator": vram_source,
                "authority": "A0",
                "raw_value": str(vram_bytes),
            }])
            gib = vram_bytes / 1_073_741_824
            _write("vram_gb", [{
                "source_type": "lemonade_api",
                "source_locator": vram_source,
                "authority": "A0",
                "raw_value": f"{gib:.0f} GiB",
            }])

        # ── OS ────────────────────────────────────────────────────────────────
        os_data = payload.get("os") or {}
        if os_data.get("Caption"):
            _write("os_name", [{"source_type": "windows_cim",
                                 "source_locator": "Win32_OperatingSystem.Caption",
                                 "authority": "A0", "raw_value": os_data["Caption"]}])
        if os_data.get("Version"):
            _write("os_version", [{"source_type": "windows_cim",
                                    "source_locator": "Win32_OperatingSystem.Version",
                                    "authority": "A0", "raw_value": os_data["Version"]}])
        if os_data.get("BuildNumber"):
            _write("os_build", [{"source_type": "windows_cim",
                                  "source_locator": "Win32_OperatingSystem.BuildNumber",
                                  "authority": "A0", "raw_value": str(os_data["BuildNumber"])}])

        # ── BIOS ──────────────────────────────────────────────────────────────
        bios = payload.get("bios") or {}
        if bios.get("Manufacturer"):
            _write("bios_manufacturer", [{"source_type": "windows_cim",
                                           "source_locator": "Win32_BIOS.Manufacturer",
                                           "authority": "A1", "raw_value": bios["Manufacturer"]}])
        if bios.get("SMBIOSBIOSVersion"):
            _write("bios_version", [{"source_type": "windows_cim",
                                      "source_locator": "Win32_BIOS.SMBIOSBIOSVersion",
                                      "authority": "A1", "raw_value": bios["SMBIOSBIOSVersion"]}])

        # ── Storage ───────────────────────────────────────────────────────────
        storage = payload.get("storage") or {}
        if storage.get("TotalBytes"):
            _write("storage_total_bytes", [{
                "source_type": "windows_cim",
                "source_locator": "Win32_DiskDrive.Size:sum",
                "authority": "A0", "raw_value": str(storage["TotalBytes"]),
            }])

        # ── Lemonade installed models ─────────────────────────────────────────
        models = payload.get("installed_models") or []
        if models:
            models_str = ", ".join(str(m) for m in models[:20])
            _write("installed_models", [{
                "source_type": "lemonade_api",
                "source_locator": "lemonade_api:models",
                "authority": "A0", "raw_value": models_str,
            }])

        logger.info(
            "WindowsInventoryAdapter: ingested %d claims (%d verified, %d conflicted, %d unavailable); %d violations",
            claims_written, claims_verified, claims_conflicted, claims_unavailable, len(violations),
        )

        return {
            "claims_written": claims_written,
            "claims_verified": claims_verified,
            "claims_conflicted": claims_conflicted,
            "claims_unavailable": claims_unavailable,
            "violations": violations,
        }
