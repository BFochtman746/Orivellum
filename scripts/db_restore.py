#!/usr/bin/env python3
"""Orivellum — Database Restore from Backup.

Usage:
    uv run python scripts/db_restore.py <backup-folder>
    uv run python scripts/db_restore.py <backup-folder> --yes  # skip confirmation

Reads any backup folder produced by db_reset.py, verifies checksums,
and restores the database from it.  The live database is wiped first.

All output from db_reset.py's wipe + import + validate pipeline is reused
so the restore path is identical to the reset path — no code duplication.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root / "src") not in sys.path:
    sys.path.insert(0, str(_repo_root / "src"))

# Import shared logic from db_reset (same directory)
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

from db_reset import (  # noqa: E402
    _find_db,
    _get_tables,
    _build_fk_graph,
    export_backup,
    import_from_backup,
    topo_sort,
    validate_database,
    verify_manifest,
    wipe_database,
    _open_db,
)

import json
import time


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("backup_dir", help="Path to the backup folder produced by db_reset.py")
    parser.add_argument("--data-dir", default=None, help="Path to data directory (default: data/)")
    parser.add_argument(
        "--yes", action="store_true", help="Skip confirmation prompt (use in scripts)"
    )
    args = parser.parse_args()

    backup_dir = Path(args.backup_dir)
    if not backup_dir.exists():
        print(f"❌ Backup directory not found: {backup_dir}", file=sys.stderr)
        sys.exit(1)

    # Verify backup before touching anything
    print(f"Backup directory: {backup_dir}")
    print("Verifying checksums …")
    try:
        manifest = verify_manifest(backup_dir)
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)
    print(
        f"  ✅ {manifest['total_tables']} tables, {manifest['total_rows']} rows — checksums OK"
    )
    print(f"  Backup created: {manifest['created_at']}")

    db_path = _find_db(args.data_dir)
    print(f"\nTarget database: {db_path}")

    # Read current schema version
    conn = _open_db(db_path)
    tables, fts_virtual, _ = _get_tables(conn)
    deps = _build_fk_graph(conn, tables)
    tables_sorted = topo_sort(tables, deps)

    pre_schema_version = conn.execute(
        "SELECT value FROM settings WHERE scope='global' AND key='schema_version'"
    ).fetchone()
    pre_schema_version = pre_schema_version[0] if pre_schema_version else "0"

    # Confirmation gate
    if not args.yes:
        print()
        print("━" * 60)
        print("  ⚠️  DESTRUCTIVE OPERATION")
        print(f"  This will WIPE and restore {db_path}")
        print(f"  from backup: {backup_dir}")
        print("━" * 60)
        answer = input('\nType "yes" to proceed, anything else to abort: ').strip()
        if answer != "yes":
            print("Aborted — no changes made.")
            sys.exit(0)

    # Wipe
    print("\nWiping database …")
    t0 = time.monotonic()
    try:
        wipe_database(conn, tables_sorted)
    except RuntimeError as e:
        print(f"\n❌ Wipe failed: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"  Wipe complete in {round(time.monotonic() - t0, 2)}s")

    # Re-import
    print("\nRestoring from backup …")
    t0 = time.monotonic()
    try:
        stats = import_from_backup(conn, tables_sorted, backup_dir, fts_virtual, manifest)
    except RuntimeError as e:
        print(f"\n❌ Restore failed: {e}", file=sys.stderr)
        print("Database may be in a partial state.", file=sys.stderr)
        sys.exit(1)
    elapsed = round(time.monotonic() - t0, 2)
    imported = sum(s["imported"] for s in stats.values())
    cleared = sum(
        s["exported"] for s in stats.values()
        if s.get("reason") == "log_operational"
    )
    print(f"  Restore complete in {elapsed}s — {imported} rows imported, {cleared} log rows cleared")

    # Validate
    print("\nValidating …")
    result = validate_database(
        conn, tables_sorted, manifest, fts_virtual, pre_schema_version
    )
    for line in result["report"]:
        print(line)

    if result["errors"]:
        print("\n❌ VALIDATION FAILED — errors:")
        for err in result["errors"]:
            print(f"  • {err}")
        sys.exit(1)

    print()
    print("━" * 60)
    print("  ✅ Restore complete")
    print(f"  Rows imported : {imported}")
    print(f"  Log rows cleared: {cleared}")
    print("━" * 60)


if __name__ == "__main__":
    main()
