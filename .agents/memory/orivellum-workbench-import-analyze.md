---
name: Workbench import + analyze mode
description: How Workbench imports existing files as v1 and produces analysis-report versions; safety and dispatch rules learned building it.
---

## Design
- Import: uploaded .xlsx or .zip becomes v1 verbatim (verdict `imported`), no LLM build. Kind auto-detect: xlsx only if a workbook is present AND no code extensions. Verification problems (unloadable workbook) are recorded as `import_warnings`, not rejected — reviewing broken files is a valid use. Structural limits (file count/bytes) still reject.
- Analyze: new version = byte-copy of previous version + `ANALYSIS_REPORT.md` (verdict `analyzed`), so "every version is the complete project state" holds. Deterministic findings first (openpyxl checks + independent `formulas` recalc comparing computed vs cached values); LLM narrative on top; model failure is stated in the report, never silent.
- No schema change needed — verdict strings on `wb_versions` distinguish imported/analyzed/verified.
- UI report viewer fetches a dedicated `/versions/{n}/report` endpoint; workbench UI uses direct apiFetch (no orval), so unspecced endpoints are fine with the drift gate (spec ⊆ app).

## Durable lessons
- **`submit_bg` returns bool now.** It never raises, but it CAN drop work. Any caller that holds a claim/lock for background work must check the return and release + record `last_error` on False, or the entity is stranded "building" forever.
- **xlsx files are zips**: guard against OOXML decompression bombs by summing declared `file_size` of members before `load_workbook`/`formulas` (`check_xlsx_zip_safety`). The outer upload cap does not protect you.
- **Failed publish must clean the filesystem too**, not just DB rows — rmtree the project dir on import-publish failure, and clean staging if `copytree` itself fails.
- **Lint ratchet closes the per-file-ignore loophole**: adding a file to ruff per-file-ignores makes the ratchet re-lint it with ignores disabled against a zero baseline. New code must be genuinely clean (Annotated[] for FastAPI File/Form defaults, mkstemp+fdopen instead of NamedTemporaryFile(delete=False), split complex functions).
- `formulas` solver keys look like `'[book.xlsx]SHEETNAME'!A1` with the sheet UPPERCASED — map upper→actual sheet names before comparing to openpyxl views.
