"""File-size budget gate — fails CI when a source file outgrows its ceiling.

Policy (adopted 2026-08-10, see docs/adr/0007):
- New files: hard limit of MAX_LINES lines.
- Existing over-limit files are grandfathered in ``file_budget_baseline.json``
  with a frozen ceiling (their size at adoption + 10% headroom).
- Ceilings may only be lowered. When a file shrinks below MAX_LINES, remove
  its baseline entry. Never raise a ceiling or add a new entry — split the
  file instead (or record an ADR explaining why it cannot be split).

Usage: python scripts/check_file_budget.py
Exit 0 = within budget, 1 = violation.
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASELINE = pathlib.Path(__file__).resolve().parent / "file_budget_baseline.json"
MAX_LINES = 1500
SCAN_DIRS = [
    "src/orivellum",
    "orivellum-runner/runner",
    "artifacts/orivellum-ui/src",
]
SUFFIXES = {".py", ".ts", ".tsx"}


def count_lines(path: pathlib.Path) -> int:
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def main() -> int:
    baseline: dict[str, int] = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    failures: list[str] = []
    stale = set(baseline)

    for d in SCAN_DIRS:
        for p in sorted((ROOT / d).rglob("*")):
            if p.suffix not in SUFFIXES or not p.is_file():
                continue
            rel = str(p.relative_to(ROOT))
            n = count_lines(p)
            ceiling = baseline.get(rel, MAX_LINES)
            if rel in baseline:
                stale.discard(rel)
                if n <= MAX_LINES:
                    failures.append(
                        f"{rel}: now {n} lines (<= {MAX_LINES}) — remove its "
                        "baseline entry so the ceiling drops permanently"
                    )
                    continue
            if n > ceiling:
                failures.append(
                    f"{rel}: {n} lines exceeds its ceiling of {ceiling} — "
                    "split the file instead of growing it"
                )

    for rel in sorted(stale):
        failures.append(f"{rel}: baseline entry is stale (file deleted/moved) — remove it")

    if failures:
        print("File-size budget violations:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"File-size budget OK ({len(baseline)} grandfathered files, limit {MAX_LINES} lines).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
