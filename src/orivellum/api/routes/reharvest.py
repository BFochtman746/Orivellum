"""Ontology + domain re-harvest routes (THE RE-PROJECTION Phases 5-6).

Policy lives here; the mechanism lives in ``capabilities.reharvest``:

- ``GET  /api/ontology``                     — the closed per-domain kind sets.
- ``GET  /api/ontology/violations``          — acceptance invariant check.
- ``POST /api/works/{id}/reharvest``         — run one Work (pilot-gated).
- ``GET  /api/works/{id}/reharvest/report``  — status + last report.
- ``GET  /api/works/{id}/reharvest/sample``  — fresh ai_auto items for author reading.
- ``POST /api/reharvest/pilot-signoff``      — author signs the pilot, unlocking the rest.
- ``POST /api/reharvest/all``                — batch every ratified Work (post-sign-off only).

Pilot gate: the FIRST Work to run claims ``reharvest_pilot_work_id``.  Until
an author signs off on that pilot's output (``reharvest_pilot_signed_by``),
every other Work's run returns 409.  Sign-off is a name, recorded forever.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db, require_auth
from orivellum.api.executor import submit_bg
from orivellum.capabilities.ontology import (
    DOMAIN_ONTOLOGIES,
    PERMITTED_DOC_TYPES,
    find_ontology_violations,
)
from orivellum.capabilities.reharvest import (
    ReharvestBusy,
    claim_run,
    get_report,
    get_run_status,
    reharvest_work,
    release_run,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])

_PILOT_WORK_KEY = "reharvest_pilot_work_id"
_PILOT_SIGNED_KEY = "reharvest_pilot_signed_by"


@router.get("/ontology")
def get_ontology():
    """The closed per-domain ontologies and permitted doc_types."""
    return {
        "domains": {d: sorted(kinds) for d, kinds in DOMAIN_ONTOLOGIES.items()},
        "permitted_doc_types": {d: sorted(t) for d, t in PERMITTED_DOC_TYPES.items()},
    }


@router.get("/ontology/violations")
def ontology_violations(limit: int = 200):
    """Acceptance invariant: machine-extracted items off their Work's domain
    ontology.  An empty list is a pass."""
    db = get_db()
    violations = find_ontology_violations(db, limit=min(max(limit, 1), 500))
    return {"violations": violations, "count": len(violations), "pass": not violations}


def _run_and_log(db, work_id: str, token: str) -> None:
    """Background body: the claim is already held by the route."""
    try:
        reharvest_work(db, work_id, claimed=True, token=token)
    except Exception:
        logger.exception("reharvest run failed for work %s", work_id)


@router.post("/works/{work_id}/reharvest")
def start_reharvest(work_id: str):
    """Start a background re-harvest for one ratified Work (pilot-gated)."""
    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    if not work.get("domain"):
        raise HTTPException(
            409,
            "This Work has no ratified domain — re-harvest is refused. "
            "Ratify the Work (assigning a domain) first.",
        )

    # Pilot gate + run claim in ONE transaction: two concurrent POSTs for
    # different Works cannot both claim the pilot, and a double-click gets a
    # clean 409 instead of two racing runs.
    claimed_pilot_here = False
    try:
        with db.atomic():
            signed = db.get_setting(_PILOT_SIGNED_KEY, "")
            pilot_id = db.get_setting(_PILOT_WORK_KEY, "")
            if not signed:
                if pilot_id and pilot_id != work_id:
                    raise HTTPException(
                        409,
                        f"Pilot re-harvest is scoped to work {pilot_id!r}. Read its "
                        "output and sign off (POST /api/reharvest/pilot-signoff) "
                        "before running other Works.",
                    )
                if not pilot_id:
                    db.set_setting(_PILOT_WORK_KEY, work_id)
                    claimed_pilot_here = True
            token = claim_run(db, work_id)
    except ReharvestBusy as exc:
        raise HTTPException(409, str(exc)) from exc

    if not submit_bg(
        _run_and_log, db, work_id, token, kind="reharvest", label=f"reharvest:{work_id}"
    ):
        # Roll back everything this request claimed so a retry starts clean.
        with db.atomic():
            release_run(db, work_id, "error", token)
            if claimed_pilot_here:
                db.set_setting(_PILOT_WORK_KEY, "")
        raise HTTPException(503, "Background executor unavailable — try again shortly")

    return {
        "ok": True,
        "work_id": work_id,
        "pilot": not signed,
        "status": get_run_status(db, work_id),
    }


@router.get("/works/{work_id}/reharvest/report")
def reharvest_report(work_id: str):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    return {
        "work_id": work_id,
        "status": get_run_status(db, work_id),
        "report": get_report(db, work_id),
        "pilot_work_id": db.get_setting(_PILOT_WORK_KEY, "") or None,
        "pilot_signed_by": db.get_setting(_PILOT_SIGNED_KEY, "") or None,
    }


@router.get("/works/{work_id}/reharvest/sample")
def reharvest_sample(work_id: str, limit: int = 100):
    """Freshly re-harvested (ai_auto) items so the author can read the output
    before signing off on the pilot."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    items = db.list_knowledge(
        work_id=work_id,
        limit=min(max(limit, 1), 200),
        review_status_in=("ai_auto",),
    )
    return {"work_id": work_id, "items": items, "count": len(items)}


class SignoffBody(BaseModel):
    author: str = ""


@router.post("/reharvest/pilot-signoff")
def pilot_signoff(body: SignoffBody):
    """Author signs the pilot Work's output, unlocking batch re-harvest."""
    db = get_db()
    author = (body.author or "").strip()
    if not author:
        raise HTTPException(422, "Sign-off requires an author name")
    pilot_id = db.get_setting(_PILOT_WORK_KEY, "")
    if not pilot_id:
        raise HTTPException(409, "No pilot re-harvest has been run yet")
    report = get_report(db, pilot_id)
    if not report or report.get("state") != "done":
        raise HTTPException(409, "The pilot run has not finished successfully yet")
    db.set_setting(_PILOT_SIGNED_KEY, author)
    db.audit(
        "reharvest.pilot_signed",
        object_id=pilot_id,
        object_type="work",
        actor=author,
        detail=f"pilot work {pilot_id} signed off; batch re-harvest unlocked",
    )
    return {"ok": True, "pilot_work_id": pilot_id, "signed_by": author}


def _run_batch(db, work_ids: list[str]) -> None:
    """Sequential batch — one Work at a time keeps LLM load and locks sane."""
    for wid in work_ids:
        try:
            reharvest_work(db, wid)
        except ReharvestBusy:
            logger.info("reharvest batch: work %s busy — skipped", wid)
        except Exception:
            logger.exception("reharvest batch: work %s failed — continuing", wid)


@router.post("/reharvest/all")
def reharvest_all():
    """Re-harvest every ratified (domain-set) Work.  Locked until the pilot
    is signed off."""
    db = get_db()
    if not db.get_setting(_PILOT_SIGNED_KEY, ""):
        raise HTTPException(
            409,
            "Batch re-harvest is locked until an author signs off on the pilot "
            "(POST /api/reharvest/pilot-signoff).",
        )
    with db._lock:
        rows = db._conn.execute(
            "SELECT id FROM works WHERE domain IS NOT NULL AND domain != ''"
        ).fetchall()
    work_ids = [r["id"] for r in rows]
    if not work_ids:
        return {"ok": True, "queued": 0, "work_ids": []}
    if not submit_bg(_run_batch, db, work_ids, kind="reharvest", label="reharvest:all"):
        raise HTTPException(503, "Background executor unavailable — try again shortly")
    return {"ok": True, "queued": len(work_ids), "work_ids": work_ids}
