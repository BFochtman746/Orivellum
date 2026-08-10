---
name: Orivellum Runner (xlsx prove pipeline)
description: Standalone batch harness at orivellum-runner/; Excel rules v2 — surgery-only writes, six proof gates, built test manifests.
---

# Orivellum Runner

Standalone budget-bounded analysis harness at `orivellum-runner/` (own tests, own
CI step, not part of `src/orivellum`). Runs on the user's Windows box against
their workbooks with Lemonade (`MOCK=0`); mock mode must stay fully functional.
CLI: `python -m runner run --job code|xlsx --target X`, `resume`, `verify`.

## Excel rules v2 (replaced the read-only doctrine)
**Rule:** the runner may write workbooks, but ONLY via zip-level XML surgery
(`xlsx_surgery.py`) — never an openpyxl round-trip on a deliverable (strips
extlink caches/VBA). Output `runs/<id>/PROVEN_<name>.xlsx` is emitted only when
all six gates pass (recalc coverage, values match, no error cells, OOXML order,
byte-diff containment, loads clean); engine unavailable ⇒ UNVERIFIED, never
"clean"; failed proof ships nothing. Semantic fixes stay proposals. Volatile/
dynamic-fn doctrine findings downgrade to PROVEN WITH WARNINGS, don't block.
**Why:** user's explicit instruction — "a complete system that builds tests and
returns a fully completely tested and proven workbook" — plus their iOS Excel
Mobile OOXML-order requirement.

## Hard-won lessons
- Recalc engine is the pure-Python `formulas` package (pin 1.3.4 — 1.3.5 does
  not exist). Emits SyntaxWarnings on import; wrapper suppresses.
- Certification safety: write to a `.candidate_` path, run every gate, then
  `Path.replace()` to the PROVEN name; `finally` unlinks the candidate. Never
  let a crash leave a file that looks certified.
- Comparison must be strict: missing cached value = mismatch; bool never equals
  a number. Looseness there certifies broken workbooks.
- XML surgery refuses what it can't prove: top-level tokenizer over direct
  `<worksheet>` children; unknown elements/comments ⇒ return input unchanged,
  gate fails honestly. Skip cells with `t="s"`/`t="inlineStr"` in cache refresh.
- Regex tokenizer trap: an attribute char-class `[^>"']*` swallows the trailing
  `/` of self-closing tags — detect self-close from the raw token text.
- Test manifests store datetimes as Excel serial numbers (one type system for
  re-runs); manifest verify re-runs structural checks too, not just values.
- `store.next_unit` claims atomically (queued→running); resume requeues
  stranded 'running' units.
- openpyxl-created workbooks have no cached values — that's the "never
  calculated" repair path.
- Runner tests: `cd orivellum-runner && uv run --with pytest --with openpyxl
  --with 'formulas==1.3.4' --with httpx python -m pytest tests/ -q`.
