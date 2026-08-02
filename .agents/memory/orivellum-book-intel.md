---
name: Book intelligence view
description: Work-level Knowledge Object view — canonical resolution rules and per-chapter research counting.
---

# Book intelligence view

- `GET /api/works/{id}/book-intelligence` (capability `capabilities/book_intelligence.py`) composes versions, canonical, outline, per-chapter research counts, completeness pcts, gaps, next_action. Web UI: "Book" tab (default tab) in works/detail.tsx, component at works/book-tab.tsx (apiFetch + useQuery, not in generated client).

**Rule: lifecycle canonical is per Work+kind, so view layers needing ONE canonical must resolve conflicts themselves.**
**Why:** `update_document_lifecycle` only demotes same-work/same-kind docs; a PDF and a DOCX can both be 'canonical' simultaneously. A code review failed the first version for silently ignoring the user's latest choice.
**How to apply:** pick the declared canonical with the newest `objects.updated_at` (bumped on lifecycle change), surface a `canonical_conflict` gap when several are declared. Auto fallback: biggest DOCX by word count, else biggest doc.

**Rule: never run one FTS query per outline item.** Per-chapter research counts prefetch the Work's knowledge texts once (capped ~3000) and match title tokens in a single in-memory regex pass — N+1 FTS queries under the DB lock was flagged as a scaling bug.
