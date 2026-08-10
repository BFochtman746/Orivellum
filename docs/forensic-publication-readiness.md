# Forensic Publication Readiness — Standards Research & Gap Analysis

**Date:** 2026-08-10 · **Scope:** every generator that produces a document a user could
publish: `capabilities/generate.py` (PDF/DOCX/XLSX/PPTX via ReportLab, python-docx,
openpyxl, python-pptx), Document Workshop, book packaging (stdlib EPUB 3), Forge
static sites, runner Markdown reports, action-pack DOCX/ZIP exports.

**Verdict up front: Forensic Publication Readiness Score ≈ 15 / 100.**
The system already has two real assets — a SHA-256 recorded for every registered
output, and the Press ledger's hash-chained event log — but nothing it emits today is
PDF/A, signed, tagged for accessibility, reproducible, or metadata-sanitised.

---

## Standards landscape (researched 2026-08)

| Standard | Current state | What matters for us |
| --- | --- | --- |
| PDF/A-3 (ISO 19005-3) | Stable; PDF/A-4 exists but archives still ask for A-2b/A-3b | A-3 is the only level that allows **embedding the source dataset** inside the archive PDF |
| PAdES (ETSI EN 319 142) | B-B → B-T → B-LT → B-LTA ladder unchanged | B-LT = signature + RFC 3161 timestamp + embedded revocation data (DSS), so it validates decades later without the network |
| PDF/UA-1 (ISO 14289-1) / PDF/UA-2 (ISO 14289-2:2024) | UA-2 published 2024, tooling still immature; UA-1 is what validators and buyers test | The European Accessibility Act has been enforced since June 2025 — tagged PDF is no longer optional for public-facing docs |
| WCAG 2.2 | W3C Recommendation (Dec 2024 update) | Applies to our HTML outputs (Forge, EPUB XHTML) |
| Reproducible builds | `SOURCE_DATE_EPOCH` is the cross-tool convention | ReportLab has `invariant=1`; WeasyPrint honours `SOURCE_DATE_EPOCH` |
| Tamper-evident publishing | RFC 3161 tokens; OpenTimestamps (free, Bitcoin-anchored); C2PA for media | A plain `.sha256` + `.tsr` sidecar pair covers the requirement without any blockchain dependency |

**Key tooling conclusions (all open-source, all runnable on Nimo):**

- **WeasyPrint ≥ 66** is the pivotal tool: renders HTML/CSS to PDF with
  `pdf_variant="pdf/a-3b"` **or** `"pdf/ua-1"`, full font embedding, tagged output,
  attachments with `AFRelationship`. One variant per file today (upstream issue
  #2783), so we generate PDF/A-3b **with tagging enabled** and validate against both
  profiles. Windows needs the GTK/Pango runtime — a one-time install.
- **pyHanko** (MIT, actively maintained, v0.36.x) does PAdES B-LT/B-LTA end-to-end:
  CMS signing, RFC 3161 TSA, DSS/VRI embedding for LTV, PKCS#11 for hardware
  qualified certificates.
- **veraPDF** (the industry reference validator, Java) validates both PDF/A and
  PDF/UA profiles from the CLI — this becomes a build gate, exactly like the
  runner's six proof gates.
- **pikepdf** for post-processing: XMP metadata writes, attachment injection,
  docinfo scrubbing.
- **ReportLab alone cannot get us there**: the open-source edition produces untagged
  PDF 1.4-ish output with no PDF/A conformance and no accessibility tree.

---

## Capability-by-capability assessment

### 1. PDF/A-3 with embedded source data — **MISSING · Critical**
ReportLab output has no XMP conformance metadata, no OutputIntent, no embedded-file
support in our code, and Helvetica is referenced, not embedded.
**Fix:** move report rendering to an HTML template → WeasyPrint
(`pdf_variant="pdf/a-3b"`, bundled DejaVu/Noto fonts, sRGB OutputIntent automatic).
Attach the source dataset JSON with `AFRelationship=Source`. Gate every build on
`verapdf --flavour 3b`.

### 2. PAdES B-LT signature + timestamp — **MISSING · Critical**
No signing code exists anywhere in the repo.
**Fix:** pyHanko signing step after render: sign with the configured certificate,
timestamp via a free RFC 3161 TSA, then a second pass embeds OCSP/CRL responses
(DSS) to reach B-LT. Certificate reality check: a **qualified** certificate can only
come from a QTSP (a purchase + identity verification, often on hardware). The
pipeline should accept any cert (self-signed for dev → org cert → qualified via
PKCS#11) and *report* the certificate class honestly in the manifest rather than
claim more than it has.

### 3. Chain-of-custody log in document metadata — **PARTIAL · High**
The Press ledger already keeps a hash-chained SQLite event log (generation, review,
sign-off) — the hard part exists. It just never leaves the database.
**Fix:** serialize the ledger slice for the document as JSON (each event carrying
`prev_hash`/`event_hash`, exactly as stored), embed it as a second PDF/A-3
attachment, and record its SHA-256 in the XMP metadata.

### 4. PDF/UA + WCAG 2.2 AA — **MISSING · High**
ReportLab/python-docx outputs are untagged; EPUB has only a nav TOC; Forge sites
have no enforced accessibility.
**Fix:** the same WeasyPrint move gives tagged PDF from semantic HTML nearly for
free — the discipline shifts to the template: heading hierarchy, `alt` on every
image, `lang` attribute, real tables. Validate with veraPDF's PDF/UA profile.
For HTML outputs (Forge, EPUB), wire **axe-core or pa11y** as an automated WCAG 2.2
gate. Target PDF/UA-1 now; revisit UA-2 when validators mature.

### 5. Deterministic reproducibility — **MISSING · High**
Outputs embed wall-clock timestamps in content, metadata, and ZIP entries; two runs
never hash the same.
**Fix:** a `deterministic=True` build mode: honour `SOURCE_DATE_EPOCH`, fixed PDF
document ID derived from the dataset hash, fixed ZIP mtimes for EPUB/bundles,
`invariant=1` where ReportLab remains. Then a verify command — same shape as the
runner's `verify` — that rebuilds from the signed dataset + template and compares
SHA-256. One subtlety: signing happens *after* the reproducible render, so the
manifest records **both** hashes (pre-signature = reproducible, post-signature =
distributed file).

### 6. True redaction — **MISSING · Medium**
Nothing redacts today. General-purpose PDF redaction is a minefield — but we have an
advantage most systems lack: **we generate from data**. Redaction by regeneration is
the only approach that is provably complete.
**Fix:** redact fields in the dataset, re-render the document from scratch (nothing
to leak — the content was never in the file), and emit a redaction certificate:
which fields, why, hash of the redacted dataset, hash of the new document. Never
ship rectangle-overlay "redaction".

### 7. Metadata sanitisation — **PARTIAL · High**
The library DB keeps provenance internally (good), but generated files leak:
DOCX/XLSX/PPTX carry `creator`/`lastModifiedBy` in `core.xml`, ReportLab writes
producer strings, ZIPs carry timestamps.
**Fix:** a mandatory sanitise pass before registration: pikepdf scrubs
docinfo/XMP down to an allow-list; Office formats get `core.xml`/`app.xml`/
`custom.xml` rewritten; plus a leak test that greps final bytes for usernames,
paths, and hostnames — in CI, like everything else.

### 8. Tamper-evident publishing — **PARTIAL · Medium**
SHA-256 is computed and stored internally for every registered file — but recipients
never see it.
**Fix:** emit `<name>.sha256` and an RFC 3161 timestamp token `<name>.tsr` beside
every published document, plus a one-line verify command. If public anchoring is
wanted later, OpenTimestamps adds it for free without running any infrastructure.

---

## Action plan (priority order)

| # | Work | Covers | Effort |
| --- | --- | --- | --- |
| P1 | HTML-template rendering pipeline via WeasyPrint: PDF/A-3b, tagging, embedded fonts, dataset attachment; veraPDF gate in CI | 1, 4 (PDF) | the big one — new render path, keep ReportLab as fallback |
| P2 | pyHanko signing stage (cert-agnostic) + TSA timestamp + DSS for B-LT; `.sha256` + `.tsr` sidecars | 2, 8 | moderate; TSA + validation tests |
| P3 | Deterministic build mode + rebuild-and-compare verify command | 5 | moderate; mostly discipline + tests |
| P4 | Metadata sanitise pass for PDF + Office formats + leak tests | 7 | small, high value |
| P5 | Press-ledger custody JSON embedded as second A-3 attachment + XMP hash | 3 | small — data already exists |
| P6 | Redaction-by-regeneration + redaction certificate | 6 | small once P1/P3 exist |
| P7 | axe-core/pa11y WCAG 2.2 gate for Forge & EPUB HTML | 4 (HTML) | small |

**External dependencies to plan for (cannot be solved in code):**
- Qualified certificate → purchase from a QTSP if legal defensibility at that level
  is actually required; everything below that level works immediately.
- veraPDF needs Java on Nimo; WeasyPrint needs the GTK runtime on Windows.

**Score trajectory:** P1+P2 alone lift readiness to roughly 60%; through P5 ≈ 85%;
the remainder is redaction tooling and certificate procurement.
