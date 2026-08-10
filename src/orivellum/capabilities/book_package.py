"""Book packaging — assemble a pipeline's chapters into a distributable export.

This is the finishing step for the Works book pipeline: it takes the
chapters linked to a ``book_pipelines`` row and produces real files —

  · a valid EPUB 3 (stdlib zipfile only, no external tooling), and
  · a companion bundle: one Markdown file per chapter plus manifest.json

— delivered together as a single ZIP archive, built entirely in memory.

Design rules (same philosophy as the rest of the system):
  · the server owns structure and naming; nothing here calls a model
  · readiness is explicit: ``package_readiness()`` says exactly what is
    missing instead of failing silently
  · chapters without text are skipped and reported, never invented
"""

from __future__ import annotations

import html
import io
import json
import re
import zipfile
from datetime import UTC, datetime

__all__ = ["package_readiness", "build_book_export"]

_MIN_CHAPTER_CHARS = 1  # a chapter counts if it has any text at all

# Everything is assembled in memory; refuse pathological inputs rather than
# risk exhausting the process. 50 MB of raw text is far beyond any real book.
MAX_TOTAL_TEXT_CHARS = 50_000_000

# XML 1.0 forbids most C0 control characters even when escaped.
_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xml_clean(s: str) -> str:
    return _XML_INVALID.sub("", s)


def _slug(s: str | None, maxlen: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", (s or "book").lower()).strip()
    s = re.sub(r"[\s_]+", "-", s)
    return (s[:maxlen] or "book").strip("-")


def package_readiness(pipeline: dict, chapters: list[dict]) -> dict:
    """Report whether a book package can be built, and if not, why.

    Never raises — the result is meant to be shown to the user verbatim.
    """
    with_text = [c for c in chapters if (c.get("text") or "").strip()]
    empty = len(chapters) - len(with_text)
    reasons: list[str] = []
    if not chapters:
        reasons.append(
            "No chapters are linked to this pipeline yet — run the pipeline "
            "through Chapter Extraction (B4) first."
        )
    elif not with_text:
        reasons.append(
            f"All {len(chapters)} linked chapters are empty — chapter text is "
            "produced by Chapter Extraction/Drafting (B4–B5)."
        )
    return {
        "ready": bool(with_text),
        "chapters_total": len(chapters),
        "chapters_with_text": len(with_text),
        "chapters_empty": empty,
        "stage": pipeline.get("status"),
        "reasons": reasons,
    }


# ── EPUB 3 assembly (stdlib only) ────────────────────────────────────────────

_CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_CSS = """body { font-family: serif; line-height: 1.5; margin: 5%; }
h1 { text-align: center; margin: 2em 0 1em; }
p { text-indent: 1.2em; margin: 0 0 0.2em; }
"""


def _chapter_xhtml(title: str, text: str) -> str:
    paras = "".join(
        f"    <p>{html.escape(_xml_clean(p.strip()))}</p>\n"
        for p in re.split(r"\n\s*\n", text)
        if p.strip()
    )
    t = html.escape(_xml_clean(title))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
        f"  <head><title>{t}</title>"
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f"  <body>\n    <h1>{t}</h1>\n{paras}  </body>\n</html>\n"
    )


def _build_epub(title: str, author: str, book_id: str, chapters: list[dict]) -> bytes:
    """Assemble a minimal, valid EPUB 3 and return its bytes."""
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    t = html.escape(_xml_clean(title))

    items, spine, nav_lis, files = [], [], [], {}
    for i, ch in enumerate(chapters, start=1):
        name = f"chapter-{i:03d}.xhtml"
        ch_title = (ch.get("title") or "").strip() or f"Chapter {i}"
        files[f"OEBPS/{name}"] = _chapter_xhtml(ch_title, ch["text"])
        items.append(f'    <item id="ch{i}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'    <itemref idref="ch{i}"/>')
        nav_lis.append(f'      <li><a href="{name}">{html.escape(_xml_clean(ch_title))}</a></li>')

    nav = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">\n'
        f"  <head><title>{t}</title></head>\n"
        '  <body>\n    <nav epub:type="toc" id="toc">\n'
        f"    <h1>{t}</h1>\n    <ol>\n" + "\n".join(nav_lis) + "\n    </ol>\n"
        "    </nav>\n  </body>\n</html>\n"
    )

    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="pub-id">urn:orivellum:book:{html.escape(book_id)}</dc:identifier>\n'
        f"    <dc:title>{t}</dc:title>\n"
        f"    <dc:creator>{html.escape(_xml_clean(author))}</dc:creator>\n"
        "    <dc:language>en</dc:language>\n"
        f'    <meta property="dcterms:modified">{now}</meta>\n'
        "  </metadata>\n  <manifest>\n"
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
        '    <item id="css" href="style.css" media-type="text/css"/>\n'
        + "\n".join(items)
        + "\n  </manifest>\n  <spine>\n"
        + "\n".join(spine)
        + "\n  </spine>\n</package>\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        # Spec: 'mimetype' must be first and stored uncompressed.
        zf.writestr(
            zipfile.ZipInfo("mimetype"),
            "application/epub+zip",
            compress_type=zipfile.ZIP_STORED,
        )
        zf.writestr("META-INF/container.xml", _CONTAINER_XML, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/style.css", _CSS, compress_type=zipfile.ZIP_DEFLATED)
        for path, content in files.items():
            zf.writestr(path, content, compress_type=zipfile.ZIP_DEFLATED)
    return buf.getvalue()


def build_book_export(
    pipeline: dict, chapters: list[dict], work: dict | None = None
) -> tuple[str, bytes]:
    """Build the full book package ZIP; returns (filename, bytes).

    Raises ValueError with a user-readable message when not ready — callers
    should check ``package_readiness()`` first for a friendlier flow.
    """
    readiness = package_readiness(pipeline, chapters)
    if not readiness["ready"]:
        raise ValueError("; ".join(readiness["reasons"]) or "No packageable chapters")

    title = (
        (pipeline.get("title") or "").strip()
        or ((work or {}).get("title") or "").strip()
        or "Untitled Book"
    )
    author = ((work or {}).get("author") or "").strip() or "Orivellum"
    usable = [c for c in chapters if (c.get("text") or "").strip()]
    total_chars = sum(len(c.get("text") or "") for c in usable)
    if total_chars > MAX_TOTAL_TEXT_CHARS:
        raise ValueError(
            f"Book text is too large to package in one export "
            f"({total_chars:,} characters; limit {MAX_TOTAL_TEXT_CHARS:,})."
        )
    skipped = [
        {"seq": c.get("seq"), "title": c.get("title"), "reason": "empty text"}
        for c in chapters
        if not (c.get("text") or "").strip()
    ]

    epub = _build_epub(title, author, pipeline["id"], usable)
    slug = _slug(title)

    from orivellum.version import code_version

    manifest = {
        "generator": "orivellum-book-package",
        "code_version": code_version(),
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "title": title,
        "pipeline_id": pipeline["id"],
        "work_id": pipeline.get("work_id"),
        "pipeline_stage": pipeline.get("status"),
        "chapters_included": len(usable),
        "chapters_skipped": skipped,
        "contents": [f"{slug}.epub", "markdown/", "manifest.json"],
        "note": (
            "Built from pipeline chapters as they exist today; "
            "stage gates were not bypassed — check pipeline_stage to see "
            "how far editing had progressed at export time."
        ),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{slug}.epub", epub)
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        for i, ch in enumerate(usable, start=1):
            ch_title = ch.get("title") or f"Chapter {i}"
            md = f"# {ch_title}\n\n{ch['text'].strip()}\n"
            zf.writestr(f"markdown/{i:03d}-{_slug(ch_title, 40)}.md", md)
    return f"{slug}-package.zip", buf.getvalue()
