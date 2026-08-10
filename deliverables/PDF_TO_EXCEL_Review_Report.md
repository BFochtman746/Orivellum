# PDF_TO_EXCEL Pack — Verification & Optimization Report

**Date:** August 10, 2026
**Input:** `PDF_TO_EXCEL_1786397324415.zip` (49 files: 17 protocol documents, 6 workbooks, 12 config/template files, registers, manifests)
**Output:** `deliverables/PDF_TO_EXCEL_v2.1.zip` (corrected and updated pack, 53 files)

---

## 1. What was verified

| Check | Method | Result |
|---|---|---|
| Document integrity | SHA-256 manifest verification | ✅ All 17 protocol documents match their recorded checksums — nothing corrupted or tampered |
| Workbook loading | Opened all 6 workbooks programmatically | ✅ All load cleanly (37,676 cells, 9,876 formulas total) |
| Error cells | Scanned every cell for #REF!, #DIV/0!, #VALUE!, #NAME?, #N/A | ✅ Zero error values stored in any workbook |
| Formula recalculation | Independent recalculation engine (`formulas` 1.3.4, not Excel) | ✅ All workbooks recalculate without failures |
| Cross-sheet references | Missing-sheet detection during recalculation | ⚠️ **1 defect found** (see below) |
| Package layout | Manifest paths vs. actual archive layout | ⚠️ **12 files at wrong paths** (see below) |
| Practice materials | `Level_10_Practice_SHA256SUMS.txt` | ⚠️ **2 referenced PDFs missing** from the archive |

## 2. Defects found and fixed

### Defect 1 — Broken quality check in the master workbook (fixed)
`A8407_UNDP1970_MASTER_after_B06_v012.xlsx`, check **S4-19 "Transcription reviews documented"** (`Checks!C521`) pointed at a sheet named `Transcription Review`. That sheet had been renamed `Schedule 4 Review`, so in Excel the check shows **#REF!** and silently stops verifying. The stored value (16) still *looked* right, which is exactly how this kind of break hides.

**Fix:** formula repaired to `=COUNTA('Schedule 4 Review'!A4:A19)` by patching only that reference inside the file — all formatting, all 26 sheets, and all other formulas untouched. Verified: expected value 16 matches the 16 review rows, and the whole workbook recalculates with no missing-sheet errors.

### Defect 2 — Flattened package layout (fixed)
The manifest expects `config/`, `schemas/`, and `templates/` subfolders, but the zip had all 12 of those files dumped at the root, so `sha256sum -c` reported 12 failures even though every file was present and intact. **Fix:** folder structure restored to match the manifest; manifest regenerated and now also covers the workbooks and registers (the old one only covered documents).

### Defect 3 — Missing practice PDFs (documented)
`Level_10_Forensic_PDF_to_Excel_Practice.pdf` and its instructor key are listed with checksums but absent from the archive. They couldn't be recreated, so this is documented as a known gap in the changelog; the checksums are kept so the files can be verified if you find them.

## 3. Research: is the protocol current?

The v2.0 research basis (FADGI, NARA, W3C PROV, BagIt) is still sound. What's moved since it was written (June 2026 and earlier):

1. **AI models reading table images now beat traditional OCR at cell content** (ACL 2025 benchmark on PubTables-1M), while dedicated vision models still recover table *structure* slightly better. Best practice is now a hybrid: structure model proposes the grid, multimodal AI reads the cells.
2. **Open-source extraction matured** — IBM's Docling (60k+ stars, MIT license) now does VLM-based table extraction and works as an automated "second opinion."
3. **Verification research converged on three patterns**: reconstruct-and-compare validation, per-value grounding to exact source locations (financial hallucination research, 2026), and calibrated abstention — models' own confidence is unreliable, so disagreement should route to review, never be guessed through.
4. **Silent page loss** is now a named failure mode with dedicated certification tooling.

## 4. Optimizations added (v2.1, in `16_RESEARCH_UPDATE_2026.md`)

- **O-1 Dual-channel extraction** — every batch extracted two independent ways (image transcription + machine text-layer/structure extraction); cell-level disagreements go to the exception register instead of being silently resolved.
- **O-2 Deterministic recalculation gate** — every milestone export must reload in an independent engine, recalculate with zero error cells, and have every cross-sheet reference resolve. This gate would have caught Defect 1 automatically.
- **O-3 Confidence-tiered exception routing** — low-agreement cells auto-route to the exception register; an empty cell plus an exception row always beats a guessed value.
- **O-4 Page-completeness certification** — page register vs. renders vs. extraction output reconciled as a blocking gate, not an operator habit.
- **O-5 2026 tooling reference** — which tool for which job (pdfplumber/Camelot for born-digital, TATR/Docling for scan structure, multimodal AI for cell text, `formulas`/LibreOffice for independent recalc).

## 5. Bottom line

The pack is well-built — the methodology holds up against current research, the workbooks are formula-clean, and the documents are intact. It had one real (and sneaky) broken quality check, a packaging error that made integrity verification fail, and two missing practice files. All fixed or documented in **PDF_TO_EXCEL_v2.1.zip**, and the protocol now includes the 2026 research update with five concrete upgrades.
