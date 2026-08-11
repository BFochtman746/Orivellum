"""G3 Canon Seed → canon_fact rows.

Passing the G3 gate no longer stores canon as loose markdown: the tiered
fact table in the G3 artifact is parsed and written into the canon
authority (canon_fact), classified and sourced, signed by the gate author.

Table format (3 or 4 columns; the 4th Scope column is optional):

    | Fact | Tier (HISTORICAL / INFERRED / INVENTED) | source_pointer | Scope |
    |------|-----------------------------------------|----------------|-------|
    | ...  | HISTORICAL                              | Job 1:8        | SERIES|

Rules enforced at parse time (the gate refuses to pass otherwise):
- Tier must be HISTORICAL, INFERRED, or INVENTED.
- HISTORICAL rows need a non-empty source_pointer.
- INFERRED rows must cite the fact rows they derive from as ``#N`` refs
  (e.g. ``#1, #3`` — row numbers of earlier fact rows in this table);
  any remaining text in the cell is kept as the source_ref.
- Scope is WORK (default, this book only) or SERIES (whole trilogy).

Seeding is idempotent: identical active facts are skipped, so a re-passed
gate after a partial failure never double-writes.
"""

from __future__ import annotations

import re
from typing import Any

from orivellum.database.canon_store import CLASSIFICATIONS, CanonStore

_ROW_REF = re.compile(r"#(\d+)")


def _parse_fact_row(cells: list[str], n: int) -> tuple[dict | None, str | None]:
    """Validate one fact-table row.  Returns (row, None) or (None, error)."""
    statement, tier_raw, pointer = cells[0], cells[1], cells[2]
    scope_raw = cells[3].upper() if len(cells) >= 4 else "WORK"

    tier = tier_raw.strip().upper()
    if tier not in CLASSIFICATIONS:
        return None, f"row {n}: tier {tier_raw!r} is not one of {', '.join(CLASSIFICATIONS)}"
    if not statement:
        return None, f"row {n}: empty fact statement"
    if scope_raw not in ("WORK", "SERIES", ""):
        return None, f"row {n}: scope must be WORK or SERIES (got {cells[3]!r})"

    parent_rows: list[int] = []
    source_ref = pointer
    if tier == "HISTORICAL" and not pointer:
        return None, f"row {n}: a HISTORICAL fact requires a source_pointer"
    if tier == "INFERRED":
        parent_rows = [int(m) for m in _ROW_REF.findall(pointer)]
        if not parent_rows:
            return None, (
                f"row {n}: an INFERRED fact must cite the rows it derives "
                "from as #N refs in source_pointer (e.g. '#1, #2'), or be "
                "reclassified as HISTORICAL (with a source) or INVENTED"
            )
        bad = [p for p in parent_rows if p >= n or p < 1]
        if bad:
            return None, (
                f"row {n}: parent refs must point to earlier rows "
                f"(got {', '.join('#' + str(b) for b in bad)})"
            )
        source_ref = _ROW_REF.sub("", pointer).strip(" ,;")

    return {
        "statement": statement,
        "classification": tier,
        "source_ref": source_ref,
        "parent_rows": parent_rows,
        "series": scope_raw == "SERIES",
    }, None


def parse_canon_seed(content: str) -> tuple[list[dict], list[str]]:
    """Parse the canon-fact table out of the G3 artifact markdown.

    Returns (rows, errors).  Each row: {statement, classification,
    source_ref, parent_rows (list of 1-based ints), series (bool)}.
    """
    rows: list[dict] = []
    errors: list[str] = []
    in_section = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            in_section = stripped.lower().startswith("## canon facts")
            continue
        if not in_section or not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if len(cells) < 3:
            continue
        # Skip the header row and the |---|---| separator row
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if cells[0].lower() == "fact" or cells[1].lower().startswith("tier"):
            continue

        row, err = _parse_fact_row(cells, len(rows) + 1)
        if err:
            errors.append(err)
        elif row:
            rows.append(row)
    if not rows and not errors:
        errors.append("no canon fact rows found under '## Canon facts'")
    return rows, errors


def seed_canon_facts(db: Any, work_id: str, content: str, author: str) -> dict:
    """Parse the G3 artifact and write its facts into the canon authority.

    Raises ValueError (with every problem listed) when the table violates
    the authority rules — the caller blocks the gate pass on that error.
    Returns {"created": int, "skipped": int, "fact_ids": [...]}.
    """
    parsed, errors = parse_canon_seed(content)
    if errors:
        raise ValueError("; ".join(errors))

    # One transaction — a refused row rolls back the whole seed, so a failed
    # gate pass never leaves partial canon behind (CanonFactError is a
    # ValueError, so the caller's 422 blocking also covers store refusals).
    batch = [
        {
            "statement": row["statement"],
            "classification": row["classification"],
            "work_id": None if row["series"] else work_id,
            "source_ref": row["source_ref"],
            "parent_rows": row["parent_rows"],
        }
        for row in parsed
    ]
    return CanonStore(db).create_facts_batch(batch, signed_by=author, origin="g3_seed")
