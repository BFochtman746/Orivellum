"""
WR-00 Forensic Baseline Orchestrator
====================================

Produces the exit artifact the spec requires before any other release:

    "Immutable archive snapshot, complete manifest, duplicate/lineage report,
     authority candidates."  (spec sec. 14, WR-00)

    "Begin WR-00 — Forensic Baseline and Authority Resolution. Produce the
     immutable archive hash, full recursive manifest, duplicate and derivative
     classification, capability map, version/supersession graph, and proposed
     canonical authority for each writing capability."  (spec, Mandatory next
     action)

Outputs (written to an output directory, never over the source):
    baseline_manifest.json   — the complete machine-readable record
    INVENTORY.csv            — one row per payload (spreadsheet-friendly)
    DUPLICATES.csv           — duplicate groups
    WR00_REPORT.md           — the human-readable forensic report
    baseline.sha256          — hash of baseline_manifest.json (self-sealing)
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone

from . import authority, capability_map, duplicates, inventory


def _ext_census(payloads) -> dict:
    census: dict[str, int] = {}
    for r in payloads:
        key = r.ext or "(none)"
        census[key] = census.get(key, 0) + 1
    return dict(sorted(census.items(), key=lambda kv: kv[1], reverse=True))


def run_baseline(archive_path: str, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)

    inv = inventory.build_inventory(archive_path)
    payloads = inv.payloads
    packaging = inv.packaging

    dup = duplicates.duplicate_summary(payloads)
    auth = authority.build_authority_graph(payloads)
    caps = capability_map.map_capabilities(payloads)
    disp = capability_map.classify_dispositions(
        payloads, packaging, dup["groups"], auth
    )

    manifest = {
        "artifact": "WR-00 Forensic Baseline",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_archive": {
            "path": os.path.basename(archive_path),
            "sha256": inv.archive_sha256,
        },
        "measures": {
            "total_records": len(inv.records),
            "payload_files_analyzed": len(payloads),
            "packaging_records": len(packaging),
            "distinct_sha256_payloads": dup["distinct_sha256_payloads"],
            "exact_duplicate_groups": dup["exact_duplicate_groups"],
            "files_in_duplicate_groups": dup["files_participating_in_duplicate_groups"],
            "redundant_copies": dup["redundant_copies"],
            "nested_containers_expanded": len(
                {r.container for r in inv.records if r.depth > 0}
            ),
            "read_errors": len(inv.errors),
        },
        "extension_census": _ext_census(payloads),
        "duplicates": dup,
        "authority_graph": auth,
        "capability_map": caps,
        "disposition": disp,
        "errors": inv.errors,
    }

    # --- write JSON manifest ---
    manifest_path = os.path.join(out_dir, "baseline_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)

    # --- self-seal: hash the manifest ---
    with open(manifest_path, "rb") as fh:
        seal = hashlib.sha256(fh.read()).hexdigest()
    with open(os.path.join(out_dir, "baseline.sha256"), "w", encoding="utf-8") as fh:
        fh.write(f"{seal}  baseline_manifest.json\n")
    manifest["baseline_manifest_sha256"] = seal

    # --- inventory CSV ---
    with open(os.path.join(out_dir, "INVENTORY.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["logical_path", "ext", "size_bytes", "depth", "sha256", "kind"])
        disp_lookup = {d["logical_path"]: d["disposition"] for d in disp["dispositions"]}
        for r in inv.records:
            w.writerow([r.logical_path, r.ext, r.size, r.depth, r.sha256, r.kind])

    # --- duplicates CSV ---
    with open(os.path.join(out_dir, "DUPLICATES.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["copies", "size_bytes", "ext", "sha256", "paths"])
        for g in dup["groups"]:
            w.writerow([g["count"], g["size"], g["ext"], g["sha256"], " | ".join(g["copies"])])

    # --- human report ---
    _write_report(os.path.join(out_dir, "WR00_REPORT.md"), manifest)

    return manifest


def _write_report(path: str, m: dict) -> None:
    meas = m["measures"]
    lines = []
    A = lines.append
    A("# WR-00 — Forensic Baseline & Authority Resolution")
    A("")
    A(f"*Generated {m['generated_utc']}*")
    A("")
    A("This report is produced by reading the archive **read-only**. No source "
      "file was modified. The manifest that backs this report is self-sealed "
      "with its own SHA-256 so any later tampering is detectable.")
    A("")
    A("## Source archive")
    A("")
    A(f"- **File:** `{m['source_archive']['path']}`")
    A(f"- **SHA-256:** `{m['source_archive']['sha256']}`")
    A(f"- **Manifest seal:** `{m.get('baseline_manifest_sha256','(computed)')}`")
    A("")
    A("## Measures")
    A("")
    A("| Measure | Value |")
    A("|---|---|")
    for k, v in meas.items():
        A(f"| {k.replace('_',' ')} | {v} |")
    A("")
    A("## Extension census (payloads only)")
    A("")
    A("| Extension | Count |")
    A("|---|---|")
    for ext, n in m["extension_census"].items():
        A(f"| {ext} | {n} |")
    A("")
    A("## Duplication")
    A("")
    d = m["duplicates"]
    A(f"- Distinct payloads (by SHA-256): **{d['distinct_sha256_payloads']}**")
    A(f"- Exact duplicate groups: **{d['exact_duplicate_groups']}**")
    A(f"- Redundant copies (beyond first): **{d['redundant_copies']}**")
    A("")
    A("Top duplicate groups:")
    A("")
    A("| Copies | Ext | Example path |")
    A("|---|---|---|")
    for g in d["groups"][:12]:
        A(f"| {g['count']} | {g['ext']} | `{g['copies'][0]}` |")
    A("")
    A("## Authority label census")
    A("")
    A("| Label | Occurrences |")
    A("|---|---|")
    for lab, n in m["authority_graph"]["authority_label_census"].items():
        A(f"| {lab} | {n} |")
    A("")
    A(f"**{m['authority_graph']['families_with_version_conflicts']}** system "
      "families contain more than one version and therefore raise a "
      "supersession question. Every proposal below is a *candidate* that "
      "**requires human confirmation** — the system never auto-decides "
      "authority.")
    A("")
    A("## Proposed canonical authority per capability")
    A("")
    A("| Capability | Proposed primary source |")
    A("|---|---|")
    for cap, prop in m["capability_map"]["capability_proposals"].items():
        src = prop["proposed_primary_source"] or "_(none matched — needs human input)_"
        A(f"| {cap} | `{src}` |")
    A("")
    A("## Disposition tally")
    A("")
    A("| Disposition | Count |")
    A("|---|---|")
    for disp, n in m["disposition"]["disposition_tally"].items():
        A(f"| {disp} | {n} |")
    A("")
    A("## What this baseline authorizes")
    A("")
    A("Per the specification, **no integrated \"master\" document and no "
      "drafting work may begin until this baseline is accepted.** Acceptance "
      "means a human has reviewed the authority proposals and dispositions "
      "above and recorded approval. The next release, WR-01, builds the "
      "governed book-domain foundation on top of the accepted authority set.")
    A("")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
