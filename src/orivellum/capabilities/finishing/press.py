"""PRESS — Manuscript Finalization & Delivery (Orivellum-integrated module).

Enforced standards:
  - ONE chapter-number style book-wide: words | arabic | roman.
  - Chapter opening contract: number -> title -> optional original epigraph.
  - Epigraphs are ORIGINAL only; gateway abstains rather than fabricate.
  - Immutable style lock, hash-chained ledger, author sign-off on seals.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import gateway as gw

# ── configurable DB path (call configure(data_dir) before first use) ──────────
_DB_PATH: str = ""
_PKG_DIR: str = str(Path(__file__).parent)

GENESIS_HASH = "0" * 64
WORDS_PER_PAGE = 300

CHAPTER_STYLES = ("words", "arabic", "roman")
STYLE_KEYS = ["trim", "body_font", "heading_font", "body_size", "leading",
              "chapter_style", "epigraphs"]

SUBMISSION_SPEC = {
    "font": "Times New Roman", "size_pt": 12, "spacing": "double",
    "margins_in": 1.0, "indent_in": 0.5,
    "header": "Surname / TITLE / page#", "scene_break": "#",
    "title_page": "title + author contact + rounded word count",
    "chapter_start": "new page",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS press_book (
    slug TEXT PRIMARY KEY, title TEXT NOT NULL, series TEXT DEFAULT '',
    author_name TEXT NOT NULL, work_id TEXT DEFAULT '',
    style TEXT NOT NULL DEFAULT '{}',
    style_locked INTEGER NOT NULL DEFAULT 0,
    has_front INTEGER NOT NULL DEFAULT 0, has_back INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS press_chapter (
    book TEXT NOT NULL, number INTEGER NOT NULL, title TEXT NOT NULL,
    words INTEGER NOT NULL DEFAULT 0, has_epigraph INTEGER NOT NULL DEFAULT 0,
    epigraph_text TEXT NOT NULL DEFAULT '', epigraph_status TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (book, number)
);
CREATE TABLE IF NOT EXISTS press_distribution (
    id INTEGER PRIMARY KEY AUTOINCREMENT, book TEXT NOT NULL,
    recipient TEXT NOT NULL, watermark TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS press_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL, seq INTEGER NOT NULL,
    kind TEXT NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL, at TEXT NOT NULL
);
"""


def configure(data_dir: str) -> None:
    global _DB_PATH
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    _DB_PATH = str(Path(data_dir) / "press.db")


def _db_path() -> str:
    if not _DB_PATH:
        raise RuntimeError("PRESS not configured — call configure(data_dir) first.")
    return _DB_PATH


# ── helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha(t: str) -> str:
    return hashlib.sha256(t.encode()).hexdigest()


def _canon(p: Any) -> str:
    return json.dumps(p, sort_keys=True, separators=(",", ":"))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "untitled"


# ── number rendering ──────────────────────────────────────────────────────────

_ONES = ["zero","one","two","three","four","five","six","seven","eight",
         "nine","ten","eleven","twelve","thirteen","fourteen","fifteen",
         "sixteen","seventeen","eighteen","nineteen"]
_TENS = ["","","twenty","thirty","forty","fifty","sixty","seventy","eighty","ninety"]


def _to_words(n: int) -> str:
    if n < 20: return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else "-" + _ONES[n % 10])
    if n < 1000:
        rem = n % 100
        return _ONES[n // 100] + " hundred" + ("" if rem == 0 else " " + _to_words(rem))
    return str(n)


def _to_roman(n: int) -> str:
    vals = [(1000,"M"),(900,"CM"),(500,"D"),(400,"CD"),(100,"C"),
            (90,"XC"),(50,"L"),(40,"XL"),(10,"X"),(9,"IX"),(5,"V"),(4,"IV"),(1,"I")]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s; n -= v
    return out


def chapter_header(style: str, n: int) -> str:
    if style == "arabic": return f"Chapter {n}"
    if style == "roman":  return f"Chapter {_to_roman(n)}"
    return f"Chapter {_to_words(n).title()}"


# ── DB helpers ────────────────────────────────────────────────────────────────

def _connect() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


def _ledger_append(conn: sqlite3.Connection, scope: str, kind: str, payload: Any) -> str:
    row = conn.execute(
        "SELECT seq,hash FROM press_ledger WHERE scope=? ORDER BY seq DESC LIMIT 1", (scope,)
    ).fetchone()
    seq = (row["seq"] + 1) if row else 0
    prev = row["hash"] if row else GENESIS_HASH
    body = _canon({"seq": seq, "kind": kind, "payload": payload})
    h = _sha(prev + body)
    conn.execute(
        "INSERT INTO press_ledger (scope,seq,kind,payload,prev_hash,hash,at) VALUES (?,?,?,?,?,?,?)",
        (scope, seq, kind, _canon(payload), prev, h, _now()),
    )
    return h


# ── public API ────────────────────────────────────────────────────────────────

def cmd_init(_a: Any = None) -> int:
    conn = _connect()
    conn.executescript(SCHEMA)
    conn.commit()
    return 0


def list_books(work_id: str | None = None) -> list[dict]:
    conn = _connect()
    if work_id:
        rows = conn.execute("SELECT * FROM press_book WHERE work_id=? ORDER BY created_at DESC", (work_id,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM press_book ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_book(slug: str) -> dict | None:
    conn = _connect()
    row = conn.execute("SELECT * FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        return None
    b = dict(row)
    b["style"] = json.loads(b["style"])
    # Attach chapters
    chs = conn.execute(
        "SELECT * FROM press_chapter WHERE book=? ORDER BY number", (slug,)
    ).fetchall()
    b["chapters"] = [dict(c) for c in chs]
    return b


def create_book(title: str, author_name: str, series: str = "", work_id: str = "") -> dict:
    s = _slug(title)
    conn = _connect()
    if conn.execute("SELECT 1 FROM press_book WHERE slug=?", (s,)).fetchone():
        raise ValueError(f"Book '{s}' already exists.")
    conn.execute(
        "INSERT INTO press_book (slug,title,series,author_name,work_id,created_at) VALUES (?,?,?,?,?,?)",
        (s, title, series, author_name, work_id, _now()),
    )
    _ledger_append(conn, f"book:{s}", "book.created", {"title": title, "series": series, "work_id": work_id})
    conn.commit()
    return get_book(s)  # type: ignore[return-value]


def update_style(slug: str, updates: dict) -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    if row["style_locked"]:
        raise PermissionError("Style is LOCKED and immutable.")
    style = json.loads(row["style"])
    for k in STYLE_KEYS:
        if k in updates and updates[k] is not None:
            style[k] = updates[k]
    if style.get("chapter_style") and style["chapter_style"] not in CHAPTER_STYLES:
        raise ValueError(f"chapter_style must be one of {CHAPTER_STYLES}")
    if style.get("epigraphs") and style["epigraphs"] not in ("on", "off"):
        raise ValueError("epigraphs must be 'on' or 'off'")
    conn.execute("UPDATE press_book SET style=? WHERE slug=?", (_canon(style), slug))
    _ledger_append(conn, f"book:{slug}", "style.set", style)
    conn.commit()
    return style


def lock_style(slug: str, author: str) -> None:
    conn = _connect()
    row = conn.execute("SELECT * FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    style = json.loads(row["style"])
    need = ["trim", "body_font", "heading_font", "body_size", "chapter_style", "epigraphs"]
    missing = [k for k in need if not style.get(k)]
    if missing:
        raise ValueError(f"Cannot lock — unset fields: {', '.join(missing)}")
    conn.execute("UPDATE press_book SET style_locked=1 WHERE slug=?", (slug,))
    _ledger_append(conn, f"book:{slug}", "style.locked", {"author": author, "style": style})
    conn.commit()


def add_chapter(slug: str, number: int, title: str, words: int = 0, has_epigraph: bool = False) -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    style = json.loads(row["style"])
    if has_epigraph and style.get("epigraphs") == "off":
        raise ValueError("Style epigraph policy is OFF; cannot add an epigraph slot.")
    if conn.execute("SELECT 1 FROM press_chapter WHERE book=? AND number=?", (slug, number)).fetchone():
        raise ValueError(f"Chapter {number} already exists.")
    conn.execute(
        "INSERT INTO press_chapter (book,number,title,words,has_epigraph) VALUES (?,?,?,?,?)",
        (slug, number, title, words, 1 if has_epigraph else 0),
    )
    conn.commit()
    return {"number": number, "title": title, "words": words, "has_epigraph": has_epigraph,
            "header": chapter_header(style.get("chapter_style", "arabic"), number)}


def draft_epigraph(slug: str, number: int, soul: str = "", in_world: str = "",
                   gateway_name: str = "mock", want_quote: bool = False) -> dict:
    conn = _connect()
    row = conn.execute("SELECT 1 FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    ch = conn.execute("SELECT * FROM press_chapter WHERE book=? AND number=?", (slug, number)).fetchone()
    if not ch:
        raise KeyError(f"Chapter {number} not found.")
    if not ch["has_epigraph"]:
        raise ValueError("This chapter has no epigraph slot.")
    engine = gw.get_gateway(gateway_name)
    res = engine.original_epigraph({"soul": soul, "in_world_source": in_world,
                                    "chapter": ch["title"], "want_quote": want_quote})
    if res.status == "ABSTAINED":
        return {"status": "ABSTAINED", "reason": res.reason}
    text = res.text + (f"\n— {res.attribution}" if res.attribution else "")
    conn.execute(
        "UPDATE press_chapter SET epigraph_text=?, epigraph_status=? WHERE book=? AND number=?",
        (text, res.status, slug, number),
    )
    _ledger_append(conn, f"book:{slug}", "epigraph.drafted",
                   {"chapter": number, "status": res.status})
    conn.commit()
    return {"status": res.status, "text": text, "attribution": res.attribution}


def approve_epigraph(slug: str, number: int, author: str) -> None:
    conn = _connect()
    ch = conn.execute("SELECT * FROM press_chapter WHERE book=? AND number=?", (slug, number)).fetchone()
    if not ch or not ch["epigraph_text"]:
        raise ValueError("No drafted epigraph to approve.")
    conn.execute(
        "UPDATE press_chapter SET epigraph_status='APPROVED' WHERE book=? AND number=?", (slug, number)
    )
    _ledger_append(conn, f"book:{slug}", "epigraph.approved", {"chapter": number, "author": author})
    conn.commit()


def set_matter(slug: str, front: bool, back: bool) -> None:
    conn = _connect()
    if not conn.execute("SELECT 1 FROM press_book WHERE slug=?", (slug,)).fetchone():
        raise KeyError(f"Book '{slug}' not found.")
    conn.execute("UPDATE press_book SET has_front=?, has_back=? WHERE slug=?",
                 (1 if front else 0, 1 if back else 0, slug))
    conn.commit()


def _page_estimate(conn: sqlite3.Connection, slug: str, has_front: bool, has_back: bool) -> tuple[int, int]:
    total = conn.execute(
        "SELECT COALESCE(SUM(words),0) w FROM press_chapter WHERE book=?", (slug,)
    ).fetchone()["w"]
    body = math.ceil(total / WORDS_PER_PAGE) if total else 0
    pages = body + (8 if has_front else 0) + (4 if has_back else 0)
    if pages % 2:
        pages += 1
    return total, pages


def verify(slug: str) -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    b = dict(row)
    style = json.loads(b["style"])
    chs = conn.execute("SELECT * FROM press_chapter WHERE book=? ORDER BY number", (slug,)).fetchall()
    nums = [c["number"] for c in chs]
    checks = {
        "style_locked": bool(b["style_locked"]),
        "has_chapters": len(chs) >= 1,
        "chapters_contiguous": nums == list(range(1, len(nums) + 1)) if nums else False,
        "chapters_titled": all(c["title"].strip() for c in chs) if chs else False,
        "epigraph_policy": True,
        "front_matter": bool(b["has_front"]),
        "back_matter": bool(b["has_back"]),
    }
    if style.get("epigraphs") == "off":
        checks["epigraph_policy"] = all(not c["has_epigraph"] for c in chs)
    else:
        for c in chs:
            if c["has_epigraph"] and c["epigraph_status"] != "APPROVED":
                checks["epigraph_policy"] = False
    total, pages = _page_estimate(conn, slug, bool(b["has_front"]), bool(b["has_back"]))
    passed = all(checks.values())
    return {"passed": passed, "checks": checks, "word_count": total, "estimated_pages": pages}


def build_package(slug: str, pkg_type: str = "publisher", target: str = "production") -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    b = dict(row)
    style = json.loads(b["style"])
    vr = verify(slug)
    if not vr["passed"] and not (pkg_type == "publisher" and target == "submission"):
        raise ValueError("Pre-flight failed — package blocked. (Only submission MS format is allowed pre-typeset.)")
    total, pages = _page_estimate(conn, slug, bool(b["has_front"]), bool(b["has_back"]))
    if pkg_type == "publisher" and target == "submission":
        spec = {"format": "standard-manuscript-format", **SUBMISSION_SPEC,
                "word_count_rounded": int(round(total, -2))}
    elif pkg_type == "publisher":
        spec = {"format": "typeset-production", **{k: style.get(k) for k in STYLE_KEYS},
                "estimated_pages": pages}
    else:
        spec = {"format": "advance-reader-copy", "stamp": "ADVANCE READER COPY — NOT FOR RESALE",
                "based_on": {k: style.get(k) for k in STYLE_KEYS},
                "estimated_pages": pages, "delivery": "PDF/EPUB"}
    return {"package_type": pkg_type, "target": target, "spec": spec, "preflight": vr}


def seal_package(slug: str, pkg_type: str, target: str, author: str, recipient: str = "") -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    b = dict(row)
    style = json.loads(b["style"])
    vr = verify(slug)
    if not vr["passed"] and not (pkg_type == "publisher" and target == "submission"):
        raise ValueError("Pre-flight failed — cannot seal.")
    total, pages = _page_estimate(conn, slug, bool(b["has_front"]), bool(b["has_back"]))
    if pkg_type == "publisher" and target == "submission":
        spec = {"format": "standard-manuscript-format", **SUBMISSION_SPEC,
                "word_count_rounded": int(round(total, -2))}
    elif pkg_type == "publisher":
        spec = {"format": "typeset-production", **{k: style.get(k) for k in STYLE_KEYS},
                "estimated_pages": pages}
    else:
        spec = {"format": "advance-reader-copy", "stamp": "ADVANCE READER COPY — NOT FOR RESALE",
                "based_on": {k: style.get(k) for k in STYLE_KEYS},
                "estimated_pages": pages, "delivery": "PDF/EPUB"}
    manifest: dict = {
        "book": b["title"], "series": b["series"], "author": b["author_name"],
        "package_type": pkg_type, "target": target if pkg_type == "publisher" else None,
        "spec": spec, "signoff": author, "sealed_at": _now(),
    }
    if pkg_type == "test-reader":
        if not recipient:
            raise ValueError("Test-reader seal requires a recipient for per-copy watermark.")
        wm = _sha(f"{slug}|{recipient}")[:8].upper()
        conn.execute(
            "INSERT INTO press_distribution (book,recipient,watermark,at) VALUES (?,?,?,?)",
            (slug, recipient, wm, _now()),
        )
        manifest["recipient"] = recipient
        manifest["watermark"] = wm
        _ledger_append(conn, f"book:{slug}", "arc.distributed", {"recipient": recipient, "watermark": wm})
    ph = _sha(_canon(manifest))
    manifest["package_sha256"] = ph
    _ledger_append(conn, f"book:{slug}", "package.sealed",
                   {"type": pkg_type, "target": manifest["target"], "sha256": ph, "author": author})
    conn.commit()
    return manifest


def list_distribution(slug: str) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM press_distribution WHERE book=? ORDER BY at DESC", (slug,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_ledger(slug: str) -> list[dict]:
    conn = _connect()
    rows = conn.execute(
        "SELECT * FROM press_ledger WHERE scope=? ORDER BY seq", (f"book:{slug}",)
    ).fetchall()
    return [dict(r) for r in rows]
