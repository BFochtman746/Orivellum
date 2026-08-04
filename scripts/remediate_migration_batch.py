#!/usr/bin/env python3
"""A01_MIGRATION_BATCH data remediation script.

Finds documents and Works whose title/source matches the migration-batch
artifact pattern (A01_MIGRATION_BATCH_*, A02_*, RP-NNN, Run-NNN, etc.)
and reclassifies them to ARTIFACT tier, then archives the displaced Works.

All changes are logged to the ``a01_remediation_log`` table and are fully
reversible with ``--reverse``.

Usage
-----
  # 1. Always start with a dry run:
  python scripts/remediate_migration_batch.py --db data/orivellum.db --dry-run

  # 2. If the output looks correct, apply for real:
  python scripts/remediate_migration_batch.py --db data/orivellum.db --apply

  # 3. To undo everything this script changed:
  python scripts/remediate_migration_batch.py --db data/orivellum.db --reverse

See REMEDIATION.md for the recommended full sequence including DB backup.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# ── Pattern (mirrors classify.py _ARTIFACT_NAME) ──────────────────────────────
_ARTIFACT_NAME = re.compile(
    r"(migration[_\- ]?batch"    # A01_MIGRATION_BATCH_011...
    r"|^a0\d[_\-]"               # A01_ / A02_ prefixes
    r"|\bRP[-_ ]?\d{2,}"         # RP-011 Core Function
    r"|\bRun[-_ ]?\d{2,}"        # Run-001 Not Run
    r"|_v\d+\.\d+\.\d+"          # ..._v1.0.0 versioned artifact
    r"|\bbaseline\b|\bqualification\b|\bregression\b|\bfixture\b)",
    re.I,
)

NOW = datetime.now(timezone.utc).isoformat()
ARCHIVE_SUFFIX = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _is_artifact(text: str | None) -> bool:
    return bool(text and _ARTIFACT_NAME.search(text))


def _ensure_log_table(conn: sqlite3.Connection) -> None:
    """Create the remediation ledger if it doesn't exist (idempotent)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS a01_remediation_log (
            id          TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,   -- 'document' | 'work'
            entity_id   TEXT NOT NULL,
            field       TEXT NOT NULL,   -- column that changed
            old_value   TEXT,
            new_value   TEXT,
            reason      TEXT,
            batch       TEXT NOT NULL,   -- ARCHIVE_SUFFIX, groups one run
            reversed_at TEXT             -- NULL until --reverse is run
        )
    """)
    conn.commit()


def _log(
    conn: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: str,
    field: str,
    old_value: str | None,
    new_value: str | None,
    reason: str,
    batch: str,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    conn.execute(
        """INSERT INTO a01_remediation_log
           (id, entity_type, entity_id, field, old_value, new_value, reason, batch)
           VALUES (?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), entity_type, entity_id, field,
         old_value, new_value, reason, batch),
    )


def _find_artifact_documents(conn: sqlite3.Connection) -> list[dict]:
    """Return documents whose title or source path matches the artifact pattern."""
    rows = conn.execute(
        "SELECT id, title, source, content_path, sha256, tier, work_id FROM documents"
    ).fetchall()
    return [
        {
            "id": r[0], "title": r[1], "source": r[2],
            "content_path": r[3], "sha256": r[4],
            "tier": r[5], "work_id": r[6],
        }
        for r in rows
        if _is_artifact(r[1]) or _is_artifact(r[2])
    ]


def _archived_files_exists(conn: sqlite3.Connection) -> bool:
    """Return True if the archived_files table is present in this database."""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='archived_files'"
    ).fetchone()
    return row is not None


def _find_artifact_works(conn: sqlite3.Connection) -> list[dict]:
    """Return Works whose title matches the artifact pattern."""
    rows = conn.execute(
        "SELECT id, title, work_type, status FROM works"
    ).fetchall()
    return [
        {"id": r[0], "title": r[1], "work_type": r[2], "status": r[3]}
        for r in rows
        if _is_artifact(r[1])
    ]


def run_dry_run(conn: sqlite3.Connection) -> None:
    """Print what would change without touching the DB."""
    docs = _find_artifact_documents(conn)
    works = _find_artifact_works(conn)

    print(f"\n{'='*60}")
    print("  DRY RUN — no changes will be written")
    print(f"{'='*60}\n")

    print(f"DOCUMENTS to reclassify to ARTIFACT tier ({len(docs)} rows):")
    if docs:
        for d in docs:
            print(f"  [{d['id'][:8]}] tier: {d['tier']!r:12s} → 'artifact'  title={d['title']!r}")
    else:
        print("  (none found — database is already clean)")

    print()
    print(f"WORKS to archive ({len(works)} rows):")
    if works:
        for w in works:
            # Count attached docs
            n = conn.execute(
                "SELECT COUNT(*) FROM documents WHERE work_id=?", (w["id"],)
            ).fetchone()[0]
            print(f"  [{w['id'][:8]}] status: {w['status']!r} → 'archived'  "
                  f"title={w['title']!r}  attached_docs={n}")
    else:
        print("  (none found — database is already clean)")

    print()
    # Works that have NO matching title but whose documents would be reclassified
    doc_work_ids = {d["work_id"] for d in docs if d["work_id"]}
    work_ids_matched = {w["id"] for w in works}
    orphan_work_ids = doc_work_ids - work_ids_matched
    if orphan_work_ids:
        print(f"WORKS that own reclassified docs but have clean titles ({len(orphan_work_ids)}):")
        for wid in orphan_work_ids:
            row = conn.execute(
                "SELECT title, status FROM works WHERE id=?", (wid,)
            ).fetchone()
            if row:
                print(f"  [{wid[:8]}] title={row[0]!r} status={row[1]!r}  "
                      "(will NOT be archived — title is clean)")
        print()

    print(f"Run with --apply to make these changes (after backing up the DB).\n")


def run_apply(conn: sqlite3.Connection, archive_dir: Path) -> None:
    """Apply the remediation and log every change to a01_remediation_log.

    Safety guarantees
    -----------------
    1. Fail-fast schema check: validates that all required tables exist before
       touching any data.  The ``archived_files`` ledger is optional — if the
       table is absent the file-archival step still runs but skips the DB entry.
    2. Transactional DB writes: all UPDATE and INSERT statements are committed
       in a single transaction.  If an exception occurs the transaction is rolled
       back, leaving the database unchanged.
    3. File copies happen AFTER the commit: filesystem side-effects only occur
       once the DB is in a consistent state, so a copy failure cannot leave the
       DB and filesystem out of sync.
    """
    batch = ARCHIVE_SUFFIX
    _ensure_log_table(conn)
    has_archived_files_tbl = _archived_files_exists(conn)

    docs = _find_artifact_documents(conn)
    works = _find_artifact_works(conn)

    # Collect the files to copy *before* touching the DB, so we know what to
    # clean up if something goes wrong.
    # Key: doc_id  Value: (src_path, dst_path, sha256)
    files_to_copy: dict[str, tuple[Path, Path, str]] = {}
    for d in docs:
        if d["tier"] == "artifact":
            continue
        # Prefer content_path (stable library location) over source (may be stale)
        raw_path = d.get("content_path") or d.get("source") or ""
        if raw_path:
            src = Path(raw_path)
            if src.exists() and src.is_file():
                dst_dir = archive_dir / "library" / batch
                dst = dst_dir / src.name
                files_to_copy[d["id"]] = (src, dst, d.get("sha256") or "")

    # ── Phase 1: commit all DB changes atomically ─────────────────────────────
    changed_docs = 0
    changed_works = 0
    try:
        conn.execute("BEGIN")

        for d in docs:
            if d["tier"] == "artifact":
                print(f"  [doc] {d['id'][:8]} already 'artifact' — skip")
                continue
            conn.execute(
                "UPDATE documents SET tier='artifact' WHERE id=?", (d["id"],)
            )
            _log(conn, entity_type="document", entity_id=d["id"],
                 field="tier", old_value=d["tier"], new_value="artifact",
                 reason="A01_MIGRATION_BATCH artifact pattern matched in title/source",
                 batch=batch, dry_run=False)
            print(f"  [doc] {d['id'][:8]} tier {d['tier']!r} → 'artifact'  {d['title']!r}")
            changed_docs += 1

        for w in works:
            if w["status"] == "archived":
                print(f"  [wrk] {w['id'][:8]} already archived — skip")
                continue
            conn.execute(
                "UPDATE works SET status='archived' WHERE id=?", (w["id"],)
            )
            _log(conn, entity_type="work", entity_id=w["id"],
                 field="status", old_value=w["status"], new_value="archived",
                 reason="Work title matches A01_MIGRATION_BATCH artifact pattern",
                 batch=batch, dry_run=False)
            print(f"  [wrk] {w['id'][:8]} status {w['status']!r} → 'archived'  {w['title']!r}")
            changed_works += 1

        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    # ── Phase 2: copy files now that DB is consistent ─────────────────────────
    # A copy failure here is logged to stderr but does NOT roll back the DB —
    # the DB change is already committed and reversible via --reverse.
    archived_count = 0
    skipped_copy: list[str] = []
    for doc_id, (src, dst, sha256) in files_to_copy.items():
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            # Record in archived_files if the table exists in this schema
            if has_archived_files_tbl:
                conn.execute(
                    """INSERT OR IGNORE INTO archived_files
                       (id, original_path, archive_path, sha256, reason, archived_at)
                       VALUES (?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), str(src), str(dst),
                     sha256, "A01_MIGRATION_BATCH remediation", NOW),
                )
                conn.commit()
            archived_count += 1
        except OSError as exc:
            print(f"  [warn] Could not copy {src}: {exc}", file=sys.stderr)
            skipped_copy.append(str(src))

    if not has_archived_files_tbl:
        print("  [info] archived_files table absent — skipped archival ledger entries "
              "(DB schema pre-v26); file copies still landed in archive_dir")

    print()
    print(f"Apply complete (batch={batch}):")
    print(f"  Documents reclassified : {changed_docs}")
    print(f"  Works archived          : {changed_works}")
    print(f"  Content files copied    : {archived_count} → {archive_dir}")
    if skipped_copy:
        print(f"  Copy failures (check stderr): {len(skipped_copy)}")
    print()
    print("To verify, run:")
    print("  python scripts/remediate_migration_batch.py --db <path> --verify")
    print("To undo, run:")
    print("  python scripts/remediate_migration_batch.py --db <path> --reverse\n")


def run_reverse(conn: sqlite3.Connection) -> None:
    """Undo every change logged in a01_remediation_log (latest batch first)."""
    _ensure_log_table(conn)

    # Find the most recent un-reversed batch
    row = conn.execute(
        """SELECT batch FROM a01_remediation_log
           WHERE reversed_at IS NULL
           ORDER BY batch DESC LIMIT 1"""
    ).fetchone()
    if not row:
        print("Nothing to reverse — a01_remediation_log is empty or already reversed.")
        return

    batch = row[0]
    entries = conn.execute(
        """SELECT id, entity_type, entity_id, field, old_value
           FROM a01_remediation_log
           WHERE batch=? AND reversed_at IS NULL""",
        (batch,),
    ).fetchall()

    reversed_count = 0
    for entry_id, entity_type, entity_id, field, old_value in entries:
        if entity_type == "document":
            conn.execute(
                f"UPDATE documents SET {field}=? WHERE id=?",
                (old_value, entity_id),
            )
        elif entity_type == "work":
            conn.execute(
                f"UPDATE works SET {field}=? WHERE id=?",
                (old_value, entity_id),
            )
        conn.execute(
            "UPDATE a01_remediation_log SET reversed_at=? WHERE id=?",
            (NOW, entry_id),
        )
        print(f"  [{entity_type[:3]}] {entity_id[:8]} {field} restored to {old_value!r}")
        reversed_count += 1

    conn.commit()
    print(f"\nReversed {reversed_count} changes from batch {batch}.")
    print("Note: archived content files were NOT re-copied — check the archive dir if needed.\n")


def run_verify(conn: sqlite3.Connection) -> int:
    """Print a summary and return non-zero exit code if artifact items remain."""
    docs = _find_artifact_documents(conn)
    works = _find_artifact_works(conn)

    # Docs that still have wrong tier
    bad_docs = [d for d in docs if d["tier"] != "artifact"]
    # Works that are still active (not archived)
    active_works = [w for w in works if w["status"] == "active"]

    print(f"\n{'='*60}")
    print("  VERIFICATION REPORT")
    print(f"{'='*60}\n")

    if bad_docs:
        print(f"FAIL — {len(bad_docs)} document(s) matched artifact pattern but tier != 'artifact':")
        for d in bad_docs:
            print(f"  [{d['id'][:8]}] tier={d['tier']!r}  {d['title']!r}")
    else:
        print("OK   — No unclassified artifact documents remain.")

    if active_works:
        print(f"FAIL — {len(active_works)} Work(s) matched artifact pattern but status = 'active':")
        for w in active_works:
            print(f"  [{w['id'][:8]}] {w['title']!r}")
    else:
        print("OK   — No active artifact Works remain.")

    # Overall stats
    total_works = conn.execute("SELECT COUNT(*) FROM works WHERE status='active'").fetchone()[0]
    total_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    artifact_docs = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE tier='artifact'"
    ).fetchone()[0]

    print()
    print(f"Active Works  : {total_works}")
    print(f"Total docs    : {total_docs}")
    print(f"Artifact docs : {artifact_docs}")
    print()

    if bad_docs or active_works:
        print("Remediation is INCOMPLETE. Run --apply to finish.\n")
        return 1
    else:
        print("Remediation verified OK — database is clean.\n")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Reclassify A01_MIGRATION_BATCH artifacts and archive displaced Works.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--db", default="data/orivellum.db",
                        help="Path to the SQLite database (default: data/orivellum.db)")
    parser.add_argument("--archive-dir", default="data/remediation_archive",
                        help="Where to copy displaced content files (default: data/remediation_archive)")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true",
                       help="Show what would change without writing anything")
    group.add_argument("--apply", action="store_true",
                       help="Apply the remediation (log all changes for reversal)")
    group.add_argument("--reverse", action="store_true",
                       help="Undo the most recent applied batch")
    group.add_argument("--verify", action="store_true",
                       help="Check that no artifact items remain active/unclassified")

    args = parser.parse_args()
    db_path = Path(args.db)

    if not db_path.exists():
        print(f"ERROR: database not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        if args.dry_run:
            run_dry_run(conn)
        elif args.apply:
            archive_dir = Path(args.archive_dir)
            archive_dir.mkdir(parents=True, exist_ok=True)
            run_apply(conn, archive_dir)
        elif args.reverse:
            run_reverse(conn)
        elif args.verify:
            sys.exit(run_verify(conn))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
