"""
WR-00 Authority & Version Graph
===============================

The spec's #1 critical finding is "Authority collapse" (sec. 1.1): the archive
reuses MASTER / FINAL / COMPLETE / HARDENED / DOMINANT / ULTIMATE / LOCK / RC
labels across independent systems with no release registry or supersession
chain.

This module does NOT presume to decide authority automatically — the spec is
explicit that "software cannot safely infer that every later number supersedes
every earlier component." Instead it:

  1. Extracts authority-bearing labels and version tokens from each filename.
  2. Groups files into *system families* by their stem (name minus version).
  3. Within a family, proposes an ordering by parsed version, marking the
     highest as CANONICAL_CANDIDATE and the rest as HISTORICAL_CANDIDATE.
  4. Flags every proposal as REQUIRES_HUMAN_CONFIRMATION.

The output is a *proposal for a human to approve*, never an executed decision.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

AUTHORITY_LABELS = [
    "MASTER", "FINAL", "COMPLETE", "HARDENED", "DOMINANT",
    "ULTIMATE", "LOCK", "RC", "FROZEN", "CANONICAL",
]

# version patterns: v1.2.0, v24_4, v2_0, _v4.1, -RC2, v1.2.0-RC2
_VERSION_RE = re.compile(
    r"""
    v?
    (?P<major>\d+)
    (?:[._](?P<minor>\d+))?
    (?:[._](?P<patch>\d+))?
    (?:[-_ ]?rc(?P<rc>\d+))?
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass
class VersionKey:
    major: int = -1
    minor: int = -1
    patch: int = -1
    rc: int = 9999          # a release (no rc) sorts *after* its own RCs

    def tuple(self) -> tuple:
        # RC absence => 9999 so final > rc2 > rc1
        return (self.major, self.minor, self.patch, self.rc)


def parse_version(name: str) -> VersionKey | None:
    m = _VERSION_RE.search(name)
    if not m or m.group("major") is None:
        return None
    def g(k: str, default: int) -> int:
        v = m.group(k)
        return int(v) if v is not None else default
    return VersionKey(
        major=g("major", -1),
        minor=g("minor", 0),
        patch=g("patch", 0),
        rc=g("rc", 9999),
    )


def _family_stem(display_path: str) -> str:
    """Strip directories, extension, and version/authority tokens to a stem."""
    import posixpath
    base = posixpath.basename(display_path)
    base = posixpath.splitext(base)[0]
    # remove version tokens
    base = _VERSION_RE.sub("", base)
    # remove authority labels
    for label in AUTHORITY_LABELS:
        base = re.sub(rf"[_\- ]?{label}", "", base, flags=re.IGNORECASE)
    # collapse separators
    base = re.sub(r"[_\-\s]+", "_", base).strip("_").lower()
    return base or "unnamed"


def detect_labels(display_path: str) -> list:
    up = display_path.upper()
    return [lab for lab in AUTHORITY_LABELS if lab in up]


@dataclass
class AuthorityProposal:
    family: str
    members: list           # list of dicts: path, version, labels, proposed_status
    note: str = "REQUIRES_HUMAN_CONFIRMATION"

    def as_dict(self) -> dict:
        return {"family": self.family, "note": self.note, "members": self.members}


def build_authority_graph(payloads: list) -> dict:
    families: dict[str, list] = defaultdict(list)
    for rec in payloads:
        stem = _family_stem(rec.display_path)
        families[stem].append(rec)

    proposals = []
    label_census: dict[str, int] = defaultdict(int)
    for rec in payloads:
        for lab in detect_labels(rec.display_path):
            label_census[lab] += 1

    for stem, recs in sorted(families.items()):
        if len(recs) < 2:
            continue  # a family needs >= 2 members to have a supersession question
        ranked = sorted(
            recs,
            key=lambda r: (parse_version(r.display_path) or VersionKey()).tuple(),
            reverse=True,
        )
        members = []
        for i, r in enumerate(ranked):
            vk = parse_version(r.display_path)
            members.append({
                "logical_path": r.logical_path,
                "display_path": r.display_path,
                "version": vk.tuple() if vk else None,
                "labels": detect_labels(r.display_path),
                "proposed_status": (
                    "CANONICAL_CANDIDATE" if i == 0 else "HISTORICAL_CANDIDATE"
                ),
            })
        proposals.append(AuthorityProposal(family=stem, members=members))

    return {
        "authority_label_census": dict(sorted(label_census.items())),
        "families_with_version_conflicts": len(proposals),
        "proposals": [p.as_dict() for p in proposals],
    }
