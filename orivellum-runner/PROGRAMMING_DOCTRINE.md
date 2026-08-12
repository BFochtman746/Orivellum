# PROGRAMMING DOCTRINE

The rules this platform's audits keep finding broken by hand. Each rule names
the check that enforces it — a rule without a check is a wish, and a check
without a rule is noise. The code job (`runner/jobs/code.py`) enforces every
rule below on every run; generic security patterns are delegated to mature
scanners (rule D7) so the bespoke effort goes where nothing off the shelf
looks.

The division of labour is unchanged and absolute: **deterministic tools FIND,
the model EXPLAINS.** Every finding below carries file:line evidence a human
can check without trusting anyone.

---

## D1 — Gates fail CLOSED

A verification, permission, or quality gate that cannot run must block, not
pass. An `except` clause that returns an approval value (`True`, a nonzero
number, a non-empty string) converts every future bug into silent approval.
`return None` from a gate is the same defect wearing a coat: callers using
truthiness or `is not None` read it however they need to.

- **Check:** `DOCTRINE-FAILOPEN` — AST walk of every `except` handler.
  Returning a truthy constant from any handler is HIGH; returning
  `None`/bare `return`, or a pass-only handler, inside a gate-named function
  (verify/check/allow/valid/auth/…) is MEDIUM.
- **Origin:** the book-pipeline gate `except Exception: return None # skip
  gate rather than block` — found by hand, August 2026.

## D2 — No self-referential denominators

A percentage used in a gate must have a denominator independent of its
numerator's filter. "95% of the items we counted are present" is always true
when the counter and the checker share a filter; the gate then measures
itself. Every percentage compared against a threshold in gate logic is a
suspect until its denominator is traced.

- **Check:** `DOCTRINE-PCTGATE` — every comparison of a percentage-named
  value (`*_pct`, `*percent*`, `*coverage*`, `*ratio*`, `*rate*`) against a
  numeric threshold inside branch logic that returns or raises. Each finding
  demands the denominator's origin be named.
- **Origin:** the coverage estimate whose denominator was the set of things
  already found — found by hand, August 2026.

## D3 — No unwired public functions

A public function nothing imports or calls is not a feature, it is a claim.
Five instances of "built with no wire" were found in one week: code written,
tested by nobody, reachable by nothing, reported as done.

- **Check:** `DOCTRINE-NOCALLER` — module-level public functions (Python)
  and exported symbols (TS/JS) whose name appears in no other file in the
  repository. Framework-decorated functions are exempt (the framework is the
  caller).

## D4 — Security controls ON by default

A control that ships disabled protects the demo, not the user. TLS
verification, authentication, sandboxing, quarantine, signing — if the
environment variable or parameter default turns it off, the safe path is the
one nobody takes.

- **Check:** `DOCTRINE-DEFAULTOFF` — env-var reads and parameter defaults
  where a security-named setting defaults to a falsy value.

## D5 — Every function is NAMED in a test

Name-mention beats line coverage: line coverage rewards incidental
execution; a test that names a function encodes intent to test it. 61 of 100
functions never named in any test is a floor collapse, not a statistic.

- **Check:** `DOCTRINE-TESTGAP` — per-function coverage-by-name. The worst
  offenders (largest untested public functions) are individually named
  findings, not a count. The absence of any test files at all is `NOTESTS`
  (HIGH). CI floor enforcement for security modules lives outside this job;
  this job supplies the per-function signal.

## D6 — Every environment variable is documented

Configuration the code reads but no document names is drift by construction:
it cannot be set correctly by anyone who did not read the source.

- **Check:** `DOCTRINE-ENVDOC` — every env var read anywhere (Python
  `os.getenv`/`os.environ`, TS/JS `process.env`/`import.meta.env`), each as
  its own finding naming the file that reads it, minus those documented in
  README / `.env.example` / docs.

## D7 — No silent exception swallows; generic patterns are DELEGATED

Bare `except:`, `except Exception: pass`, blind exception logging — and the
whole catalogue of eval/exec/shell/pickle/yaml/TLS patterns — are mature-
scanner territory. This job runs the scanners and normalises their output
instead of reimplementing a thinner version of them:

- **Python:** `ruff` (E9 syntax, F correctness, B bugbear, S security,
  BLE blind-except), `bandit` (B110/B112 swallow classes and the rest),
  `mypy` (type contract violations).
- **TypeScript/JS:** `tsc --noEmit` (type errors), `eslint` when the target
  repo carries a config.
- A scanner that is unavailable is a `TOOL-GAP` finding: that defect class
  is **unexamined, not clean**. This is D1 applied to the auditor itself.

## D8 — Findings speak SARIF

Every run emits `findings.sarif` alongside the report so results render in
editors, CI, and code-review surfaces, and merge with any other tool's
output. A finding that renders nowhere changes nothing.
