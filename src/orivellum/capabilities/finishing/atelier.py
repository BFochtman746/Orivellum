"""ATELIER — Cover, Product & Series Design (Orivellum-integrated module).

Manages every visual/physical aspect of the product and enforces series
branding across many books and many series. Deterministic core (spine + wrap
math, series-token cascade, design verification, sealing) is fully working.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import gateway as gw

# ── configurable DB path ───────────────────────────────────────────────────────
_DB_PATH: str = ""

GENESIS_HASH = "0" * 64

BLEED = 0.125
SPINE_FACTOR = {"white": 0.002252, "cream": 0.0025, "color": 0.0032}
SPINE_COVER_ALLOWANCE = 0.06
SPINE_TEXT_MIN_PAGES = 79
SAFE_ZONE = 0.25  # keep text/logos this far inside every trim/fold edge

# Hardcover (case laminate) geometry — KDP print model.
BOARD_OVERHANG = 0.16  # board extends past the text block on 3 sides
HINGE_WIDTH = 0.394  # groove between board and spine, each side
WRAP_MARGIN = 0.75  # artwork folded around the board edges
HARDCOVER_SPINE_ALLOWANCE = 0.06
HARDCOVER_MIN_PAGES = 75
HARDCOVER_MAX_PAGES = 550
BINDINGS = ("paperback", "hardcover")
TRIMS = {
    "5x8": (5.0, 8.0),
    "5.25x8": (5.25, 8.0),
    "5.5x8.5": (5.5, 8.5),
    "6x9": (6.0, 9.0),
    "7x10": (7.0, 10.0),
    "4.25x6.87": (4.25, 6.87),
}
BRAND_KEYS = [
    "body_font",
    "heading_font",
    "palette",
    "imagery",
    "composition",
    "title_pos",
    "author_pos",
    "logo",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS atelier_series (
    slug   TEXT PRIMARY KEY,
    name   TEXT NOT NULL,
    books  INTEGER NOT NULL DEFAULT 1,
    brand  TEXT NOT NULL DEFAULT '{}',
    locked INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS atelier_book (
    slug   TEXT PRIMARY KEY,
    title  TEXT NOT NULL,
    series TEXT NOT NULL,
    number INTEGER NOT NULL DEFAULT 1,
    pages  INTEGER NOT NULL,
    paper  TEXT NOT NULL CHECK (paper IN ('white','cream','color')),
    trim   TEXT NOT NULL,
    tagline TEXT NOT NULL DEFAULT '',
    state  TEXT NOT NULL DEFAULT 'DRAFT',
    sealed_version TEXT NOT NULL DEFAULT '',
    isbn TEXT NOT NULL DEFAULT '',
    binding TEXT NOT NULL DEFAULT 'paperback',
    actual_pages INTEGER NOT NULL DEFAULT 0,
    pages_source TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (series) REFERENCES atelier_series(slug)
);
CREATE TABLE IF NOT EXISTS atelier_cover_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book TEXT NOT NULL, version_id TEXT NOT NULL, prompt TEXT NOT NULL,
    status TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '', at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS atelier_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL, seq INTEGER NOT NULL, kind TEXT NOT NULL,
    payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL, at TEXT NOT NULL
);
"""


def configure(data_dir: str) -> None:
    global _DB_PATH
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    _DB_PATH = str(Path(data_dir) / "atelier.db")


def _db_path() -> str:
    if not _DB_PATH:
        raise RuntimeError("ATELIER not configured — call configure(data_dir) first.")
    return _DB_PATH


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def _canon(p: Any) -> str:
    return json.dumps(p, sort_keys=True, separators=(",", ":"))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "untitled"


# ── cover math (deterministic, researched KDP formulas) ──────────────────────


def spine_width(pages: int, paper: str) -> float:
    return pages * SPINE_FACTOR[paper] + SPINE_COVER_ALLOWANCE


def wrap_dimensions(trim: str, pages: int, paper: str) -> dict:
    tw, th = TRIMS[trim]
    spine = spine_width(pages, paper)
    return {
        "trim": trim,
        "trim_w": tw,
        "trim_h": th,
        "paper": paper,
        "pages": pages,
        "bleed": BLEED,
        "spine_width": round(spine, 4),
        "full_cover_width": round(2 * BLEED + 2 * tw + spine, 4),
        "full_cover_height": round(th + 2 * BLEED, 4),
        "spine_text_allowed": pages >= SPINE_TEXT_MIN_PAGES,
        "barcode_zone": "2.0in x 1.2in clear area, lower-right of back cover",
        "safe_zone_in": SAFE_ZONE,
        "min_dpi": 300,
    }


def hardcover_dimensions(trim: str, pages: int, paper: str) -> dict:
    """Case-laminate hardcover wrap geometry (KDP hardcover model).

    A hardcover wrap is printed oversize, wrapped around board, and hinged:
    each cover panel is the trim plus board overhang, the spine sits between
    two hinge grooves, and WRAP_MARGIN of artwork folds around every edge.
    Page count must be inside KDP's hardcover range — refuse otherwise.
    """
    if trim not in TRIMS:
        raise ValueError(f"Unknown trim '{trim}'. Valid: {', '.join(TRIMS)}")
    if not HARDCOVER_MIN_PAGES <= pages <= HARDCOVER_MAX_PAGES:
        raise ValueError(
            f"Hardcover requires {HARDCOVER_MIN_PAGES}-{HARDCOVER_MAX_PAGES} pages (got {pages})."
        )
    tw, th = TRIMS[trim]
    board_w = tw + BOARD_OVERHANG
    board_h = th + 2 * BOARD_OVERHANG
    spine = round(pages * SPINE_FACTOR[paper] + HARDCOVER_SPINE_ALLOWANCE, 4)
    full_w = round(2 * WRAP_MARGIN + 2 * board_w + 2 * HINGE_WIDTH + spine, 4)
    full_h = round(board_h + 2 * WRAP_MARGIN, 4)
    return {
        "binding": "hardcover-case-laminate",
        "trim": trim,
        "paper": paper,
        "pages": pages,
        "board_width": round(board_w, 4),
        "board_height": round(board_h, 4),
        "spine_width": spine,
        "hinge_width": HINGE_WIDTH,
        "wrap_margin": WRAP_MARGIN,
        "full_wrap_width": full_w,
        "full_wrap_height": full_h,
        "safe_zone_in": SAFE_ZONE,
        "spine_text_allowed": pages >= SPINE_TEXT_MIN_PAGES,
        "barcode_zone": "2.0in x 1.2in clear area, lower-right of back cover",
        "min_dpi": 300,
    }


def ean13(isbn: str) -> dict:
    """Real EAN-13 for the barcode zone: digits validated, check digit
    recomputed from the first twelve — never trusted as given."""
    digits = re.sub(r"[^0-9]", "", isbn or "")
    if len(digits) != 13:
        return {"valid": False, "reason": f"ISBN-13 needs 13 digits, got {len(digits)}."}
    if not digits.startswith(("978", "979")):
        return {"valid": False, "reason": "ISBN-13 must begin with 978 or 979."}
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(digits[:12]))
    check = (10 - total % 10) % 10
    if check != int(digits[12]):
        return {
            "valid": False,
            "reason": f"Check digit is {digits[12]}, should be {check}.",
            "expected_check_digit": check,
        }
    return {"valid": True, "ean13": digits, "check_digit": check}


def pdfx_spec(kind: str = "cover") -> dict:
    """PDF/X-1a:2001 delivery requirements for print files — the contract
    the final artwork must meet, verifiable by any preflight tool."""
    return {
        "standard": "PDF/X-1a:2001",
        "kind": kind,
        "color_space": "CMYK (no RGB, no ICC-tagged objects)",
        "output_intent": "GRACoL2006_Coated1v2 (CGATS TR 006)",
        "transparency": "flattened (PDF 1.3 — live transparency prohibited)",
        "fonts": "embedded and subset",
        "min_dpi": 300,
        "bleed_in": BLEED,
        "boxes": "TrimBox and BleedBox required; BleedBox = TrimBox + bleed",
        "max_ink_coverage_pct": 240,
    }


# ── DB helpers ────────────────────────────────────────────────────────────────


def _connect() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path())
    c.execute("PRAGMA foreign_keys=ON;")
    c.row_factory = sqlite3.Row
    return c


def _ledger_append(conn: sqlite3.Connection, scope: str, kind: str, payload: Any) -> str:
    row = conn.execute(
        "SELECT seq,hash FROM atelier_ledger WHERE scope=? ORDER BY seq DESC LIMIT 1", (scope,)
    ).fetchone()
    seq = (row["seq"] + 1) if row else 0
    prev = row["hash"] if row else GENESIS_HASH
    body = _canon({"seq": seq, "kind": kind, "payload": payload})
    h = _sha(prev + body)
    conn.execute(
        "INSERT INTO atelier_ledger (scope,seq,kind,payload,prev_hash,hash,at) VALUES (?,?,?,?,?,?,?)",
        (scope, seq, kind, _canon(payload), prev, h, _now()),
    )
    return h


# ── public API ────────────────────────────────────────────────────────────────


def cmd_init(_a: Any = None) -> int:
    conn = _connect()
    conn.executescript(SCHEMA)
    # Older atelier_book tables predate the print-model completion columns.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(atelier_book)").fetchall()}
    if cols and "isbn" not in cols:
        conn.execute("ALTER TABLE atelier_book ADD COLUMN isbn TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "ALTER TABLE atelier_book ADD COLUMN binding TEXT NOT NULL DEFAULT 'paperback'"
        )
        conn.execute("ALTER TABLE atelier_book ADD COLUMN actual_pages INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE atelier_book ADD COLUMN pages_source TEXT NOT NULL DEFAULT ''")
    conn.commit()
    return 0


def list_series() -> list[dict]:
    conn = _connect()
    rows = conn.execute("SELECT * FROM atelier_series ORDER BY created_at DESC").fetchall()
    return [_series_dict(r) for r in rows]


def get_series(slug: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM atelier_series WHERE slug=?", (slug,)).fetchone()
    return _series_dict(row) if row else None


def _series_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["brand"] = json.loads(d["brand"])
    return d


def create_series(name: str, books: int = 1) -> dict:
    s = _slug(name)
    conn = _connect()
    if conn.execute("SELECT 1 FROM atelier_series WHERE slug=?", (s,)).fetchone():
        raise ValueError(f"Series '{s}' already exists.")
    conn.execute(
        "INSERT INTO atelier_series (slug,name,books,brand,locked,created_at) VALUES (?,?,?,?,0,?)",
        (s, name, books, "{}", _now()),
    )
    _ledger_append(conn, f"series:{s}", "series.created", {"name": name, "books": books})
    conn.commit()
    return get_series(s)  # type: ignore[return-value]


def update_series_brand(slug: str, tokens: dict) -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM atelier_series WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Series '{slug}' not found.")
    if row["locked"]:
        raise PermissionError("Series is LOCKED; brand tokens are immutable.")
    brand = json.loads(row["brand"])
    for k in BRAND_KEYS:
        if k in tokens and tokens[k] is not None:
            brand[k] = tokens[k]
    conn.execute("UPDATE atelier_series SET brand=? WHERE slug=?", (_canon(brand), slug))
    _ledger_append(conn, f"series:{slug}", "series.brand_set", brand)
    conn.commit()
    return brand


def lock_series(slug: str, author: str) -> None:
    conn = _connect()
    row = conn.execute("SELECT * FROM atelier_series WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Series '{slug}' not found.")
    brand = json.loads(row["brand"])
    missing = [k for k in BRAND_KEYS if not brand.get(k)]
    if missing:
        raise ValueError(f"Cannot lock — unset brand tokens: {', '.join(missing)}")
    conn.execute("UPDATE atelier_series SET locked=1 WHERE slug=?", (slug,))
    _ledger_append(conn, f"series:{slug}", "series.locked", {"author": author, "brand": brand})
    conn.commit()


def list_books(series_slug: str | None = None) -> list[dict]:
    conn = _connect()
    if series_slug:
        rows = conn.execute(
            "SELECT * FROM atelier_book WHERE series=? ORDER BY number", (series_slug,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM atelier_book ORDER BY series,number").fetchall()
    result = []
    for row in rows:
        d = dict(row)
        covers = conn.execute(
            "SELECT * FROM atelier_cover_version WHERE book=? ORDER BY at DESC", (d["slug"],)
        ).fetchall()
        d["cover_versions"] = [dict(c) for c in covers]
        result.append(d)
    return result


def get_book(slug: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM atelier_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        return None
    d = dict(row)
    covers = conn.execute(
        "SELECT * FROM atelier_cover_version WHERE book=? ORDER BY at DESC", (slug,)
    ).fetchall()
    d["cover_versions"] = [dict(c) for c in covers]
    ser = conn.execute("SELECT * FROM atelier_series WHERE slug=?", (d["series"],)).fetchone()
    d["series_brand"] = json.loads(ser["brand"]) if ser else {}
    d["spec"] = wrap_dimensions(d["trim"], d["pages"], d["paper"])
    return d


def create_book(
    series_slug: str,
    title: str,
    number: int = 1,
    pages: int = 300,
    paper: str = "cream",
    trim: str = "6x9",
) -> dict:
    conn = _connect()
    ser = conn.execute("SELECT * FROM atelier_series WHERE slug=?", (series_slug,)).fetchone()
    if not ser:
        raise KeyError(f"Series '{series_slug}' not found.")
    if trim not in TRIMS:
        raise ValueError(f"Unknown trim '{trim}'. Valid: {', '.join(TRIMS)}")
    if number > ser["books"]:
        raise ValueError(f"Book number {number} exceeds series size ({ser['books']}).")
    s = _slug(title)
    if conn.execute("SELECT 1 FROM atelier_book WHERE slug=?", (s,)).fetchone():
        raise ValueError(f"Book '{s}' already exists.")
    conn.execute(
        "INSERT INTO atelier_book (slug,title,series,number,pages,paper,trim,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (s, title, series_slug, number, pages, paper, trim, _now()),
    )
    _ledger_append(
        conn,
        f"book:{s}",
        "book.created",
        {
            "title": title,
            "series": series_slug,
            "number": number,
            "pages": pages,
            "paper": paper,
            "trim": trim,
        },
    )
    conn.commit()
    return get_book(s)  # type: ignore[return-value]


def get_spec(slug: str) -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM atelier_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    b = dict(row)
    return {
        "book": b["title"],
        "series": b["series"],
        "number": b["number"],
        **wrap_dimensions(b["trim"], b["pages"], b["paper"]),
    }


def generate_covers(
    slug: str, versions: int = 3, mood: str = "", gateway_name: str = "mock"
) -> list[dict]:
    conn = _connect()
    row = conn.execute("SELECT * FROM atelier_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    b = dict(row)
    ser = conn.execute("SELECT * FROM atelier_series WHERE slug=?", (b["series"],)).fetchone()
    if not ser or not ser["locked"]:
        raise PermissionError("Series must be LOCKED before generating covers.")
    if mood:
        conn.execute("UPDATE atelier_book SET tagline=? WHERE slug=?", (mood, slug))
        conn.commit()
        b = dict(conn.execute("SELECT * FROM atelier_book WHERE slug=?", (slug,)).fetchone())
    brand = json.loads(ser["brand"])
    brief = {
        "title": b["title"],
        "series": ser["name"],
        "series_number": b["number"],
        "mood": b["tagline"],
        **{k: brand.get(k, "") for k in BRAND_KEYS},
    }
    engine = gw.get_gateway(gateway_name)
    cover_list = engine.cover_versions(brief, n=versions)
    out = []
    for v in cover_list:
        notes = f"{v.notes} [asset: {v.asset_ref}]" if v.asset_ref else v.notes
        conn.execute(
            "INSERT INTO atelier_cover_version (book,version_id,prompt,status,notes,at) VALUES (?,?,?,?,?,?)",
            (slug, v.version_id, v.prompt, v.status, notes, _now()),
        )
        out.append(
            {"version_id": v.version_id, "prompt": v.prompt, "status": v.status, "notes": v.notes}
        )
    _ledger_append(
        conn,
        f"book:{slug}",
        "cover.generated",
        {
            "gateway": engine.name,
            "count": len(cover_list),
            "versions": [v.version_id for v in cover_list],
        },
    )
    conn.commit()
    return out


def set_print_metadata(slug: str, isbn: str | None = None, binding: str | None = None) -> dict:
    """Set ISBN / binding for a book. The ISBN must be a REAL EAN-13 —
    a bad check digit is refused at write time, not discovered at print."""
    conn = _connect()
    row = conn.execute("SELECT * FROM atelier_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    if row["state"] == "SEALED":
        raise PermissionError("Book design is SEALED — print metadata is immutable.")
    updates: dict[str, str] = {}
    if isbn is not None:
        if isbn != "":
            check = ean13(isbn)
            if not check["valid"]:
                raise ValueError(f"Invalid ISBN-13: {check['reason']}")
            isbn = check["ean13"]
        updates["isbn"] = isbn
    if binding is not None:
        if binding not in BINDINGS:
            raise ValueError(f"Unknown binding '{binding}'. Valid: {', '.join(BINDINGS)}")
        updates["binding"] = binding
    if updates:
        sets = ", ".join(f"{k}=?" for k in updates)
        conn.execute(
            f"UPDATE atelier_book SET {sets} WHERE slug=?",  # noqa: S608 (keys are literals)
            (*updates.values(), slug),
        )
        _ledger_append(conn, f"book:{slug}", "print.metadata", updates)
        conn.commit()
    return get_book(slug)  # type: ignore[return-value]


def record_actual_pages(slug: str, pages: int, source: str) -> dict:
    """Record the RENDERED page count (from the PRESS print PDF) and re-base
    all cover geometry on it. Estimates never overwrite a rendered count."""
    conn = _connect()
    row = conn.execute("SELECT * FROM atelier_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    if row["state"] == "SEALED":
        raise PermissionError("Book design is SEALED — page geometry is immutable.")
    if not isinstance(pages, int) or pages <= 0:
        raise ValueError("actual pages must be a positive integer")
    conn.execute(
        "UPDATE atelier_book SET actual_pages=?, pages=?, pages_source=? WHERE slug=?",
        (pages, pages, source[:200], slug),
    )
    _ledger_append(
        conn, f"book:{slug}", "pages.actual", {"actual_pages": pages, "source": source[:200]}
    )
    conn.commit()
    return get_book(slug)  # type: ignore[return-value]


def verify_ledger(slug: str) -> tuple[bool, str]:
    """Walk the book's hash chain; any break is corruption, loudly."""
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM atelier_ledger WHERE scope=? ORDER BY seq", (f"book:{slug}",)
    ).fetchall()
    prev = GENESIS_HASH
    for r in rows:
        if r["prev_hash"] != prev:
            return False, f"seq {r['seq']}: prev_hash mismatch"
        body = _canon({"seq": r["seq"], "kind": r["kind"], "payload": json.loads(r["payload"])})
        if _sha(prev + body) != r["hash"]:
            return False, f"seq {r['seq']}: hash mismatch"
        prev = r["hash"]
    return True, f"{len(rows)} entries verified"


def verify_design(slug: str) -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM atelier_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    b = dict(row)
    ser = conn.execute("SELECT * FROM atelier_series WHERE slug=?", (b["series"],)).fetchone()
    brand = json.loads(ser["brand"]) if ser else {}
    w = wrap_dimensions(b["trim"], b["pages"], b["paper"])
    cover_count = conn.execute(
        "SELECT COUNT(*) c FROM atelier_cover_version WHERE book=?", (slug,)
    ).fetchone()["c"]
    ean = ean13(b.get("isbn") or "")
    checks = {
        "series_brand_locked": bool(ser["locked"]) if ser else False,
        "book_number_in_range": b["number"] <= (ser["books"] if ser else 0),
        "spine_text_policy": w["spine_text_allowed"],
        "brand_tokens_complete": all(brand.get(k) for k in BRAND_KEYS),
        "cover_version_exists": cover_count > 0,
        # Print-model completion: geometry must rest on RENDERED pages, and
        # the barcode zone needs a real EAN-13. Both fail closed — an
        # estimate or a missing ISBN is not print-ready.
        "pages_are_actual": bool(b.get("actual_pages")) and b["pages"] == b["actual_pages"],
        "isbn_ean13_valid": ean["valid"],
    }
    out = {"passed": all(checks.values()), "checks": checks, "spec": w, "ean13": ean}
    if (b.get("binding") or "paperback") == "hardcover":
        try:
            out["hardcover"] = hardcover_dimensions(b["trim"], b["pages"], b["paper"])
        except ValueError as exc:
            checks["hardcover_page_range"] = False
            out["hardcover_error"] = str(exc)
            out["passed"] = False
    out["pdfx"] = pdfx_spec("cover")
    return out


def seal_design(slug: str, author: str, choose_version: str) -> dict:
    conn = _connect()
    vr = verify_design(slug)
    if not vr["passed"]:
        raise ValueError("Design verify failed — cannot seal.")
    chosen = conn.execute(
        "SELECT * FROM atelier_cover_version WHERE book=? AND version_id=?", (slug, choose_version)
    ).fetchone()
    if not chosen:
        raise ValueError(f"Version '{choose_version}' not found for this book.")
    row = conn.execute("SELECT * FROM atelier_book WHERE slug=?", (slug,)).fetchone()
    b = dict(row)
    manifest = {
        "book": b["title"],
        "series": b["series"],
        "number": b["number"],
        "spec": vr["spec"],
        "chosen_cover": choose_version,
        "author": author,
        "sealed_at": _now(),
    }
    ph = _sha(_canon(manifest))
    manifest["package_sha256"] = ph
    conn.execute(
        "UPDATE atelier_book SET state='SEALED', sealed_version=? WHERE slug=?",
        (choose_version, slug),
    )
    _ledger_append(
        conn,
        f"book:{slug}",
        "cover.sealed",
        {"version": choose_version, "sha256": ph, "author": author},
    )
    conn.commit()
    return manifest
