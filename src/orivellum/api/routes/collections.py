"""Collections, canon domains, and safe book conversions.

Three distinct layers (never conflated):
- /api/collections     — reader/production families (book_collection)
- /api/canon-domains   — shared universes / evidence domains
- /api/conversions     — recommend-only classification + ledgered,
                         reversible conversions (standalone→series,
                         per-item canon promotion, merge previews)

NOT the provenance `collection` table (import bookkeeping) — that one is
never a subject and has no routes here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orivellum.api._deps import get_db, require_auth
from orivellum.database.series_store import SeriesError
from orivellum.database.structure_store import (
    CollectionStore,
    ConversionService,
    DomainStore,
    StructureError,
    recommend_classification,
)

router = APIRouter(prefix="/api", tags=["collections"], dependencies=[Depends(require_auth)])


# ─── Collections ──────────────────────────────────────────────────────────────


class CollectionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    collection_type: str = "branded-theme"
    reader_promise: str = ""


class CollectionPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    collection_type: str | None = None
    status: str | None = None
    reader_promise: str | None = None


class MemberBody(BaseModel):
    member_kind: str
    member_id: str
    position: int = 0
    confirm_canon_binding: bool = False


@router.get("/collections")
def list_collections():
    return {"collections": CollectionStore(get_db()).list()}


@router.post("/collections")
def create_collection(req: CollectionCreate):
    try:
        return CollectionStore(get_db()).create(
            title=req.title,
            description=req.description,
            collection_type=req.collection_type,
            reader_promise=req.reader_promise,
        )
    except StructureError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/collections/{collection_id}")
def get_collection(collection_id: str):
    c = CollectionStore(get_db()).get(collection_id)
    if not c:
        raise HTTPException(404, f"Collection {collection_id!r} not found")
    return c


@router.patch("/collections/{collection_id}")
def patch_collection(collection_id: str, req: CollectionPatch):
    try:
        c = CollectionStore(get_db()).update(
            collection_id,
            title=req.title,
            description=req.description,
            collection_type=req.collection_type,
            status=req.status,
            reader_promise=req.reader_promise,
        )
    except StructureError as e:
        raise HTTPException(422, str(e)) from e
    if not c:
        raise HTTPException(404, f"Collection {collection_id!r} not found")
    return c


@router.delete("/collections/{collection_id}")
def delete_collection(collection_id: str):
    result = CollectionStore(get_db()).delete(collection_id)
    if result == "not_found":
        raise HTTPException(404, f"Collection {collection_id!r} not found")
    if result == "in_domain":
        raise HTTPException(
            409,
            "A canon domain serves books through this collection — remove it "
            "from the domain first.",
        )
    return {"ok": True, "id": collection_id}


@router.post("/collections/{collection_id}/members")
def add_collection_member(collection_id: str, req: MemberBody):
    try:
        return CollectionStore(get_db()).add_member(
            collection_id,
            member_kind=req.member_kind,
            member_id=req.member_id,
            position=req.position,
            confirm_canon_binding=req.confirm_canon_binding,
        )
    except StructureError as e:
        raise HTTPException(422, str(e)) from e


@router.delete("/collections/{collection_id}/members/{member_kind}/{member_id}")
def remove_collection_member(collection_id: str, member_kind: str, member_id: str):
    try:
        removed = CollectionStore(get_db()).remove_member(
            collection_id, member_kind=member_kind, member_id=member_id
        )
    except StructureError as e:
        raise HTTPException(409, str(e)) from e
    if not removed:
        raise HTTPException(404, "Not a member of this collection")
    return {"ok": True}


# ─── Canon domains ────────────────────────────────────────────────────────────


class DomainCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    domain_type: str = "fictional"


class DomainPatch(BaseModel):
    title: str | None = None
    description: str | None = None


class DomainMemberBody(BaseModel):
    member_kind: str
    member_id: str


@router.get("/canon-domains")
def list_domains():
    return {"domains": DomainStore(get_db()).list()}


@router.post("/canon-domains")
def create_domain(req: DomainCreate):
    try:
        return DomainStore(get_db()).create(
            title=req.title, description=req.description, domain_type=req.domain_type
        )
    except StructureError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/canon-domains/{domain_id}")
def get_domain(domain_id: str):
    d = DomainStore(get_db()).get(domain_id)
    if not d:
        raise HTTPException(404, f"Canon domain {domain_id!r} not found")
    return d


@router.patch("/canon-domains/{domain_id}")
def patch_domain(domain_id: str, req: DomainPatch):
    try:
        d = DomainStore(get_db()).update(
            domain_id, title=req.title, description=req.description
        )
    except StructureError as e:
        raise HTTPException(422, str(e)) from e
    if not d:
        raise HTTPException(404, f"Canon domain {domain_id!r} not found")
    return d


@router.delete("/canon-domains/{domain_id}")
def delete_domain(domain_id: str):
    result = DomainStore(get_db()).delete(domain_id)
    if result == "not_found":
        raise HTTPException(404, f"Canon domain {domain_id!r} not found")
    if result == "has_canon":
        raise HTTPException(
            409,
            "This domain still has active canon facts — retract them first. "
            "Authority never loses its scope silently.",
        )
    return {"ok": True, "id": domain_id}


@router.post("/canon-domains/{domain_id}/members")
def add_domain_member(domain_id: str, req: DomainMemberBody):
    try:
        return DomainStore(get_db()).add_member(
            domain_id, member_kind=req.member_kind, member_id=req.member_id
        )
    except StructureError as e:
        raise HTTPException(422, str(e)) from e


@router.delete("/canon-domains/{domain_id}/members/{member_kind}/{member_id}")
def remove_domain_member(domain_id: str, member_kind: str, member_id: str):
    try:
        removed = DomainStore(get_db()).remove_member(
            domain_id, member_kind=member_kind, member_id=member_id
        )
    except StructureError as e:
        raise HTTPException(409, str(e)) from e
    if not removed:
        raise HTTPException(404, "Not a member of this domain")
    return {"ok": True}


@router.get("/canon-domains/{domain_id}/facts")
def list_domain_facts(domain_id: str, status: str | None = None, limit: int = 500):
    from orivellum.database.canon_store import CanonStore

    db = get_db()
    if not DomainStore(db).get(domain_id):
        raise HTTPException(404, f"Canon domain {domain_id!r} not found")
    return {
        "facts": CanonStore(db).list_facts(domain_id=domain_id, status=status, limit=limit)
    }


# ─── Conversions ──────────────────────────────────────────────────────────────


class RecommendBody(BaseModel):
    recurring_cast: bool = False
    unresolved_arc: bool = False
    existing_series_id: str | None = None
    before_existing: bool = False
    shorter_form: bool = False
    shared_world_only: bool = False
    branding_only: bool = False


class StandaloneToSeriesBody(BaseModel):
    work_id: str
    series_id: str | None = None
    series_title: str | None = None
    volume: int = Field(default=1, ge=1)
    confirm_canon_binding: bool = False


class PromoteBody(BaseModel):
    fact_ids: list[str] = Field(min_length=1, max_length=200)
    target_series_id: str | None = None
    target_domain_id: str | None = None
    signed_by: str = Field(min_length=1)


@router.post("/conversions/recommend")
def recommend(req: RecommendBody):
    """Deterministic New-Book classification recommendation — recommends,
    never imposes. The author selects the final classification."""
    answers = req.model_dump()
    if answers.get("existing_series_id"):
        db = get_db()
        row = (
            db.read_conn()
            .execute("SELECT 1 FROM series WHERE id=?", (answers["existing_series_id"],))
            .fetchone()
        )
        if not row:
            raise HTTPException(422, "existing_series_id does not name a real series")
    return recommend_classification(answers)


@router.post("/conversions/standalone-to-series")
def standalone_to_series(req: StandaloneToSeriesBody):
    try:
        return ConversionService(get_db()).convert_standalone_to_series(
            req.work_id,
            series_id=req.series_id,
            series_title=req.series_title,
            volume=req.volume,
            confirm_canon_binding=req.confirm_canon_binding,
        )
    except (StructureError, SeriesError) as e:
        raise HTTPException(422, str(e)) from e


@router.get("/conversions/link-preview")
def link_preview(
    work_id: str,
    series_id: str | None = None,
    collection_id: str | None = None,
    domain_id: str | None = None,
):
    if not (series_id or collection_id or domain_id):
        raise HTTPException(422, "Pass series_id, collection_id, or domain_id")
    try:
        return ConversionService(get_db()).link_preview(
            work_id, series_id=series_id, collection_id=collection_id, domain_id=domain_id
        )
    except StructureError as e:
        raise HTTPException(422, str(e)) from e


@router.post("/conversions/promote-canon")
def promote_canon(req: PromoteBody):
    try:
        return ConversionService(get_db()).promote_facts(
            req.fact_ids,
            target_series_id=req.target_series_id,
            target_domain_id=req.target_domain_id,
            signed_by=req.signed_by,
        )
    except StructureError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/conversions/ledger")
def conversions_ledger(subject_id: str | None = None, limit: int = 200):
    return {"entries": ConversionService(get_db()).list_ledger(subject_id=subject_id, limit=limit)}


@router.post("/conversions/{ledger_id}/reverse")
def reverse_conversion(ledger_id: str):
    try:
        return ConversionService(get_db()).reverse(ledger_id)
    except ValueError as e:
        # StructureError, SeriesError, CanonFactError — all refusals, never 500s
        raise HTTPException(409, str(e)) from e
