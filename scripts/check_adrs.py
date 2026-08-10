"""ADR format gate — docs/adr/ must stay sequential and well-formed.

Checks (see docs/adr/README.md):
- names match NNNN-slug.md, numbers strictly sequential from 1, no gaps/dupes
- each file has a Date/Status line and Context/Decision/Consequences sections

Exit 0 = OK, 1 = violation.
"""

from __future__ import annotations

import pathlib
import re
import sys

ADR_DIR = pathlib.Path(__file__).resolve().parent.parent / "docs" / "adr"
NAME_RE = re.compile(r"^(\d{4})-[a-z0-9][a-z0-9-]*\.md$")
REQUIRED = ["Date: ", "Status: ", "## Context", "## Decision", "## Consequences"]


def _check_file(p: pathlib.Path, numbered: dict[int, str], errors: list[str]) -> None:
    m = NAME_RE.match(p.name)
    if not m:
        errors.append(f"{p.name}: name must match NNNN-short-slug.md")
        return
    n = int(m.group(1))
    if n in numbered:
        errors.append(f"{p.name}: number {n:04d} already used by {numbered[n]}")
    numbered[n] = p.name
    text = p.read_text(encoding="utf-8")
    for marker in REQUIRED:
        if marker not in text:
            errors.append(f"{p.name}: missing required element {marker!r}")


def main() -> int:
    errors: list[str] = []
    numbered: dict[int, str] = {}
    for p in sorted(ADR_DIR.glob("*.md")):
        if p.name != "README.md":
            _check_file(p, numbered, errors)

    if numbered:
        missing = [f"{n:04d}" for n in range(1, max(numbered) + 1) if n not in numbered]
        if missing:
            errors.append(f"gaps in ADR numbering: {missing}")
    else:
        errors.append("docs/adr/ contains no ADRs")

    if errors:
        print("ADR check failed:")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"ADR check OK ({len(numbered)} records, latest {max(numbered):04d}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
