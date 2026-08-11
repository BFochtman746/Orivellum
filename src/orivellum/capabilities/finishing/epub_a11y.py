"""Accessible EPUB 3 builder (EPUB Accessibility 1.1 / EAA).

Builds the ebook from the SAME single manuscript source as the print PDF,
with accessibility manufactured in, not bolted on:

  · TOC nav AND landmarks nav; page-list keyed to the REAL print edition
    (page anchors come from the PDF renderer's page → paragraph map);
  · epub:type semantics on front matter, body matter, back matter, chapters,
    and epigraphs;
  · schema.org accessibility metadata + dcterms:conformsTo + certifier;
  · xml:lang on every Hebrew/Aramaic-script run;
  · alt text on every image (the builder refuses an image without one);
  · properly nested headings; reflowable CSS that survives reader overrides.

stdlib zipfile only — validation is EPUBCheck + Ace's job (see
``scripts/validate_epub.py``), never this builder's own opinion of itself.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from datetime import UTC, datetime
from typing import Any

__all__ = ["build_accessible_epub", "EpubError"]


class EpubError(ValueError):
    pass


_XML_INVALID = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
# Hebrew + Hebrew presentation forms; Imperial Aramaic uses the same block in
# modern transliteration sources, so a run of Hebrew script gets xml:lang.
_HEBREW_RUN = re.compile(r"[\u0590-\u05FF\uFB1D-\uFB4F][\u0590-\u05FF\uFB1D-\uFB4F\s]*")

_CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

# Reflowable, override-friendly: relative sizes only, no absolute positioning,
# nothing conveyed by colour alone.
_CSS = """body { line-height: 1.5; margin: 0 5%; font-family: serif; }
h1, h2 { text-align: center; margin: 2em 0 1em; }
p { text-indent: 1.2em; margin: 0 0 0.2em; }
p.first { text-indent: 0; }
blockquote.epigraph { font-style: italic; margin: 0 3em 1.5em; text-align: center; }
span.pagebreak { display: inline; }
"""


def _clean(s: str) -> str:
    return _XML_INVALID.sub("", s or "")


def _esc(s: str) -> str:
    return html.escape(_clean(s))


def _mark_hebrew(escaped: str) -> str:
    """Wrap Hebrew/Aramaic-script runs in a language-tagged span.

    Operates on already-escaped text — escaping never introduces Hebrew
    codepoints, so match positions are safe.
    """
    return _HEBREW_RUN.sub(lambda m: f'<span xml:lang="he" lang="he">{m.group(0)}</span>', escaped)


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def _pagebreak_span(page: int) -> str:
    return (
        f'<span epub:type="pagebreak" role="doc-pagebreak" id="pg{page}" '
        f'aria-label="{page}" class="pagebreak"></span>'
    )


def _chapter_xhtml(
    header: str,
    title: str,
    epigraph: str,
    text: str,
    number: int,
    page_anchors: dict[int, int],
    chapter_start_page: int | None,
) -> str:
    """One chapter document. ``page_anchors`` maps paragraph index → print
    page number that begins at that paragraph."""
    h_line = _esc(header)
    t_line = _mark_hebrew(_esc(title))
    heading = f"{h_line} · {t_line}" if title else h_line
    parts: list[str] = []
    if chapter_start_page is not None:
        parts.append(f"  {_pagebreak_span(chapter_start_page)}\n")
    parts.append(f"  <h1>{heading}</h1>\n")
    if epigraph:
        epi = _mark_hebrew(_esc(epigraph)).replace("\n", "<br/>")
        parts.append(
            f'  <blockquote class="epigraph" epub:type="epigraph"><p>{epi}</p></blockquote>\n'
        )
    for i, para in enumerate(_paragraphs(text)):
        anchor = ""
        page = page_anchors.get(i)
        if page is not None and page != chapter_start_page:
            anchor = _pagebreak_span(page)
        cls = ' class="first"' if i == 0 else ""
        parts.append(f"  <p{cls}>{anchor}{_mark_hebrew(_esc(para))}</p>\n")
    body = "".join(parts)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">\n'
        f"<head><title>{h_line}</title>"
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f'<body>\n<section epub:type="bodymatter chapter" role="doc-chapter" '
        f'aria-labelledby="ch{number}-h">\n'
        + body.replace("<h1>", f'<h1 id="ch{number}-h">', 1)
        + "</section>\n</body>\n</html>\n"
    )


def _simple_doc(title: str, epub_type: str, role: str, inner: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">\n'
        f"<head><title>{_esc(title)}</title>"
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f'<body>\n<section epub:type="{epub_type}" role="{role}">\n{inner}</section>\n'
        "</body>\n</html>\n"
    )


def _validate_inputs(chapters: list[dict], cover_image: tuple | None) -> None:
    if not chapters:
        raise EpubError("No chapters to package.")
    empty = [c["number"] for c in chapters if not (c.get("text") or "").strip()]
    if empty:
        raise EpubError(f"Chapters without text cannot be packaged: {empty}")
    if cover_image is not None and not (cover_image[2] or "").strip():
        raise EpubError("Cover image has no alt text — describe it or drop it.")


def _page_list_items(
    chapter_pages: dict[int, int], page_map: dict[int, tuple[int, int]]
) -> list[str]:
    """Every print-page anchor the PDF render produced, in page order."""
    page_lis: list[str] = []
    for n in sorted(chapter_pages):
        page_lis.append(
            f'      <li><a href="chapter-{n:03d}.xhtml#pg{chapter_pages[n]}">'
            f"{chapter_pages[n]}</a></li>"
        )
    chapter_start_pages = set(chapter_pages.values())
    for page in sorted(page_map):
        ch_num, _para = page_map[page]
        if page in chapter_start_pages:
            continue
        page_lis.append(f'      <li><a href="chapter-{ch_num:03d}.xhtml#pg{page}">{page}</a></li>')
    return page_lis


def _a11y_metadata(
    *,
    has_page_list: bool,
    has_cover: bool,
    isbn: str,
    book_id: str,
    certifier: str,
) -> list[str]:
    meta = [
        '    <meta property="schema:accessMode">textual</meta>',
        '    <meta property="schema:accessModeSufficient">textual</meta>',
        '    <meta property="schema:accessibilityFeature">tableOfContents</meta>',
        '    <meta property="schema:accessibilityFeature">readingOrder</meta>',
        '    <meta property="schema:accessibilityFeature">structuralNavigation</meta>',
        '    <meta property="schema:accessibilityFeature">displayTransformability</meta>',
        '    <meta property="schema:accessibilityHazard">none</meta>',
        '    <meta property="schema:accessibilitySummary">'
        "This publication conforms to the EPUB Accessibility 1.1 specification "
        "at WCAG 2.1 Level AA. It provides full structural navigation"
        + (", print-equivalent page numbering" if has_page_list else "")
        + ", and language markup on Hebrew and Aramaic terms.</meta>",
        '    <link rel="dcterms:conformsTo" '
        'href="http://www.idpf.org/epub/a11y/accessibility-20170105.html#wcag-aa"/>',
        f'    <meta property="a11y:certifiedBy">{_esc(certifier)}</meta>',
    ]
    if has_page_list:
        meta.insert(5, '    <meta property="schema:accessibilityFeature">pageBreakMarkers</meta>')
        src = f"urn:isbn:{isbn}" if isbn else f"urn:orivellum:print:{html.escape(book_id)}"
        meta.append(f"    <dc:source>{src}</dc:source>")
    if has_cover:
        meta.insert(5, '    <meta property="schema:accessibilityFeature">alternativeText</meta>')
    return meta


def _add_cover(
    cover_image: tuple[bytes, str, str],
    files: dict,
    manifest_items: list[str],
    landmarks: list[str],
    add_doc: Any,
) -> None:
    img_bytes, media_type, alt = cover_image
    ext = {"image/jpeg": "jpg", "image/png": "png"}.get(media_type)
    if not ext:
        raise EpubError(f"Unsupported cover media type: {media_type}")
    files[f"OEBPS/cover.{ext}"] = img_bytes
    manifest_items.append(
        f'    <item id="cover-img" href="cover.{ext}" '
        f'media-type="{media_type}" properties="cover-image"/>'
    )
    add_doc(
        "cover.xhtml",
        _simple_doc(
            "Cover",
            "frontmatter cover",
            "doc-cover",
            f'  <h1 class="hidden">Cover</h1>\n'
            f'  <p><img src="cover.{ext}" alt="{_esc(alt)}"/></p>\n',
        ),
        "cover",
    )
    landmarks.append('        <li><a epub:type="cover" href="cover.xhtml">Cover</a></li>')


def build_accessible_epub(
    *,
    title: str,
    author: str,
    book_id: str,
    language: str = "en",
    chapters: list[dict],
    chapter_headers: dict[int, str],
    page_map: dict[int, tuple[int, int]] | None = None,
    chapter_pages: dict[int, int] | None = None,
    has_front: bool = False,
    has_back: bool = False,
    isbn: str = "",
    cover_image: tuple[bytes, str, str] | None = None,
    certifier: str = "Orivellum PRESS",
) -> bytes:
    """Assemble the accessible EPUB 3 and return its bytes.

    ``page_map`` — print page → (chapter number, paragraph index), from the
    PDF render; drives the page-list so ebook pages reference the REAL print
    edition. ``cover_image`` is (bytes, media_type, alt_text); an empty alt
    is refused (LAW 3 of accessibility: describe it or it does not ship).
    """
    _validate_inputs(chapters, cover_image)

    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    t = _esc(title)
    page_map = page_map or {}
    chapter_pages = chapter_pages or {}

    # Invert page_map: chapter → {para_index: page}, skipping chapter-start
    # pages (those anchor at the top of the chapter file instead).
    anchors_by_chapter: dict[int, dict[int, int]] = {}
    for page, (ch_num, para_idx) in page_map.items():
        anchors_by_chapter.setdefault(ch_num, {})[para_idx] = page

    files: dict[str, bytes | str] = {}
    manifest_items: list[str] = []
    spine_refs: list[str] = []
    toc_lis: list[str] = []
    landmarks: list[str] = []

    def add_doc(name: str, content: str, item_id: str, in_spine: bool = True) -> None:
        files[f"OEBPS/{name}"] = content
        manifest_items.append(
            f'    <item id="{item_id}" href="{name}" media-type="application/xhtml+xml"/>'
        )
        if in_spine:
            spine_refs.append(f'    <itemref idref="{item_id}"/>')

    if cover_image is not None:
        _add_cover(cover_image, files, manifest_items, landmarks, add_doc)

    if has_front:
        add_doc(
            "titlepage.xhtml",
            _simple_doc(
                title,
                "frontmatter titlepage",
                "doc-part",
                f"  <h1>{t}</h1>\n  <p>{_esc(author)}</p>\n",
            ),
            "titlepage",
        )
        toc_lis.append('      <li><a href="titlepage.xhtml">Title Page</a></li>')
        landmarks.append(
            '        <li><a epub:type="titlepage" href="titlepage.xhtml">Title Page</a></li>'
        )

    first_chapter_file = ""
    for ch in chapters:
        n = ch["number"]
        name = f"chapter-{n:03d}.xhtml"
        if not first_chapter_file:
            first_chapter_file = name
        header = chapter_headers[n]
        add_doc(
            name,
            _chapter_xhtml(
                header,
                (ch.get("title") or "").strip(),
                (ch.get("epigraph_text") or "").strip(),
                ch["text"],
                n,
                anchors_by_chapter.get(n, {}),
                chapter_pages.get(n),
            ),
            f"ch{n}",
        )
        label = _esc(header + (f" · {ch['title']}" if (ch.get("title") or "").strip() else ""))
        toc_lis.append(f'      <li><a href="{name}">{label}</a></li>')

    landmarks.append(
        f'        <li><a epub:type="bodymatter" href="{first_chapter_file}">'
        "Start of Content</a></li>"
    )

    if has_back:
        add_doc(
            "backmatter.xhtml",
            _simple_doc(
                "About the Author",
                "backmatter",
                "doc-afterword",
                f"  <h1>About the Author</h1>\n  <p>{_esc(author)}</p>\n",
            ),
            "backmatter",
        )
        toc_lis.append('      <li><a href="backmatter.xhtml">About the Author</a></li>')
        landmarks.append(
            '        <li><a epub:type="backmatter" href="backmatter.xhtml">Back Matter</a></li>'
        )

    page_lis = _page_list_items(chapter_pages, page_map)
    page_list_nav = (
        '    <nav epub:type="page-list" role="doc-pagelist" aria-labelledby="pl-h" hidden="">\n'
        '      <h2 id="pl-h">Pages</h2>\n      <ol>\n'
        + "\n".join(f"  {li}" for li in page_lis)
        + "\n      </ol>\n    </nav>\n"
        if page_lis
        else ""
    )

    nav = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">\n'
        f"<head><title>{t}</title>"
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        "<body>\n"
        '  <nav epub:type="toc" role="doc-toc" id="toc" aria-labelledby="toc-h">\n'
        f'    <h1 id="toc-h">Contents</h1>\n    <ol>\n'
        + "\n".join(toc_lis)
        + "\n    </ol>\n  </nav>\n"
        '  <nav epub:type="landmarks" aria-labelledby="lm-h" hidden="">\n'
        '    <h2 id="lm-h">Landmarks</h2>\n    <ol>\n'
        + "\n".join(landmarks)
        + "\n    </ol>\n  </nav>\n"
        + page_list_nav
        + "</body>\n</html>\n"
    )

    a11y_meta = _a11y_metadata(
        has_page_list=bool(page_lis),
        has_cover=cover_image is not None,
        isbn=isbn,
        book_id=book_id,
        certifier=certifier,
    )

    identifier = f"urn:isbn:{isbn}" if isbn else f"urn:orivellum:book:{html.escape(book_id)}"
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        'unique-identifier="pub-id" xml:lang="en" '
        'prefix="schema: http://schema.org/ a11y: http://www.idpf.org/epub/vocab/package/a11y/#">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        f'    <dc:identifier id="pub-id">{identifier}</dc:identifier>\n'
        f"    <dc:title>{t}</dc:title>\n"
        f"    <dc:creator>{_esc(author)}</dc:creator>\n"
        f"    <dc:language>{_esc(language)}</dc:language>\n"
        f'    <meta property="dcterms:modified">{now}</meta>\n'
        + "\n".join(a11y_meta)
        + "\n  </metadata>\n  <manifest>\n"
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" '
        'properties="nav"/>\n'
        '    <item id="css" href="style.css" media-type="text/css"/>\n'
        + "\n".join(manifest_items)
        + "\n  </manifest>\n  <spine>\n"
        + "\n".join(spine_refs)
        + "\n  </spine>\n</package>\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
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
