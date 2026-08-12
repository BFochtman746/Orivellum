---
name: xlsx auditor (dependency graph + tokenizer)
description: Durable correctness rules for the runner's Excel auditor — tokenized parsing, exact-or-refuse resolution, honest graph bounds
---

# xlsx auditor correctness rules (runner)

- Formula parsing must go through a real tokenizer, never regexes.
- **Why:** regexes cannot distinguish `"See B2:C9"` (text) from a reference; an audit tool that emits false findings stops being read. String literals, table refs, defined names, and array constants are the false-positive classes to guard.
- Structured table references resolve EXACTLY or refuse: disjoint selections (headers+totals, non-adjacent columns like `T[[A],[C]]`) must yield one rectangle per region — a bounding box invents dependencies on unselected cells (false unread-input/orphan/cycle results). Refusals are disclosed as graph-partial, never silently substituted with the data body.
- Excel resolves table columns against HEADER TEXT; files written by non-Excel tools desync stored column names from headers, so header cells are authoritative.
- The dependency graph is an honest LOWER BOUND: dynamic refs (INDIRECT/OFFSET) and unresolvable names are always disclosed; orphan/cycle claims are qualified by that disclosure.
- Displayed circular-reference chains must follow actual directed edges (each consecutive pair a real "reads" edge), not just sorted SCC members.
- Self-edges must be kept during range expansion — dropping "cell references itself" kills direct circular-ref detection.
- Huge ranges (whole column/row) must be indexed as rectangles and walked on their NARROW dimension; membership checks batched per row. Never expand 16k columns or scan every rectangle per cell.
- **How to apply:** the false-positive, structured-ref, and large-range regression tests in the runner's xlsx audit suite are the compatibility boundary — any parser/graph change must keep them green.
