"""
WR-00 Capability Map & Disposition
==================================

Two spec sections drive this module:

  * sec. 12.2 "Capability consolidation map" — maps the archive's documents onto
    the eight canonical writing capabilities and names a primary source for each.
  * sec. 15 "Archive-level disposition rules" — assigns every file exactly one
    disposition: CANONICAL / SUPPORTING / HISTORICAL / DUPLICATE / DERIVATIVE /
    IMPLEMENTATION / REJECTED / PACKAGING.

Matching is heuristic and evidence-linked: every assignment records *why* it
was made (which pattern fired), so a human can audit and override. Nothing here
deletes or moves a file; disposition is metadata.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Canonical capability -> (primary-source filename patterns, secondary patterns)
CAPABILITY_RULES = {
    "lifecycle_and_gates": {
        "primary": [r"narrativeos.*v?24[._]4", r"narrativeos.*final"],
        "secondary": [r"narrativeos", r"\bums\b", r"unhindered", r"mastery"],
    },
    "evaluation": {
        "primary": [r"forge.*v?3[._]3", r"forge.*complete"],
        "secondary": [r"ultimate_prose", r"diagnostic", r"detection"],
    },
    "worker_orchestration": {
        "primary": [r"unified_writers_room", r"writers.room"],
        "secondary": [r"narrativeos.*engine", r"execution_engine"],
    },
    "voice": {
        "primary": [r"voice_architect", r"author_profile"],
        "secondary": [r"held.breath", r"prose_style", r"the_voice"],
    },
    "canon": {
        "primary": [r"book_bible"],
        "secondary": [r"world_build", r"character_profile", r"bible_data", r"motif"],
    },
    "research": {
        "primary": [r"biblical_research", r"ultimate_biblical"],
        "secondary": [r"bible_data", r"etymology", r"material_culture", r"kingship"],
    },
    "provenance": {
        "primary": [r"ai_provenance.*v?2[._]0", r"provenance.*system.*2"],
        "secondary": [r"provenance", r"module_?7", r"detection_system"],
    },
    "release": {
        "primary": [r"sovereign_master", r"sovereign.*repository"],
        "secondary": [r"sovereign", r"deployment", r"program_repository"],
    },
}

IMPLEMENTATION_EXT = {"py", "sql", "json", "yaml", "yml", "sh", "ps1", "toml", "cfg", "ini"}
DOC_EXT = {"docx", "md", "txt", "pdf", "html", "rtf", "dotx"}


def _match_any(patterns, text: str) -> str | None:
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            return p
    return None


@dataclass
class CapabilityHit:
    logical_path: str
    capability: str
    tier: str          # "primary" | "secondary"
    matched_pattern: str


def map_capabilities(payloads: list) -> dict:
    hits = []
    per_capability: dict[str, dict] = {c: {"primary": [], "secondary": []} for c in CAPABILITY_RULES}
    for rec in payloads:
        text = rec.display_path
        for cap, rules in CAPABILITY_RULES.items():
            pat = _match_any(rules["primary"], text)
            if pat:
                hits.append(CapabilityHit(rec.logical_path, cap, "primary", pat))
                per_capability[cap]["primary"].append(rec.logical_path)
                continue
            pat = _match_any(rules["secondary"], text)
            if pat:
                hits.append(CapabilityHit(rec.logical_path, cap, "secondary", pat))
                per_capability[cap]["secondary"].append(rec.logical_path)

    # propose a primary source per capability: the shortest-path primary hit,
    # else the shortest-path secondary hit
    proposals = {}
    for cap, buckets in per_capability.items():
        pool = buckets["primary"] or buckets["secondary"]
        chosen = min(pool, key=len) if pool else None
        proposals[cap] = {
            "proposed_primary_source": chosen,
            "primary_candidates": buckets["primary"],
            "secondary_candidates": buckets["secondary"],
            "note": "REQUIRES_HUMAN_CONFIRMATION",
        }
    return {"capability_proposals": proposals,
            "hit_count": len(hits)}


def classify_dispositions(payloads, packaging, duplicate_groups, authority_graph) -> dict:
    """Assign one disposition per record with an evidence reason."""
    # Which hashes are duplicated, and which single path is the 'kept' copy
    dup_hash_to_copies = {g["sha256"]: g["copies"] for g in duplicate_groups}
    kept_copy = {}
    for sha, copies in dup_hash_to_copies.items():
        # keep the shallowest / shortest path as the representative
        kept_copy[sha] = min(copies, key=lambda p: (p.count("!"), len(p)))

    # historical candidates from authority proposals
    historical_paths = set()
    canonical_paths = set()
    for prop in authority_graph.get("proposals", []):
        for m in prop["members"]:
            if m["proposed_status"] == "HISTORICAL_CANDIDATE":
                historical_paths.add(m["logical_path"])
            elif m["proposed_status"] == "CANONICAL_CANDIDATE":
                canonical_paths.add(m["logical_path"])

    dispositions = []
    tally: dict[str, int] = {}

    def record(rec, disp, reason):
        dispositions.append({
            "logical_path": rec.logical_path,
            "disposition": disp,
            "reason": reason,
        })
        tally[disp] = tally.get(disp, 0) + 1

    for rec in packaging:
        record(rec, "PACKAGING", "macOS resource-fork / archive metadata")

    for rec in payloads:
        # duplicate that is NOT the kept representative
        if rec.sha256 in dup_hash_to_copies and rec.logical_path != kept_copy[rec.sha256]:
            record(rec, "DUPLICATE",
                   f"byte-identical to kept copy {kept_copy[rec.sha256]}")
            continue
        # nested/expanded derivative mirror
        if "_EXPANDED" in rec.logical_path or rec.logical_path.count("!") >= 2:
            record(rec, "DERIVATIVE",
                   "generated/packaged copy inside a nested or _EXPANDED container")
            continue
        if rec.ext in IMPLEMENTATION_EXT:
            record(rec, "IMPLEMENTATION", f".{rec.ext} code/schema/tool artifact")
            continue
        if rec.logical_path in historical_paths:
            record(rec, "HISTORICAL", "superseded version within its system family")
            continue
        if rec.logical_path in canonical_paths:
            record(rec, "CANONICAL", "highest version in its system family (candidate)")
            continue
        if rec.ext in DOC_EXT:
            record(rec, "SUPPORTING", "doctrine/example document, not a governing authority")
            continue
        record(rec, "SUPPORTING", "unclassified payload retained pending review")

    return {"disposition_tally": dict(sorted(tally.items())),
            "dispositions": dispositions}
