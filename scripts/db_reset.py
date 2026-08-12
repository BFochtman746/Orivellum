#!/usr/bin/env python3
"""Orivellum — Full Database Reset & Clean Re-import Tool.

Usage:
    uv run python scripts/db_reset.py --dry-run       # Pre-flight report, no changes
    uv run python scripts/db_reset.py --backup-only   # Timestamped backup and exit
    uv run python scripts/db_reset.py                 # Full cycle (requires "yes" confirmation)

Full cycle:
  1. Export verified backup (JSON + schema + manifest + readme)
  2. Print dry-run report showing every table's action
  3. Prompt for explicit "yes" — no auto-proceed
  4. Wipe database in reverse FK order (single transaction)
  5. Re-import user data in forward FK order (per-table savepoints)
  6. Validate: PRAGMA integrity_check, PRAGMA foreign_key_check, row-count match
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import sys
import textwrap
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

# Allow running from the project root without installing the package.
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root / "src") not in sys.path:
    sys.path.insert(0, str(_repo_root / "src"))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_FTS_SHADOW_SUFFIXES = (
    "_data", "_idx", "_content", "_docsize", "_config", "_rowids",
)

_SQLITE_INTERNAL = frozenset({
    "sqlite_sequence", "sqlite_stat1", "sqlite_stat2",
    "sqlite_stat3", "sqlite_stat4",
})

# Rows are cleared (DELETE all) but NOT re-imported on reset.
# These are operational logs with no user-authored content.
_LOG_OPERATIONAL = frozenset({"access_log", "outbox"})

# Shown as "derived_cache" in the dry-run report; re-imported by default
# because losing them forces expensive nightly rebuilds.
_DERIVED_CACHE = frozenset({"work_gap_cache", "minhash_sig"})


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _open_db(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# BLOB-safe JSON serialization
# ---------------------------------------------------------------------------

def _json_default(obj: object) -> object:
    """JSON encoder hook: converts bytes to tagged base64 so BLOBs survive a
    round-trip through JSON without data loss or TypeError."""
    if isinstance(obj, bytes):
        return {"__blob__": base64.b64encode(obj).decode("ascii")}
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _revive_row(row: dict) -> dict:
    """Inverse of _json_default: converts tagged base64 dicts back to bytes
    so BLOB columns can be re-inserted into SQLite without corruption."""
    result: dict = {}
    for k, v in row.items():
        if isinstance(v, dict) and tuple(v) == ("__blob__",):
            result[k] = base64.b64decode(v["__blob__"])
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Table discovery and classification
# ---------------------------------------------------------------------------

def _get_fts_virtual_names(conn: sqlite3.Connection) -> set[str]:
    """Return names of FTS5 virtual tables (not their shadows)."""
    return {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND (sql LIKE '%USING fts5%' OR sql LIKE '%using fts5%')"
        ).fetchall()
    }


def _get_fts_shadow_names(fts_virtual: set[str]) -> set[str]:
    shadows = set()
    for vt in fts_virtual:
        for sfx in _FTS_SHADOW_SUFFIXES:
            shadows.add(vt + sfx)
    return shadows


def _get_tables(conn: sqlite3.Connection) -> tuple[list[str], set[str], set[str]]:
    """Return (all_user_tables, fts_virtual_set, fts_shadow_set).

    all_user_tables excludes sqlite_internal and fts_shadow tables.
    It INCLUDES fts_virtual tables so they are backed up and re-imported.
    """
    fts_virtual = _get_fts_virtual_names(conn)
    fts_shadows = _get_fts_shadow_names(fts_virtual)

    all_tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    ]

    result = []
    for name in all_tables:
        if name in _SQLITE_INTERNAL or name.startswith("sqlite_"):
            continue
        if name in fts_shadows:
            continue
        result.append(name)

    return sorted(result), fts_virtual, fts_shadows


def classify_table(name: str, fts_virtual: set[str]) -> str:
    """Return classification: user_data / log_operational / derived_cache / fts_virtual."""
    if name in fts_virtual:
        return "fts_virtual"
    if name in _LOG_OPERATIONAL:
        return "log_operational"
    if name in _DERIVED_CACHE:
        return "derived_cache"
    return "user_data"


# ---------------------------------------------------------------------------
# FK dependency graph + topological sort
# ---------------------------------------------------------------------------

def _build_fk_graph(conn: sqlite3.Connection, tables: list[str]) -> dict[str, set[str]]:
    """Build {table: {tables_it_depends_on}}.

    An edge A → {B} means A has a FK pointing to B.
    B must be inserted before A; A must be deleted before B.
    FTS virtual tables have no FKs and sort to roots.
    """
    tables_set = set(tables)
    deps: dict[str, set[str]] = {t: set() for t in tables}

    for table in tables:
        try:
            fks = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
            for fk in fks:
                ref_table = fk[2]  # referenced table
                if ref_table in tables_set and ref_table != table:
                    deps[table].add(ref_table)
        except Exception:
            pass

    return deps


def topo_sort(tables: list[str], deps: dict[str, set[str]]) -> list[str]:
    """Kahn's algorithm — returns tables in insertion order (roots first).

    Tables with no dependencies (e.g. objects, settings) come first.
    Alphabetical tie-breaking for determinism.
    Any tables stuck in a cycle are appended at the end.
    """
    in_degree: dict[str, int] = {t: 0 for t in tables}
    children: dict[str, list[str]] = {t: [] for t in tables}

    for table, table_deps in deps.items():
        for dep in table_deps:
            if dep in in_degree:
                in_degree[table] += 1
                children[dep].append(table)

    queue = deque(sorted(t for t in tables if in_degree[t] == 0))
    result: list[str] = []

    while queue:
        table = queue.popleft()
        result.append(table)
        for child in sorted(children.get(table, [])):
            in_degree[child] -= 1
            if in_degree[child] == 0:
                queue.append(child)

    # Append any remaining (cycles — shouldn't exist in a clean schema)
    seen = set(result)
    result.extend(sorted(t for t in tables if t not in seen))
    return result


# ---------------------------------------------------------------------------
# Backup exporter
# ---------------------------------------------------------------------------

def _export_table_rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    cur = conn.execute(f'SELECT * FROM "{table}"')
    col_names = [d[0] for d in cur.description]
    return [{col_names[i]: row[i] for i in range(len(col_names))} for row in cur.fetchall()]


def export_backup(
    conn: sqlite3.Connection,
    backup_dir: Path,
    tables_sorted: list[str],
    fts_virtual: set[str],
) -> dict:
    """Write backup to backup_dir. Returns the manifest dict."""
    tables_dir = backup_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    # 1. Schema SQL dump
    schema_rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type DESC, name"
    ).fetchall()
    schema_sql = "\n\n".join(r[0] for r in schema_rows if r[0])
    (backup_dir / "schema.sql").write_text(schema_sql + "\n", encoding="utf-8")

    # 2. Per-table JSON files
    manifest_tables = []
    for table in tables_sorted:
        rows = _export_table_rows(conn, table)
        content = json.dumps(rows, ensure_ascii=False, default=_json_default).encode("utf-8")
        json_path = tables_dir / f"{table}.json"
        json_path.write_bytes(content)
        sha = _sha256_bytes(content)
        manifest_tables.append({
            "name": table,
            "row_count": len(rows),
            "sha256": sha,
            "file": f"tables/{table}.json",
            "classification": classify_table(table, fts_virtual),
        })

    # 3. Manifest
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tables": manifest_tables,
        "total_tables": len(manifest_tables),
        "total_rows": sum(t["row_count"] for t in manifest_tables),
    }
    manifest_path = backup_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 4. Readme with restore instructions
    readme = textwrap.dedent(f"""
        Orivellum Database Backup
        ========================
        Created : {manifest["created_at"]}
        Tables  : {manifest["total_tables"]}
        Rows    : {manifest["total_rows"]}

        Files
        -----
          manifest.json  — table names, row counts, SHA-256 checksums
          schema.sql     — full DDL (re-create tables from scratch)
          tables/        — one JSON array per table

        To restore from this backup:
          uv run python scripts/db_restore.py {backup_dir.resolve()}

        To verify checksums manually:
          cd {backup_dir.resolve()}
          python -c "
        import json, hashlib
        m = json.load(open('manifest.json'))
        for t in m['tables']:
            h = hashlib.sha256(open(t['file'],'rb').read()).hexdigest()
            print('OK' if h == t['sha256'] else 'MISMATCH', t['name'])
        "

        WARNING: Restoring will WIPE the live database. Verify checksums first.
    """).strip()
    (backup_dir / "readme.txt").write_text(readme + "\n", encoding="utf-8")

    return manifest


def verify_manifest(backup_dir: Path) -> dict:
    """Verify every file in manifest.json against its SHA-256. Returns manifest."""
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"manifest.json not found in {backup_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    failed = []
    for t in manifest["tables"]:
        file_path = backup_dir / t["file"]
        if not file_path.exists():
            failed.append(f"  {t['name']}: file missing ({t['file']})")
            continue
        actual = _sha256_bytes(file_path.read_bytes())
        if actual != t["sha256"]:
            failed.append(
                f"  {t['name']}: SHA-256 mismatch "
                f"(expected {t['sha256'][:12]}…, got {actual[:12]}…)"
            )

    if failed:
        raise RuntimeError("Manifest verification failed:\n" + "\n".join(failed))

    return manifest


def check_restore_compatibility(
    conn: sqlite3.Connection,
    manifest: dict,
    fts_virtual: set[str],
) -> None:
    """Raise RuntimeError before any destructive step if the target DB is
    missing tables that exist in the backup manifest.

    This prevents silent data loss where a table present in the backup
    (e.g. from a newer schema) cannot be imported because the target DB
    was never migrated to include it.

    Call this BEFORE wipe_database so the database is still intact when
    the error is reported.

    Tables classified as log_operational are exempt: they are intentionally
    cleared during reset and never re-imported.
    """
    live_tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

    missing: list[str] = []
    for t in manifest["tables"]:
        name = t["name"]
        cl = t.get("classification", classify_table(name, fts_virtual))
        # log_operational tables are cleared and never re-imported, so missing
        # ones do not block the restore.
        if cl == "log_operational":
            continue
        if name not in live_tables:
            missing.append(f"  {name!r} (classification: {cl})")

    if missing:
        raise RuntimeError(
            f"Restore compatibility check failed — {len(missing)} table(s) from the backup "
            f"are absent from the target database.\n"
            f"Apply the missing schema migrations before restoring this backup.\n"
            + "\n".join(missing)
        )


# ---------------------------------------------------------------------------
# Dry-run report
# ---------------------------------------------------------------------------

def dry_run_report(
    conn: sqlite3.Connection,
    tables_sorted: list[str],
    fts_virtual: set[str],
) -> None:
    """Print a pre-flight report. Does not touch the database."""
    print("\n=== Orivellum DB Reset — Dry-Run Report ===\n")

    _ACTION = {
        "user_data":       "re-import",
        "derived_cache":   "re-import (derived cache)",
        "fts_virtual":     "re-import (FTS virtual — shadows rebuilt by SQLite)",
        "log_operational": "CLEAR — not re-imported",
    }

    col_w = max((len(t) for t in tables_sorted), default=20)
    hdr = f"{'Table':<{col_w}}  {'Class':<16}  {'Rows':>8}  Action"
    print(hdr)
    print("-" * len(hdr))

    totals: dict[str, int] = {
        "user_data": 0, "derived_cache": 0,
        "fts_virtual": 0, "log_operational": 0,
    }

    for table in tables_sorted:
        cl = classify_table(table, fts_virtual)
        try:
            n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except Exception:
            n = 0
        totals[cl] = totals.get(cl, 0) + n
        action = _ACTION.get(cl, "re-import")
        print(f"{table:<{col_w}}  {cl:<16}  {n:>8}  {action}")

    print()
    print("Summary")
    print("-------")
    print(f"  User data rows to re-import     : {totals['user_data']:>8}")
    print(f"  Derived cache rows to re-import : {totals['derived_cache']:>8}")
    print(f"  FTS virtual rows to re-import   : {totals['fts_virtual']:>8}")
    print(f"  Log rows CLEARED (not imported) : {totals['log_operational']:>8}")
    print()
    print("NOTES")
    print("  • access_log and outbox are operational logs — cleared, NOT re-imported.")
    print("  • audit_log is user provenance/compliance data — IS re-imported.")
    print("  • FTS shadow tables (e.g. works_fts_data) are managed by SQLite —")
    print("    they are rebuilt automatically when FTS virtual rows are inserted.")
    print()


# ---------------------------------------------------------------------------
# Wipe step
# ---------------------------------------------------------------------------

def wipe_database(
    conn: sqlite3.Connection,
    tables_sorted: list[str],
) -> None:
    """Delete all rows from all tracked tables in reverse FK order.

    Uses a single transaction. Rolls back and raises on any error.
    FTS shadow tables and sqlite internals are not in tables_sorted and
    are skipped automatically.
    """
    reverse_order = list(reversed(tables_sorted))

    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN")
    try:
        for table in reverse_order:
            conn.execute(f'DELETE FROM "{table}"')
        conn.execute("COMMIT")
    except Exception as exc:
        conn.execute("ROLLBACK")
        raise RuntimeError(f"Wipe failed on table — rolled back: {exc}") from exc


# ---------------------------------------------------------------------------
# Re-import step
# ---------------------------------------------------------------------------

def import_from_backup(
    conn: sqlite3.Connection,
    tables_sorted: list[str],
    backup_dir: Path,
    fts_virtual: set[str],
    manifest: dict,
) -> dict:
    """Insert rows from backup JSON files in forward FK order.

    Skips: log_operational tables (they are intentionally cleared).
    FTS shadow tables are not in tables_sorted (managed by SQLite).

    Each table's insert is wrapped in a SAVEPOINT so a failure is isolated —
    the exact failing table and row are reported, and the outer transaction
    is rolled back to leave the DB in its pre-import state.

    Returns stats dict: {table: {"exported": N, "imported": M, "skipped": bool}}
    """
    # Index manifest by table name
    manifest_by_name = {t["name"]: t for t in manifest["tables"]}

    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN")

    stats: dict[str, dict] = {}

    try:
        for table in tables_sorted:
            cl = classify_table(table, fts_virtual)
            if cl == "log_operational":
                m = manifest_by_name.get(table, {})
                stats[table] = {
                    "exported": m.get("row_count", 0),
                    "imported": 0,
                    "skipped": True,
                    "reason": "log_operational",
                }
                continue

            json_path = backup_dir / "tables" / f"{table}.json"
            if not json_path.exists():
                stats[table] = {"exported": 0, "imported": 0, "skipped": True, "reason": "no backup file"}
                continue

            rows = json.loads(json_path.read_text(encoding="utf-8"))
            if not rows:
                stats[table] = {"exported": 0, "imported": 0, "skipped": False}
                continue

            col_names = list(rows[0].keys())
            placeholders = ", ".join("?" for _ in col_names)
            col_list = ", ".join(f'"{c}"' for c in col_names)
            sql = f'INSERT OR REPLACE INTO "{table}" ({col_list}) VALUES ({placeholders})'

            sp = f"sp_{table}"
            conn.execute(f"SAVEPOINT {sp}")
            try:
                imported = 0
                for i, row in enumerate(rows):
                    # _revive_row decodes any {"__blob__": "<base64>"} values back
                    # to bytes so BLOB columns round-trip correctly.
                    revived = _revive_row(row)
                    values = [revived.get(c) for c in col_names]
                    try:
                        conn.execute(sql, values)
                        imported += 1
                    except sqlite3.IntegrityError as e:
                        conn.execute(f"ROLLBACK TO {sp}")
                        conn.execute("ROLLBACK")
                        raise RuntimeError(
                            f"FK/uniqueness violation in table '{table}', row {i}: {e}\n"
                            f"  Row data: {json.dumps(row, ensure_ascii=False, default=str)[:200]}"
                        ) from e
                conn.execute(f"RELEASE {sp}")
                stats[table] = {
                    "exported": len(rows),
                    "imported": imported,
                    "skipped": False,
                }
            except RuntimeError:
                raise
            except Exception as exc:
                conn.execute(f"ROLLBACK TO {sp}")
                conn.execute("ROLLBACK")
                raise RuntimeError(f"Error importing table '{table}': {exc}") from exc

        conn.execute("COMMIT")
    except RuntimeError:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    return stats


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_database(
    conn: sqlite3.Connection,
    tables_sorted: list[str],
    manifest: dict,
    fts_virtual: set[str],
    pre_reset_schema_version: str,
) -> dict:
    """Run integrity_check, FK check, row-count comparison.

    Returns {ok: bool, errors: [...], report: [...]}.
    Exits non-zero if any check fails (caller must check ok).
    """
    errors: list[str] = []
    report: list[str] = []

    # 1. PRAGMA integrity_check
    ic = conn.execute("PRAGMA integrity_check").fetchall()
    if len(ic) == 1 and ic[0][0] == "ok":
        report.append("  ✅ integrity_check: ok")
    else:
        msgs = [r[0] for r in ic]
        errors.append("integrity_check failed: " + "; ".join(msgs[:5]))
        report.append(f"  ❌ integrity_check: {msgs[:3]}")

    # 2. PRAGMA foreign_key_check
    fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if not fk_violations:
        report.append("  ✅ foreign_key_check: no violations")
    else:
        for v in fk_violations[:5]:
            errors.append(f"FK violation: table={v[0]} rowid={v[1]} parent={v[2]}")
        report.append(f"  ❌ foreign_key_check: {len(fk_violations)} violation(s)")

    # 3. Row count comparison
    manifest_by_name = {t["name"]: t for t in manifest["tables"]}
    mismatch_count = 0
    skipped_log = 0

    for table in tables_sorted:
        cl = classify_table(table, fts_virtual)
        if cl == "log_operational":
            live_n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if live_n != 0:
                errors.append(
                    f"Log table '{table}' should be 0 rows after reset, found {live_n}"
                )
            skipped_log += 1
            continue

        expected = manifest_by_name.get(table, {}).get("row_count", 0)
        try:
            actual = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except Exception:
            actual = -1
        if actual != expected:
            errors.append(f"Row count mismatch in '{table}': expected {expected}, got {actual}")
            mismatch_count += 1

    if mismatch_count == 0:
        report.append(f"  ✅ Row counts: all {len(tables_sorted) - skipped_log} re-imported tables match manifest")
    else:
        report.append(f"  ❌ Row counts: {mismatch_count} table(s) have count mismatches (see errors above)")

    # 4. Schema version check
    try:
        live_sv = conn.execute(
            "SELECT value FROM settings WHERE scope='global' AND key='schema_version'"
        ).fetchone()
        live_sv = live_sv[0] if live_sv else "?"
        if live_sv == pre_reset_schema_version:
            report.append(f"  ✅ schema_version: {live_sv} (unchanged)")
        else:
            errors.append(
                f"schema_version changed: was {pre_reset_schema_version!r}, now {live_sv!r}"
            )
            report.append(f"  ❌ schema_version: {live_sv!r} ≠ {pre_reset_schema_version!r}")
    except Exception as e:
        errors.append(f"Could not check schema_version: {e}")

    return {"ok": len(errors) == 0, "errors": errors, "report": report}


# ---------------------------------------------------------------------------
# Locate database
# ---------------------------------------------------------------------------

def _find_db(data_dir: str | None = None) -> Path:
    if data_dir:
        p = Path(data_dir) / "orivellum.db"
        if p.exists():
            return p
    # Try env var
    env_dir = os.environ.get("ORIVELLUM_DATA_DIR", "data")
    p = Path(env_dir) / "orivellum.db"
    if p.exists():
        return p
    p = _repo_root / "data" / "orivellum.db"
    if p.exists():
        return p
    raise FileNotFoundError(
        "orivellum.db not found. Set ORIVELLUM_DATA_DIR or pass --data-dir."
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Print report only, no changes")
    parser.add_argument("--backup-only", action="store_true", help="Create backup and exit")
    parser.add_argument("--data-dir", default=None, help="Path to data directory (default: data/)")
    parser.add_argument(
        "--backup-dir",
        default=None,
        help="Override backup root (default: data/backups/reset_YYYYMMDD_HHMMSS/)",
    )
    parser.add_argument(
        "--yes", action="store_true", help="Skip confirmation prompt (use in scripts)"
    )
    args = parser.parse_args()

    db_path = _find_db(args.data_dir)
    print(f"Database: {db_path}", file=sys.stderr)

    conn = _open_db(db_path)

    # Discover tables and build FK order
    tables, fts_virtual, fts_shadows = _get_tables(conn)
    deps = _build_fk_graph(conn, tables)
    tables_sorted = topo_sort(tables, deps)

    # Always start with a dry-run report
    dry_run_report(conn, tables_sorted, fts_virtual)

    if args.dry_run:
        print("--dry-run: no changes made.")
        sys.exit(0)

    # Read schema version before any changes
    pre_schema_version = conn.execute(
        "SELECT value FROM settings WHERE scope='global' AND key='schema_version'"
    ).fetchone()
    pre_schema_version = pre_schema_version[0] if pre_schema_version else "0"

    # --- Backup step ---
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    if args.backup_dir:
        backup_dir = Path(args.backup_dir)
    else:
        backup_dir = db_path.parent / "backups" / f"reset_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)

    print(f"Exporting backup → {backup_dir} …")
    t0 = time.monotonic()
    manifest = export_backup(conn, backup_dir, tables_sorted, fts_virtual)
    elapsed = round(time.monotonic() - t0, 2)
    total_rows = manifest["total_rows"]
    print(
        f"  Backup complete in {elapsed}s: "
        f"{manifest['total_tables']} tables, {total_rows} rows"
    )
    print(f"  Manifest: {backup_dir / 'manifest.json'}")

    # Verify manifest before proceeding
    print("Verifying backup checksums …")
    try:
        verify_manifest(backup_dir)
        print("  ✅ All checksums match")
    except RuntimeError as e:
        print(f"  ❌ {e}", file=sys.stderr)
        print("Aborting — backup verification failed.", file=sys.stderr)
        sys.exit(1)

    if args.backup_only:
        print("--backup-only: exiting. No changes made.")
        sys.exit(0)

    # --- Schema compatibility check (before any destructive step) ---
    print("Checking restore compatibility …")
    try:
        check_restore_compatibility(conn, manifest, fts_virtual)
        print("  ✅ All backup tables present in target schema")
    except RuntimeError as e:
        print(f"  ❌ {e}", file=sys.stderr)
        print("Aborting — apply missing migrations before running reset.", file=sys.stderr)
        sys.exit(1)

    # --- Confirmation gate ---
    if not args.yes:
        print()
        print("━" * 60)
        print("  ⚠️  DESTRUCTIVE OPERATION")
        print(f"  This will WIPE and re-import {db_path}")
        print(f"  Backup is at: {backup_dir}")
        print("━" * 60)
        answer = input('\nType "yes" to proceed, anything else to abort: ').strip()
        if answer != "yes":
            print("Aborted — no changes made.")
            sys.exit(0)

    # --- Wipe step ---
    print("\nWiping database …")
    t0 = time.monotonic()
    try:
        wipe_database(conn, tables_sorted)
    except RuntimeError as e:
        print(f"\n❌ Wipe failed: {e}", file=sys.stderr)
        print(f"Backup is available at: {backup_dir}", file=sys.stderr)
        sys.exit(1)
    print(f"  Wipe complete in {round(time.monotonic() - t0, 2)}s")

    # --- Re-import step ---
    print("\nRe-importing from backup …")
    t0 = time.monotonic()
    try:
        stats = import_from_backup(conn, tables_sorted, backup_dir, fts_virtual, manifest)
    except RuntimeError as e:
        print(f"\n❌ Import failed: {e}", file=sys.stderr)
        print(f"Database is in a partial state. Restore from backup:", file=sys.stderr)
        print(f"  uv run python scripts/db_restore.py {backup_dir}", file=sys.stderr)
        sys.exit(1)
    import_elapsed = round(time.monotonic() - t0, 2)

    imported_total = sum(s["imported"] for s in stats.values())
    cleared_total = sum(
        s["exported"] for s in stats.values()
        if s.get("reason") == "log_operational"
    )
    skipped_total = sum(
        1 for s in stats.values() if s.get("skipped") and s.get("reason") != "log_operational"
    )

    print(f"  Import complete in {import_elapsed}s")
    print(f"  Rows imported : {imported_total}")
    print(f"  Log rows cleared (not imported): {cleared_total}")
    if skipped_total:
        print(f"  Tables skipped (no backup file): {skipped_total}")

    # --- Validation step ---
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
        print(f"\nBackup is available at: {backup_dir}")
        print("Restore with: uv run python scripts/db_restore.py {backup_dir}")
        sys.exit(1)

    print()
    print("━" * 60)
    print("  ✅ Database reset complete")
    print(f"  Rows imported : {imported_total}")
    print(f"  Log rows cleared: {cleared_total}")
    print(f"  Backup at     : {backup_dir}")
    print("━" * 60)


if __name__ == "__main__":
    main()
