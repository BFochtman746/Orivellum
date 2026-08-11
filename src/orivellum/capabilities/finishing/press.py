"""PRESS — Manuscript Finalization & Delivery (Orivellum-integrated module).

Enforced standards:
  - ONE chapter-number style book-wide: words | arabic | roman.
  - Chapter opening contract: number -> title -> optional original epigraph.
  - Epigraphs are ORIGINAL only; gateway abstains rather than fabricate.
  - Immutable style lock, hash-chained ledger, author sign-off on seals.

LAW 1 — one manuscript. Chapter prose lives in exactly one place:
``book_chapters.text`` in the main database. PRESS never stores its own
chapter rows; it reads the real chapters (read-only) through the book's
linked Work and computes word counts from the actual text. The only
chapter-adjacent state PRESS owns is the epigraph slot (``press_epigraph``),
which is presentation state, not prose.
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

# ── configurable DB paths (call configure(data_dir) before first use) ─────────
_DB_PATH: str = ""
_MAIN_DB_PATH: str = ""
_PKG_DIR: str = str(Path(__file__).parent)

GENESIS_HASH = "0" * 64
WORDS_PER_PAGE = 300

CHAPTER_STYLES = ("words", "arabic", "roman")
STYLE_KEYS = [
    "trim",
    "body_font",
    "heading_font",
    "body_size",
    "leading",
    "chapter_style",
    "epigraphs",
]

SUBMISSION_SPEC = {
    "font": "Times New Roman",
    "size_pt": 12,
    "spacing": "double",
    "margins_in": 1.0,
    "indent_in": 0.5,
    "header": "Surname / TITLE / page#",
    "scene_break": "#",
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
CREATE TABLE IF NOT EXISTS press_epigraph (
    book TEXT NOT NULL, number INTEGER NOT NULL,
    work_id TEXT NOT NULL DEFAULT '',
    has_epigraph INTEGER NOT NULL DEFAULT 1,
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
    global _DB_PATH, _MAIN_DB_PATH
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    _DB_PATH = str(Path(data_dir) / "press.db")
    _MAIN_DB_PATH = str(Path(data_dir) / "orivellum.db")


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

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _to_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else "-" + _ONES[n % 10])
    if n < 1000:
        rem = n % 100
        return _ONES[n // 100] + " hundred" + ("" if rem == 0 else " " + _to_words(rem))
    return str(n)


def _to_roman(n: int) -> str:
    vals = [
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ]
    out = ""
    for v, s in vals:
        while n >= v:
            out += s
            n -= v
    return out


def chapter_header(style: str, n: int) -> str:
    if style == "arabic":
        return f"Chapter {n}"
    if style == "roman":
        return f"Chapter {_to_roman(n)}"
    return f"Chapter {_to_words(n).title()}"


# ── DB helpers ────────────────────────────────────────────────────────────────


def _connect() -> sqlite3.Connection:
    c = sqlite3.connect(_db_path())
    c.row_factory = sqlite3.Row
    return c


def _main_conn() -> sqlite3.Connection:
    """Open a READ-ONLY connection to the main Orivellum database.

    PRESS only ever reads ``book_chapters`` from it — the single source of
    truth for chapter prose (LAW 1). Raises loudly when the main DB is
    missing rather than silently pretending a linked book has no chapters.
    """
    if not _MAIN_DB_PATH:
        raise RuntimeError("PRESS not configured — call configure(data_dir) first.")
    if not Path(_MAIN_DB_PATH).exists():
        raise RuntimeError(
            f"Main database not found at {_MAIN_DB_PATH} — cannot read real chapters."
        )
    c = sqlite3.connect(f"file:{_MAIN_DB_PATH}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def _real_chapters(work_id: str) -> list[dict]:
    """Read the authoritative chapters for a Work from ``book_chapters``.

    Word counts are computed from the actual text — never typed by hand.
    Chapter numbers are the stored ``seq`` normalised to start at 1 (the
    extractor writes 0-based seqs). Duplicate or gapped seqs surface as a
    failed contiguity check in :func:`verify` rather than being papered over.
    """
    if not work_id:
        return []
    mc = _main_conn()
    try:
        rows = mc.execute(
            "SELECT seq, title, text FROM book_chapters WHERE work_id=? ORDER BY seq",
            (work_id,),
        ).fetchall()
    finally:
        mc.close()
    if not rows:
        return []
    base = min(r["seq"] for r in rows)
    out = []
    for r in rows:
        text = r["text"] or ""
        out.append(
            {
                "seq": r["seq"],
                "number": r["seq"] - base + 1,
                "title": (r["title"] or "").strip(),
                "words": len(text.split()),
                "has_text": bool(text.strip()),
            }
        )
    return out


def _chapters_for_book(conn: sqlite3.Connection, slug: str, work_id: str) -> list[dict]:
    """Real chapters merged with PRESS epigraph-slot state.

    Only slots authored against the CURRENT linked Work are merged — a slot
    written for a different Work's "chapter 3" must never silently attach to
    this Work's chapter 3.
    """
    chs = _real_chapters(work_id)
    slots = {
        r["number"]: dict(r)
        for r in conn.execute(
            "SELECT * FROM press_epigraph WHERE book=? AND work_id=?", (slug, work_id)
        ).fetchall()
    }
    for ch in chs:
        s = slots.get(ch["number"])
        ch["has_epigraph"] = bool(s and s["has_epigraph"])
        ch["epigraph_text"] = s["epigraph_text"] if s else ""
        ch["epigraph_status"] = s["epigraph_status"] if s else ""
    return chs


def _orphan_slots(
    conn: sqlite3.Connection, slug: str, work_id: str, chapters: list[dict]
) -> list[int]:
    """Epigraph slots that no longer map to a chapter of the CURRENT Work.

    A slot is stale when its chapter number no longer exists, or when it was
    authored against a different Work (the book was relinked). Either way it
    must surface loudly and fail verification, never be silently dropped or
    silently reattached to unrelated prose.
    """
    valid = {c["number"] for c in chapters}
    return sorted(
        {
            r["number"]
            for r in conn.execute(
                "SELECT number, work_id FROM press_epigraph WHERE book=?", (slug,)
            ).fetchall()
            if r["work_id"] != work_id or r["number"] not in valid
        }
    )


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


# ── migration: consolidate onto book_chapters (LAW 1) ─────────────────────────


def _migrate_legacy_chapters(conn: sqlite3.Connection) -> None:
    """One-time migration away from the duplicate press-side chapter table.

    The old ``press_chapter`` table held hand-typed titles and word counts —
    a second, incompatible chapter model. Epigraph slot state (the only part
    PRESS legitimately owns) is carried into ``press_epigraph``; the legacy
    table is renamed to ``press_chapter_legacy`` so nothing is silently lost.
    Idempotent: does nothing once the rename has happened.
    """
    # Older press_epigraph tables predate slot provenance — add the column.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(press_epigraph)").fetchall()}
    if cols and "work_id" not in cols:
        conn.execute("ALTER TABLE press_epigraph ADD COLUMN work_id TEXT NOT NULL DEFAULT ''")
        conn.execute(
            "UPDATE press_epigraph SET work_id="
            "COALESCE((SELECT work_id FROM press_book WHERE slug=press_epigraph.book),'')"
        )
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='press_chapter'"
    ).fetchone()
    if not row:
        return
    moved = 0
    for r in conn.execute("SELECT * FROM press_chapter").fetchall():
        if r["has_epigraph"] or (r["epigraph_text"] or "").strip():
            book_work = conn.execute(
                "SELECT work_id FROM press_book WHERE slug=?", (r["book"],)
            ).fetchone()
            cur = conn.execute(
                "INSERT OR IGNORE INTO press_epigraph "
                "(book,number,work_id,has_epigraph,epigraph_text,epigraph_status) "
                "VALUES (?,?,?,?,?,?)",
                (
                    r["book"],
                    r["number"],
                    (book_work["work_id"] if book_work else "") or "",
                    1 if r["has_epigraph"] else 0,
                    r["epigraph_text"] or "",
                    r["epigraph_status"] or "",
                ),
            )
            moved += cur.rowcount
    conn.execute("ALTER TABLE press_chapter RENAME TO press_chapter_legacy")
    _ledger_append(
        conn,
        "press:migration",
        "chapters.consolidated",
        {"epigraph_rows_migrated": moved, "legacy_table": "press_chapter_legacy"},
    )


# ── public API ────────────────────────────────────────────────────────────────


def cmd_init(_a: Any = None) -> int:
    conn = _connect()
    conn.executescript(SCHEMA)
    _migrate_legacy_chapters(conn)
    conn.commit()
    return 0


def list_books(work_id: str | None = None) -> list[dict]:
    conn = _connect()
    if work_id:
        rows = conn.execute(
            "SELECT * FROM press_book WHERE work_id=? ORDER BY created_at DESC", (work_id,)
        ).fetchall()
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
    b["chapters"] = _chapters_for_book(conn, slug, b["work_id"] or "")
    b["orphan_epigraph_slots"] = _orphan_slots(conn, slug, b["work_id"] or "", b["chapters"])
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
    _ledger_append(
        conn, f"book:{s}", "book.created", {"title": title, "series": series, "work_id": work_id}
    )
    conn.commit()
    return get_book(s)  # type: ignore[return-value]


def link_work(slug: str, work_id: str) -> dict:
    """Point a press book at the Work whose real chapters it finalizes."""
    conn = _connect()
    row = conn.execute("SELECT * FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    conn.execute("UPDATE press_book SET work_id=? WHERE slug=?", (work_id, slug))
    _ledger_append(conn, f"book:{slug}", "work.linked", {"work_id": work_id})
    conn.commit()
    return get_book(slug)  # type: ignore[return-value]


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


def set_epigraph_slot(slug: str, number: int, has_epigraph: bool = True) -> dict:
    """Declare (or clear) an epigraph slot on a REAL chapter.

    Chapters themselves come from ``book_chapters`` — this only records that
    chapter *number* opens with an original epigraph. Refuses when the book
    has no linked Work, when the chapter does not exist in the manuscript,
    or when the style's epigraph policy is OFF.
    """
    conn = _connect()
    row = conn.execute("SELECT * FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    style = json.loads(row["style"])
    if has_epigraph and style.get("epigraphs") == "off":
        raise ValueError("Style epigraph policy is OFF; cannot add an epigraph slot.")
    work_id = row["work_id"] or ""
    if has_epigraph and not work_id:
        raise ValueError("Book is not linked to a Work — there are no real chapters yet.")
    chs = {c["number"]: c for c in _real_chapters(work_id)} if work_id else {}
    ch = chs.get(number)
    if has_epigraph and not ch:
        # Clearing is always allowed (stale slots must be removable); adding
        # requires the chapter to actually exist in the manuscript.
        raise KeyError(f"Chapter {number} does not exist in the manuscript.")
    if has_epigraph:
        # Slots carry the Work they were authored against. Recreating a slot
        # after a relink resets any drafted/approved text — an epigraph
        # written for another Work's prose is never carried over.
        conn.execute(
            "INSERT INTO press_epigraph (book,number,work_id,has_epigraph) VALUES (?,?,?,1) "
            "ON CONFLICT(book,number) DO UPDATE SET has_epigraph=1, "
            "epigraph_text=CASE WHEN work_id=excluded.work_id THEN epigraph_text ELSE '' END, "
            "epigraph_status=CASE WHEN work_id=excluded.work_id THEN epigraph_status ELSE '' END, "
            "work_id=excluded.work_id",
            (slug, number, work_id),
        )
    else:
        conn.execute("DELETE FROM press_epigraph WHERE book=? AND number=?", (slug, number))
    _ledger_append(
        conn, f"book:{slug}", "epigraph.slot", {"chapter": number, "has_epigraph": has_epigraph}
    )
    conn.commit()
    return {
        "number": number,
        "title": ch["title"] if ch else "",
        "words": ch["words"] if ch else 0,
        "has_epigraph": has_epigraph,
        "header": chapter_header(style.get("chapter_style", "arabic"), number),
    }


def draft_epigraph(
    slug: str,
    number: int,
    soul: str = "",
    in_world: str = "",
    gateway_name: str = "mock",
    want_quote: bool = False,
) -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    chs = {c["number"]: c for c in _real_chapters(row["work_id"] or "")}
    ch = chs.get(number)
    if not ch:
        raise KeyError(f"Chapter {number} not found in the manuscript.")
    slot = conn.execute(
        "SELECT * FROM press_epigraph WHERE book=? AND number=? AND has_epigraph=1 AND work_id=?",
        (slug, number, row["work_id"] or ""),
    ).fetchone()
    if not slot:
        raise ValueError("This chapter has no epigraph slot for the current manuscript.")
    engine = gw.get_gateway(gateway_name)
    res = engine.original_epigraph(
        {
            "soul": soul,
            "in_world_source": in_world,
            "chapter": ch["title"],
            "want_quote": want_quote,
        }
    )
    if res.status == "ABSTAINED":
        return {"status": "ABSTAINED", "reason": res.reason}
    text = res.text + (f"\n— {res.attribution}" if res.attribution else "")
    conn.execute(
        "UPDATE press_epigraph SET epigraph_text=?, epigraph_status=? WHERE book=? AND number=?",
        (text, res.status, slug, number),
    )
    _ledger_append(
        conn, f"book:{slug}", "epigraph.drafted", {"chapter": number, "status": res.status}
    )
    conn.commit()
    return {"status": res.status, "text": text, "attribution": res.attribution}


def approve_epigraph(slug: str, number: int, author: str) -> None:
    conn = _connect()
    row = conn.execute("SELECT * FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    slot = conn.execute(
        "SELECT * FROM press_epigraph WHERE book=? AND number=? AND work_id=?",
        (slug, number, row["work_id"] or ""),
    ).fetchone()
    if not slot or not slot["epigraph_text"]:
        raise ValueError("No drafted epigraph to approve for the current manuscript.")
    conn.execute(
        "UPDATE press_epigraph SET epigraph_status='APPROVED' WHERE book=? AND number=?",
        (slug, number),
    )
    _ledger_append(conn, f"book:{slug}", "epigraph.approved", {"chapter": number, "author": author})
    conn.commit()


def set_matter(slug: str, front: bool, back: bool) -> None:
    conn = _connect()
    if not conn.execute("SELECT 1 FROM press_book WHERE slug=?", (slug,)).fetchone():
        raise KeyError(f"Book '{slug}' not found.")
    conn.execute(
        "UPDATE press_book SET has_front=?, has_back=? WHERE slug=?",
        (1 if front else 0, 1 if back else 0, slug),
    )
    conn.commit()


def _page_estimate(chapters: list[dict], has_front: bool, has_back: bool) -> tuple[int, int]:
    """Word total and page estimate computed from REAL chapter text."""
    total = sum(c["words"] for c in chapters)
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
    work_id = b["work_id"] or ""
    chs = _chapters_for_book(conn, slug, work_id)
    nums = [c["number"] for c in chs]
    orphans = _orphan_slots(conn, slug, work_id, chs)
    checks = {
        "style_locked": bool(b["style_locked"]),
        "linked_to_work": bool(work_id),
        "has_chapters": len(chs) >= 1,
        "chapters_contiguous": nums == list(range(1, len(nums) + 1)) if nums else False,
        "chapters_titled": all(c["title"] for c in chs) if chs else False,
        "chapters_have_text": all(c["has_text"] for c in chs) if chs else False,
        "epigraph_policy": True,
        "epigraph_slots_valid": not orphans,
        "front_matter": bool(b["has_front"]),
        "back_matter": bool(b["has_back"]),
    }
    if style.get("epigraphs") == "off":
        checks["epigraph_policy"] = all(not c["has_epigraph"] for c in chs)
    else:
        for c in chs:
            if c["has_epigraph"] and c["epigraph_status"] != "APPROVED":
                checks["epigraph_policy"] = False
    total, pages = _page_estimate(chs, bool(b["has_front"]), bool(b["has_back"]))
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
    if not vr["checks"]["epigraph_slots_valid"]:
        # Non-bypassable: stale slots mean the epigraph state was authored
        # against different or nonexistent prose. No package may carry that.
        raise ValueError("Stale epigraph slots — clear or recreate them before packaging.")
    if not vr["passed"] and not (pkg_type == "publisher" and target == "submission"):
        raise ValueError(
            "Pre-flight failed — package blocked. (Only submission MS format is allowed pre-typeset.)"
        )
    chs = _chapters_for_book(conn, slug, b["work_id"] or "")
    total, pages = _page_estimate(chs, bool(b["has_front"]), bool(b["has_back"]))
    if pkg_type == "publisher" and target == "submission":
        spec = {
            "format": "standard-manuscript-format",
            **SUBMISSION_SPEC,
            "word_count_rounded": int(round(total, -2)),
        }
    elif pkg_type == "publisher":
        spec = {
            "format": "typeset-production",
            **{k: style.get(k) for k in STYLE_KEYS},
            "estimated_pages": pages,
        }
    else:
        spec = {
            "format": "advance-reader-copy",
            "stamp": "ADVANCE READER COPY — NOT FOR RESALE",
            "based_on": {k: style.get(k) for k in STYLE_KEYS},
            "estimated_pages": pages,
            "delivery": "PDF/EPUB",
        }
    return {"package_type": pkg_type, "target": target, "spec": spec, "preflight": vr}


def seal_package(slug: str, pkg_type: str, target: str, author: str, recipient: str = "") -> dict:
    conn = _connect()
    row = conn.execute("SELECT * FROM press_book WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise KeyError(f"Book '{slug}' not found.")
    b = dict(row)
    style = json.loads(b["style"])
    vr = verify(slug)
    if not vr["checks"]["epigraph_slots_valid"]:
        # Non-bypassable, even for the submission-format exception.
        raise ValueError("Stale epigraph slots — clear or recreate them before sealing.")
    if not vr["passed"] and not (pkg_type == "publisher" and target == "submission"):
        raise ValueError("Pre-flight failed — cannot seal.")
    chs = _chapters_for_book(conn, slug, b["work_id"] or "")
    total, pages = _page_estimate(chs, bool(b["has_front"]), bool(b["has_back"]))
    if pkg_type == "publisher" and target == "submission":
        spec = {
            "format": "standard-manuscript-format",
            **SUBMISSION_SPEC,
            "word_count_rounded": int(round(total, -2)),
        }
    elif pkg_type == "publisher":
        spec = {
            "format": "typeset-production",
            **{k: style.get(k) for k in STYLE_KEYS},
            "estimated_pages": pages,
        }
    else:
        spec = {
            "format": "advance-reader-copy",
            "stamp": "ADVANCE READER COPY — NOT FOR RESALE",
            "based_on": {k: style.get(k) for k in STYLE_KEYS},
            "estimated_pages": pages,
            "delivery": "PDF/EPUB",
        }
    manifest: dict = {
        "book": b["title"],
        "series": b["series"],
        "author": b["author_name"],
        "package_type": pkg_type,
        "target": target if pkg_type == "publisher" else None,
        "spec": spec,
        "signoff": author,
        "sealed_at": _now(),
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
        _ledger_append(
            conn, f"book:{slug}", "arc.distributed", {"recipient": recipient, "watermark": wm}
        )
    ph = _sha(_canon(manifest))
    manifest["package_sha256"] = ph
    _ledger_append(
        conn,
        f"book:{slug}",
        "package.sealed",
        {"type": pkg_type, "target": manifest["target"], "sha256": ph, "author": author},
    )
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
