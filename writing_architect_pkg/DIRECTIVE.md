# WRITING_ARCHITECT — Develop & Implement Directive

**Status:** WR-00 implemented and executed against the live archive. WR-01
governed foundation implemented, tested (12/12), and installable. WR-00
accepted by Brian Fochtman on 2026-08-02 (`wr00_baseline/WR00_ACCEPTANCE.md`).
WR-02 research & evidence implemented, tested (16/16 WR-02 + 12/12 WR-01
regression = **28/28 total**), and exit-condition chain verified.
**Authority:** This directive governs the build. It supersedes every "master,"
"final," "ultimate," and "complete" document in the archive for the purpose of
*what gets built next*. Those documents remain **source doctrine**, not build
authority.
**Prime rule:** Do not create another integrated "master" prompt. Build the
governed system, one accepted release at a time.

---

## A. What this directive is answering

Two requests, both discharged here:

1. **Forensic audit** — performed by running the WR-00 engine against the real
   `WRITING_ARCHITECT.zip`, not by restating the specification. Results in
   Section B.
2. **Develop & implement the build directive** — the executable plan for the
   whole system, with WR-00 and the WR-01 foundation already built and the
   remaining releases specified as gated, testable work. Sections C–E.

---

## B. Forensic audit — findings from the live run

Run command (reproducible):
```
wa forensics WRITING_ARCHITECT.zip --out wr00_baseline
```

### B.1 Measured reality (read-only)

| Measure | Spec's stated figure | This run (live bytes) |
|---|---|---|
| Distinct SHA-256 payloads | 318 | **318** ✅ exact match |
| Payload files analyzed | 485 non-metadata | 759 |
| DOCX files | 322 | 470 |
| Exact duplicate groups | 164 | **275** |
| Redundant copies (beyond first) | — | **441** |
| Nested containers expanded | 5 | **7** |
| Packaging/metadata records | — | 526 |
| Read errors | — | **0** |
| Source archive SHA-256 | — | `e6802b7891e60483b28b96d3c56f36cc331acf8233fb3760bbd3a76dd7cdcbd0` |

**Reconciliation and finding.** The distinct-payload count matches the spec
exactly (318), which validates payload identification on both sides. The higher
duplicate and DOCX counts are not a contradiction — they are a *sharper*
finding: the earlier analysis did not fully expand the triple-nested
`SOVEREIGN_PROGRAM_REPOSITORY_v1.2.0-RC2.zip` nor the `_EXPANDED` mirror trees
inside `WRITING_SYSTEM.zip`. The real physical redundancy is therefore **worse**
than headline: **441 redundant copies** of the same 318 unique payloads. This
strengthens, not weakens, the spec's core verdict of duplicate divergence risk
(FM-02).

### B.2 Authority collapse, quantified

Authority-bearing labels counted in the live tree:

| Label | Occurrences |
|---|---|
| RC | 248 |
| MASTER | 37 |
| LOCK | 10 |
| HARDENED | 7 |
| ULTIMATE | 7 |
| COMPLETE | 6 |
| FINAL | 2 |

**238** system families contain more than one version. No file carries a
machine-checkable acceptance signature. This is FM-01 (authority ambiguity),
confirmed at scale.

### B.3 Capability authority — tool agrees with the analyst

The WR-00 capability mapper, run independently, proposed the **same** primary
source for all eight capabilities that the specification named by hand in §12.2:

| Capability | Proposed canonical primary (live run) |
|---|---|
| Lifecycle & gates | `NARRATIVEOS_v24_4_FINAL_SYSTEM.docx` |
| Evaluation | `FORGE_v3_3_Complete.docx` |
| Worker orchestration | `Unified_Writers_Room_System_v4.1.docx` |
| Voice | `The_Voice_Architect.docx` |
| Canon | `Book_Bible.docx` |
| Research | `Ultimate_Biblical_Research.docx` |
| Provenance | `AI_Provenance_Verification_System_v2.0.docx` |
| Release | `SOVEREIGN_MASTER_v1.4` |

Independent agreement between a heuristic tool and the human analyst is the
audit's strongest signal that these eight are the correct canonical seeds.

### B.4 Disposition of the archive (every file classified)

| Disposition | Count | Meaning |
|---|---|---|
| PACKAGING | 526 | macOS resource-fork / archive metadata — not content |
| DUPLICATE | 441 | byte-identical to a kept copy |
| HISTORICAL | 166 | superseded version within its family |
| DERIVATIVE | 113 | generated/packaged copy in a nested / `_EXPANDED` tree |
| SUPPORTING | 32 | doctrine/examples, not governing |
| CANONICAL | 5 | highest-version candidate in its family |
| IMPLEMENTATION | 2 | code/schema artifacts (kept copies) |

Payload total 759, all-records total 1285 — both reconcile exactly.

### B.5 Audit verdict

**ACCEPT AS SOURCE CORPUS; REJECT AS PRODUCTION SYSTEM** — confirmed against the
live bytes. The eight canonical seeds are sound. The archive cannot run
autonomously as-is: authority is ambiguous, redundancy is heavier than
previously counted, and enforcement is prose-only. The remedy is the governed
system in Section C.

---

## C. What has been built (WR-00 + WR-01 + WR-02)

| Component | Spec anchor | State | Evidence |
|---|---|---|---|
| Recursive read-only forensic inventory | 12.1 | Done | `forensics/inventory.py`, 0 read errors on live run |
| SHA-256 duplicate analysis | forensic scope | Done | `forensics/duplicates.py`, 275 groups found |
| Authority/version graph (proposal-only) | 1.1 | Done | `forensics/authority.py` |
| Capability map + disposition classifier | 12.2, 15 | Done | reproduces §12.2 exactly |
| Self-sealed baseline artifact | 14 (WR-00) | Done | `baseline.sha256` verifies |
| Canonical data model (24 objects) | 4 | Done | `domain/schema.sql` |
| B0–B13 lifecycle + no-skip / upstream-return rule | 3.2 | Done | `domain/lifecycle.py` |
| Policy engine (entry gates + release battery) | 9.2, 11.3 | Done | `domain/policy.py` |
| 8 minimum DB constraints | 11.3 | Done | 6 as triggers/CHECKs, 2 as policy gates |
| Tamper-evident append-only audit ledger | 10 | Done | `domain/db.py`, hash-chained |
| CLI + installer + plain-language guide | 11.1 UI | Done | `install.sh`, `install.ps1`, `docs/GUIDE.md` |
| Test suite (WR-01) | 13 | Done | `tests/test_system.py` — 12/12 pass |
| WR-02 research intake CLI | 5.1–5.3 | Done | `wa question/source/claim/evidence/verify/accept-claim/conflict/research-status` |
| T6/T7 intake block | 5.1 | Done | `policy.check_source_tier_admissible()` — refused before any DB write |
| 9-point claim acceptance gate | 5.3 | Done | `policy.check_claim_acceptance_gate()` — pre-flight + DB trigger |
| Canonical seed loading | WR-02 | Done | `wa seed-sources` — idempotent, audit-logged |
| Exit-condition demo | WR-02 | Done | `wa demo-wr02` — full chain verified, audit intact |
| Test suite (WR-02) | 13 | Done | `tests/test_wr02.py` — 16/16 pass; 28/28 total |

**Minimum-constraint coverage (spec 11.3):**

| # | Constraint | Enforced by |
|---|---|---|
| 1 | No DraftUnit without approved ChapterContract | trigger `trg_draft_requires_approved_contract` |
| 2 | No factual Claim accepted without EvidenceUnit | trigger `trg_claim_accept_requires_evidence` |
| 3 | No ReleaseCandidate with an open blocker | policy `check_release_gates` |
| 4 | No transition without authorized actor + record | `lifecycle._transition` + `lifecycle_transition` table |
| 5 | No overwrite of a released artifact | triggers `trg_no_overwrite_released_*` |
| 6 | No quotation without edition/location metadata | trigger `trg_evidence_requires_location` + NOT NULL |
| 7 | No worker approves its own blocking finding | CHECK on `editorial_finding` |
| 8 | No authority designation without supersession rationale | CHECK on `edition` |

All eight are demonstrated firing in `wa demo` and asserted in tests.

---

## D. Acceptance gate for WR-00 (human action required)

Per the spec, **nothing downstream may begin until WR-00 is accepted.**
Acceptance is a human decision recorded against the baseline. To accept:

1. Open `wr00_baseline/WR00_REPORT.md` and read Sections B.2–B.4.
2. Confirm the eight canonical seeds in B.3 are correct, or override any you
   disagree with (the tool marks all as `REQUIRES_HUMAN_CONFIRMATION`).
3. Confirm the source archive SHA-256 matches what you shipped.
4. Record the decision. Until WR-02 tooling exists, record it by keeping the
   sealed `baseline.sha256` alongside a short signed note:
   *"WR-00 baseline <seal> accepted by Brian Fochtman on <date>; canonical seeds
   as listed, with these overrides: <none|list>."*

**Do not build WR-02 until this note exists.** That is the gate.

---

## E. Executable build order for WR-02 → WR-09

Each release below has an **entry gate** (must be true to start), **build
tasks**, and an **exit artifact** (must exist to finish). No release starts
until the prior release's exit artifact is accepted. This mirrors the lifecycle
discipline the system itself enforces.

### WR-02 — Research & evidence ✅ DONE (2026-08-02)

- **Entry gate:** WR-00 accepted. `wr00_baseline/WR00_ACCEPTANCE.md` signed.
- **Built:**
  - `wa seed-sources` — loads 8 canonical seeds as `source_artifact` rows
  - `wa question` — create `research_question` rows
  - `wa source` — register bibliographic sources; T6 and T7 **blocked at intake**
    by `policy.check_source_tier_admissible()` (spec 5.1)
  - `wa claim` — create unaccepted candidate claims
  - `wa evidence` — attach evidence units with required location reference
  - `wa verify` — record independent review (spec 8.1)
  - `wa accept-claim` — 9-point pre-flight gate (spec 5.3) before DB update
  - `wa conflict` — record competing claims
  - `wa research-status` — full research chain summary
  - `wa demo-wr02` — seeds the exit-condition chain for *Ash and Silence*
  - `policy.check_source_tier_admissible()` and `policy.check_claim_acceptance_gate()`
  - `tests/test_wr02.py` — 16 tests; all 28 tests (WR-01 + WR-02) pass
- **Exit condition verified:** `wa demo-wr02` produces a complete
  question → T2-source → fact-claim → evidence → verifier → accepted-claim
  chain that passes all nine gate checks. Audit chain intact after run.

### WR-03 — Canon & continuity
- **Entry:** WR-02 exit accepted.
- **Build:** `canon_entity` / `canon_fact` / `timeline_event` population from
  Book Bible; continuity validators (age/date conflict, impossible travel,
  knowledge leak, name drift, object resurrection) as deterministic checks that
  raise `editorial_finding` rows.
- **Exit:** the five continuity validators each catch a seeded defect fixture
  (spec 13.2 continuity family) with 100% recall.

### WR-04 — Architecture
- **Entry:** WR-03 exit accepted.
- **Build:** plan-tree commands (`wa plan add/approve`), chapter/scene contract
  authoring, change-request-with-impact-analysis flow (spec 6.1: no silent edit
  of an approved ancestor).
- **Exit:** an approved plan tree and at least one approved `chapter_contract`
  with an attached evidence packet for *Ash and Silence*.

### WR-05 — Drafting vertical slice
- **Entry:** WR-04 exit accepted **and** a local OpenAI-compatible model endpoint
  is configured (spec 11.1 model service).
- **Build:** the bounded generation loop (spec 7.2) behind a `Drafter` interface;
  deterministic post-checks (required names/dates, forbidden facts, length,
  quotation integrity); a critic pass that returns *findings, not rewrites*;
  provenance `generation_event` recorded per run.
- **Exit:** the spec's first vertical slice (14.1) runs on a real chapter and
  produces a reviewed DOCX with a provenance map and an approval record. The
  `wa demo` skeleton already exercises the governance path; WR-05 replaces its
  placeholder prose with real bounded generation.

### WR-06 — Editorial passes
- **Entry:** WR-05 exit accepted.
- **Build:** developmental, continuity, factual, line, and copy passes as
  independent workers, each emitting `editorial_finding` rows; FORGE's 18
  dimensions converted to atomic `evaluation_observation` rows with confidence
  (spec 8.2) — **not** a single composite grade.
- **Exit:** on a seeded manuscript, known-defect recall meets the qualification
  threshold and no worker can close its own blocker (already enforced).

### WR-07 — Document production
- **Entry:** WR-06 exit accepted.
- **Build:** DOCX export with styles, comments, tracked changes; render-and-proof
  QA (convert to PDF, rasterize, verify TOC/headings/page breaks).
- **Exit:** document test family (spec 13.2) passes on the benchmark manuscript.

### WR-08 — Benchmark & qualification
- **Entry:** WR-07 exit accepted.
- **Build:** the rights-cleared benchmark corpus with oracles (spec 13.1);
  schema/retrieval/research/continuity/drafting/voice/editorial/document/
  recovery/security test families (13.2); calibration and inter-rater agreement.
- **Exit:** acceptance thresholds in 13.3 met — 100% seeded-blocker recall,
  zero unauthorized authority transitions, zero fabricated source identifiers,
  reproducible release artifacts.

### WR-09 — 24/7 governed operation
- **Entry:** WR-08 exit accepted.
- **Build:** queues, schedules, per-worker budgets, resumable long-running
  workflow (LangGraph→Temporal path, spec 11.1), alerts, and *safe* autonomous
  research bounded by the same gates.
- **Exit:** the system runs unattended without a single ungated state change,
  proven by the security and recovery test families over a sustained run.

---

## F. Standing prohibitions (never relax)

1. No new "master/final/ultimate/complete" prose document as build authority.
2. No autonomous drafting before WR-00 acceptance and a configured, sandboxed
   model endpoint.
3. No claim of "flawless." The system's release claim is bounded: *no known
   blocking defect, every gate has evidence, an accountable human approved.*
4. No provenance claim of definitive AI detection from prose alone; record
   process lineage instead (spec 10).
5. No deletion of archive originals during migration; disposition is metadata
   until a sealed snapshot proves each duplicate byte-identical (spec 12.3).

---

## G. Migration note (stack)

The foundation ships on SQLite for a zero-dependency, sovereign, offline local
build. Every construct — foreign keys, CHECK constraints, triggers, the
append-only ledger — was chosen to port to the spec's recommended PostgreSQL
target without redesign: triggers become `BEFORE` row triggers or exclusion
constraints, and the hash-chained `audit_log` maps to an append-only table with
the same chaining. Move to PostgreSQL at WR-08/09 when concurrency and
long-running workflows justify it; not before.

*End of directive.*
