# START HERE — Build Handoff for an AI Builder

You are receiving a **governed Book Production Operating System (BPOS)** that is
partially built. Your job is to continue building it **without breaking its
governance**. This document is the single orientation point. Read it fully
before you touch anything.

---

## 0. The one rule that overrides everything

**Do not create another integrated "master prompt."** The entire reason this
package exists is that the archive it came from collapsed under dozens of
competing "master / final / ultimate / complete" prompt documents. The
specification's verdict was: *stop making master prompts; build a stateful,
testable system where the rules are enforced by software.* If at any point your
plan is "write one big prompt that does everything," you have failed. Build the
governed system, one accepted release at a time.

---

## 1. What is in this package

```
START_HERE_AI.md          <- you are here (AI build handoff)
DIRECTIVE.md              <- the governing build directive + live audit findings
README.md                 <- human quick-start
docs/GUIDE.md             <- plain-language, start-to-finish operator guide

spec/
  WRITING_ARCHITECT_Deep_Forensic_Research_and_Implementation_Specification.docx
  spec.md                 <- same specification, converted to Markdown (source of truth)

writing_architect/        <- the working system (WR-00 + WR-01), stdlib-only Python
  forensics/              WR-00: inventory, duplicates, authority, capability_map, baseline
  domain/                 WR-01: schema.sql, db, lifecycle, policy
  cli.py                  the `wa` command
  demo.py                 the spec §14.1 first vertical slice
tests/test_system.py      12 governance/forensics tests (must stay green)

install.sh / install.ps1  installers (POSIX / Windows)
pyproject.toml            packaging + `wa` entry point

wr00_baseline/            <- FORENSIC EVIDENCE produced by running WR-00 on the real archive
  WR00_REPORT.md          human-readable audit report
  baseline_manifest.json  every payload, hash, disposition, authority proposal
  INVENTORY.csv           flat inventory
  DUPLICATES.csv          duplicate groups
  baseline.sha256         self-seal of the manifest

CHECKSUMS.sha256          <- integrity seal over the whole package
```

**Reading order:** this file → `DIRECTIVE.md` → `spec/spec.md` → `docs/GUIDE.md`.
The spec is the source of truth for *what the system must eventually do*. The
DIRECTIVE is the source of truth for *what order to build it in and what is
already done*.

---

## 2. Verify integrity before you trust anything

```bash
# from the package root
sha256sum -c CHECKSUMS.sha256        # every file must report: OK
python3 tests/test_system.py         # expect: 12 passed, 0 failed
```

If either fails, stop and report — do not build on a package that does not
verify.

---

## 3. What is already built (do not rebuild)

**WR-00 — Forensic baseline (DONE).** Read-only recursive fingerprinting of an
archive: expands nested zips (skips Office packages), hashes every payload,
groups duplicates, parses version/authority labels, proposes supersessions
(all flagged `REQUIRES_HUMAN_CONFIRMATION`), classifies disposition, and writes
a self-sealed baseline. It has already been run against the real
`WRITING_ARCHITECT.zip`; the output is in `wr00_baseline/`.

**WR-01 — Governed foundation (DONE).** The canonical data model (24 objects),
the B0–B13 lifecycle with hard gates, the policy engine, and a tamper-evident
hash-chained audit ledger. All enforced by SQLite (schema.sql) + policy.py, not
by any prompt. The 8 minimum constraints from spec §11.3 are live and tested.

**These are load-bearing. Extend them; do not replace them.** Every new release
must keep `python3 tests/test_system.py` green and must add its own tests.

---

## 4. What you build next — the gated plan

Build in this order. **Each release is a hard gate: do not start a release until
the previous one's acceptance criteria pass.** Full detail is in `DIRECTIVE.md`
sections D–E; this is the map.

| Release | Deliverable | Done when |
|---|---|---|
| **WR-02** | Source-tier ingestion (T1–T7) + provenance capture on every source | Every source row has a tier + origin; evidence cannot attach without a source; new tests green |
| **WR-03** | Chapter contracts (spec §6): promise, constraints, canon refs, acceptance tests per chapter | No chapter can advance past B-draft without an *approved* contract; enforced in policy.py |
| **WR-04** | Evidence & canon binding (spec §5, §10): claims ↔ evidence ↔ source location | No factual claim reaches release with an unbound/located evidence gap |
| **WR-05** | Drafting engine interface (spec §7) — *interface only*, human-in-loop; NO autonomous drafting | A draft is always tied to an approved contract + logged actor; refuses otherwise |
| **WR-06** | Editorial passes (spec §8) as discrete, ordered, logged reviewer states | No reviewer closes their own blocker; pass order enforced |
| **WR-07** | Release gates battery (spec §9.2) as a single command | `wa release-check` runs the full battery and blocks on any red |
| **WR-08** | Provenance export + reproducibility bundle (spec §10) | A released chapter can be re-derived from its recorded provenance |
| **WR-09** | Migration/disposition execution (spec §12/§15) using the WR-00 baseline | Supersession actions are applied only after human confirmation, each logged |

---

## 5. Standing prohibitions (enforced, and to keep enforcing)

1. No draft without an **approved chapter contract**.
2. No factual claim accepted without **supporting evidence**.
3. No quotation without an **exact location reference**.
4. No reviewer **closes their own blocker**.
5. No **overwrite of a released artifact**.
6. No lifecycle move without a **named actor and recorded reason**.
7. The audit log is **append-only and hash-chained**.
8. No canonical authority without a **supersession rationale**.
9. No **autonomous drafting** before the human accepts WR-00 (see §6).
10. No new **"master / final / ultimate / complete" mega-prompt.**

If a feature you are asked to add would violate one of these, the answer is to
refuse and surface the conflict — not to weaken the rule.

---

## 6. The human acceptance gate (blocking)

The specification mandates that **nothing downstream proceeds until a human
accepts the WR-00 forensic baseline.** That acceptance is a human action, not
something you perform. Concretely: the archive owner reviews
`wr00_baseline/WR00_REPORT.md` (and the supersession proposals inside
`baseline_manifest.json`, each marked `REQUIRES_HUMAN_CONFIRMATION`) and
confirms it. Until that confirmation exists, you may build WR-02→WR-09
scaffolding and tests, but you may **not** execute any disposition/supersession
against the real archive (that is WR-09) and you may **not** enable autonomous
drafting.

---

## 7. Environment

- **Python 3.9+**, **standard library only.** No third-party dependencies. This
  is deliberate: the system must run offline and install with zero friction.
  Do not add a dependency without an explicit decision recorded in DIRECTIVE.md.
- Storage is **SQLite** for the local-first build. Every construct was chosen to
  port to PostgreSQL later (spec §11.1); see DIRECTIVE.md §G for the migration
  note. Keep new schema Postgres-portable.
- Install: `bash install.sh` (POSIX) or `install.ps1` (Windows). Verify with
  `wa doctor`.

---

## 8. How to work

1. Pick the lowest un-built release from §4.
2. Read its spec sections (linked in DIRECTIVE.md §E) in `spec/spec.md`.
3. Write the schema/policy/code additions **and their tests** together.
4. Run `python3 tests/test_system.py` — all prior tests plus yours must pass.
5. Update `DIRECTIVE.md` with what you built and re-seal `CHECKSUMS.sha256`.
6. Stop at the gate. Do not run ahead.

That is the whole job: extend the governed system, release by release, keeping
every rule enforced by software.
