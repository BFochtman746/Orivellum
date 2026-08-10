#!/usr/bin/env python3
"""Orivellum System Diagnostic — standalone CLI runner.

Usage:
    uv run python scripts/run_diagnostics.py [--vacuum] [--json] [--out FILE]

Options:
    --vacuum    Run SQLite VACUUM after checks (compacts and rebuilds the DB)
    --json      Output raw JSON instead of Markdown
    --out FILE  Write report to FILE instead of stdout

The Markdown report is designed to be copy-pasted directly to an AI assistant
for a complete evaluation of system health.

Examples:
    uv run python scripts/run_diagnostics.py
    uv run python scripts/run_diagnostics.py --vacuum --out diag.md
    uv run python scripts/run_diagnostics.py --json > diag.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# Allow running from the project root without installing the package.
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, os.path.join(_repo_root, "src"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a full Orivellum system diagnostic and produce a report."
    )
    parser.add_argument(
        "--vacuum",
        action="store_true",
        help="Run SQLite VACUUM after checks (safe, recommended periodically)",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of Markdown")
    parser.add_argument("--out", metavar="FILE", help="Write report to FILE (default: stdout)")
    args = parser.parse_args()

    print("⏳ Running Orivellum system diagnostic…", file=sys.stderr)
    t0 = time.monotonic()

    from orivellum.capabilities.diagnostics import run_full_diagnostic
    from orivellum.configuration.config import load_config
    from orivellum.database.db import OrivellumDB

    cfg = load_config()
    data_dir = os.environ.get("ORIVELLUM_DATA_DIR", "data")
    db_path = os.path.join(data_dir, "orivellum.db")

    if not os.path.exists(db_path):
        # Fallback: look for db in common locations
        alt = os.path.join(_repo_root, "data", "orivellum.db")
        if os.path.exists(alt):
            db_path = alt
        else:
            print(f"ERROR: Database not found at {db_path} or {alt}", file=sys.stderr)
            print("Make sure the API server has been started at least once.", file=sys.stderr)
            sys.exit(1)

    db = OrivellumDB(db_path)

    if args.vacuum:
        print("ℹ️  --vacuum flag set: will compact the database after checks.", file=sys.stderr)

    result = run_full_diagnostic(db, cfg, vacuum=args.vacuum)

    elapsed = round(time.monotonic() - t0, 2)
    summary = result["summary"]
    print(
        f"✅ Done in {elapsed}s — "
        f"{summary['ok']} OK  {summary['warn']} WARN  "
        f"{summary['error']} ERROR  ({summary['total']} checks total)",
        file=sys.stderr,
    )

    if summary["error"] > 0:
        print(f"❌ {summary['error']} ERROR(s) found — see report for details.", file=sys.stderr)
    elif summary["warn"] > 0:
        print(f"⚠️  {summary['warn']} WARNING(s) found — review recommended.", file=sys.stderr)
    else:
        print("🎉 All checks passed — system looks healthy!", file=sys.stderr)

    output = (
        json.dumps(result, indent=2, ensure_ascii=False) if args.json else result["markdown_report"]
    )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"📄 Report written to {args.out}", file=sys.stderr)
    else:
        print(output)


if __name__ == "__main__":
    main()
