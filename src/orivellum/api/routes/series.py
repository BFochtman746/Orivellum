"""Series — /api/series/*

Ordered groups of Works (a trilogy) with shared canon, voice, and
continuity.  Membership is strict: one series per Work, unique volumes.

- GET    /api/series                    list series (member counts)
- POST   /api/series                    create a series
- GET    /api/series/{id}               one series with ordered members
- PATCH  /api/series/{id}               rename / redescribe
- DELETE /api/series/{id}               delete (refused while series canon exists)
- POST   /api/series/{id}/members       add a Work at a volume
- DELETE /api/series/{id}/members/{work_id}   remove a Work
- PATCH  /api/series/{id}/members/{work_id}   change a Work's volume
- GET    /api/series/{id}/overview      per-volume canon/continuity health
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orivellum.api._deps import get_db, require_auth
from orivellum.database.series_store import SeriesError, SeriesStore

router = APIRouter(prefix="/api/series", tags=["series"], dependencies=[Depends(require_auth)])


class SeriesCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""


class SeriesPatch(BaseModel):
    title: str | None = None
    description: str | None = None


class MemberAdd(BaseModel):
    work_id: str
    volume: int = Field(ge=1)


class MemberPatch(BaseModel):
    volume: int = Field(ge=1)


class MemberOrdersPatch(BaseModel):
    chronology_order: int | None = Field(default=None, ge=0)
    publication_order: int | None = Field(default=None, ge=0)
    relationship_type: str | None = None


@router.get("")
def list_series():
    db = get_db()
    return {"series": SeriesStore(db).list_series()}


@router.post("")
def create_series(req: SeriesCreate):
    db = get_db()
    try:
        return SeriesStore(db).create_series(title=req.title, description=req.description)
    except SeriesError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/{series_id}")
def get_series(series_id: str):
    db = get_db()
    s = SeriesStore(db).get_series(series_id)
    if not s:
        raise HTTPException(404, f"Series {series_id!r} not found")
    return s


@router.patch("/{series_id}")
def update_series(series_id: str, req: SeriesPatch):
    db = get_db()
    try:
        s = SeriesStore(db).update_series(series_id, title=req.title, description=req.description)
    except SeriesError as e:
        raise HTTPException(422, str(e)) from e
    if not s:
        raise HTTPException(404, f"Series {series_id!r} not found")
    return s


@router.delete("/{series_id}")
def delete_series(series_id: str):
    db = get_db()
    result = SeriesStore(db).delete_series(series_id)
    if result == "not_found":
        raise HTTPException(404, f"Series {series_id!r} not found")
    if result == "has_canon":
        raise HTTPException(
            409,
            "This series still has series-scoped canon facts — retract or "
            "rescope them before deleting the series.",
        )
    if result == "has_continuity":
        raise HTTPException(
            409,
            "This series has established cross-book continuity — remove the "
            "member books latest-volume-first (retracting their canon or "
            "overrides as refused) before deleting it.",
        )
    return {"ok": True, "id": series_id}


@router.post("/{series_id}/members")
def add_member(series_id: str, req: MemberAdd):
    db = get_db()
    try:
        return SeriesStore(db).add_member(series_id, req.work_id, volume=req.volume)
    except SeriesError as e:
        raise HTTPException(422, str(e)) from e


@router.delete("/{series_id}/members/{work_id}")
def remove_member(series_id: str, work_id: str):
    db = get_db()
    try:
        removed = SeriesStore(db).remove_member(series_id, work_id)
    except SeriesError as e:
        # Continuity-protected removal — actionable refusal, not a 500.
        raise HTTPException(409, str(e)) from e
    if not removed:
        raise HTTPException(404, "That Work is not a member of this series")
    return {"ok": True}


@router.patch("/{series_id}/members/{work_id}")
def set_member_volume(series_id: str, work_id: str, req: MemberPatch):
    db = get_db()
    try:
        return SeriesStore(db).set_member_volume(series_id, work_id, volume=req.volume)
    except SeriesError as e:
        raise HTTPException(422, str(e)) from e


@router.patch("/{series_id}/members/{work_id}/orders")
def patch_member_orders(series_id: str, work_id: str, req: MemberOrdersPatch):
    """Set chronology/publication order and relationship type — descriptive
    dimensions that never touch the authority (volume) order."""
    db = get_db()
    try:
        return SeriesStore(db).set_member_orders(
            series_id,
            work_id,
            chronology_order=req.chronology_order,
            publication_order=req.publication_order,
            relationship_type=req.relationship_type,
        )
    except SeriesError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/{series_id}/reorder-preview")
def reorder_preview(series_id: str, work_id: str, volume: int):
    """Every downstream impact of moving a member to a new volume — shown
    BEFORE any commit.  Reordering is refused outright once ANY member
    canon exists (order is authority); this endpoint says so honestly and
    lists what the move would touch when it IS allowed."""
    db = get_db()
    store = SeriesStore(db)
    s = store.get_series(series_id)
    if not s:
        raise HTTPException(404, f"Series {series_id!r} not found")
    members = store.list_members(series_id)
    me = next((m for m in members if m["work_id"] == work_id), None)
    if not me:
        raise HTTPException(404, "That Work is not a member of this series")
    conn = db.read_conn()
    blockers: list[str] = []
    canon_rows = conn.execute(
        """SELECT w.title, COUNT(*) AS n FROM canon_fact f
           JOIN series_member m ON m.work_id = f.work_id AND m.series_id=?
           JOIN works w ON w.id = f.work_id
           WHERE f.status='active' GROUP BY f.work_id""",
        (series_id,),
    ).fetchall()
    for r in canon_rows:
        blockers.append(
            f"{r['title']} has {r['n']} established canon fact(s) — reordering "
            "would rewrite which facts bind which book."
        )
    series_facts = conn.execute(
        "SELECT COUNT(*) AS n FROM canon_fact WHERE series_id=? AND status='active'",
        (series_id,),
    ).fetchone()
    taken = next((m for m in members if m["volume"] == int(volume)), None)
    if taken and taken["work_id"] != work_id:
        blockers.append(f"Volume {volume} is already taken by {taken.get('title', taken['work_id'])!r}.")
    # Impacts (only meaningful when the move is allowed)
    impacts: list[str] = []
    old_vol = int(me["volume"])
    if int(volume) != old_vol:
        impacts.append(f"Reading order changes: volume {old_vol} → {volume}.")
        impacts.append(
            "Persona and voice-baseline inheritance re-resolves — later books "
            "inherit from the nearest EARLIER volume, so what counts as "
            "'earlier' changes."
        )
        impacts.append("Next-book labels, numbering, and exports follow the new order.")
        earlier_after = [m for m in members if m["work_id"] != work_id and m["volume"] < int(volume)]
        impacts.append(
            f"After the move, {len(earlier_after)} volume(s) would bind this book "
            "with their established state."
        )
    return {
        "series_id": series_id,
        "work_id": work_id,
        "from_volume": old_vol,
        "to_volume": int(volume),
        "allowed": not blockers,
        "blockers": blockers,
        "series_fact_count": int(series_facts["n"]),
        "impacts": impacts,
    }


@router.get("/{series_id}/overview")
def series_overview(series_id: str):
    db = get_db()
    """Per-volume progress, canon counts, and continuity health.

    Cross-book findings are counted from open findings whose canon fact was
    established in a DIFFERENT (earlier) volume — the series-level signal
    that book N drifted from book 1..N-1's accumulated state.
    """
    store = SeriesStore(db)
    s = store.get_series(series_id)
    if not s:
        raise HTTPException(404, f"Series {series_id!r} not found")
    conn = db.read_conn()

    volumes = []
    for m in s["members"]:
        wid = m["work_id"]
        ch = conn.execute(
            """SELECT COUNT(*) AS chapters,
                      COALESCE(SUM(LENGTH(text) - LENGTH(REPLACE(text, ' ', '')) + 1), 0)
                        AS words
               FROM book_chapters WHERE work_id=? AND COALESCE(text,'') != ''""",
            (wid,),
        ).fetchone()
        canon = conn.execute(
            "SELECT COUNT(*) AS n FROM canon_fact WHERE work_id=? AND status='active'",
            (wid,),
        ).fetchone()
        overrides = conn.execute(
            """SELECT COUNT(*) AS n FROM canon_fact
               WHERE work_id=? AND status='active' AND overrides IS NOT NULL""",
            (wid,),
        ).fetchone()
        findings = conn.execute(
            """SELECT
                 SUM(CASE WHEN disposition='open' THEN 1 ELSE 0 END) AS open_total,
                 SUM(CASE WHEN disposition='open' AND severity IN ('critical','high')
                     THEN 1 ELSE 0 END) AS open_severe
               FROM narrative_finding WHERE work_id=?""",
            (wid,),
        ).fetchone()
        # A finding is cross-book only when the canon fact it contradicts was
        # established by an EARLIER volume of THIS series — never merely
        # "some other work" (stale facts from removed members don't count).
        cross = conn.execute(
            """SELECT COUNT(*) AS n
               FROM narrative_finding nf
               JOIN canon_fact cf ON cf.id = nf.canon_fact_id
               JOIN series_member m1
                 ON m1.work_id = nf.work_id AND m1.series_id=?
               JOIN series_member m2
                 ON m2.work_id = cf.work_id AND m2.series_id = m1.series_id
                AND m2.volume < m1.volume
               WHERE nf.work_id=? AND nf.disposition='open'
                 AND cf.work_id IS NOT NULL AND cf.work_id != nf.work_id""",
            (series_id, wid),
        ).fetchone()
        open_severe = int(findings["open_severe"] or 0)
        open_total = int(findings["open_total"] or 0)
        volumes.append(
            {
                **m,
                "chapters": int(ch["chapters"]),
                "words": int(ch["words"]),
                "canon_facts": int(canon["n"]),
                "overrides": int(overrides["n"]),
                "open_findings": open_total,
                "open_severe_findings": open_severe,
                "cross_book_findings": int(cross["n"]),
                "continuity": ("attention" if open_severe else ("warn" if open_total else "ok")),
            }
        )

    series_facts = conn.execute(
        "SELECT COUNT(*) AS n FROM canon_fact WHERE series_id=? AND status='active'",
        (series_id,),
    ).fetchone()
    return {
        "series": {k: v for k, v in s.items() if k != "members"},
        "volumes": volumes,
        "series_canon_facts": int(series_facts["n"]),
        "total_overrides": sum(v["overrides"] for v in volumes),
        "total_cross_book_findings": sum(v["cross_book_findings"] for v in volumes),
        "continuity": (
            "attention"
            if any(v["continuity"] == "attention" for v in volumes)
            else ("warn" if any(v["continuity"] == "warn" for v in volumes) else "ok")
        ),
    }
