"""Authority resolver — per-claim authority policy.

Spec §5.2: For each claim predicate, returns:
  - preferred_sources: ordered list of source identifiers to try
  - minimum_authority: the lowest acceptable tier for this predicate
  - minimum_corroboration: number of independent sources required
  - conflict_policy: what to do when sources disagree

This is a deterministic policy service — NOT an LLM call.
ENF-REQ-002: adapters expose narrow typed operations, never 'run_any_command'.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from .authority import AuthorityTier, ConflictType


@dataclass(frozen=True)
class AuthorityPolicy:
    """The resolved policy for a specific claim predicate."""
    predicate: str
    preferred_sources: list[str]
    minimum_authority: AuthorityTier
    minimum_corroboration: int          # number of independent sources required
    conflict_policy: str                # "hold_and_report" | "prefer_higher" | "prefer_newer"
    ttl_class: str = "DURABLE"
    notes: str = ""


# ── Policy registry ────────────────────────────────────────────────────────────
# Keyed by predicate name (exact match first, then prefix/suffix wildcards).
# Spec example: installed physical memory → A1 minimum, 2 corroborating sources.
#
# INV-REQ-001: on unified-memory architecture, AdapterRAM is PROHIBITED
# as a source for "vram_*" predicates.

_POLICIES: dict[str, AuthorityPolicy] = {

    # ── Hardware: RAM ──────────────────────────────────────────────────────────
    "installed_physical_memory_bytes": AuthorityPolicy(
        predicate="installed_physical_memory_bytes",
        preferred_sources=[
            "windows_cim:Win32_ComputerSystem.TotalPhysicalMemory",
            "windows_cim:Win32_PhysicalMemory.Capacity:sum",
            "proc_meminfo:MemTotal",
        ],
        minimum_authority=AuthorityTier.A1,
        minimum_corroboration=2,
        conflict_policy="hold_and_report",
        ttl_class="installed_ram",
        notes="Both CIM sources must agree within tolerance before VERIFIED is set.",
    ),
    "ram_gb": AuthorityPolicy(
        predicate="ram_gb",
        preferred_sources=[
            "windows_cim:Win32_ComputerSystem.TotalPhysicalMemory",
            "windows_cim:Win32_PhysicalMemory.Capacity:sum",
            "user_assertion",
        ],
        minimum_authority=AuthorityTier.A7,
        minimum_corroboration=1,
        conflict_policy="hold_and_report",
        ttl_class="installed_ram",
    ),

    # ── Hardware: CPU ──────────────────────────────────────────────────────────
    "cpu_model": AuthorityPolicy(
        predicate="cpu_model",
        preferred_sources=[
            "windows_cim:Win32_Processor.Name",
            "windows_cim:Win32_Processor.ProcessorId",
            "registry:HKLM\\Hardware\\Description\\System\\CentralProcessor\\0\\ProcessorNameString",
        ],
        minimum_authority=AuthorityTier.A1,
        minimum_corroboration=1,
        conflict_policy="prefer_higher",
        ttl_class="cpu_identity",
        notes="Normalize known aliases: 'Ryzen AI Max+ 395' / 'AMD Ryzen AI MAX+ 395' / 'Strix Halo 395' → single canonical id.",
    ),
    "cpu_cores": AuthorityPolicy(
        predicate="cpu_cores",
        preferred_sources=["windows_cim:Win32_Processor.NumberOfCores", "user_assertion"],
        minimum_authority=AuthorityTier.A1,
        minimum_corroboration=1,
        conflict_policy="prefer_higher",
        ttl_class="cpu_identity",
    ),
    "cpu_threads": AuthorityPolicy(
        predicate="cpu_threads",
        preferred_sources=["windows_cim:Win32_Processor.NumberOfLogicalProcessors", "user_assertion"],
        minimum_authority=AuthorityTier.A1,
        minimum_corroboration=1,
        conflict_policy="prefer_higher",
        ttl_class="cpu_identity",
    ),

    # ── Hardware: GPU — CRITICAL (INV-REQ-001) ────────────────────────────────
    # AdapterRAM is PROHIBITED as a source for VRAM on unified-memory architecture.
    "gpu_model": AuthorityPolicy(
        predicate="gpu_model",
        preferred_sources=[
            "windows_cim:Win32_VideoController.Name",
            "windows_cim:Win32_VideoController.VideoProcessor",
            "user_assertion",
        ],
        minimum_authority=AuthorityTier.A1,
        minimum_corroboration=1,
        conflict_policy="prefer_higher",
        ttl_class="cpu_identity",
    ),
    "vram_usable_bytes": AuthorityPolicy(
        predicate="vram_usable_bytes",
        preferred_sources=[
            "lemonade_api:memory.total",
            "rocm_smi:memory",
            "vendor_arch_doc",
        ],
        minimum_authority=AuthorityTier.A0,
        minimum_corroboration=1,
        conflict_policy="hold_and_report",
        ttl_class="installed_ram",
        notes=(
            "INV-REQ-001: Win32_VideoController.AdapterRAM is PROHIBITED on "
            "unified-memory architecture (32-bit field misreports). "
            "Source ONLY from runtime API or vendor architecture doc."
        ),
    ),
    "vram_gb": AuthorityPolicy(
        predicate="vram_gb",
        preferred_sources=["lemonade_api:memory.total", "rocm_smi:memory", "vendor_arch_doc"],
        minimum_authority=AuthorityTier.A1,
        minimum_corroboration=1,
        conflict_policy="hold_and_report",
        ttl_class="installed_ram",
        notes="INV-REQ-001: never derive from AdapterRAM. See vram_usable_bytes.",
    ),

    # ── OS / Software ──────────────────────────────────────────────────────────
    "os_name": AuthorityPolicy(
        predicate="os_name",
        preferred_sources=["windows_cim:Win32_OperatingSystem.Caption", "user_assertion"],
        minimum_authority=AuthorityTier.A1,
        minimum_corroboration=1,
        conflict_policy="prefer_higher",
        ttl_class="os_build",
    ),
    "os_version": AuthorityPolicy(
        predicate="os_version",
        preferred_sources=["windows_cim:Win32_OperatingSystem.Version", "windows_cim:Win32_OperatingSystem.BuildNumber"],
        minimum_authority=AuthorityTier.A1,
        minimum_corroboration=1,
        conflict_policy="prefer_higher",
        ttl_class="os_build",
    ),
    "os_build": AuthorityPolicy(
        predicate="os_build",
        preferred_sources=["windows_cim:Win32_OperatingSystem.BuildNumber"],
        minimum_authority=AuthorityTier.A1,
        minimum_corroboration=1,
        conflict_policy="prefer_higher",
        ttl_class="os_build",
    ),

    # ── Storage ────────────────────────────────────────────────────────────────
    "storage_total_bytes": AuthorityPolicy(
        predicate="storage_total_bytes",
        preferred_sources=["windows_cim:Win32_DiskDrive.Size:sum", "user_assertion"],
        minimum_authority=AuthorityTier.A1,
        minimum_corroboration=1,
        conflict_policy="prefer_higher",
        ttl_class="DURABLE",
    ),

    # ── Installed AI models ────────────────────────────────────────────────────
    "installed_models": AuthorityPolicy(
        predicate="installed_models",
        preferred_sources=["lemonade_api:models", "file_scan:model_dirs"],
        minimum_authority=AuthorityTier.A0,
        minimum_corroboration=1,
        conflict_policy="prefer_higher",
        ttl_class="installed_models",
    ),
}

# Default policy for predicates not in the registry
_DEFAULT_POLICY = AuthorityPolicy(
    predicate="*",
    preferred_sources=["user_assertion"],
    minimum_authority=AuthorityTier.A7,
    minimum_corroboration=1,
    conflict_policy="hold_and_report",
    ttl_class="DURABLE",
)


class AuthorityResolver:
    """Returns the authority policy for a given claim predicate.

    Usage:
        resolver = AuthorityResolver()
        policy = resolver.resolve("ram_gb")
        # policy.preferred_sources → [...], policy.minimum_authority → A1, etc.
    """

    def resolve(self, predicate: str) -> AuthorityPolicy:
        """Return the policy for this predicate.

        Lookup order:
          1. Exact predicate match
          2. Normalized predicate (lowercase, underscores)
          3. Default policy
        """
        # Exact match
        if predicate in _POLICIES:
            return _POLICIES[predicate]
        # Normalized
        normalized = predicate.lower().replace("-", "_").replace(" ", "_")
        if normalized in _POLICIES:
            return _POLICIES[normalized]
        return _DEFAULT_POLICY

    def is_prohibited_source(self, predicate: str, source: str) -> bool:
        """Return True if a source is explicitly prohibited for this predicate.

        INV-REQ-001: AdapterRAM is prohibited for all vram_* predicates on
        unified-memory architecture (32-bit field misreports VRAM capacity).
        Uses substring matching so it catches any capitalization variant.
        """
        vram_predicates = {"vram_usable_bytes", "vram_gb", "vram_total"}
        if predicate.lower() not in vram_predicates:
            return False
        source_lower = source.lower()
        # "AdapterRAM" → lowercase = "adapterram" (Adapter+RAM, double-r)
        # Check for "adapterram" as a substring to match any CIM locator format.
        return "adapterram" in source_lower

    def meets_minimum_authority(
        self,
        predicate: str,
        achieved_tier: AuthorityTier,
    ) -> bool:
        """True if achieved_tier meets or exceeds the minimum for this predicate."""
        policy = self.resolve(predicate)
        return achieved_tier.numeric <= policy.minimum_authority.numeric

    def requires_verification(self, predicate: str) -> bool:
        """True if the predicate has a deterministic verification path available."""
        return predicate in _POLICIES and _POLICIES[predicate].minimum_authority.numeric < AuthorityTier.A7.numeric


# Module-level singleton
_resolver = AuthorityResolver()


def resolve(predicate: str) -> AuthorityPolicy:
    return _resolver.resolve(predicate)


def is_prohibited_source(predicate: str, source: str) -> bool:
    return _resolver.is_prohibited_source(predicate, source)
