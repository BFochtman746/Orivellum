# Forensic-Grade Excel Standard — Evaluation for Orivellum

**Date:** August 10, 2026
**Input:** pasted proposal "The Optimal Forensic-Grade Excel Standard" (7 criteria + AI verification prompt)
**Question answered:** Does this standard hold up against current research? What would adopting it do for our system? What do we already have, and what do we need?

---

## 1. Does the proposed standard hold up?

Mostly yes — it aligns with the recognized authorities in this space: ICAEW's *Twenty Principles for Good Spreadsheet Practice* (2024 edition) and *Financial Modelling Code*, the FAST Standard (Flexible, Appropriate, Structured, Transparent), and AICPA expert-witness practice aids. Its core ideas — no hidden content, exposed formulas, separated data/calculation/report sheets, no merged cells, cross-foot checks, hash-published outputs, deterministic generation — are all genuine current best practice, and the 2023 amendments to Federal Rule of Evidence 702 (expert testimony reliability) have pushed forensic accounting further in exactly this direction: every number must be reproducible and traceable.

**But four points need correction before we adopt it:**

1. **Digital signatures on the .xlsx itself (criterion 5) — reject.** Two independent reasons:
   - There is no viable open-source tooling for OOXML digital signatures in Python (only commercial .NET libraries like EPPlus).
   - More importantly, OOXML signatures have been **demonstrated spoofable** (published "sigspoox" research on signature spoofing in Word/Excel/PowerPoint files). A court-facing claim resting on a spoofable mechanism is a liability, not an asset.
   - **Better practice (what current research supports):** publish a SHA-256 hash manifest alongside the file, and sign the *manifest* with a detached signature (GPG/minisign). This is stronger, verifiable with universal tooling, and matches archival practice (BagIt) — and it's what our Workbench archives already partially do.

2. **The "very hidden audit log sheet" (criterion 6) contradicts the standard's own rule #1** ("zero hidden content"). A hidden sheet in a forensic workbook is exactly what opposing counsel looks for. Audit logs belong *outside* the workbook, in the audit trail package.

3. **"No cross-sheet references, named ranges only" is stricter than the actual standards.** ICAEW/FAST recommend named ranges for key inputs and outputs, not for every reference — blanket enforcement makes generated formulas harder to audit, not easier.

4. **"Byte-for-byte deterministic output (excluding timestamps)" is achievable but requires deliberate engineering** — xlsx files are zip archives with embedded timestamps and nondeterministic part ordering. Neither openpyxl nor XlsxWriter produces stable bytes by default (XlsxWriter has an open issue on it). It's solvable: fix zip entry timestamps, sort part order, pin library versions. Worth doing — "same input, same hash" is a very strong courtroom claim.

## 2. What we already have (we're ahead on verification)

Our verification side is already **stronger than what the proposal's AI-prompt approach would give us**, because it's deterministic code, not an LLM's opinion:

| Capability | Where | Status |
|---|---|---|
| Independent formula recalculation (every formula, pure-Python engine, computed vs. saved comparison) | Runner gates G1–G2 | ✅ Built and proven |
| Error-value scan (#REF! etc.) | Runner gate G3 | ✅ Built |
| Volatile function detection (NOW/TODAY/RAND, plus OFFSET/INDIRECT) | Runner detection | ✅ Built — already flags criterion 1's volatile rule |
| External link detection | Runner detection | ✅ Built |
| File integrity: SHA-256 per version file, hash-verified archives | Workbench | ✅ Built — criterion 5's hash rule, done properly |
| Structural integrity of the file format itself (OOXML part order, surgical edits only) | Runner gates G4–G5 | ✅ Built — beyond what the proposal asks |
| VBA detection | Runner | ✅ Built |

## 3. What we need (the gaps)

The gaps are all on the **generation and workbook-content** side — the Workbench currently checks that workbooks *load and calculate*, not that they're *structured to standard*:

**Gap A — Workbook standards gate (the big one).** A new deterministic checker that inspects a finished workbook for: README sheet present and populated; data in real Excel Tables; no merged cells; no hidden rows/columns/sheets; data validation on input cells; cells locked + sheet protection set; document properties populated (title, author, unique document ID, build version, source hash); print setup defined; cross-foot check formulas present; no external links. Every item is mechanically checkable with openpyxl — no AI judgment needed. This becomes a seventh gate next to the Runner's six, and the same checker doubles as a scored report (the "Excel Forensic Readiness Score" the prompt asks for — but computed, not guessed).

**Gap B — Generation templates that meet the standard by construction.** The Workbench's build prompts ask for headers and number formats but don't require README sheets, Tables, named key ranges, protection, or properties. Cheaper to generate right than to reject after: bake the standard into the generation instructions and templates, and let Gap A's gate enforce it.

**Gap C — Deterministic builds.** Normalize zip timestamps and part ordering at publish time so identical inputs give identical hashes. This slots into the existing publish path (which already stages and hashes files).

**Gap D — Metadata sanitizer + document ID stamping.** Run a document-inspector pass at publish: strip revision/personal metadata, then stamp title, document ID, generation timestamp, and source hash into properties. We already stamp build versions into other outputs (`code_version()` goes into archives and manifests) — this extends the same pattern into the workbook itself.

**Explicitly not needed:** embedded OOXML digital signatures (spoofable, no tooling — use signed hash manifests instead), the hidden audit-log sheet (contradiction — keep logs in the archive), and a separate AI-verifier service (our deterministic gates outperform an LLM grader; the LLM prompt is useful only as a final human-readable narrative on top of computed results).

## 4. What adopting this does for our system

Today the Workbench can say: *"this workbook opens and every formula calculates correctly"* (and the Runner can prove it). With the four gaps closed, it can say: *"this workbook is a self-documenting, tamper-evident, reproducible data package — every number recomputable, every input traceable, nothing hidden, hash-verifiable, and structured to ICAEW/FAST standards."* That's the difference between a correct spreadsheet and defensible evidence — and it applies to every Excel output the system produces, not just forensic reports: Workbench builds, future report exports, and the planned workbook-review feature (#1176) all inherit the same gate. The review feature gets a major upgrade for free: it can score *any uploaded workbook* against the standard, which is a capability people pay auditors for.

**Effort estimate:** Gap A is the core and is very tractable (a pure-openpyxl checker + score). B is prompt/template work. C and D are small, surgical additions to the existing publish path. No new infrastructure, no new services, no external dependencies beyond what's installed.

## 5. Sources

- ICAEW, *Twenty Principles for Good Spreadsheet Practice*, 2024 edition; *Financial Modelling Code*; *How to Review a Spreadsheet*.
- FAST Standard Organisation — fast-standard.org.
- AICPA & CIMA FVS Practice Aids: *Serving as an Expert Witness or Consultant* (Sep 2025); *Attaining Reasonable Certainty in Economic Damages Calculations*.
- Federal Rule of Evidence 702, 2023 amendments (expert testimony reliability standards).
- "sigspoox" — published signature-spoofing research against OOXML digital signatures.
- XlsxWriter issue #494 (deterministic binary output); openpyxl reproducible-output techniques (fixed zip timestamps).
