"""LOOM — /api/works/{work_id}/loom/*

Chapter drafting engine (B5).  Personas are review-gated (created 'proposed';
only an author signature approves them, through the review queue).  Drafting
runs claim via the run row and dispatch in the background; every pre-dispatch
failure finishes the row as 'error' — never a leaked 'running' row.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orivellum.api._deps import get_config, get_db, require_auth
from orivellum.api.executor import submit_bg

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


# ── Personas ──────────────────────────────────────────────────────────────────


class PersonaBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    role: str = ""
    personality: str = ""
    goals: str = ""
    description: str = ""
    relationships: dict = Field(default_factory=dict)
    diction_profile: dict = Field(default_factory=dict)
    # {"1": [canon_fact_id, ...], "2": [...]} — what the character can know,
    # per act boundary.
    knowledge_horizon: dict = Field(default_factory=dict)


@router.post("/works/{work_id}/loom/personas", status_code=201)
def create_persona(work_id: str, body: PersonaBody):
    db = get_db()
    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    payload = body.model_dump(exclude={"name"})
    try:
        pid = db.create_loom_persona(work_id, body.name, payload)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"persona_id": pid, "status": "proposed"}


@router.get("/works/{work_id}/loom/personas")
def list_personas(work_id: str, status: str | None = None):
    db = get_db()
    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    return {"personas": db.list_loom_personas(work_id, status=status)}


# ── Drafting ──────────────────────────────────────────────────────────────────


class DraftBody(BaseModel):
    chapter_id: str = Field(min_length=1)


@router.post("/works/{work_id}/loom/draft", status_code=202)
def start_draft(work_id: str, body: DraftBody):
    db = get_db()
    cfg = get_config()
    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    try:
        run_id = db.create_loom_run(work_id, body.chapter_id)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e

    # We hold the claim from here on: every failure before a successful
    # dispatch must finish the row as 'error'.
    try:
        from orivellum.capabilities.loom import run_loom_draft  # noqa: PLC0415

        dispatched = submit_bg(
            run_loom_draft,
            db,
            cfg,
            run_id=run_id,
            work_id=work_id,
            chapter_id=body.chapter_id,
            kind="loom_draft",
            label=f"loom:{work_id}",
        )
    except Exception as exc:
        db.finish_loom_run(run_id, status="error", error=str(exc))
        raise
    if not dispatched:
        db.finish_loom_run(run_id, status="error", error="background dispatch refused")
        raise HTTPException(503, "draft could not be dispatched; try again")
    return {"run_id": run_id, "status": "running"}


@router.get("/works/{work_id}/loom")
def loom_overview(work_id: str):
    db = get_db()
    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    state = db.get_world_state(work_id)
    return {
        "runs": db.list_loom_runs(work_id, limit=20),
        "personas": db.list_loom_personas(work_id),
        "world_state_keys": len(state),
        "world_state": state,
    }


def _chapter_escalations(db, chapter_id: str) -> list[dict]:
    """Open + resolved LOOM escalation findings for a chapter.

    Escalations target the pipeline (or the chapter) as object_id, so the
    chapter linkage lives in meta — created by db.create_finding with default
    json.dumps separators, hence the exact `"chapter_id": "<id>"` needle.
    """
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, kind, severity, description, state, created_at, meta
               FROM findings
               WHERE kind='loom_escalation' AND meta LIKE ?
               ORDER BY created_at DESC LIMIT 50""",
            (f'%"chapter_id": "{chapter_id}"%',),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except (TypeError, json.JSONDecodeError):
            d["meta"] = {}
        out.append(d)
    return out


@router.get("/loom/runs/{run_id}")
def get_loom_run(run_id: str):
    db = get_db()
    run = db.get_loom_run(run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id!r} not found")
    return {"run": run, "escalations": _chapter_escalations(db, run["chapter_id"])}


@router.get("/loom/chapters/{chapter_id}/revisions")
def list_revisions(chapter_id: str):
    db = get_db()
    return {"revisions": db.list_chapter_revisions(chapter_id)}


@router.get("/loom/revisions/{revision_id}")
def get_revision(revision_id: str):
    """A single chapter revision INCLUDING its full text (the list omits it)."""
    db = get_db()
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM loom_chapter_revision WHERE id=?", (revision_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"revision {revision_id!r} not found")
    rev = dict(row)
    for field in ("meta", "edit_scope"):
        try:
            rev[field] = json.loads(rev.get(field) or "{}") if rev.get(field) else None
        except (TypeError, json.JSONDecodeError):
            rev[field] = None
    return {"revision": rev}


# ── Chapter contracts (the author's beat sheet — LOOM refuses without one) ────

_MAX_BEAT_LEN = 4_000
_MAX_CAST = 12
_MAX_WORDS = 50_000


class ContractBody(BaseModel):
    """A chapter contract: the beat this chapter MUST accomplish, who is on
    stage, how long it runs, and which act it belongs to.  Validation here
    mirrors what the engine itself refuses on (loom._get_contract /
    _word_range) so a saved contract is always a draftable contract."""

    beat: str = Field(min_length=1, max_length=_MAX_BEAT_LEN)
    word_range: list[int] = Field(min_length=2, max_length=2)
    cast: list[str] = Field(min_length=1, max_length=_MAX_CAST)
    act: int = Field(ge=1, le=20, default=1)
    location: str = Field(default="", max_length=300)

    def validated(self) -> dict:
        if not self.beat.strip():
            raise HTTPException(422, "the beat cannot be blank")
        lo, hi = self.word_range
        if not (0 < lo <= hi <= _MAX_WORDS):
            raise HTTPException(422, f"word_range must satisfy 0 < min <= max <= {_MAX_WORDS}")
        cast = [str(c).strip() for c in self.cast if str(c).strip()]
        if not cast:
            raise HTTPException(422, "the cast names nobody — LOOM needs at least one character")
        if len(set(c.lower() for c in cast)) != len(cast):
            raise HTTPException(422, "the cast repeats a character")
        contract = {
            "beat": self.beat.strip(),
            "word_range": [int(lo), int(hi)],
            "cast": cast,
            "act": int(self.act),
        }
        if self.location.strip():
            contract["location"] = self.location.strip()
        return contract


def _get_chapter_row(db, work_id: str, chapter_id: str) -> dict:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM book_chapters WHERE id=? AND work_id=?",
            (chapter_id, work_id),
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"chapter {chapter_id!r} not found in work {work_id!r}")
    ch = dict(row)
    try:
        ch["meta"] = json.loads(ch.get("meta") or "{}")
    except (TypeError, json.JSONDecodeError):
        ch["meta"] = {}
    return ch


def _contract_problems(contract) -> list[str]:
    """Why this contract is not draftable, in author language.  Empty = ready."""
    if not isinstance(contract, dict):
        return ["no contract yet"]
    problems = []
    if not str(contract.get("beat") or "").strip():
        problems.append("no beat — say what this chapter must accomplish")
    wr = contract.get("word_range") or []
    ok_range = (
        isinstance(wr, (list, tuple))
        and len(wr) == 2
        and all(isinstance(x, (int, float)) for x in wr)
        and 0 < wr[0] <= wr[1]
    )
    if not ok_range:
        problems.append("no valid word range [min, max]")
    if not [c for c in (contract.get("cast") or []) if str(c).strip()]:
        problems.append("no cast — name who is on stage")
    return problems


def _persona_status_by_name(db, work_id: str) -> dict[str, dict]:
    """name(lower) → {status, inherited_from_work_id?} across this work and
    earlier volumes of its series (mirrors loom._personas_for_cast lookup)."""
    from orivellum.database.series_store import SeriesStore  # noqa: PLC0415

    out: dict[str, dict] = {}
    # Nearest volume wins, so walk from the earliest prior to the local work.
    for prior_id in reversed(SeriesStore(db).prior_volume_work_ids(work_id)):
        for p in db.list_loom_personas(prior_id, status="approved"):
            out[p["name"].lower()] = {
                "status": "approved",
                "inherited_from_work_id": prior_id,
            }
    for p in db.list_loom_personas(work_id):
        key = p["name"].lower()
        if p["status"] == "approved" or key not in out:
            out[key] = {"status": p["status"], "persona_id": p["id"]}
    return out


@router.get("/works/{work_id}/loom/chapters")
def list_loom_chapters(work_id: str):
    """Every chapter of the Work annotated with its contract and whether LOOM
    would accept a draft request right now (and if not, why — in plain words)."""
    db = get_db()
    if db.get_work(work_id) is None:
        raise HTTPException(404, f"work {work_id!r} not found")
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, seq, title, status, meta,
                      COALESCE(LENGTH(text), 0) AS text_len
               FROM book_chapters WHERE work_id=? ORDER BY seq""",
            (work_id,),
        ).fetchall()
    persona_status = _persona_status_by_name(db, work_id)
    running = {
        r["chapter_id"]: r["id"] for r in db.list_loom_runs(work_id) if r["status"] == "running"
    }
    chapters = []
    for r in rows:
        try:
            meta = json.loads(r["meta"] or "{}")
        except (TypeError, json.JSONDecodeError):
            meta = {}
        contract = meta.get("contract")
        problems = _contract_problems(contract)
        cast_status = []
        if isinstance(contract, dict):
            for name in contract.get("cast") or []:
                st = persona_status.get(str(name).strip().lower())
                cast_status.append(
                    {"name": str(name), "status": st["status"] if st else "missing"}
                )
                if st is None:
                    problems.append(f"“{name}” has no persona — create one")
                elif st["status"] != "approved":
                    problems.append(f"persona for “{name}” is {st['status']} — approve it first")
        if r["status"] == "approved":
            problems.append("chapter is approved — approved chapters are never redrafted")
        chapters.append(
            {
                "id": r["id"],
                "seq": r["seq"],
                "title": r["title"],
                "status": r["status"],
                "has_text": r["text_len"] > 0,
                "contract": contract if isinstance(contract, dict) else None,
                "cast_status": cast_status,
                "problems": problems,
                "draft_ready": not problems,
                "active_run_id": running.get(r["id"]),
            }
        )
    return {"chapters": chapters}


@router.get("/works/{work_id}/loom/chapters/{chapter_id}/contract")
def get_chapter_contract(work_id: str, chapter_id: str):
    db = get_db()
    ch = _get_chapter_row(db, work_id, chapter_id)
    contract = ch["meta"].get("contract")
    return {
        "contract": contract if isinstance(contract, dict) else None,
        "problems": _contract_problems(contract),
    }


@router.put("/works/{work_id}/loom/chapters/{chapter_id}/contract")
def put_chapter_contract(work_id: str, chapter_id: str, body: ContractBody):
    """Author-confirmed contract write.  Meta is MERGED, never replaced —
    other meta keys (scene counts, extraction data) survive the save.  The
    read-merge-write happens under ONE lock/transaction so a concurrent meta
    write can never be clobbered by a stale snapshot."""
    db = get_db()
    contract = body.validated()
    from datetime import UTC, datetime  # noqa: PLC0415

    with db._lock, db._conn:
        row = db._conn.execute(
            "SELECT meta FROM book_chapters WHERE id=? AND work_id=?",
            (chapter_id, work_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, f"chapter {chapter_id!r} not found in work {work_id!r}")
        try:
            current = json.loads(row["meta"] or "{}")
        except (TypeError, json.JSONDecodeError):
            current = {}
        if not isinstance(current, dict):
            current = {}
        meta = {**current, "contract": contract}
        db._conn.execute(
            "UPDATE book_chapters SET meta=?, updated_at=? WHERE id=? AND work_id=?",
            (
                json.dumps(meta, ensure_ascii=False),
                datetime.now(UTC).isoformat(),
                chapter_id,
                work_id,
            ),
        )
    return {"contract": contract, "problems": []}


@router.post("/works/{work_id}/loom/chapters/{chapter_id}/contract/suggest")
def suggest_chapter_contract(work_id: str, chapter_id: str):
    """Deterministic contract SUGGESTION — pre-filled from the Work's own
    outline artifact, chapter text, and persona roster.  Nothing is saved and
    nothing is approved here: the author edits and confirms in the editor."""
    db = get_db()
    ch = _get_chapter_row(db, work_id, chapter_id)
    existing = ch["meta"].get("contract") if isinstance(ch["meta"].get("contract"), dict) else {}
    text = ch.get("text") or ""
    seq = int(ch.get("seq") or 0)

    # Beat: the outline artifact's entry for this chapter, else the chapter's
    # opening passage as a starting point, else empty (author writes it).
    beat, beat_source = "", "none"
    pipeline = db.get_book_pipeline_for_work(work_id)
    if pipeline:
        artifact = db.get_pipeline_artifact(pipeline["id"], "B1")
        outline_chapters = (artifact or {}).get("content", {}).get("chapters") or []
        for oc in outline_chapters:
            if isinstance(oc, dict) and int(oc.get("seq") or 0) == seq:
                beat = str(
                    oc.get("beat") or oc.get("summary") or oc.get("description") or ""
                ).strip()
                if beat:
                    beat_source = "chapter_outline"
                break
    if not beat and text.strip():
        first_para = next((p.strip() for p in text.split("\n\n") if p.strip()), "")
        beat = first_para[:300]
        beat_source = "chapter_text"

    # Cast: personas (any status) whose names appear in the chapter text;
    # with no text, every approved persona is offered.
    personas = db.list_loom_personas(work_id)
    if text.strip():
        lowered = text.lower()
        cast = [p["name"] for p in personas if p["name"].lower() in lowered]
        cast_source = "personas_in_text"
    else:
        cast = [p["name"] for p in personas if p["status"] == "approved"]
        cast_source = "approved_personas"
    if not cast:
        cast_source = "none"

    # Word range: banded around the current text, else a sane default.
    wc = len(text.split())
    if wc > 200:
        lo = max(500, (wc // 500) * 500)
        word_range = [lo, max(lo + 500, ((wc + 999) // 500) * 500 + 500)]
        range_source = "current_text"
    else:
        word_range = [1500, 4000]
        range_source = "default"

    return {
        "suggestion": {
            "beat": existing.get("beat") or beat,
            "word_range": existing.get("word_range") or word_range,
            "cast": existing.get("cast") or cast[:_MAX_CAST],
            "act": existing.get("act") or 1,
            "location": existing.get("location") or "",
        },
        "sources": {"beat": beat_source, "cast": cast_source, "word_range": range_source},
    }
