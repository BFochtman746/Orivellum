"""Lint-debt ratchet — grandfathered files cannot accumulate NEW violations.

ruff.toml's per-file-ignores silence whole rule codes in baselined files, which
means a grandfathered file could quietly gain MORE violations of an ignored
code. This gate closes that hole: it re-lints every baselined file with the
ignores disabled (--isolated) and compares per-(file, code) violation counts
against the committed snapshot ``lint_ratchet_counts.json``.

Rules (ADR 0006):
- any count above its snapshot fails CI — fix the new violations;
- any count below its snapshot also fails CI, telling you to lower the
  snapshot — so the debt permanently ratchets down and can never bounce back;
- when a file's counts reach zero, remove it from the snapshot AND from
  ruff.toml's per-file-ignores.

Usage: python scripts/check_lint_ratchet.py           (verify)
       python scripts/check_lint_ratchet.py --update  (regenerate, only for
       shrinking — CI runs verify mode and the diff shows any tampering)
"""

from __future__ import annotations

import collections
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAPSHOT = pathlib.Path(__file__).resolve().parent / "lint_ratchet_counts.json"
RUFF_TOML = ROOT / "ruff.toml"
SELECT = "E,F,I,UP,B,C90,SIM,RET"


def baselined_files() -> list[str]:
    files = []
    in_section = False
    for line in RUFF_TOML.read_text().splitlines():
        if line.strip() == "[lint.per-file-ignores]":
            in_section = True
            continue
        if in_section:
            m = re.match(r'^"([^"]+)"\s*=', line)
            if m:
                files.append(m.group(1))
            elif line.startswith("["):
                break
    return files


def current_counts(files: list[str]) -> dict[str, dict[str, int]]:
    existing = [f for f in files if (ROOT / f).exists()]
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--exit-zero",
            "--line-length",
            "100",
            "--target-version",
            "py312",
            "--select",
            SELECT,
            "--output-format",
            "json",
            *existing,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    counts: dict[str, dict[str, int]] = collections.defaultdict(
        lambda: collections.defaultdict(int)
    )
    for item in json.loads(out.stdout):
        rel = str(pathlib.Path(item["filename"]).relative_to(ROOT))
        counts[rel][item["code"]] += 1
    return {f: dict(sorted(c.items())) for f, c in sorted(counts.items())}


def main() -> int:
    files = baselined_files()
    counts = current_counts(files)

    if "--update" in sys.argv:
        SNAPSHOT.write_text(json.dumps(counts, indent=1) + "\n")
        print(f"Snapshot regenerated for {len(counts)} files.")
        return 0

    snapshot = json.loads(SNAPSHOT.read_text())
    failures: list[str] = []
    for f in sorted(set(snapshot) | set(counts)):
        old, new = snapshot.get(f, {}), counts.get(f, {})
        for code in sorted(set(old) | set(new)):
            o, n = old.get(code, 0), new.get(code, 0)
            if n > o:
                failures.append(f"{f}: {code} grew {o} -> {n} — fix the new violations")
            elif n < o:
                failures.append(
                    f"{f}: {code} shrank {o} -> {n} — run "
                    "`python scripts/check_lint_ratchet.py --update` to lock in the win"
                )

    if failures:
        print("Lint-debt ratchet violations:")
        for x in failures:
            print(f"  - {x}")
        return 1
    total = sum(sum(c.values()) for c in counts.values())
    print(f"Lint ratchet OK ({len(counts)} baselined files, {total} frozen violations).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
