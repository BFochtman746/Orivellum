"""
io_orivellum.py — book text assembly from the Orivellum DB.

Replaces the filesystem-based io_book.load_text() for use inside the
Orivellum server.  Given a DB handle and work_id it:

  1. Pulls all 'ready' documents attached to the Work (full extracted text).
  2. Falls back to knowledge items if no extracted text is available.
  3. Applies the same sample_passages() window logic as the original tool so
     extremely large corpora don't overflow the context window.
"""
from __future__ import annotations


def sample_passages(text: str, windows: int = 6, window_chars: int = 1800) -> str:
    """Return opening + evenly-spaced middles + ending, labelled."""
    text = text.strip()
    n = len(text)
    if n <= window_chars * (windows + 2):
        return text

    picks: list[tuple[str, str]] = []
    picks.append(("OPENING", text[:window_chars]))
    for i in range(1, windows + 1):
        center = int(n * i / (windows + 1))
        start = max(0, center - window_chars // 2)
        picks.append((f"MIDDLE ~{int(100*i/(windows+1))}%", text[start:start + window_chars]))
    picks.append(("ENDING", text[-window_chars:]))

    return "\n\n".join(f"[{label}]\n{chunk}" for label, chunk in picks)


def book_text_from_work(db, work_id: str) -> str:
    """Return a single text string representing the Work's full corpus."""
    parts: list[str] = []

    # 1. Pull extracted text from ready documents (prefer chapter-ordered)
    with db._lock:
        doc_rows = db._conn.execute(
            """SELECT d.title, d.extracted_text
               FROM documents d
               WHERE d.work_id=? AND d.readiness='ready' AND d.extracted_text IS NOT NULL
                 AND d.extracted_text != ''
                 AND COALESCE(d.quarantined, 0) = 0
               ORDER BY d.title""",
            (work_id,),
        ).fetchall()

    for row in doc_rows:
        title = row["title"] or "Untitled"
        parts.append(f"=== {title} ===\n{row['extracted_text']}")

    if parts:
        return "\n\n".join(parts)

    # 2. Fallback: stitch knowledge items into prose passages
    with db._lock:
        ki_rows = db._conn.execute(
            """SELECT k.kind, k.text FROM knowledge k
               WHERE k.work_id=? AND k.review_status != 'rejected'
               ORDER BY k.kind, k.created_at""",
            (work_id,),
        ).fetchall()

    for row in ki_rows:
        parts.append(f"[{row['kind']}] {row['text']}")

    if parts:
        return "\n\n".join(parts)

    return ""


def slugify(title: str) -> str:
    keep = [c.lower() if c.isalnum() else "-" for c in (title or "trailer")]
    s = "".join(keep)
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")[:48] or "trailer"
