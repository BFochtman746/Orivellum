"""Adapter base interface — spec §5.4.

Every adapter:
  1. Implements AdapterBase
  2. Registers with the AdapterRegistry
  3. Returns Evidence records in the canonical shape
  4. NEVER subclasses the foundation — only calls and registers with it

ENF-REQ-002: adapters expose narrow typed operations, never 'run_any_command'.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class Recipe:
    """Evidence recipe for a specific predicate.

    Describes how to collect evidence: which sources to try,
    in what order, with what corroboration threshold and tolerance.
    """
    predicate: str
    sources: list[str]                    # ordered source identifiers to try
    minimum_authority: str = "A7"         # minimum tier required for this adapter
    minimum_corroboration: int = 1        # how many independent sources needed
    tolerance: float = 0.02              # relative tolerance for numeric agreement
    notes: str = ""


@dataclass
class Evidence:
    """A single piece of evidence from an adapter.

    This is NOT a Claim — it feeds the ClaimVerifier which produces Claims.
    """
    source_type: str                      # e.g. "user_assertion", "windows_cim"
    source_locator: str                   # e.g. "Win32_ComputerSystem.TotalPhysicalMemory"
    authority: str                        # A0–A8
    raw_value: str                        # as-returned, before normalization
    predicate: str = ""
    subject: str = ""
    captured_at: str = ""
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_type": self.source_type,
            "source_locator": self.source_locator,
            "authority": self.authority,
            "raw_value": self.raw_value,
            "predicate": self.predicate,
            "subject": self.subject,
            "captured_at": self.captured_at,
            "meta": self.meta,
        }


class AdapterBase(ABC):
    """Base class for all PKLOS adapters.

    Adapters are composed with the foundation — they never subclass it.
    The foundation owns claim shapes and invariants.
    Adapters own how to fetch and rank evidence for their own domain.
    """

    @property
    @abstractmethod
    def adapter_id(self) -> str:
        """Unique identifier for this adapter (e.g. 'windows-inventory@0.1.0')."""

    @abstractmethod
    def capabilities(self) -> list[str]:
        """Return the list of predicates this adapter can answer."""

    @abstractmethod
    def evidence_recipe(self, predicate: str) -> Recipe:
        """Return the evidence recipe for a specific predicate."""

    @abstractmethod
    def fetch(self, predicate: str, *, freshness: str = "DURABLE") -> list[Evidence]:
        """Fetch evidence for a predicate.  Read-only.  Returns list[Evidence]."""

    def can_answer(self, predicate: str) -> bool:
        """True if this adapter claims to answer the given predicate."""
        return predicate in self.capabilities()


class AdapterRegistry:
    """Registry of active adapters.

    The PolicyEnforcer queries this to find which adapters can answer
    a given predicate before calling them.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, AdapterBase] = {}

    def register(self, adapter: AdapterBase) -> None:
        self._adapters[adapter.adapter_id] = adapter

    def unregister(self, adapter_id: str) -> None:
        self._adapters.pop(adapter_id, None)

    def adapters_for(self, predicate: str) -> list[AdapterBase]:
        """Return all adapters that can answer a given predicate, best first."""
        return [a for a in self._adapters.values() if a.can_answer(predicate)]

    def all_capabilities(self) -> dict[str, list[str]]:
        """Return {adapter_id: [predicates]} for diagnostics."""
        return {aid: a.capabilities() for aid, a in self._adapters.items()}

    def fetch_all(self, predicate: str, *, freshness: str = "DURABLE") -> list[Evidence]:
        """Collect evidence from all registered adapters that can answer predicate."""
        evidence: list[Evidence] = []
        for adapter in self.adapters_for(predicate):
            try:
                evidence.extend(adapter.fetch(predicate, freshness=freshness))
            except Exception:
                pass  # adapter failure → UNAVAILABLE handled by verifier
        return evidence


# Global registry — populated at startup
registry = AdapterRegistry()
