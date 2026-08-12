---
name: Runner code auditor
description: Rebuilt code job — scanner orchestration, doctrine checks, SARIF; durable rules for extending it.
---

# Runner code auditor (orivellum-runner code job)

- Three detection layers: orchestrated scanners (`code_tools`), doctrine checks (`code_doctrine`), and the kept bespoke pair (SECRETS regexes + injection shield). The RISKY_PY bespoke pattern list is retired — do not resurrect regex clones of what ruff (S/BLE) and bandit already do.
- **Fail-closed applies to the auditor itself:** a scanner that cannot run must become a TOOL-GAP finding ("unexamined, not clean"), never a silent skip. Per-tool output is capped with an explicit overflow disclosure finding.
- Every rule in `orivellum-runner/PROGRAMMING_DOCTRINE.md` names its enforcing check code; adding a rule without a check (or vice versa) breaks the contract.
- **Why:** the August 2026 hand-audits kept finding the same classes (fail-open gates, None-as-pass, unwired public functions, self-referential percentage gates) that no off-the-shelf tool flags — the doctrine checks exist to catch exactly those, with file:line evidence, deterministically (the model never votes).
- **How to apply:** new defect classes go in `code_doctrine.py` as pure AST/text functions testable without binaries; scanner parsers in `code_tools.py` are split from runners (normalize_* take canned output) so tests never need the tools installed. SARIF emission lives in `report.write_sarif` and runs on every `report.write`.
- Substring traps: name-based reference scans must use word boundaries AND tests must exact-match refs (`"usedWidget" in "unusedWidget"` is true).
- The runner also targets the user's Windows machine — never assume ruff/bandit/mypy/tsc/eslint exist; `missing_tools()` disclosure at plan time is mandatory.
