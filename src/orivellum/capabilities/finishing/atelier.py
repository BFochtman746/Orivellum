"""ATELIER — Cover, Product & Series Design (Orivellum-integrated module).

Manages every visual/physical aspect of the product and enforces series
branding across many books and many series. Deterministic core (spine + wrap
math, series-token cascade, design verification, sealing) is fully working.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
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
TRIMS = {
    "5x8": (5.0, 8.0), "5.25x8": (5.25, 8.0), "5.5x8.5": (5.5, 8.5),
    "6x9": (6.0, 9.0), "7x10": (7.0, 10.0), "4.25x6.87": (4.25, 6.87),
}
BRAND_KEYS = ["body_font", "heading_font", "palette", "imagery",
              "composition", "title_pos", "author_pos", "logo"]

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
    return datetime.now(timezone.utc).isoformat()

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
        "trim": trim, "trim_w": tw, "trim_h": th, "paper": paper, "pages": pages,
        "bleed": BLEED, "spine_width": round(spine, 4),
        "full_cover_width": round(2 * BLEED + 2 * tw + spine, 4),
        "full_cover_height": round(th + 2 * BLEED, 4),
        "spine_text_allowed": pages >= SPINE_TEXT_MIN_PAGES,
        "barcode_zone": "2.0in x 1.2in clear area, lower-right of back cover",
        "min_dpi": 300,
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


def create_book(series_slug: str, title: str, number: int = 1,
                pages: int = 300, paper: str = "cream", trim: str = "6x9") -> dict:
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
    _ledger_append(conn, f"book:{s}", "book.created",
                   {"title": title, "series": series_slug, "number": number,
                    "pages": pages, "paper": paper, "trim": trim})
    conn.commit()
    return get_book(s)  # type: ignore[return-value]


def get_spec(slug: str) -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM atelier_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    b = dict(row)
    return {"book": b["title"], "series": b["series"], "number": b["number"],
            **wrap_dimensions(b["trim"], b["pages"], b["paper"])}


def generate_covers(slug: str, versions: int = 3, mood: str = "",
                    gateway_name: str = "mock") -> list[dict]:
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
        "title": b["title"], "series": ser["name"], "series_number": b["number"],
        "mood": b["tagline"], **{k: brand.get(k, "") for k in BRAND_KEYS},
    }
    engine = gw.get_gateway(gateway_name)
    cover_list = engine.cover_versions(brief, n=versions)
    out = []
    for v in cover_list:
        conn.execute(
            "INSERT INTO atelier_cover_version (book,version_id,prompt,status,notes,at) VALUES (?,?,?,?,?,?)",
            (slug, v.version_id, v.prompt, v.status, v.notes, _now()),
        )
        out.append({"version_id": v.version_id, "prompt": v.prompt, "status": v.status, "notes": v.notes})
    _ledger_append(conn, f"book:{slug}", "cover.generated",
                   {"gateway": engine.name, "count": len(cover_list),
                    "versions": [v.version_id for v in cover_list]})
    conn.commit()
    return out


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
    checks = {
        "series_brand_locked": bool(ser["locked"]) if ser else False,
        "book_number_in_range": b["number"] <= (ser["books"] if ser else 0),
        "spine_text_policy": w["spine_text_allowed"],
        "brand_tokens_complete": all(brand.get(k) for k in BRAND_KEYS),
        "cover_version_exists": cover_count > 0,
    }
    return {"passed": all(checks.values()), "checks": checks, "spec": w}


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
        "book": b["title"], "series": b["series"], "number": b["number"],
        "spec": vr["spec"], "chosen_cover": choose_version,
        "author": author, "sealed_at": _now(),
    }
    ph = _sha(_canon(manifest))
    manifest["package_sha256"] = ph
    conn.execute("UPDATE atelier_book SET state='SEALED', sealed_version=? WHERE slug=?",
                 (choose_version, slug))
    _ledger_append(conn, f"book:{slug}", "cover.sealed",
                   {"version": choose_version, "sha256": ph, "author": author})
    conn.commit()
    return manifest
