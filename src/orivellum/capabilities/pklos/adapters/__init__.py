"""PKLOS Layer 1 — Source Adapters.

Each adapter implements the AdapterBase interface (spec §5.4):
  capabilities()         → list of predicates this adapter can answer
  evidence_recipe(pred)  → Recipe describing sources + corroboration + tolerance
  fetch(pred, freshness) → list[Evidence]  (narrow, read-only)

The foundation owns the claim shape and the verifier.
Adapters own how to fetch and rank evidence for their own domain.

Available adapters:
  recollection  — Adapter 4: user recollection / conversational assertion (A7)
  library       — Adapter 2: local library / Second Brain documents (A4)

Planned (BUILDABLE-NOW, not yet wired):
  windows_inventory — Adapter 1: Windows CIM inventory (A0/A1)

[NEEDS-ENGINEERING]:
  web           — Adapter 3: governed web / external sources (A5/A6)
  calculator    — Adapter 5: derived / computed facts (A5)
"""
from .base import AdapterBase, Evidence, Recipe, AdapterRegistry
from .recollection import RecollectionAdapter
from .library import LibraryAdapter

__all__ = [
    "AdapterBase",
    "Evidence",
    "Recipe",
    "AdapterRegistry",
    "RecollectionAdapter",
    "LibraryAdapter",
]
