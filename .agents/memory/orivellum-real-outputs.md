---
name: Real book outputs (PRESS render / EPUB a11y / B16 gate)
description: Fail-closed rules for print PDF/DOCX/EPUB rendering, validation records, sealing, and the release gate.
---

# Real book outputs — durable rules

- **Validation is keyed to the EPUB sha256.** `record_validation` stores (tool, epub_sha, clean); `validation_status` only counts records matching the CURRENT rendered EPUB's sha — re-rendering silently invalidates old clean records. Never "carry forward" a clean verdict.
- **Seal gate covers every package that carries the rendered book.** Production publisher AND test-reader/ARC seals require actual_pages > 0 plus clean EPUBCheck+Ace for the current build. Only the submission manuscript format (typeset-free .docx) is exempt.
  **Why:** an ARC delivers the same PDF/EPUB; exempting it was a real bypass found in code review.
- **assay_run.instrument_id stores the instrument UUID, not its key.** Any query joining runs to an instrument must resolve `get_assay_instrument(key)["id"]` first; an unregistered instrument is a failed check, not an empty result.
- **actual_pages propagation:** render writes it to the press book row AND the linked Work's meta; ATELIER re-bases geometry via sync-pages (reads press verify, refuses when 0).
- **page_map is honestly sparse** (~13 anchors / 59 pages): split paragraphs lose the anchor attr. Accepted — a sparse-but-true page-list nav beats an invented dense one.
- **EPUBCheck/Ace run only in CI** (Java/Node absent locally); `scripts/validate_epub.py` builds a representative EPUB and fails on either tool.

# CI debt gates (repo-wide lesson)

- The lint ratchet (`scripts/check_lint_ratchet.py`) re-lints grandfathered files with ignores disabled; ANY new violation of an ignored code in a baselined file fails CI even though plain `ruff check` passes. Fix new code with singletons (`_DB = Depends(get_db)`), `raise ... from e`, or helper extraction — never grow the snapshot.
- `scripts/check_file_budget.py` ceilings can only shrink. works.py / db.py / schema.py were already over their ceilings before Aug 11 2026 — splitting them is its own task.
