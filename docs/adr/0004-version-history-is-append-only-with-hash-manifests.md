# 4. Version history is append-only with hash manifests

Date: 2026-08-10 | Status: Accepted

## Context
Forensic credibility is the product. Rewriting history — even to fix a mistake — destroys it.

## Decision
Versioned things (Workbench projects, press ledger, proofs) only ever append. Reverting copies an old state forward as a new version. Every accepted file records a SHA-256; archives re-hash and refuse on mismatch.

## Consequences
Storage grows with history (acceptable at this scale). Nothing the system certifies can silently change afterwards.
