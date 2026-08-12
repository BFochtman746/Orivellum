---
name: xlsx auditor (dependency graph + tokenizer)
description: Design rules for the runner's Excel auditor layer — tokenized formula analysis, workbook dependency graph, honesty guarantees
---

# xlsx auditor layer (runner)

- Formula parsing is via `openpyxl.formula.tokenizer` (xlsx_formula.py), never regexes. String literals, table refs, defined names, and array constants were the regex era's false-positive classes — regression tests pin zero phantom findings.
- **Why:** an audit tool that emits false findings stops being read; regexes cannot distinguish `"See B2:C9"` (text) from a reference.
- Structured refs (`Table1[...]`) resolve EXACTLY or refuse: `_table_area` returns a LIST of areas (disjoint `[#Headers],[#Totals]` = two rects, never a bounding box over the data body); `@` needs the calling cell; unknown columns / missing totals rows return None → disclosed via `XL-GRAPH-PARTIAL`, never silently substituted with the data body.
- Excel resolves table columns against HEADER TEXT — openpyxl-written files desync tableColumns names from headers, so extract_tables prefers header cell values.
- WorkbookGraph (xlsx_graph.py): edges expanded up to EXPAND_CAP cells per area; larger areas go into a per-sheet rectangle index. Containment pass buckets formula cells by (sheet,col) AND (sheet,row) and walks each capped rect's NARROW dimension (whole-row refs must never iterate 16k columns). `unread_inputs` batches rect membership per row (active col intervals cached on row change) — never a per-cell rect scan.
- Honesty rule: INDIRECT/OFFSET and unresolvable names make the graph a lower bound — always disclosed (`XL-GRAPH-PARTIAL`), and orphan/cycle claims are qualified by it.
- Cycle detection = iterative Tarjan SCC + self-loops (self-edges must be KEPT in expansion — dropping `pk == key` kills direct self-reference detection).
- New finding codes: XL-CIRCULAR (CRITICAL), XL-MERGED-RANGE (HIGH), XL-ANCHOR-DRIFT/XL-DATE-MIX/XL-NAME-SHADOW (MEDIUM), XL-NAME-ORPHAN (LOW), XL-UNREAD-INPUT/XL-GRAPH-PARTIAL (INFO). Error findings name their chain root via trace_error_root.
- **How to apply:** any future formula-parser change must keep the false-positive regressions and the large-range/structured-ref tests in tests/test_xlsx_audit.py green — they are the compatibility boundary. Graph cache (_GRAPH_CACHE) lives beside _WB_CACHE and is dropped in _drop_cache.
