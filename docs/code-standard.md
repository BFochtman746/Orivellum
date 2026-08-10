# Orivellum Code Standard — Research & Adoption Plan

**Date:** 2026-08-10 · **Purpose:** define an enforceable code standard so everything
this system creates is clean, traceable, tested, and workable by any developer —
and audit the current repo against it.

## Calibration first: what "highest standard" actually means here

The pasted "Optimal Build Standard" is written for a multi-team, internet-facing,
distributed service. Orivellum is a **local-first, single-operator forensic tool**.
Adopting the elite list verbatim would burn months on gates that protect against
risks this system does not have, while the real risks (untested logic, silent
exception swallowing, 6,000-line files) go unaddressed. Current industry practice
(2025–26) is explicit about this: standards are only credible when **every rule is
machine-enforced in CI** — a rule that relies on humans remembering it is a wish,
not a standard.

**Adopted as-is:** clean-code gates, complexity caps, formatter in CI, explicit
error handling, ADRs, OpenAPI drift check, coverage ratchet, property-based tests,
secret scanning, dependency audit, SAST, architecture fitness functions, commit
traceability, threat model, structured audit logs.

**Adapted:** function length/complexity thresholds (≤5 cyclomatic complexity and
≤20 lines rejects half of all idiomatic real-world code; industry consensus gates
at complexity 10), mutation testing (scoped to critical modules, not repo-wide —
whole-repo mutation on 100k+ LOC takes days), 100% branch coverage (ratchet
upward instead; 100% breeds assertion-free coverage theater).

**Rejected as not applicable:** chaos engineering, WAF/RASP, canary deploys,
distributed tracing, Prometheus dashboards, Pact contract testing between
services (there is one service), memory-safe-language migration (Python/TS
already are), "no tech debt older than one sprint" (there are no sprints).

---

## Where the repo stands today

Already in place — a real foundation:
- CI as code: ruff (errors-only), pytest with a **coverage ratchet at 54%** (never
  lowered, only raised — this is the single best pattern already in the repo),
  UI typecheck, `pnpm audit --prod`, runner test job
- 99 backend test files + Playwright e2e suite (not CI-gated)
- Hash-chained audit ledger, provenance on generated artifacts
- CONTRIBUTING.md, hand-maintained OpenAPI spec driving generated clients

The sloppiness signals the user is worried about, measured:
- **`db.py` is 6,384 lines**; four more backend files exceed 2,000; five UI pages
  exceed 2,000 (worst: 4,236)
- **763 `except Exception` sites**, ~294 of them swallow-and-continue shaped
- Ruff configured for `E,F,I,UP` but **CI only enforces `F,E9`** — the standard
  exists on paper and is not enforced (exactly the failure mode to eliminate)
- No formatter gate, no Python type checking, no secret scan, no pip-audit, no
  ADRs, no OpenAPI drift check, TS `strict` umbrella off

---

## The standard (each rule = a CI gate)

### 1. Clean code — enforced by ruff, not by review comments
- `ruff format --check` (the Black-compatible formatter) gates CI; no style debate
  ever again.
- Rule set widened from `F,E9` to: `E, F, I, UP, B` (bugbear), `C90` (complexity),
  `SIM` (simplify), `RET`, `PL` subset. **Complexity cap: C901 = 10.** New code
  complies immediately; existing violations go into a frozen baseline file that
  only shrinks (same ratchet philosophy as coverage).
- **File-size budget:** no file grows past 1,500 lines without an ADR; the five
  current giants get split when next touched ("boy-scout rule", enforced by a
  small CI script comparing against a checked-in baseline).
- **Exception policy:** `except Exception` is legal only with (a) a log call and
  (b) a comment stating why continuing is safe; bare `pass` swallowing is a CI
  failure (ruff `S110`/`BLE001` on new code via baseline).

### 2. Documentation — living, or it doesn't count
- **ADRs in `docs/adr/` using MADR format** (the 2025-era lightweight consensus:
  context → decision → consequences, one file per decision, numbered). Seed it
  by converting the existing memory/replit.md architecture decisions. New rule:
  any change that would surprise a future maintainer ships with an ADR.
- **OpenAPI drift gate:** CI boots the FastAPI app, dumps its generated schema,
  and diffs the paths/operations against `lib/api-spec/openapi.yaml`. Hand-edited
  spec + no drift check is how contracts rot.
- README must take a new machine from clone → running app + tests in under 10
  minutes; verified once per quarter by actually doing it.

### 3. Testing — proof, proportional to criticality
- Coverage ratchet continues (54% → raise by 1–2 points per feature merge; target
  ≥ 75% within a quarter). Branch coverage (`--cov-branch`) turned on so the
  number means decisions, not lines.
- **Property-based tests (Hypothesis)** for the pure-algorithm cores where they
  pay off most: state machine transitions, dedup/MinHash, xlsx engine
  compare/serial-date math, chunking, hash-chain ledger. These are the modules
  where a single wrong branch silently corrupts results.
- **Mutation testing (mutmut), scoped:** run nightly (not per-push) against the
  3–5 forensically critical modules only, using `mutate_only_covered_lines`;
  report score, gate at ≥ 80% for those modules once baselined. This is the
  2026-standard pragmatic adoption — whole-repo mutation gating is vanity.
- Playwright e2e: promote the existing suite into CI on a schedule (nightly) —
  it exists and runs nowhere, which is wasted proof.

### 4. Static analysis & quality gates
- **Secret scanning: gitleaks** in CI (fails the build) + as a pre-commit hook.
- **Dependency audit: `pip-audit`** joins the existing `pnpm audit --prod`; block
  on known vulns with fix available, CVSS ≥ 7.
- **SAST: bandit** (Python security ruleset) gated on medium+ severity, and the
  repo's existing security-scan capability run before releases.
- **Architecture fitness: import-linter** with layer contracts —
  `api.routes → capabilities → database`, never the reverse; capabilities may
  not import from routes; `database/db.py` may not import capabilities. Six
  lines of config that permanently prevents spaghetti.

### 5. Build, traceability & provenance
- Every artifact/report the system emits already records provenance + SHA-256;
  extend the stamp with the **git commit SHA** so any output traces to exact
  source in seconds (`git describe --always --dirty` at startup, included in
  `/api/system` info and generated-document metadata).
- Toolchain pinning: `packageManager` is already pinned; add `.python-version`
  and pin `uv` in CI so builds are machine-independent.
- Deterministic/signed *documents* are covered by the companion plan in
  `docs/forensic-publication-readiness.md` (P2/P3 there).

### 6. Security & forensic integrity
- `threat_model.md` written and kept current (the platform has a structured
  threat-modeling flow for this).
- Secrets already live outside code — the gitleaks gate turns that habit into a
  guarantee.
- The hash-chained ledger already provides structured, tamper-evident audit
  events; ADR to document its guarantees and non-goals.

### 7. Observability (right-sized)
- Structured JSON logs with a request/correlation ID on the API path — already
  partially present via the access log; standardize the logger so every error
  path carries the request ID. Prometheus/tracing/SLO stacks: not until this
  runs for more than one operator.

### 8. Maintainability
- TODOs must reference an issue/task or they fail lint (`FIX` ruleset, new code).
- Monthly dependency-refresh session gated by the full test suite (the repo's
  own CI makes this safe); no automation theater beyond that.
- Churn watch: the five giant files above are the redesign queue — split on
  touch, never big-bang rewrite.

---

## Adoption plan (each phase leaves CI green)

| Phase | Work | Outcome |
| --- | --- | --- |
| 1 | `ruff format --check` + widened rule set with frozen baseline; exception-policy rules on new code | style/complexity enforced forever, zero review nitpicks |
| 2 | gitleaks + pip-audit + bandit jobs in CI | supply-chain + secret guarantees |
| 3 | import-linter contracts + file-size budget script | architecture can't silently rot |
| 4 | ADR directory seeded (~10 backfilled decisions) + MADR template + CONTRIBUTING update | decisions traceable |
| 5 | OpenAPI drift gate | contract honesty |
| 6 | Hypothesis suites for the 5 critical cores; branch coverage on; ratchet plan | proof where it matters |
| 7 | Nightly job: mutmut on critical modules + Playwright e2e | test-quality measurement |
| 8 | Commit-SHA provenance stamp in system info + generated documents | one-minute artifact→source traceability |

**Score against the adopted standard today: ~35/100** (strong CI skeleton,
ratchet culture, and audit ledger; missing enforcement breadth, typing, ADRs,
security gates). Phases 1–3 are one focused session and lift it past 60; the
full plan is realistically 3–4 sessions.
