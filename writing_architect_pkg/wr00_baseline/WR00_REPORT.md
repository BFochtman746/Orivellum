# WR-00 — Forensic Baseline & Authority Resolution

*Generated 2026-08-02T01:41:35.838296+00:00*

This report is produced by reading the archive **read-only**. No source file was modified. The manifest that backs this report is self-sealed with its own SHA-256 so any later tampering is detectable.

## Source archive

- **File:** `WRITING_ARCHITECT.zip`
- **SHA-256:** `e6802b7891e60483b28b96d3c56f36cc331acf8233fb3760bbd3a76dd7cdcbd0`
- **Manifest seal:** `2b960e5ce4c852304ce909ad2e4e9a84a4657ad6b80e8b204f4b67360dd4f9b9`

## Measures

| Measure | Value |
|---|---|
| total records | 1285 |
| payload files analyzed | 759 |
| packaging records | 526 |
| distinct sha256 payloads | 318 |
| exact duplicate groups | 275 |
| files in duplicate groups | 716 |
| redundant copies | 441 |
| nested containers expanded | 7 |
| read errors | 0 |

## Extension census (payloads only)

| Extension | Count |
|---|---|
| docx | 470 |
| md | 238 |
| json | 12 |
| txt | 9 |
| py | 8 |
| zip | 7 |
| html | 4 |
| 4 | 3 |
| sha256 | 2 |
| pdf | 2 |
| 4 2 | 2 |
| 0 | 1 |
| 3 | 1 |

## Duplication

- Distinct payloads (by SHA-256): **318**
- Exact duplicate groups: **275**
- Redundant copies (beyond first): **441**

Top duplicate groups:

| Copies | Ext | Example path |
|---|---|---|
| 5 | 4 2 | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Module writing.zip!Module writing/SOVEREIGN_MASTER_v1.4` |
| 4 | md | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Module writing.zip!Module writing/SOVEREIGN_PROGRAM_REPOSITORY_v1.2.0-RC2.zip!SOV_v1.2.0/01_ENGINEERING/SES/SES-011_TERMINOLOGY.md` |
| 4 | md | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Module writing.zip!Module writing/SOVEREIGN_PROGRAM_REPOSITORY_v1.2.0-RC2.zip!SOV_v1.2.0/Bootstrap_Package/05_SESSION/NEXT_ACTIONS.md` |
| 3 | docx | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Module writing.zip!Module writing/WRITING_SYSTEM.zip!WRITING_SYSTEM/ENGINE/NARRATIVEOS_EXECUTION_ENGINE_HARDENED_NEXT3.docx` |
| 3 | docx | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Module writing.zip!Module writing/WRITING_SYSTEM.zip!WRITING_SYSTEM/ENGINE/NARRATIVEOS_EXECUTION_ENGINE_HARDENED_TOP3.docx` |
| 3 | docx | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Module writing.zip!Module writing/WRITING_SYSTEM.zip!WRITING_SYSTEM/RUNTIME/schema_engine_input_output.docx` |
| 3 | docx | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Module writing.zip!Module writing/WRITING_SYSTEM.zip!WRITING_SYSTEM/ENGINE/ENGINE_11_STRUCTURAL_ENFORCEMENT.docx` |
| 3 | docx | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Module writing.zip!Module writing/WRITING_SYSTEM.zip!WRITING_SYSTEM/ENGINE/ENGINE_07_LORE_AUDIT.docx` |
| 3 | docx | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Module writing.zip!Module writing/WRITING_SYSTEM.zip!WRITING_SYSTEM/ENGINE/ENGINE_12_NARRATIVE_PHYSICS.docx` |
| 3 | docx | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Module writing.zip!Module writing/WRITING_SYSTEM.zip!WRITING_SYSTEM/ENGINE/ENGINE_17_THEME_INTEGRITY.docx` |
| 3 | docx | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Module writing.zip!Module writing/WRITING_SYSTEM.zip!WRITING_SYSTEM/ENGINE/ENGINE_16_STORY_MOMENTUM.docx` |
| 3 | docx | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Module writing.zip!Module writing/WRITING_SYSTEM.zip!WRITING_SYSTEM/ENGINE/ENGINE_15_SCENE_PURPOSE.docx` |

## Authority label census

| Label | Occurrences |
|---|---|
| COMPLETE | 6 |
| FINAL | 2 |
| HARDENED | 7 |
| LOCK | 10 |
| MASTER | 37 |
| RC | 248 |
| ULTIMATE | 7 |

**238** system families contain more than one version and therefore raise a supersession question. Every proposal below is a *candidate* that **requires human confirmation** — the system never auto-decides authority.

## Proposed canonical authority per capability

| Capability | Proposed primary source |
|---|---|
| lifecycle_and_gates | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/NARRATIVEOS_v24_4_FINAL_SYSTEM.docx` |
| evaluation | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/FORGE_v3_3_Complete.docx` |
| worker_orchestration | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Unified_Writers_Room_System_v4.1.docx` |
| voice | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/The_Voice_Architect.docx` |
| canon | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Book_Bible.docx` |
| research | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/Ultimate_Biblical_Research.docx` |
| provenance | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/AI_Provenance_Verification_System_v2.0.docx` |
| release | `WRITING_ARCHITECT.zip!WRITING_ARCHITECT/SOVEREIGN_MASTER_v1.4` |

## Disposition tally

| Disposition | Count |
|---|---|
| CANONICAL | 5 |
| DERIVATIVE | 113 |
| DUPLICATE | 441 |
| HISTORICAL | 166 |
| IMPLEMENTATION | 2 |
| PACKAGING | 526 |
| SUPPORTING | 32 |

## What this baseline authorizes

Per the specification, **no integrated "master" document and no drafting work may begin until this baseline is accepted.** Acceptance means a human has reviewed the authority proposals and dispositions above and recorded approval. The next release, WR-01, builds the governed book-domain foundation on top of the accepted authority set.
