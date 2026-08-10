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

from ..authority import SUBJECT_DEVICE_A01, ClaimStatus
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

            if result.status == ClaimStatus.UNAVAILABLE:
                claims_unavailable += 1
                return

            # Choose the display value to store
            store_value = result.normalized_value or (safe[0].get("raw_value", "") if safe else "")

            # upsert_claim() always writes RETRIEVED for A0–A6 tiers (it knows
            # nothing about the verifier's corroboration result).  We capture the
            # claim id and immediately apply the verified status so the ledger
            # reflects the actual verification outcome — not just "retrieved".
            #
            # AUTHORITY GUARD: upsert_claim() only writes when the incoming tier
            # is equal-or-better (lower number) than any existing claim.  If a
            # higher-authority claim already exists, upsert_claim() returns its id
            # WITHOUT writing.  Calling update_claim_status() on that id would
            # corrupt a claim we never wrote.  We therefore pre-check authority
            # so the status transition only fires when we know the write happened.
            incoming_tier_num = int(result.authority.value[1:]) \
                if result.authority.value[1:].isdigit() else 99
            try:
                _existing = self._db.get_claim_by_predicate(subject, predicate)
            except Exception:
                _existing = None
            _existing_tier_num = 99
            if _existing:
                _t = _existing.get("authority_tier", "A99")
                if len(_t) > 1 and _t[1:].isdigit():
                    _existing_tier_num = int(_t[1:])
            # upsert_claim writes when incoming_tier_num <= existing_tier_num
            # (equal or better authority) — same condition as inside upsert_claim.
            _write_wins = incoming_tier_num <= _existing_tier_num

            claim_id = self._db.upsert_claim(
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
            # Only apply the verifier-derived status when inventory won the
            # authority race.  update_claim_status() is a no-op when old == new.
            if claim_id and _write_wins:
                self._db.update_claim_status(
                    claim_id,
                    result.status.value,
                    actor="windows-inventory",
                    reason=result.confidence_basis[:200] if result.confidence_basis else None,
                )
            claims_written += 1

            if result.status == ClaimStatus.VERIFIED:
                claims_verified += 1
            elif result.status == ClaimStatus.CONFLICTED:
                claims_conflicted += 1

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
            # Use only Win32_VideoController.Name as the primary evidence item.
            # VideoProcessor is a different attribute (e.g. "AMD Radeon 890M" vs
            # "AMD Radeon 890M Graphics") — pairing both as competing evidence items
            # triggers a material contradiction in the verifier.  Store it as
            # metadata for display only.
            evidence = [{"source_type": "windows_cim", "source_locator": "Win32_VideoController.Name",
                         "authority": "A0", "raw_value": gpu["Name"]}]
            _write("gpu_model", evidence)

        # AdapterRAM MUST NOT be used for VRAM (INV-REQ-001)
        # Explicitly log if the collector accidentally included it
        if gpu.get("AdapterRAM") is not None:
            violations.append(
                "INV-REQ-001: Win32_VideoController.AdapterRAM present in payload — "
                "excluded from vram_* claims (misreports on unified-memory architecture)"
            )

        # ── VRAM — sourced from Lemonade/ROCm runtime ONLY ───────────────────
        #
        # Authority mapping for each collector source:
        #   lemonade_api:*      A0 — live measurement from Lemonade REST API
        #   lemonade_tool_call  A0 — live measurement via Lemonade completions API
        #   rocm_smi            A0 — live measurement from AMD ROCm driver
        #   user_supplied       A0 — user-supplied value (explicit --vram-gb flag)
        #   lemonade_config     A1 — configured allocation (not a live reading)
        #
        # INV-REQ-001: Win32_VideoController.AdapterRAM is NEVER used for vram_*
        # claims — see the note at the top of this file.  The collector places any
        # AdapterRAM data in payload["vram_hint"] (not payload["vram"]) so it
        # cannot accidentally slip through.
        # Strict allowlist of recognized VRAM sources.
        # Any source NOT in this map is REJECTED with a policy violation —
        # fail-closed to prevent unknown or spoofed sources from writing claims.
        # source prefix/value → (source_type, authority)
        _VRAM_SOURCE_ALLOWLIST: dict[str, tuple[str, str]] = {
            "lemonade_api":    ("lemonade_api",   "A0"),
            "rocm_smi":        ("rocm_smi",        "A0"),
            "user_supplied":   ("user_supplied",   "A0"),
            "lemonade_config": ("lemonade_config", "A1"),
        }

        # AdapterRAM variant patterns to block proactively, in addition to the
        # resolver's "adapterram" substring check.  These cover underscore/spaced
        # forms that the contiguous-substring check would miss.
        _ADAPTER_RAM_PATTERNS = (
            "adapterram",           # CamelCase / contiguous (resolver already catches this)
            "adapter_ram",          # underscore-separated
            "cim_adapter",          # CIM prefix variants
            "win32_adapter",        # Win32 prefix variants
            "videocontroller.video", # VideoMemory field variants
        )

        vram = payload.get("vram") or {}
        vram_source: str = vram.get("source", "unavailable")
        vram_bytes  = vram.get("total_bytes")

        if vram_source != "unavailable" and vram_bytes:
            vram_src_lower = vram_source.lower()

            # Explicit AdapterRAM block before allowlist lookup
            if any(pat in vram_src_lower for pat in _ADAPTER_RAM_PATTERNS):
                violations.append(
                    f"INV-REQ-001: VRAM source {vram_source!r} matches an AdapterRAM "
                    f"variant pattern — excluded from all vram_* claims"
                )
                claims_unavailable += 1
            else:
                # Strict allowlist: only recognized prefixes produce evidence
                resolved: tuple[str, str] | None = None
                for prefix, mapping in _VRAM_SOURCE_ALLOWLIST.items():
                    if vram_source == prefix or vram_source.startswith(f"{prefix}:"):
                        resolved = mapping
                        break

                if resolved is None:
                    violations.append(
                        f"VRAM source {vram_source!r} is not in the recognized source "
                        f"allowlist — excluded from all vram_* claims (fail-closed policy)"
                    )
                    claims_unavailable += 1
                else:
                    st, auth = resolved
                    gib = vram_bytes / 1_073_741_824

                    # vram_usable_bytes requires A0 (live runtime measurement).
                    # lemonade_config is a configured allocation at A1 — writing it
                    # to vram_usable_bytes would persist as RETRIEVED without being
                    # VERIFIED, giving a false impression of live-measurement authority.
                    # Config-derived VRAM writes only to vram_gb (A1 minimum) instead.
                    if auth == "A0":
                        _write("vram_usable_bytes", [{
                            "source_type": st,
                            "source_locator": vram_source,
                            "authority": auth,
                            "raw_value": str(vram_bytes),
                        }])

                    # vram_gb accepts A1 — written for every allowlisted source.
                    _write("vram_gb", [{
                        "source_type": st,
                        "source_locator": vram_source,
                        "authority": auth,
                        "raw_value": f"{gib:.0f} GiB",
                    }])

        # vram_hint (Win32 AdapterRAM advisory) is intentionally ignored here.
        # INV-REQ-001 prohibits AdapterRAM for all vram_* predicates regardless
        # of whether the value saturated.  The hint is present in the payload only
        # for console display in the collector; no claim is written from it.

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
