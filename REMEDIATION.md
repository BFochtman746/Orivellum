# A01_MIGRATION_BATCH Data Remediation

This runbook explains how to safely reclassify the `A01_MIGRATION_BATCH_*` 
objects that were auto-created as Works during bulk imports.  These objects 
pollute the Works list, inflate dashboard counts, and make real books and 
learning material hard to find.

**All changes are reversible.**

---

## What the script does

| Target | Before | After |
|--------|--------|-------|
| Documents whose `title` or `source` matches the migration-batch pattern | `tier = 'source'` | `tier = 'artifact'` |
| Works whose `title` matches the migration-batch pattern | `status = 'active'` | `status = 'archived'` |
| Content files for reclassified documents | live in `data/library/` | copied to `data/remediation_archive/<timestamp>/` |

The pattern covers:
- `A01_MIGRATION_BATCH_*`, `A02_*`, … (the primary polluters)
- `RP-NNN`, `Run-NNN` (batch-run labels)
- `..._v1.0.0` versioned artifact dumps
- `baseline`, `qualification`, `regression`, `fixture` (test-corpus markers)

Every change is logged in the `a01_remediation_log` table for reversal.

---

## Prerequisites

- Python 3.10+
- Access to the Orivellum SQLite database file (`data/orivellum.db` by default,
  or the path you pass via `--db`)
- The Orivellum API server **stopped** before you run `--apply` 
  (avoids concurrent write conflicts)

---

## Step-by-step procedure

### Step 1 — Back up the live database

```bash
# Creates a timestamped copy you can restore from if anything goes wrong
cp data/orivellum.db data/orivellum_$(date +%Y%m%dT%H%M%S).db.bak
```

> **Windows (PowerShell)**
> ```powershell
> Copy-Item data\orivellum.db "data\orivellum_$(Get-Date -Format yyyyMMddTHHmmss).db.bak"
> ```

### Step 2 — Stop the API server

Stop the running Orivellum API before writing so there are no lock conflicts.

### Step 3 — Dry run on the backup copy

Run the dry run against your **backup copy** first so the original is untouched:

```bash
python scripts/remediate_migration_batch.py \
    --db data/orivellum_<timestamp>.db.bak \
    --dry-run
```

Review the output:
- Does the list of Works to archive look right?
- Are real books / learning materials absent from the list?
- Are the document counts reasonable?

### Step 4 — Apply on the live database

Once the dry-run output looks correct, apply to the live database:

```bash
python scripts/remediate_migration_batch.py \
    --db data/orivellum.db \
    --apply
```

The script prints each change and its batch identifier.

### Step 5 — Verify

```bash
python scripts/remediate_migration_batch.py \
    --db data/orivellum.db \
    --verify
```

Expected output:
```
OK   — No unclassified artifact documents remain.
OK   — No active artifact Works remain.
Remediation verified OK — database is clean.
```

Exit code 0 = clean; non-zero = items still need attention (run `--apply` again).

### Step 6 — Restart the API server

```bash
./start.sh          # Linux / macOS
.\scripts\start.ps1  # Windows
```

---

## Rolling back

If something looks wrong after `--apply`, restore from backup **or** run the
reverse command:

```bash
# Option A — restore the backup (safest)
cp data/orivellum_<timestamp>.db.bak data/orivellum.db

# Option B — undo via the remediation log (reverses the most recent batch)
python scripts/remediate_migration_batch.py \
    --db data/orivellum.db \
    --reverse
```

`--reverse` reads `a01_remediation_log`, restores every `old_value`, and marks
entries as reversed.  Archived content files are **not** automatically
re-copied (they remain in `data/remediation_archive/` as a safety net).

---

## Verification query

Run this in any SQLite browser to confirm no artifact Works remain active:

```sql
-- No rows should be returned after a successful remediation
SELECT w.id, w.title, w.status
FROM works w
WHERE w.status = 'active'
  AND (
    w.title LIKE '%A01_MIGRATION_BATCH%'
    OR w.title LIKE '%A02_%'
    OR w.title REGEXP '^RP[-_ ]?[0-9]{2,}'
    OR w.title REGEXP '^Run[-_ ]?[0-9]{2,}'
  );

-- Documents should all be tier='artifact'
SELECT id, title, tier
FROM documents
WHERE tier != 'artifact'
  AND (
    title LIKE '%migration%batch%'
    OR title LIKE '%A01_%'
    OR title LIKE '%A02_%'
  );
```

---

## Script reference

```
usage: remediate_migration_batch.py [--db PATH] [--archive-dir PATH]
                                     (--dry-run | --apply | --reverse | --verify)

Options:
  --db PATH          Path to the SQLite DB (default: data/orivellum.db)
  --archive-dir PATH Where to back up displaced content files
                     (default: data/remediation_archive)
  --dry-run          Print what would change — no writes
  --apply            Apply the remediation and log all changes
  --reverse          Undo the most recent applied batch
  --verify           Exit 0 if clean, non-zero if artifact items remain
```

---

## What is NOT touched

- Documents with a clean title that happen to contain the word "batch" in 
  unrelated context (the pattern requires the full `A01_MIGRATION_BATCH` 
  structure or the `^a0\d_` prefix).
- Works that own reclassified documents but whose own title is clean — they
  are logged in the dry-run output for manual inspection only.
- Any table other than `documents` and `works`.
- The API server configuration, settings, or schema.
