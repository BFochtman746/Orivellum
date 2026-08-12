---
name: Docling PDF tier
description: Layout-aware Docling as the first PDF extraction tier — gating, serialization, and meta-merge rules.
---

# Docling layout-aware PDF extraction tier

- Docling is an OPTIONAL pyproject extra (`uv sync --extra docling`), never a hard dependency: it pulls PyTorch and downloads ~500 MB of layout models on first convert. The probe is a pure import-spec check (cached, `force=True` re-checks) so it is Windows-safe and never loads models.
- Tier order for PDFs: docling → pdfplumber → pypdf → VLM OCR → markitdown. `docling_pdf_tier()` never raises and returns None on unavailable/disabled/empty/crash — Docling being broken can never fail ingestion.
- **Docling's DocumentConverter caches a stateful PdfPipeline whose execute() is NOT safe for concurrent calls.** All `convert()` calls must go through the dedicated module `_convert_lock` (separate from the lazy-init lock). A concurrency regression test with a blocking fake converter pins this.
- Docling-internal OCR is deliberately OFF (`do_ocr=False`): scanned pages remain the job of the VLM/tesseract tiers — Docling upgrades layout, not OCR.
- Gated by db setting `docling_enabled` (default "true" — installing is the only setup step). Endpoints: GET/PUT `/api/system/settings/docling`, POST `/api/system/docling/probe`.
- Every PDF tier now stamps `meta.extraction_method` (docling/pdfplumber/pypdf/vlm_ocr/markitdown); the document detail Overview shows it via `extractionMethodLabel()`.
- **Why the pipeline meta write MERGES:** extraction meta used to `UPDATE documents SET meta=?` wholesale — that would clobber import-time keys (from_zip, zip_folder) on ZIP children once every PDF tier started emitting meta. The pipeline now reads-merges-writes under `db._lock`.
- docling-core API drift defense: item serialization duck-types `export_to_markdown` (tries with-doc and no-arg signatures) and falls back to whole-document markdown as a single page if `iterate_items()` fails.
