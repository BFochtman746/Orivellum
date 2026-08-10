# Orivellum Runner

Assign a task. Walk away. Read the report.

    python -m runner run --job xlsx --target "GDLS_TIME_KEEPING.xlsx"
    python -m runner run --job code --target Orivellum-main.zip
    python -m runner resume --run 3          # picks up mid-run
    python -m runner report --run 3

Press **Run** on Replit and it analyses a demo workbook built with five
deliberate defects, so you can see real findings before pointing it at anything
of yours.

## It never asks you to continue

The loop is code, not conversation. A run ends for exactly one of three
reasons, and the report says which: every unit processed, a hard budget
reached, or an unrecoverable error. Budgets are `MAX_UNITS`, `MAX_MINUTES`,
`MAX_TOKENS` — and `resume` continues from the checkpoint, because the
checkpoint database records which units ran, what came back, and what runs
next.

Context stays flat: one unit is one function or one sheet, each handled by a
sub-agent with a clean context that returns a short digest. The token budget is
deliberately capped **below** the model's real window, because an agent that
can see its own ceiling starts summarising early and abandoning work.

## The division of labour

**Deterministic tools find things. The model explains them.** Never the
reverse. LLM detection has weak inter-statement reasoning, so it is the wrong
instrument for dataflow; the AST is right, and it is checkable. Everything you
see in `MOCK=1` came from the AST, a regex, a cached value or raw XML — which
is why mock mode is genuinely useful rather than a demo.

## `--job code`

- Unzips, filtering `node_modules`, `.venv`, build output, binaries, pytest fixtures
- Real Python AST: one record per function or method, with qualified name, line
  span, args, decorators, docstring, and a call graph
- Signature scan for js/ts/ps1/sh/java/cs/go
- Pattern findings: `eval`, `exec`, `shell=True`, `os.system`, `pickle`,
  unsafe `yaml.load`, `verify=False`, bare `except`, hardcoded credentials
- Runs **bandit** and optionally **semgrep** when installed; when absent it says
  `UNAVAILABLE`, never "clean"
- Computes what is missing: functions never named in a test, entry points and
  dead code from the call graph, env vars read but undocumented
- Per-function digest: purpose, what it trusts, failure modes, unvalidated
  inputs, hardening suggestions

## `--job xlsx` — build, test, and return a PROVEN workbook

The old read-only doctrine is retired. What replaces it is stricter, not
looser — three rules:

1. **Surgery only, never a round-trip.** The runner may now write a workbook,
   but only by editing the exact bytes inside the zip. openpyxl's writer
   strips external-link caches, VBA and parts it does not model, so a library
   save on a deliverable is still forbidden. A byte-level parts diff proves
   surgery touched only what it claimed.
2. **Nothing ships unproven.** Every formula is recomputed by a real
   recalculation engine (the pure-Python `formulas` package) and compared
   against the value saved in the file. Six gates must all pass before
   `runs/<id>/PROVEN_<name>.xlsx` is emitted — otherwise **no file is
   returned** and the report names the failed gate. If the engine is missing
   or cannot compute the workbook, the verdict is **UNVERIFIED**, never
   "clean".
3. **Every run builds tests.** `runs/<id>/workbook_tests.json` holds one test
   case per formula cell with its engine-computed expected value. Re-run them
   after your own edits, any time:

       python -m runner verify --target FILE.xlsx --tests workbook_tests.json

What surgery may do is mechanical and re-checkable: reorder `<worksheet>`
children into the OOXML sequence iOS Excel enforces, and refresh stale or
missing cached values with recomputed ones. Semantic edits — changing a
formula, extending a range — stay proposals with cell addresses. A machine
that silently rewrites your formulas is not a verifier.

The proof gates:

| Gate | Proves |
| --- | --- |
| G1 | the recalculation ran and covered every formula cell |
| G2 | every computed value equals the saved value |
| G3 | zero error values (`#REF!` …) saved in the file |
| G4 | OOXML child order clean in every sheet part |
| G5 | surgery byte-diff limited to the declared sheet parts |
| G6 | the output loads cleanly, formulas and values both |

Doctrine findings (volatile / dynamic functions) do not block the gates — they
are design rules, not correctness — but they downgrade the verdict to
**PROVEN WITH WARNINGS** and are listed on the certificate.

Detection still runs first, and still reads the workbook twice — formulas
once, Excel's cached values once:

| Check | What it catches |
| --- | --- |
| `XL-ERRCELL` | `#REF!` `#VALUE!` `#DIV/0!` `#NAME?` saved in the file |
| `XL-SUMMISMATCH` | a SUM whose cached total disagrees with its own range — the error that reports wrong numbers while looking fine |
| `XL-SHORTRANGE` | a total that stops one row short of live data — **self-consistent, so no value check can ever catch it** |
| `XL-INCONSISTENT` | one edited formula inside a row *or column* of identical ones |
| `XL-VOLATILE` | `NOW` `TODAY` `INDIRECT` `OFFSET` `RAND` — against your zero-volatile doctrine |
| `XL-IOS-DYNAMIC` | `XLOOKUP` `FILTER` `LET` `UNIQUE` `SORT` `VSTACK` — ruled out for iOS-bound workbooks |
| `XL-OOXML-ORDER` | child-element order read from raw sheet XML: `sheetData → mergeCells → conditionalFormatting → dataValidations → pageMargins`. iOS Excel Mobile enforces it; desktop Excel and LibreOffice forgive it silently |
| `XL-EXTLINK` / `XL-EXTPARTS` | references to another workbook, whose cached values a library round-trip would strip |
| `XL-MAGIC` | numbers typed inside formulas instead of referenced from a labelled cell |
| `XL-VBA` | a macro project — code that needs the same review as the rest |

Function detection is **case-insensitive on purpose**: LibreOffice writes a
formula it could not parse back lowercased, so an uppercase-only scan misses
exactly the violations that already broke.

## Every xlsx run produces up to four files

    runs/<id>/REPORT.md               findings, proof gates, and what the run could NOT do
    runs/<id>/TRAINING_PLAN.md        the study plan, ordered by prerequisite
    runs/<id>/PROVEN_<name>.xlsx      only when all six gates pass
    runs/<id>/workbook_tests.json     the regression suite the run built

The report leads with completeness — units processed, units failed, units never
reached, tools unavailable — because a findings list that looks clean because
nothing ran is worse than no report.

The training plan states its own limit: generated plans are strong on facts and
procedures and weak at teaching judgement, so it names what it cannot close.

## Untrusted input

Source comments, docstrings and cell labels are text a stranger wrote, entering
a system that produces digests for your knowledge base. Everything is fenced by
`shield.wrap()` before a model sees it and screened for injection shapes;
`INJECT-SRC` and `INJECT-CELL` findings tell you where.

## Going live on A-01

    cp .env.example .env      # MOCK=0
    pip install bandit        # so scanner findings are real, not "unavailable"
    python -m runner run --job code --target C:/orivellum

With `MOCK=0` each unit also gets a model digest. Set `LLM_CODER_MODEL` to use
the coder model for code and leave the workhorse for sheets.

## Honest limits

- The engine proves the file computes what its formulas say — not that the
  formulas say what you *meant*. A self-consistent off-by-one range passes the
  gates; that is why `XL-SHORTRANGE` stays a HIGH finding beside a PROVEN verdict.
- The `formulas` engine covers the common function set, not all of Excel. A
  workbook it cannot compute is UNVERIFIED, and the report says which formula
  stopped it.
- No inter-procedural taint analysis. For that you want CodeQL; these are AST
  patterns plus bandit.
- Non-Python languages get a signature scan, not a real parse, so their call
  graphs are absent.
- On a 6,000-function repository a full model pass is hours. That is what the
  budgets and `resume` are for.
