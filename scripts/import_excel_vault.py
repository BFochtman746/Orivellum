#!/usr/bin/env python3
"""One-shot import of the Excel Training Vault zip into the Orivellum library.

Creates a 'Microsoft Excel Mastery' Work, registers the ZIP as a library document,
and runs the full extraction pipeline synchronously so every markdown chapter
becomes its own searchable document linked to the Work.

Usage:
    uv run python scripts/import_excel_vault.py
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

# ── Resolve project root ───────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

ZIP_PATH = ROOT / "attached_assets" / "Excel-Training-Vault_2_1786136802154.zip"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    if not ZIP_PATH.exists():
        sys.exit(f"[ERROR] ZIP not found: {ZIP_PATH}")

    print("Initialising Orivellum database …")
    from orivellum.api import _deps
    from orivellum.capabilities.pipeline import process_document
    from orivellum.configuration.config import load_config as get_config
    from orivellum.database.db import OrivellumDB

    cfg = get_config()
    db = OrivellumDB(cfg.database.path)
    _deps.init(db=db, cfg=cfg)  # required by process_document → get_config() inside pipeline

    # ── 1. Find or create the Work ─────────────────────────────────────────────
    WORK_TITLE = "Microsoft Excel Mastery"
    existing_works = db.list_works(limit=200)
    work = next((w for w in existing_works if w["title"] == WORK_TITLE), None)
    if work:
        print(f"[OK] Work already exists: {work['id']} — {WORK_TITLE}")
    else:
        work = db.create_work(
            title=WORK_TITLE,
            work_type="research",
            description=(
                "Comprehensive Excel training knowledge base — foundation formulas, "
                "VLOOKUP/XLOOKUP/INDEX-MATCH, PivotTables, Power Query (M), Power Pivot/DAX, "
                "VBA/Macros, LAMBDA, Dynamic Arrays, Copilot in Excel, legacy CSE arrays, "
                "keyboard shortcuts, conditional formatting, and workbook security. "
                "119 chapters covering beginner to expert level."
            ),
        )
        print(f"[OK] Created Work: {work['id']} — {WORK_TITLE}")

    work_id = work["id"]

    # ── 2. Copy zip to library storage ────────────────────────────────────────
    lib_root = Path(cfg.data_dir) / "library"
    lib_root.mkdir(parents=True, exist_ok=True)

    print(f"Hashing {ZIP_PATH.name} …")
    sha = sha256_file(ZIP_PATH)
    print(f"  SHA-256: {sha[:16]}…")

    # Check dedup
    import sqlite3

    with sqlite3.connect(cfg.database.path) as _conn:
        _conn.row_factory = sqlite3.Row
        row = _conn.execute("SELECT id FROM documents WHERE sha256=?", (sha,)).fetchone()

    if row:
        print(f"[SKIP] ZIP already in library as doc {row['id']} — reprocessing …")
        doc = db.get_document(row["id"])
        content_path = doc.get("content_path", "")
        file_path = (lib_root / content_path) if content_path else None
        if file_path and file_path.exists():
            db.update_document_extracted(row["id"], "", 0, readiness="imported", error_message=None)
            process_document(
                doc_id=row["id"],
                file_path=str(file_path),
                kind="zip",
                work_id=work_id,
                title=ZIP_PATH.name,
                db=db,
            )
        else:
            print(f"[WARN] Stored file not found at {file_path} — re-importing bytes")
            row = None

    if not row:
        shard = lib_root / sha[:2] / sha[2:4]
        shard.mkdir(parents=True, exist_ok=True)
        dest = shard / ZIP_PATH.name
        if not dest.exists():
            shutil.copy2(ZIP_PATH, dest)
            print(f"[OK] Copied to {dest.relative_to(lib_root)}")
        else:
            print(f"[OK] File already at {dest.relative_to(lib_root)}")

        doc = db.create_document(
            title=ZIP_PATH.stem.replace("_", " ").replace("-", " "),
            source=str(dest),
            sha256=sha,
            kind="zip",
            work_id=work_id,
            content_path=str(dest.relative_to(lib_root)),
            meta={"imported_by": "import_excel_vault.py"},
        )
        doc_id = doc["id"]
        print(f"[OK] Registered ZIP as doc {doc_id}")

        # ── 3. Run extraction pipeline synchronously ───────────────────────────
        print("Extracting ZIP — this creates one child document per chapter …")
        process_document(
            doc_id=doc_id,
            file_path=str(dest),
            kind="zip",
            work_id=work_id,
            title=ZIP_PATH.stem,
            db=db,
        )

    # ── 4. Report results ──────────────────────────────────────────────────────
    import sqlite3 as _s

    with _s.connect(cfg.database.path) as conn:
        conn.row_factory = _s.Row
        children = conn.execute(
            "SELECT COUNT(*) as n, readiness FROM documents WHERE work_id=? AND id!=? GROUP BY readiness",
            (work_id, doc.get("id", "")),
        ).fetchall()

    print("\n── Import complete ─────────────────────────────────────────")
    print(f"  Work : {WORK_TITLE}  ({work_id})")
    for row in children:
        print(f"  {row['readiness']:12s} : {row['n']} document(s)")
    if not children:
        print("  (no child documents found — check pipeline logs)")
    print()
    print("The Excel knowledge base is now available in the Library and")
    print("linked to the Work. Chat conversations linked to this Work will")
    print("draw from all 119 chapters automatically.")


if __name__ == "__main__":
    main()
