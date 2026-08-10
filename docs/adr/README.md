# Architecture Decision Records

Short, numbered files explaining why each significant choice was made — so the
reasoning survives the person (or agent session) that made it.

Rules (gated in CI by `scripts/check_adrs.py`):

- Files are named `NNNN-short-slug.md` with strictly sequential numbers.
- Every ADR has a title line, a `Date: ... | Status: ...` line, and the three
  sections `## Context`, `## Decision`, `## Consequences`.
- ADRs are never deleted or rewritten. A reversed decision gets a NEW ADR that
  says `Status: Accepted, supersedes NNNN`, and the old one's status line
  becomes `Status: Superseded by NNNN`.

When to write one: any decision you would have to explain to a new contributor
before they could work safely — storage, security model, layering, gating
policy, external services, irreversible data formats.
