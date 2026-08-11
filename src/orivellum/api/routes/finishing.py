"""Finishing Suite API — PRESS (manuscript finalization) + ATELIER (cover/series design).

Routes are grouped under /api/finishing/press/* and /api/finishing/atelier/*.
Both subsystems use their own SQLite stores in config.data_dir.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_config, require_auth

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/finishing", tags=["finishing"], dependencies=[Depends(require_auth)]
)
# ── lazy init helpers ─────────────────────────────────────────────────────────

_initialized = False


def _ensure_init() -> None:
    global _initialized
    if _initialized:
        return
    from orivellum.capabilities.finishing import configure

    cfg = get_config()
    configure(cfg.data_dir)
    _initialized = True


def _press():
    _ensure_init()
    from orivellum.capabilities.finishing import press

    return press


def _atelier():
    _ensure_init()
    from orivellum.capabilities.finishing import atelier

    return atelier


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(404, str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(422, str(exc))


# ═══════════════════════════════════════════════════════════════════════════════
# PRESS models
# ═══════════════════════════════════════════════════════════════════════════════


class PressBookCreate(BaseModel):
    title: str
    author_name: str
    series: str = ""
    work_id: str = ""


class StyleUpdate(BaseModel):
    trim: str | None = None
    body_font: str | None = None
    heading_font: str | None = None
    body_size: str | None = None
    leading: str | None = None
    chapter_style: str | None = None
    epigraphs: str | None = None


class StyleLock(BaseModel):
    author: str


class EpigraphSlot(BaseModel):
    has_epigraph: bool = True


class WorkLink(BaseModel):
    work_id: str


class EpigraphDraft(BaseModel):
    soul: str = ""
    in_world: str = ""
    # "lemonade" = the real LLM gateway (abstains on failure, never fabricates);
    # "mock" remains available for offline/deterministic use.
    gateway: str = "lemonade"
    want_quote: bool = False


class EpigraphApprove(BaseModel):
    author: str


class MatterSet(BaseModel):
    front: bool = False
    back: bool = False


class PackageRequest(BaseModel):
    pkg_type: str = "publisher"  # publisher | test-reader
    target: str = "production"  # production | submission (publisher only)


class SealRequest(BaseModel):
    pkg_type: str = "publisher"
    target: str = "production"
    author: str
    recipient: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# PRESS routes
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/press/books")
def press_list_books(work_id: str | None = None):
    try:
        return {"books": _press().list_books(work_id=work_id)}
    except Exception as e:
        raise _http(e) from e


@router.post("/press/books", status_code=201)
def press_create_book(body: PressBookCreate):
    try:
        return {
            "book": _press().create_book(
                title=body.title,
                author_name=body.author_name,
                series=body.series,
                work_id=body.work_id,
            )
        }
    except Exception as e:
        raise _http(e) from e


@router.get("/press/books/{slug}")
def press_get_book(slug: str):
    try:
        book = _press().get_book(slug)
        if not book:
            raise HTTPException(404, f"Book '{slug}' not found")
        return {"book": book}
    except HTTPException:
        raise
    except Exception as e:
        raise _http(e) from e


@router.patch("/press/books/{slug}/style")
def press_update_style(slug: str, body: StyleUpdate):
    try:
        style = _press().update_style(slug, body.model_dump(exclude_none=True))
        return {"style": style}
    except Exception as e:
        raise _http(e) from e


@router.post("/press/books/{slug}/style/lock")
def press_lock_style(slug: str, body: StyleLock):
    try:
        _press().lock_style(slug, body.author)
        return {"ok": True}
    except Exception as e:
        raise _http(e) from e


@router.post("/press/books/{slug}/link-work")
def press_link_work(slug: str, body: WorkLink):
    """Point a press book at the Work whose real chapters it finalizes."""
    try:
        return {"book": _press().link_work(slug, body.work_id)}
    except Exception as e:
        raise _http(e) from e


@router.post("/press/books/{slug}/chapters/{number}/epigraph-slot")
def press_set_epigraph_slot(slug: str, number: int, body: EpigraphSlot):
    """Declare or clear an epigraph slot on a real (book_chapters) chapter."""
    try:
        return {"chapter": _press().set_epigraph_slot(slug, number, body.has_epigraph)}
    except Exception as e:
        raise _http(e) from e


@router.post("/press/books/{slug}/chapters/{number}/epigraph")
def press_draft_epigraph(slug: str, number: int, body: EpigraphDraft):
    try:
        result = _press().draft_epigraph(
            slug,
            number,
            soul=body.soul,
            in_world=body.in_world,
            gateway_name=body.gateway,
            want_quote=body.want_quote,
        )
        return result
    except Exception as e:
        raise _http(e) from e


@router.post("/press/books/{slug}/chapters/{number}/epigraph/approve")
def press_approve_epigraph(slug: str, number: int, body: EpigraphApprove):
    try:
        _press().approve_epigraph(slug, number, body.author)
        return {"ok": True}
    except Exception as e:
        raise _http(e) from e


@router.post("/press/books/{slug}/matter")
def press_set_matter(slug: str, body: MatterSet):
    try:
        _press().set_matter(slug, body.front, body.back)
        return {"ok": True}
    except Exception as e:
        raise _http(e) from e


@router.get("/press/books/{slug}/verify")
def press_verify(slug: str):
    try:
        return _press().verify(slug)
    except Exception as e:
        raise _http(e) from e


@router.post("/press/books/{slug}/package")
def press_package(slug: str, body: PackageRequest):
    try:
        return _press().build_package(slug, body.pkg_type, body.target)
    except Exception as e:
        raise _http(e) from e


@router.post("/press/books/{slug}/seal")
def press_seal(slug: str, body: SealRequest):
    try:
        manifest = _press().seal_package(
            slug,
            body.pkg_type,
            body.target,
            body.author,
            body.recipient,
        )
        return {"manifest": manifest}
    except Exception as e:
        raise _http(e) from e


@router.get("/press/books/{slug}/distribution")
def press_distribution(slug: str):
    try:
        return {"distribution": _press().list_distribution(slug)}
    except Exception as e:
        raise _http(e) from e


@router.get("/press/books/{slug}/ledger")
def press_ledger(slug: str):
    try:
        return {"ledger": _press().get_ledger(slug)}
    except Exception as e:
        raise _http(e) from e


# ── real outputs: render, download, validation (B14/B15) ─────────────────────


class ValidationRecord(BaseModel):
    tool: str
    epub_sha256: str
    clean: bool
    report: str = ""


@router.post("/press/books/{slug}/render")
def press_render(slug: str):
    """Render print PDF + DOCX + accessible EPUB from the locked style.

    The PDF page count is authoritative: it lands on the press book row AND
    on the linked Work's meta (POSITION T9 reads it there), and re-bases any
    ATELIER book that tracks this press book.
    """
    press = _press()
    try:
        manifest = press.render_outputs(slug)
    except Exception as e:
        raise _http(e) from e
    # Propagate actual_pages to the Work meta so the pipeline sees reality.
    book = press.get_book(slug)
    work_id = (book or {}).get("work_id") or ""
    if work_id:
        from orivellum.api._deps import get_db  # noqa: PLC0415

        db = get_db()
        work = db.get_work(work_id)
        if work:
            meta = dict(work.get("meta") or {})
            meta["actual_pages"] = manifest["actual_pages"]
            db.update_work(work_id, meta=meta)
    return {"manifest": manifest}


@router.get("/press/books/{slug}/outputs")
def press_outputs(slug: str):
    try:
        manifest = _press().get_render_manifest(slug)
        validation = _press().validation_status(slug)
    except Exception as e:
        raise _http(e) from e
    return {"manifest": manifest, "validation": validation}


@router.get("/press/books/{slug}/outputs/{kind}")
def press_download_output(slug: str, kind: str):
    from fastapi.responses import FileResponse  # noqa: PLC0415

    try:
        path = _press().output_path(slug, kind)
    except Exception as e:
        raise _http(e) from e
    media = {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "epub": "application/epub+zip",
    }[kind]
    return FileResponse(path, media_type=media, filename=path.name)


@router.post("/press/books/{slug}/validation")
def press_record_validation(slug: str, body: ValidationRecord):
    try:
        return _press().record_validation(
            slug, body.tool, body.epub_sha256, body.clean, body.report
        )
    except Exception as e:
        raise _http(e) from e


# ═══════════════════════════════════════════════════════════════════════════════
# ATELIER models
# ═══════════════════════════════════════════════════════════════════════════════


class SeriesCreate(BaseModel):
    name: str
    books: int = 1


class BrandUpdate(BaseModel):
    body_font: str | None = None
    heading_font: str | None = None
    palette: str | None = None
    imagery: str | None = None
    composition: str | None = None
    title_pos: str | None = None
    author_pos: str | None = None
    logo: str | None = None


class SeriesLock(BaseModel):
    author: str


class AtelierBookCreate(BaseModel):
    title: str
    number: int = 1
    pages: int = 300
    paper: str = "cream"
    trim: str = "6x9"


class CoverGenerate(BaseModel):
    versions: int = 3
    mood: str = ""
    # "lemonade" = the real image pipeline; versions record ABSTAINED when the
    # image backend is unreachable instead of pretending an asset exists.
    gateway: str = "lemonade"


class SealDesign(BaseModel):
    author: str
    choose_version: str


# ═══════════════════════════════════════════════════════════════════════════════
# ATELIER routes
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/atelier/series")
def atelier_list_series():
    try:
        return {"series": _atelier().list_series()}
    except Exception as e:
        raise _http(e) from e


@router.post("/atelier/series", status_code=201)
def atelier_create_series(body: SeriesCreate):
    try:
        return {"series": _atelier().create_series(body.name, body.books)}
    except Exception as e:
        raise _http(e) from e


@router.get("/atelier/series/{slug}")
def atelier_get_series(slug: str):
    try:
        s = _atelier().get_series(slug)
        if not s:
            raise HTTPException(404, f"Series '{slug}' not found")
        return {"series": s}
    except HTTPException:
        raise
    except Exception as e:
        raise _http(e) from e


@router.patch("/atelier/series/{slug}/brand")
def atelier_update_brand(slug: str, body: BrandUpdate):
    try:
        brand = _atelier().update_series_brand(slug, body.model_dump(exclude_none=True))
        return {"brand": brand}
    except Exception as e:
        raise _http(e) from e


@router.post("/atelier/series/{slug}/lock")
def atelier_lock_series(slug: str, body: SeriesLock):
    try:
        _atelier().lock_series(slug, body.author)
        return {"ok": True}
    except Exception as e:
        raise _http(e) from e


@router.get("/atelier/series/{slug}/books")
def atelier_series_books(slug: str):
    try:
        return {"books": _atelier().list_books(series_slug=slug)}
    except Exception as e:
        raise _http(e) from e


@router.get("/atelier/books")
def atelier_list_books():
    try:
        return {"books": _atelier().list_books()}
    except Exception as e:
        raise _http(e) from e


@router.post("/atelier/series/{series_slug}/books", status_code=201)
def atelier_create_book(series_slug: str, body: AtelierBookCreate):
    try:
        return {
            "book": _atelier().create_book(
                series_slug,
                body.title,
                body.number,
                body.pages,
                body.paper,
                body.trim,
            )
        }
    except Exception as e:
        raise _http(e) from e


@router.get("/atelier/books/{slug}")
def atelier_get_book(slug: str):
    try:
        book = _atelier().get_book(slug)
        if not book:
            raise HTTPException(404, f"Book '{slug}' not found")
        return {"book": book}
    except HTTPException:
        raise
    except Exception as e:
        raise _http(e) from e


@router.get("/atelier/books/{slug}/spec")
def atelier_spec(slug: str):
    try:
        return _atelier().get_spec(slug)
    except Exception as e:
        raise _http(e) from e


@router.post("/atelier/books/{slug}/cover")
def atelier_generate_covers(slug: str, body: CoverGenerate):
    try:
        versions = _atelier().generate_covers(slug, body.versions, body.mood, body.gateway)
        return {"versions": versions}
    except Exception as e:
        raise _http(e) from e


@router.get("/atelier/books/{slug}/verify")
def atelier_verify(slug: str):
    try:
        return _atelier().verify_design(slug)
    except Exception as e:
        raise _http(e) from e


@router.post("/atelier/books/{slug}/seal")
def atelier_seal(slug: str, body: SealDesign):
    try:
        return {"manifest": _atelier().seal_design(slug, body.author, body.choose_version)}
    except Exception as e:
        raise _http(e) from e


# ── print-model completion + compliance (B15/B16) ─────────────────────────────


class PrintMetadata(BaseModel):
    isbn: str | None = None
    binding: str | None = None


@router.patch("/atelier/books/{slug}/print")
def atelier_print_metadata(slug: str, body: PrintMetadata):
    try:
        return _atelier().set_print_metadata(slug, isbn=body.isbn, binding=body.binding)
    except Exception as e:
        raise _http(e) from e


@router.post("/atelier/books/{slug}/sync-pages")
def atelier_sync_pages(slug: str, press_slug: str):
    """Re-base cover geometry on the RENDERED page count of a press book."""
    try:
        pv = _press().verify(press_slug)
        pages = pv.get("actual_pages") or 0
        if pages <= 0:
            raise ValueError(f"Press book '{press_slug}' has no rendered outputs — render first.")
        return _atelier().record_actual_pages(slug, pages, f"press:{press_slug}")
    except Exception as e:
        raise _http(e) from e


@router.get("/atelier/books/{slug}/hardcover")
def atelier_hardcover(slug: str):
    try:
        a = _atelier()
        b = a.get_book(slug)
        if not b:
            raise KeyError(f"Book '{slug}' not found.")
        return a.hardcover_dimensions(b["trim"], b["pages"], b["paper"])
    except Exception as e:
        raise _http(e) from e


@router.get("/works/{work_id}/disclosure")
def work_disclosure(work_id: str):
    from orivellum.api._deps import get_db  # noqa: PLC0415
    from orivellum.capabilities.finishing import compliance  # noqa: PLC0415

    _ensure_init()
    try:
        return compliance.disclosure_sheet(get_db(), work_id)
    except Exception as e:
        raise _http(e) from e


@router.get("/works/{work_id}/gate")
def work_assembly_gate(work_id: str, press_slug: str = "", atelier_slug: str = ""):
    """B16 — the single deterministic release decision. No override exists."""
    from orivellum.api._deps import get_db  # noqa: PLC0415
    from orivellum.capabilities.finishing import compliance  # noqa: PLC0415

    _ensure_init()
    try:
        return compliance.assembly_gate(
            get_db(), work_id, press_slug=press_slug, atelier_slug=atelier_slug
        )
    except Exception as e:
        raise _http(e) from e
