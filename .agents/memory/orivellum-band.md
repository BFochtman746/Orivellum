---
name: BAND surgical edits + revision lineage
description: Invariants for the band edit engine, revision lineage, and delta verification gates.
---

# BAND surgical edits + LINEAGE

- **Scope is structural, never trusted**: only the extracted band goes to the model; outside-band text is reassembled by code. The client also echoes `band_text` and the server refuses when `text[start:end]` doesn't reproduce it — this guards UTF-16 (JS selections) vs code-point (Python slicing) offset drift. UI converts offsets with `utf16ToCodePoints` before sending.
  - **Why:** a review caught that astral characters shift textarea selection offsets, silently editing an unselected span.
- **Checkpoint must be atomic + fingerprint-guarded**: the pre-edit checkpoint re-reads live text and validates the declared fingerprint in ONE transaction; a concurrent LOOM draft landing mid-flight refuses the edit BEFORE any write. Never checkpoint from a stale in-memory chapter object.
- **Regression gates fail closed**: malformed delta-check or pairwise output raises (never counts as a clean pass); ungrounded finding quotes are discarded, never coerced. Gates: more delta findings, new critical, higher band CED, or pairwise loss → refuse; `accept_regression=true` needs an author signature and is recorded in revision meta.
- **Delta findings live in revision meta, NOT narrative_finding** — they gate a candidate text that may never persist, and constory's DB write path owns severity for stored findings.
- **Lineage is append-only**: every revision records parent_rev/origin/created_by/edit_scope; restore copies text into a NEW head revision; nothing is ever updated or deleted. LOOM `_store_draft` records ai_generated/loom.
- **Approved chapters**: approval is of the exact text — editing/restoring one requires the author signature and demotes to 'drafted'.
- Editor/judge model split reuses LOOM's `_require_separated_models` (drafter never judges its own edit).
- Author "signature" is still a free-text string system-wide (personas, canon, band) — not bound to authenticated identity; flagged as a cross-cutting follow-up.
