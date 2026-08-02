"""
WR-00 Duplicate Analysis
========================

Groups payloads by SHA-256 so the archive can answer the foundational
governance question the spec raises (sec. forensic scope):

    "which physical copy is authoritative, and which copies are generated
     mirrors, stale releases, embedded dependencies, or accidental
     duplication?"

Byte-identical duplication is a *fact* (same SHA-256). Deciding *which* copy is
canonical is a *judgement* made later in authority resolution. This module only
establishes the fact.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass


@dataclass
class DuplicateGroup:
    sha256: str
    size: int
    copies: list          # list[logical_path]
    ext: str

    @property
    def count(self) -> int:
        return len(self.copies)

    def as_dict(self) -> dict:
        return {
            "sha256": self.sha256,
            "size": self.size,
            "count": self.count,
            "ext": self.ext,
            "copies": self.copies,
        }


def find_duplicate_groups(payloads: list) -> list:
    """Return duplicate groups (>= 2 byte-identical copies), largest first."""
    by_hash: dict[str, list] = defaultdict(list)
    meta: dict[str, tuple] = {}
    for rec in payloads:
        by_hash[rec.sha256].append(rec.logical_path)
        meta[rec.sha256] = (rec.size, rec.ext)

    groups = []
    for sha, copies in by_hash.items():
        if len(copies) >= 2:
            size, ext = meta[sha]
            groups.append(
                DuplicateGroup(sha256=sha, size=size, copies=sorted(copies), ext=ext)
            )
    groups.sort(key=lambda g: (g.count, g.size), reverse=True)
    return groups


def duplicate_summary(payloads: list) -> dict:
    groups = find_duplicate_groups(payloads)
    files_in_groups = sum(g.count for g in groups)
    distinct_payloads = len({r.sha256 for r in payloads})
    return {
        "distinct_sha256_payloads": distinct_payloads,
        "exact_duplicate_groups": len(groups),
        "files_participating_in_duplicate_groups": files_in_groups,
        "redundant_copies": files_in_groups - len(groups),  # copies beyond the first
        "groups": [g.as_dict() for g in groups],
    }
