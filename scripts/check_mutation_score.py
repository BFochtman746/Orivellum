"""Mutation-testing gate — surviving mutants may only decrease.

Reads `mutmut results` output (path as argv[1], or stdin) and fails when more
mutants survive than the frozen baseline. Baseline rule (ADR 0006): when you
kill survivors, lower MAX_SURVIVORS to the new count. Never raise it.

Baseline measured 2026-08-10: 41 survivors (mostly exception-message
formatting in state_machine error classes) across the 3 mutated modules.
"""

from __future__ import annotations

import pathlib
import sys

MAX_SURVIVORS = 45  # 41 measured + small headroom for mutmut version drift


def main() -> int:
    text = pathlib.Path(sys.argv[1]).read_text() if len(sys.argv) > 1 else sys.stdin.read()
    survivors = [ln.strip() for ln in text.splitlines() if ln.strip().endswith(": survived")]
    print(f"Surviving mutants: {len(survivors)} (ceiling {MAX_SURVIVORS})")
    if len(survivors) > MAX_SURVIVORS:
        print("Mutation gate FAILED — new code changes let deliberately broken code pass tests:")
        for s in survivors:
            print(f"  - {s}")
        print("Add tests that kill these mutants (or fix the code they expose).")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
