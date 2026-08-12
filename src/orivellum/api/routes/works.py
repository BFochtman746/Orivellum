"""Works domain routes — /api/works/*"""

from __future__ import annotations

import logging
import threading
from datetime import UTC
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from orivellum.api._deps import get_db, require_auth
from orivellum.api.errors import internal_error

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])
WORK_TYPES = [
    {"id": "research", "label": "Research", "description": "Deep research and knowledge synthesis"},
    {"id": "writing", "label": "Writing", "description": "Books, essays, articles"},
    {"id": "learning", "label": "Learning", "description": "Structured learning and mastery"},
    {"id": "project", "label": "Project", "description": "Goals and deliverables"},
    {"id": "reference", "label": "Reference", "description": "Reference material and notes"},
]


class WorkCreate(BaseModel):
    title: str
    work_type: str = "research"
    description: str | None = None
    meta: dict[str, Any] = {}


class WorkUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    work_type: str | None = None
    meta: dict[str, Any] | None = None


class TaskCreate(BaseModel):
    text: str
    priority: int = 0


class TaskUpdate(BaseModel):
    status: str | None = None
    text: str | None = None
    priority: int | None = None


@router.get("/works/types")
def works_list_types():
    return {"types": WORK_TYPES}


@router.get("/books")
def books_list():
    """Return all Works that have a book pipeline, enriched with stage + word count."""
    db = get_db()
    with db._lock:
        rows = db._conn.execute(
            """SELECT w.id, w.title, w.work_type, w.status, w.description,
                      o.updated_at, o.lifecycle,
                      bp.id        AS pipeline_id,
                      bp.status    AS pipeline_status,
                      (SELECT COUNT(*) FROM book_chapters bc
                       WHERE bc.pipeline_id=bp.id)                             AS chapter_count,
                      (SELECT COUNT(*) FROM book_chapters bc
                       WHERE bc.pipeline_id=bp.id AND bc.status='extracted')   AS chapters_extracted,
                      (SELECT COUNT(*) FROM book_chapters bc
                       WHERE bc.pipeline_id=bp.id AND bc.status='drafted')     AS chapters_drafted,
                      (SELECT COUNT(*) FROM book_chapters bc
                       WHERE bc.pipeline_id=bp.id AND bc.status='approved')    AS chapters_approved,
                      (SELECT COUNT(*) FROM documents d WHERE d.work_id=w.id) AS doc_count,
                      (SELECT COALESCE(SUM(d.word_count),0) FROM documents d
                       WHERE d.work_id=w.id AND d.readiness='ready')          AS word_count
               FROM book_pipelines bp
               JOIN objects oo ON oo.id=bp.id AND oo.lifecycle != 'deleted'
               JOIN works w    ON w.id=bp.work_id
               JOIN objects o  ON o.id=w.id  AND o.lifecycle != 'deleted'
               ORDER BY oo.updated_at DESC"""
        ).fetchall()
    from orivellum.capabilities.state_machine import BOOK_STAGE_LABELS

    books = []
    for r in rows:
        d = dict(r)
        d["stage_label"] = BOOK_STAGE_LABELS.get(
            d["pipeline_status"] or "", d.get("pipeline_status") or ""
        )
        books.append(d)
    return {"books": books}


@router.get("/learn")
def learn_list():
    """Return all Works with their mastery summary for the Learn home page."""
    db = get_db()
    works = db.list_works()
    # Annotate each work with concept count and graduation stats from a single query
    with db._lock:
        concept_rows = db._conn.execute(
            """SELECT wc.work_id,
                      COUNT(DISTINCT wc.id) AS concept_count,
                      SUM(CASE WHEN (
                          SELECT consecutive_passes FROM work_mastery wm
                          WHERE wm.concept_id=wc.id ORDER BY wm.created_at DESC LIMIT 1
                      ) >= 3 THEN 1 ELSE 0 END) AS graduated_count
               FROM work_concepts wc GROUP BY wc.work_id"""
        ).fetchall()
    mastery_by_work = {r["work_id"]: dict(r) for r in concept_rows}
    for w in works:
        m = mastery_by_work.get(w["id"], {})
        w["concept_count"] = m.get("concept_count", 0)
        w["graduated_count"] = m.get("graduated_count", 0)
        total = w["concept_count"]
        w["mastery_pct"] = round(w["graduated_count"] / total * 100) if total else 0
    return {"works": works}


@router.get("/learn/health")
def learn_health():
    """Return aggregate learning health metrics across all Works for the mobile health card.

    Response: { total_due, stuck_count, graduating_this_week }
    """
    db = get_db()
    from orivellum.capabilities.learning import get_learn_health

    return get_learn_health(db)


@router.get("/works")
def works_list(status: str | None = None, work_type: str | None = None):
    db = get_db()
    return {"works": db.list_works(status=status, work_type=work_type)}


@router.post("/works")
def works_create(body: WorkCreate):
    db = get_db()
    work = db.create_work(
        title=body.title,
        work_type=body.work_type,
        description=body.description,
        meta=body.meta,
    )
    return {"work": work}


@router.get("/works/{work_id}")
def works_get(work_id: str):
    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    return {"work": work}


@router.patch("/works/{work_id}")
def works_update(work_id: str, body: WorkUpdate):
    db = get_db()
    work = db.update_work(
        work_id,
        title=body.title,
        description=body.description,
        status=body.status,
        work_type=body.work_type,
        meta=body.meta,
    )
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    return {"work": work}


@router.delete("/works/{work_id}")
def works_delete(work_id: str):
    db = get_db()
    ok = db.delete_work(work_id)
    if not ok:
        raise HTTPException(404, f"Work {work_id!r} not found")
    return {"ok": True}


# ── Work cover images ─────────────────────────────────────────────────────────
# Stored under <data_dir>/covers/<work_id><ext>; works.cover_path holds the
# data-dir-relative path. Shown on Work cards/detail and passed to the Read
# Aloud player as lock-screen artwork.
#
# Lifecycle safety: all cover mutations serialize on _COVER_MUTATION_LOCK and
# follow a strict order so cover_path never points at a missing file:
#   upload — write temp file, atomic os.replace into place, THEN commit the DB
#            path, THEN remove obsolete other-extension files;
#   delete — clear the DB path FIRST, then unlink files.
# A failure mid-sequence can only leave an orphan file (harmless), never a
# dangling DB reference. Soft-deleting a Work deliberately keeps its cover on
# disk so restoring the Work restores its artwork.

_COVER_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_COVER_MAGIC: dict[str, list[tuple[bytes, int]]] = {
    ".png": [(b"\x89PNG\r\n\x1a\n", 0)],
    ".jpg": [(b"\xff\xd8\xff", 0)],
    ".jpeg": [(b"\xff\xd8\xff", 0)],
    ".webp": [(b"WEBP", 8)],  # RIFF????WEBP
}
_COVER_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_MAX_COVER_BYTES = 10 * 1024 * 1024  # 10 MB is plenty for a cover image

# Serializes upload/replace/delete so interleaved requests can't unlink a file
# the DB is about to reference. Global (not per-Work) — this is a single-user
# local server and cover writes are rare, so contention is a non-issue.
_COVER_MUTATION_LOCK = threading.Lock()


def _covers_dir():
    from pathlib import Path

    from orivellum.api._deps import get_config

    d = Path(get_config().data_dir) / "covers"
    d.mkdir(parents=True, exist_ok=True)
    return d


@router.post("/works/{work_id}/cover")
async def works_upload_cover(work_id: str, file: UploadFile):
    """Upload (or replace) a Work's cover image."""
    import os
    from pathlib import Path

    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _COVER_EXTS:
        raise HTTPException(415, "Cover must be a PNG, JPEG, or WebP image.")
    data = await file.read(_MAX_COVER_BYTES + 1)
    if not data:
        raise HTTPException(422, "The uploaded file is empty.")
    if len(data) > _MAX_COVER_BYTES:
        raise HTTPException(413, "Cover image is too large (max 10 MB).")
    if not any(data[off : off + len(sig)] == sig for sig, off in _COVER_MAGIC[ext]):
        raise HTTPException(
            415,
            f"File content does not match its extension ({ext}). "
            "The file may be corrupt or misnamed.",
        )
    covers = _covers_dir()
    target = covers / f"{work_id}{ext}"
    with _COVER_MUTATION_LOCK:
        # 1) Write to a temp file and atomically move into place — the target
        #    is never observable in a partially-written state.
        tmp = covers / f".{work_id}{ext}.tmp"
        tmp.write_bytes(data)
        os.replace(tmp, target)
        # 2) Commit the DB path only after the file exists.
        work = db.set_work_cover(work_id, f"covers/{work_id}{ext}")
        if work is None:
            target.unlink(missing_ok=True)
            raise HTTPException(404, f"Work {work_id!r} not found")
        # 3) Only now remove any previous cover with a different extension —
        #    a failure above leaves at most an orphan file, never a dangling
        #    cover_path.
        for old_ext in _COVER_EXTS - {ext}:
            (covers / f"{work_id}{old_ext}").unlink(missing_ok=True)
    return {"work": work}


@router.get("/works/{work_id}/cover")
def works_get_cover(work_id: str):
    """Serve a Work's cover image, or 404 when it has none."""
    from pathlib import Path

    from orivellum.api._deps import get_config

    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    cover_path = work.get("cover_path")
    if not cover_path:
        raise HTTPException(404, "This Work has no cover image")
    data_dir = Path(get_config().data_dir).resolve()
    target = (data_dir / cover_path).resolve()
    # cover_path is server-generated, but stay traversal-safe regardless.
    if not target.is_relative_to(data_dir) or not target.is_file():
        raise HTTPException(404, "Cover file missing")
    media_type = _COVER_MEDIA_TYPES.get(target.suffix.lower(), "application/octet-stream")
    # no-cache (not no-store): the browser may keep a copy but must revalidate,
    # so a replaced cover shows up without a hard refresh.
    return FileResponse(str(target), media_type=media_type, headers={"Cache-Control": "no-cache"})


@router.delete("/works/{work_id}/cover")
def works_delete_cover(work_id: str):
    """Remove a Work's cover image."""
    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    covers = _covers_dir()
    with _COVER_MUTATION_LOCK:
        # Clear the DB path FIRST, then unlink — if the unlink fails we're
        # left with an orphan file, never a cover_path to a missing file.
        updated = db.set_work_cover(work_id, None)
        for ext in _COVER_EXTS:
            (covers / f"{work_id}{ext}").unlink(missing_ok=True)
    return {"work": updated}


@router.get("/works/{work_id}/documents")
def works_documents(work_id: str):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    docs = db.list_documents(work_id=work_id)
    return {"documents": docs, "count": len(docs)}


@router.get("/works/{work_id}/duplicates")
def works_duplicates(work_id: str, resolved: bool = False):
    """Return near-duplicate document pairs where at least one doc belongs to this Work."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    pairs = db.list_near_duplicates(resolved=resolved, work_id=work_id)
    return {"pairs": pairs, "count": len(pairs)}


@router.get("/works/{work_id}/knowledge")
def works_knowledge(work_id: str, kind: str | None = None):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    items = db.list_knowledge(work_id=work_id, kind=kind)
    return {"knowledge": items, "count": len(items)}


@router.post("/works/{work_id}/quiz")
async def generate_quiz(work_id: str, count: int = 5):
    """Generate multiple-choice quiz questions from a Work's knowledge base using the AI.

    Each returned question includes an optional ``concept_id`` field when the Work has seeded
    learning concepts so that the frontend can call /learning/assess against the exact concept
    the question tests — avoiding cross-concept mastery contamination.
    """
    import json
    import logging

    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    items = db.list_knowledge(work_id=work_id, limit=20)
    if not items:
        raise HTTPException(
            422, "This Work has no knowledge items yet — import and process some documents first."
        )

    knowledge_text = "\n".join(
        f"- {it.get('kind', 'fact').upper()}: {it.get('text', '')}" for it in items[:20]
    )
    work = db.get_work(work_id)
    title = (work.get("title") or "this topic") if work else "this topic"

    # Fetch concepts so each question can be tagged with the concept it tests.
    # Cap at 20 most-studied concepts to keep the prompt within context limits;
    # Works with 50+ concepts would otherwise inflate the prompt by hundreds of
    # tokens and risk silent truncation on smaller models.
    _CONCEPT_CAP = 20
    from orivellum.capabilities.learning import list_concepts

    concepts = list_concepts(db, work_id)
    if len(concepts) > _CONCEPT_CAP:
        concepts = sorted(
            concepts,
            key=lambda c: c.get("consecutive_passes") or 0,
            reverse=True,
        )[:_CONCEPT_CAP]
    has_concepts = bool(concepts)

    if has_concepts:
        concept_list = "\n".join(
            f'  {{"id":"{c["id"]}","subject":"{c["subject"]}"}}' for c in concepts
        )
        concept_instruction = (
            f"\n\nAvailable concepts (pick the best matching concept_id for each question):\n"
            f"[{concept_list}]\n\n"
            'Add a "concept_id" field to each question with the id of the concept it tests. '
            "Format:\n"
            '{"questions":[{"q":"Question?","options":["A","B","C","D"],'
            '"answer":0,"explanation":"...","concept_id":"<id from list above>"}]}'
        )
    else:
        concept_instruction = (
            "\n\nReturn ONLY valid JSON with no markdown, no commentary, no code fences. "
            "Format:\n"
            '{"questions":[{"q":"Question?","options":["A text","B text","C text","D text"],"answer":0,"explanation":"..."}]}'
        )

    prompt = (
        f'You are an expert quiz generator. Based on the following knowledge items about "{title}", '
        f"generate exactly {count} multiple-choice questions that test real understanding. "
        "Each question must have exactly 4 options (A–D), one correct answer index (0-based), "
        "and a short explanation of why the correct answer is right."
        + concept_instruction
        + f"\n\nKnowledge items:\n{knowledge_text}"
    )

    from starlette.concurrency import run_in_threadpool

    from orivellum.capabilities.llm import llm_call
    from orivellum.config import get_config

    cfg = get_config()
    # Build a valid concept_id set for post-parse validation
    valid_concept_ids = {c["id"] for c in concepts}
    try:
        result = await run_in_threadpool(
            llm_call,
            [{"role": "user", "content": prompt}],
            base_url=cfg.serving.base_url,
            model=cfg.serving.workhorse_model,
            timeout=60,
            purpose="works",
            db=db,
        )
        if not result.ok or result.text is None:
            raise HTTPException(
                503, "AI is unavailable. Start Lemonade or Ollama to generate quizzes."
            )
        content = result.text
        # Strip markdown fences if the model added them
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        parsed = json.loads(content)
        questions = parsed["questions"][:count]
        # Validate concept_ids: strip any id the model hallucinated so the frontend
        # never calls /learning/assess against a non-existent concept.
        if valid_concept_ids:
            for q in questions:
                if q.get("concept_id") not in valid_concept_ids:
                    q.pop("concept_id", None)
        return {"questions": questions, "work_id": work_id, "has_concepts": has_concepts}
    except HTTPException:
        raise
    except json.JSONDecodeError as exc:
        raise HTTPException(502, f"AI returned invalid JSON: {exc}") from exc
    except Exception as exc:
        logging.getLogger("orivellum").warning("Quiz generation failed: %s", exc)
        raise HTTPException(
            503, "AI is unavailable. Start Lemonade or Ollama to generate quizzes."
        ) from exc


@router.get("/knowledge/ask")
def knowledge_ask(
    q: str = Query(..., description="Search query"),
    work_id: str | None = Query(None, description="Limit to a specific work"),
    doc_id: str | None = Query(None, description="Limit to a specific document"),
    limit: int = Query(12, le=50),
):
    """Cross-work knowledge and chunk search. Pass work_id or doc_id to scope."""
    db = get_db()
    if not q.strip():
        return {"knowledge": [], "chunks": [], "query": q}
    try:
        knowledge = db.search_knowledge(q, work_id=work_id, doc_id=doc_id, limit=limit)
        chunks = db.search_chunks(q, work_id=work_id, limit=limit)
    except Exception as exc:
        raise internal_error(logger, exc, "cross-work search") from exc
    return {
        "knowledge": [dict(r) for r in knowledge],
        "chunks": [dict(r) for r in chunks],
        "query": q,
        "total": len(knowledge) + len(chunks),
        "work_id": work_id,
        "doc_id": doc_id,
    }


@router.get("/works/{work_id}/search")
def works_search(work_id: str, q: str, limit: int = 20):
    """Full-text search across a Work's knowledge items and document chunks."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    if not q.strip():
        return {"knowledge": [], "chunks": [], "query": q}
    try:
        knowledge = db.search_knowledge(q, work_id=work_id, limit=limit)
        chunks = db.search_chunks(q, work_id=work_id, limit=limit)
    except Exception as exc:
        raise internal_error(logger, exc, f"work search for {work_id!r}") from exc
    return {
        "knowledge": knowledge,
        "chunks": chunks,
        "query": q,
        "total": len(knowledge) + len(chunks),
    }


@router.get("/works/{work_id}/tasks")
def works_tasks(work_id: str, status: str | None = None):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    tasks = db.list_tasks(work_id=work_id, status=status)
    return {"tasks": tasks, "count": len(tasks)}


@router.post("/works/{work_id}/tasks")
def works_create_task(work_id: str, body: TaskCreate):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    task = db.create_task(work_id, body.text, body.priority)
    return {"task": task}


@router.patch("/works/{work_id}/tasks/{task_id}")
def works_update_task(work_id: str, task_id: str, body: TaskUpdate):
    db = get_db()
    task = db.update_task(task_id, status=body.status, text=body.text, priority=body.priority)
    if not task:
        raise HTTPException(404, f"Task {task_id!r} not found")
    return {"task": task}


class KnowledgeCreate(BaseModel):
    text: str
    kind: str = "claim"
    subject: str | None = None
    predicate: str | None = None
    obj: str | None = None


@router.post("/works/{work_id}/knowledge")
def works_create_knowledge(work_id: str, body: KnowledgeCreate):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    item_id = db.create_knowledge_item(
        work_id=work_id,
        kind=body.kind,
        text=body.text.strip(),
        subject=body.subject,
        predicate=body.predicate,
        obj=body.obj,
        confidence=1.0,
        review_status="approved",
    )
    with db._lock:
        row = db._conn.execute("SELECT * FROM knowledge WHERE id=?", (item_id,)).fetchone()
    return {"item": dict(row) if row else {"id": item_id}}


@router.delete("/works/{work_id}/tasks/{task_id}", status_code=204)
def works_delete_task(work_id: str, task_id: str):
    db = get_db()
    ok = db.delete_task(task_id)
    if not ok:
        raise HTTPException(404, f"Task {task_id!r} not found")


@router.get("/works/{work_id}/conversations")
def works_conversations(work_id: str):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    convs = db.list_conversations(work_id=work_id)
    return {"conversations": convs}


def _trailer_count(db, work_id: str) -> int:
    """Return the number of trailer packages generated for a Work."""
    try:
        with db._lock:
            row = db._conn.execute(
                "SELECT COUNT(*) AS n FROM trailers WHERE work_id=?", (work_id,)
            ).fetchone()
        return row["n"] if row else 0
    except Exception:
        return 0


@router.get("/works/{work_id}/stats")
def works_stats(work_id: str):
    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    with db._lock:
        doc_by_kind = db._conn.execute(
            "SELECT kind, COUNT(*) as n FROM documents WHERE work_id=? GROUP BY kind", (work_id,)
        ).fetchall()
        knowledge_by_kind = db._conn.execute(
            "SELECT kind, COUNT(*) as n FROM knowledge WHERE work_id=? GROUP BY kind", (work_id,)
        ).fetchall()
        task_by_status = db._conn.execute(
            "SELECT status, COUNT(*) as n FROM tasks WHERE work_id=? GROUP BY status", (work_id,)
        ).fetchall()
        conv_count = db._conn.execute(
            "SELECT COUNT(*) as n FROM conversations WHERE work_id=?", (work_id,)
        ).fetchone()["n"]
        doc_by_readiness = db._conn.execute(
            "SELECT readiness, COUNT(*) as n FROM documents WHERE work_id=? GROUP BY readiness",
            (work_id,),
        ).fetchall()
        try:
            mastery_row = db._conn.execute(
                "SELECT AVG(mastery) as avg_m, COUNT(*) as cnt FROM learning_concepts WHERE work_id=?",
                (work_id,),
            ).fetchone()
            avg_mastery = mastery_row["avg_m"] or 0.0
            concept_count = mastery_row["cnt"] or 0
        except Exception:
            avg_mastery, concept_count = 0.0, 0
    return {
        "work_id": work_id,
        "documents_by_kind": {r["kind"] or "unknown": r["n"] for r in doc_by_kind},
        "documents_by_readiness": {r["readiness"] or "unknown": r["n"] for r in doc_by_readiness},
        "knowledge_by_kind": {r["kind"]: r["n"] for r in knowledge_by_kind},
        "tasks_by_status": {r["status"]: r["n"] for r in task_by_status},
        "pending_task_count": sum(
            r["n"] for r in task_by_status if r["status"] not in ("completed", "done", "complete")
        ),
        "conversation_count": conv_count,
        "avg_mastery_pct": round(avg_mastery * 100),
        "concept_count": concept_count,
        "trailer_count": _trailer_count(db, work_id),
    }


@router.get("/works/{work_id}/book-intelligence")
def works_book_intelligence(work_id: str):
    """Unified Knowledge Object view of a Work: canonical manuscript,
    manuscript versions, merged outline with per-chapter status and research
    counts, completeness dimensions, gaps, and the next recommended action.

    All data derives from existing extracted text, knowledge items, and
    book_chapters records — nothing is recomputed from source files.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.book_intelligence import build_book_intelligence

    try:
        return build_book_intelligence(work_id, db)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


# ── ConStory — story-contradiction findings ──────────────────────────────────


@router.post("/works/{work_id}/constory/run")
def constory_run(work_id: str):
    """Kick off a ConStory contradiction check for all chapters of a Work.

    Runs in the background (whole-book pairing is many gateway calls);
    poll GET /constory/status.  409 when a run is already in flight.
    """
    from orivellum.api._deps import get_config
    from orivellum.api.executor import submit_bg
    from orivellum.capabilities import constory

    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    # Atomic claim BEFORE dispatch: a second POST is refused here, and the
    # UI's first status poll already sees state='running'.
    if not constory.try_claim_run(work_id):
        raise HTTPException(409, "A contradiction check is already running for this work")
    cfg = get_config()

    def _run():
        try:
            constory.run_constory_check(db, cfg, work_id=work_id)
        except Exception:
            logger.exception("constory run failed for work %s", work_id)

    if not submit_bg(_run, kind="constory", label=f"constory:{work_id}"):
        constory.release_run_claim(work_id, error="background executor unavailable")
        raise HTTPException(503, "Background executor unavailable — try again shortly")
    return {"started": True, "work_id": work_id}


@router.get("/works/{work_id}/constory/status")
def constory_status(work_id: str):
    from orivellum.capabilities import constory

    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    return {"work_id": work_id, "run": constory.get_run_status(work_id)}


@router.get("/works/{work_id}/findings/metrics")
def narrative_finding_metrics(work_id: str):
    """CED (findings per 10,000 words) per chapter and for the book, plus
    finding counts by severity/category/disposition."""
    from orivellum.capabilities.constory import compute_ced

    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    findings = db.list_narrative_findings(work_id, limit=2000)
    by = lambda key: {  # noqa: E731
        v: sum(1 for f in findings if f[key] == v) for v in sorted({f[key] for f in findings})
    }
    return {
        **compute_ced(db, work_id),
        "counts": {
            "total": len(findings),
            "by_severity": by("severity"),
            "by_category": by("category"),
            "by_disposition": by("disposition"),
        },
    }


@router.get("/works/{work_id}/findings")
def list_narrative_findings(
    work_id: str,
    chapter_id: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    disposition: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
):
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    findings = db.list_narrative_findings(
        work_id,
        chapter_id=chapter_id,
        category=category,
        severity=severity,
        disposition=disposition,
        limit=limit,
    )
    # Cross-book labels: when a finding contradicts a canon fact that was
    # established in a DIFFERENT book (an earlier volume of this Work's
    # series), name both books so the surface reads "book 2 vs book 1".
    fact_ids = {f.get("canon_fact_id") for f in findings if f.get("canon_fact_id")}
    if fact_ids:
        from orivellum.database.series_store import SeriesStore  # noqa: PLC0415

        store = SeriesStore(db)
        marks = ",".join("?" for _ in fact_ids)
        rows = (
            db.read_conn()
            .execute(
                f"SELECT id, work_id, series_id FROM canon_fact WHERE id IN ({marks})",
                list(fact_ids),
            )
            .fetchall()
        )
        fact_scope = {r["id"]: dict(r) for r in rows}
        titles: dict[str, str] = {}
        own_membership = store.series_for_work(work_id)
        for f in findings:
            scope = fact_scope.get(f.get("canon_fact_id") or "")
            if not scope:
                continue
            src = scope["work_id"]
            if src and src != work_id:
                # Only a validated cross-book relation earns the label: the
                # source book must be an EARLIER volume of the SAME series.
                # (Stale facts from removed members or other series would
                # otherwise read as false cross-book drift.)
                membership = store.series_for_work(src)
                if (
                    own_membership
                    and membership
                    and membership["series_id"] == own_membership["series_id"]
                    and int(membership["volume"]) < int(own_membership["volume"])
                ):
                    if src not in titles:
                        src_work = db.get_work(src)
                        titles[src] = (src_work or {}).get("title") or src
                    f["cross_book"] = {
                        "work_id": src,
                        "title": titles[src],
                        "volume": membership["volume"],
                    }
            elif scope["series_id"]:
                f["series_scoped"] = True
    return {"work_id": work_id, "findings": findings, "count": len(findings)}


class FindingDisposition(BaseModel):
    disposition: str
    note: str = ""


@router.patch("/works/{work_id}/findings/{finding_id}")
def set_narrative_finding_disposition(work_id: str, finding_id: str, body: FindingDisposition):
    """Disposition a finding: open (reopen) / fixed / intentional / wontfix.

    'intentional' REQUIRES a note — delayed revelation and unreliable
    narration legitimately read as contradictions, and the note records why
    this one is deliberate.
    """
    db = get_db()
    existing = db.get_narrative_finding(finding_id)
    if not existing or existing["work_id"] != work_id:
        raise HTTPException(404, f"Finding {finding_id!r} not found for this work")
    try:
        updated = db.update_narrative_finding_disposition(
            finding_id, body.disposition, note=body.note, actor="user"
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if updated is None:
        raise HTTPException(404, f"Finding {finding_id!r} not found")
    return {"finding": updated}


@router.get("/works/{work_id}/chapters")
def works_chapters(work_id: str):
    """Return all book chapters extracted from documents linked to this Work.

    Results are grouped by document and ordered by document title then
    chapter sequence number.  Each chapter record includes ``word_count``
    (approximated from text), ``status``, and ``extraction_method``.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    import json as _json

    with db._lock:
        rows = db._conn.execute(
            """SELECT bc.id, bc.seq, COALESCE(bc.level, 1) as level, bc.title,
                      (length(coalesce(bc.text,'')) - length(replace(coalesce(bc.text,''), ' ', '')) + 1) as word_count,
                      bc.status, bc.extraction_method, bc.created_at,
                      bc.source_doc_id, bc.meta,
                      (SELECT COUNT(*) FROM knowledge k WHERE k.chapter_id = bc.id) as knowledge_count,
                      d.title as doc_title
               FROM book_chapters bc
               JOIN documents d ON d.id = bc.source_doc_id
               WHERE bc.work_id = ?
               ORDER BY d.title, bc.seq""",
            (work_id,),
        ).fetchall()

    by_doc: dict[str, dict] = {}
    for r in rows:
        doc_id = r["source_doc_id"]
        if doc_id not in by_doc:
            by_doc[doc_id] = {
                "doc_id": doc_id,
                "doc_title": r["doc_title"] or "Untitled",
                "chapters": [],
            }
        ch = dict(r)
        ch.pop("doc_title", None)
        # Parse scene_count from meta JSON stored during extraction
        try:
            _meta = _json.loads(ch.pop("meta") or "{}")
            ch["scene_count"] = _meta.get("scene_count", 1)
        except Exception:
            ch.pop("meta", None)
            ch["scene_count"] = 1
        by_doc[doc_id]["chapters"].append(ch)

    return {
        "work_id": work_id,
        "total_chapters": len(rows),
        "documents": list(by_doc.values()),
    }


@router.get("/works/{work_id}/chapters/{chapter_id}/knowledge")
def works_chapter_knowledge(work_id: str, chapter_id: str, limit: int = 50):
    """Return knowledge items tagged to a specific chapter.

    Items are ordered by kind then confidence (descending).
    Only items with review_status in ('auto', 'approved', 'ai_auto') are returned —
    rejected items are excluded so the UI never shows noise the user dismissed.

    Query params:
      limit — max items to return (default 50, max 200)
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    limit = min(limit, 200)
    with db._lock:
        # Verify the chapter belongs to this work
        ch = db._conn.execute(
            """SELECT bc.id, bc.title, bc.seq
               FROM book_chapters bc
               JOIN documents d ON d.id = bc.source_doc_id
               WHERE bc.id = ? AND d.work_id = ?""",
            (chapter_id, work_id),
        ).fetchone()
    if not ch:
        raise HTTPException(404, f"Chapter {chapter_id!r} not found in work {work_id!r}")

    import json as _json

    with db._lock:
        rows = db._conn.execute(
            """SELECT id, kind, text, subject, predicate, object as obj,
                      confidence, review_status, meta, created_at
               FROM knowledge
               WHERE chapter_id = ?
                 AND review_status IN ('auto', 'approved', 'ai_auto')
               ORDER BY kind, confidence DESC
               LIMIT ?""",
            (chapter_id, limit),
        ).fetchall()

    items = []
    for r in rows:
        d = dict(r)
        try:
            d["meta"] = _json.loads(d.get("meta") or "{}")
        except Exception:
            d["meta"] = {}
        items.append(d)

    return {
        "chapter_id": chapter_id,
        "chapter_title": ch["title"] or f"Chapter {ch['seq'] + 1}",
        "chapter_seq": ch["seq"],
        "work_id": work_id,
        "knowledge": items,
        "count": len(items),
    }


# ─── Project Compass ───────────────────────────────────────────────────────────


class CompassUpdate(BaseModel):
    focus: str | None = None
    last_reasoning: str | None = None
    next_step: str | None = None


@router.get("/works/{work_id}/compass")
def get_compass(work_id: str):
    """Return the Project Compass state for a Work."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.cognition import read_compass

    compass = read_compass(db, work_id)
    return {"work_id": work_id, "compass": compass}


@router.get("/entities")
def list_entities(kind: str | None = None, limit: int = 200):
    """Return all entities across the workspace with mention counts."""
    db = get_db()
    entities = db.list_entities(kind=kind, limit=min(limit, 1000))
    return {"entities": entities, "count": len(entities)}


@router.get("/entities/{entity_id}")
def get_entity(entity_id: str):
    """Return a single entity with its document mention list."""
    db = get_db()
    with db._lock:
        row = db._conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
    if not row:
        raise HTTPException(404, f"Entity {entity_id!r} not found")
    import json as _json

    entity = dict(row)
    try:
        entity["meta"] = _json.loads(entity.get("meta") or "{}")
    except Exception:
        entity["meta"] = {}
    with db._lock:
        mention_rows = db._conn.execute(
            """SELECT d.id, d.title, d.kind, d.work_id
               FROM relationships r
               JOIN documents d ON d.id = r.target_id
               WHERE r.source_id=? AND r.kind='MENTIONS'
               LIMIT 50""",
            (entity_id,),
        ).fetchall()
    entity["mentions"] = [dict(r) for r in mention_rows]
    entity["mention_count"] = len(entity["mentions"])
    return entity


class RippleRequest(BaseModel):
    node_id: str | None = None
    canon_fact_id: str | None = None
    name: str | None = Field(default=None, max_length=300)
    depth: int | None = Field(default=None, ge=1, le=6)


@router.post("/works/{work_id}/ripple")
def works_ripple(work_id: str, req: RippleRequest):
    """RIPPLE simulation (E12): blast radius of a proposed change.

    Seed by exactly one of ``node_id``, ``canon_fact_id``, or ``name`` and
    walk the ATLAS world graph outward.  Read-only — nothing is mutated;
    the report shows what a change would cost before it is made.
    """
    from orivellum.capabilities.ripple import RippleError, simulate_ripple

    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    try:
        return simulate_ripple(
            db,
            work_id,
            node_id=req.node_id,
            canon_fact_id=req.canon_fact_id,
            name=req.name,
            depth=req.depth,
        )
    except RippleError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/graph")
def global_graph(
    work_id: str | None = None,
    entity_kinds: str | None = None,
    limit: int = 200,
):
    """Return a knowledge graph across all Works (or scoped to one).

    Query parameters:
    - ``work_id`` — when set, restricts the graph to a single Work.
    - ``entity_kinds`` — comma-separated allow-list of entity kinds
      (e.g. ``person,place,concept``). Document nodes are always included.
    - ``limit`` — max number of nodes (capped at 300).

    Response shape: ``{nodes, edges, node_count, edge_count}``.
    Each node has ``{id, label, type, kind}``; entity nodes from the global
    view additionally carry ``{work_id, work_title}`` when available.
    Each edge has ``{source, target, label, type}``.
    """
    db = get_db()
    kinds = [k.strip() for k in entity_kinds.split(",") if k.strip()] if entity_kinds else None
    graph = db.get_global_graph(
        work_id=work_id,
        entity_kinds=kinds,
        limit=min(limit, 300),
    )
    return graph


@router.get("/works/{work_id}/graph")
def works_graph(work_id: str, limit: int = 100, entity_kinds: str | None = None):
    """Return entity graph nodes and edges for a Work.

    Uses real entity/edge tables when populated, falls back to a
    knowledge-item projection for works processed before graph support.
    Optional ``entity_kinds`` comma-separated allow-list filters node types.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    kinds = [k.strip() for k in entity_kinds.split(",") if k.strip()] if entity_kinds else None
    graph = db.get_global_graph(
        work_id=work_id,
        entity_kinds=kinds,
        limit=min(limit, 200),
    )
    return {"work_id": work_id, **graph}


@router.get("/gaps/top")
def workspace_top_gaps(limit: int = 3, refresh: bool = False):
    """Return the highest-severity research gaps across all active Works.

    Strategy (v50 cache):
    1. Read all non-stale (< 1 h) gap cache rows for active Works.
    2. For Works with no cache entry (or when ``refresh=True``), run detection
       now and write the results to the cache — capped at 10 Works to stay fast.
    3. Sort all results by severity and return the top ``limit`` entries.

    This makes the dashboard load in milliseconds on repeat visits while still
    providing fresh data when the cache is cold or the caller forces a refresh.
    """
    db = get_db()
    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    works = db.list_works(status="active")
    work_by_id = {w["id"]: w for w in works}

    sev_order = {"high": 0, "medium": 1, "low": 2, "critical": -1}

    # ── 1. Load cached rows ────────────────────────────────────────────────────
    all_gaps: list[dict] = []
    cached_work_ids: set[str] = set()

    if not refresh:
        for cached in db.get_all_cached_gaps(max_age_seconds=3600):
            wid = cached["work_id"]
            if wid not in work_by_id:
                continue  # Work was deleted or deactivated
            cached_work_ids.add(wid)
            title = work_by_id[wid].get("title", "")
            for g in cached["gaps"]:
                all_gaps.append(
                    {
                        "work_id": wid,
                        "work_title": title,
                        **{
                            k: g.get(k, "")
                            for k in ("kind", "title", "description", "severity", "metadata")
                        },
                    }
                )

    # ── 2. Detect for uncached / stale Works (cap at 10 to stay fast) ─────────
    stale_works = [w for w in works if refresh or w["id"] not in cached_work_ids]
    for work in stale_works[:10]:
        try:
            report = detect_hygiene(work["id"], db)
            gap_dicts = [
                {
                    "kind": g.kind,
                    "title": g.title,
                    "description": g.description,
                    "severity": g.severity,
                    "metadata": g.metadata,
                    "finding_key": g.finding_key,
                }
                for g in report.findings
            ]
            db.cache_work_gaps(
                work["id"],
                gap_dicts,
                report.coverage_pct,
                suggested_queries=report.suggested_queries,
            )
            for g in gap_dicts:
                all_gaps.append({"work_id": work["id"], "work_title": work.get("title", ""), **g})
        except Exception as exc:
            logger.warning("Gap detection failed for work %s: %s", work.get("id"), exc)

    all_gaps.sort(key=lambda x: sev_order.get(x.get("severity", ""), 3))

    return {
        "gaps": all_gaps[: max(1, limit)],
        "total_works_analyzed": len(works),
        "cache_hits": len(cached_work_ids),
    }


@router.get("/works/{work_id}/gaps")
def works_gaps(work_id: str, refresh: bool = False):
    """Return research gap analysis for a Work.

    Uses the cache (max 1 h staleness) unless ``refresh=True``.
    Always writes fresh results to the cache after detection.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    from orivellum.capabilities.corpus_hygiene import detect_hygiene

    # Try cache first
    if not refresh:
        cached = db.get_cached_gaps(work_id, max_age_seconds=3600)
        if cached is not None:
            return {
                "work_id": work_id,
                "coverage_pct": cached["coverage_pct"],
                "total_chapters": None,
                "suggested_queries": cached["suggested_queries"],
                "evaluated_at": cached["evaluated_at"],
                "gaps": cached["gaps"],
                "from_cache": True,
            }

    report = detect_hygiene(work_id, db)
    gap_dicts = [
        {
            "kind": g.kind,
            "title": g.title,
            "description": g.description,
            "severity": g.severity,
            "metadata": g.metadata,
            "finding_key": g.finding_key,
        }
        for g in report.findings
    ]
    # Write back to cache — persist suggested_queries so future cached
    # responses return them without re-running detection.
    try:
        db.cache_work_gaps(
            work_id, gap_dicts, report.coverage_pct, suggested_queries=report.suggested_queries
        )
    except Exception as exc:
        logger.debug("Gap cache write failed: %s", exc)

    return {
        "work_id": report.work_id,
        "coverage_pct": report.coverage_pct,
        "total_chapters": report.total_chapters,
        "suggested_queries": report.suggested_queries,
        "evaluated_at": report.evaluated_at,
        "gaps": gap_dicts,
        "from_cache": False,
    }


@router.get("/works/{work_id}/completeness")
def works_completeness(work_id: str):
    """Return multi-dimensional completeness scoring for a Work."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.completeness import calculate_work_completeness

    report = calculate_work_completeness(work_id, db)
    return {
        "work_id": report.work_id,
        "work_title": report.work_title,
        "overall": report.overall,
        "readiness": report.readiness,
        "summary": report.summary,
        "evaluated_at": report.evaluated_at,
        "dimensions": [
            {
                "name": d.name,
                "label": d.label,
                "score": d.score,
                "current": d.current,
                "target": d.target,
                "unit": d.unit,
                "rule": d.rule,
                "evidence": d.evidence,
            }
            for d in report.dimensions
        ],
    }


@router.patch("/works/{work_id}/compass")
def patch_compass(work_id: str, body: CompassUpdate):
    """Partial-update the Project Compass state for a Work.

    Only fields explicitly provided in the request body are written;
    omitted fields retain their current values.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    from orivellum.capabilities.cognition import read_compass, update_compass

    # Pass keyword args so only non-None fields are set
    update_compass(
        db,
        work_id,
        focus=body.focus,
        reasoning=body.last_reasoning,
        next_step=body.next_step,
    )
    return {"work_id": work_id, "compass": read_compass(db, work_id)}


# ─── Book Pipeline ──────────────────────────────────────────────────────────────


class PipelineCreateRequest(BaseModel):
    title: str | None = None


@router.post("/works/{work_id}/pipeline")
def create_pipeline(
    work_id: str, body: PipelineCreateRequest = Body(default=PipelineCreateRequest())
):
    """Create (or return existing) book pipeline for a Work, initialised at B0.

    Idempotent — calling multiple times returns the same pipeline.
    Orphan book_chapters already extracted for this Work are linked
    to the new pipeline automatically.
    """
    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    title = (body.title or "").strip() or work.get("title") or "Book Pipeline"
    pipeline = db.create_book_pipeline(work_id, title)
    return {"pipeline": pipeline}


@router.get("/works/{work_id}/pipeline")
def get_pipeline(work_id: str):
    """Return the current book pipeline state for a Work, or null if none exists.

    Enriches the DB row with computed ``stage_label``, ``next_status``,
    ``chapters_total``, the current stage's AI artifact (or null), and any
    open governance findings on the pipeline so clients don't need extra calls.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    pipeline = db.get_book_pipeline_for_work(work_id)
    if pipeline:
        from orivellum.capabilities.state_machine import BOOK_SM, BOOK_STAGE_LABELS

        status = pipeline.get("status", "")
        pipeline["stage_label"] = BOOK_STAGE_LABELS.get(status, status)
        allowed = BOOK_SM.allowed_from(status)
        pipeline["next_status"] = next(iter(allowed)) if allowed else None
        pipeline["chapters_total"] = pipeline.get("chapter_count", 0)
        # Enrich with current-stage artifact (None when not yet run)
        pipeline["stage_artifact"] = db.get_pipeline_artifact(pipeline["id"], status)
        # Open findings block state-machine transitions; surface them so the UI
        # can explain why Advance is unavailable without a 409 round-trip.
        pipeline["open_findings"] = db.list_findings(
            object_id=pipeline["id"], state="open", limit=20
        )
    return {"pipeline": pipeline}


@router.get("/works/{work_id}/pipeline/package")
def pipeline_package_status(work_id: str):
    """Report whether the book pipeline's chapters can be packaged for export.

    Never fails when unready — returns ``ready: false`` plus human-readable
    reasons so the UI can explain exactly what is missing.
    """
    from orivellum.capabilities.book_package import package_readiness

    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    pipeline = db.get_book_pipeline_for_work(work_id)
    if not pipeline:
        return {"ready": False, "reasons": ["No book pipeline exists for this Work yet."]}
    chapters = db.list_pipeline_chapters(pipeline["id"])
    return package_readiness(pipeline, chapters)


@router.get("/works/{work_id}/pipeline/package/download")
def pipeline_package_download(work_id: str):
    """Build and download the book package — an EPUB plus per-chapter
    Markdown and a manifest, in one ZIP. Assembled in memory on demand;
    nothing is persisted."""
    from fastapi.responses import Response

    from orivellum.capabilities.book_package import build_book_export

    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")
    pipeline = db.get_book_pipeline_for_work(work_id)
    if not pipeline:
        raise HTTPException(409, "No book pipeline exists for this Work yet.")
    chapters = db.list_pipeline_chapters(pipeline["id"])
    try:
        filename, payload = build_book_export(pipeline, chapters, work)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _check_stage_gate(
    current: str,
    next_state: str,
    work_id: str,
    db,
    pipeline_id: str | None = None,
) -> dict | None:
    """Return a structured gate-fail dict, or None when the gate is satisfied.

    Gates are evaluated inline against live DB data; no HTTP self-calls.
    If gate data is unavailable (e.g. capability import fails) the gate is
    silently skipped so a transient dependency failure never permanently blocks
    a user.
    """
    gate_key = (current, next_state)

    # ── AI stage artifact must be completed before advancing ─────────────────────
    # Workers exist for B0–B3 (planning) and B6/B7 (continuity + fact check);
    # each of those stages' artifacts must be status='done'.  B4 (Chapter
    # Extraction) and B5 (Chapter Drafting) have no LLM worker.
    _ARTIFACT_REQUIRED_FOR = {"B0", "B1", "B2", "B3", "B6", "B7"}
    if current in _ARTIFACT_REQUIRED_FOR and pipeline_id:
        try:
            artifact = db.get_pipeline_artifact(pipeline_id, current)
            if not artifact or artifact.get("status") != "done":
                status_desc = (artifact or {}).get("status", "not started")
                try:
                    from orivellum.capabilities.pipeline_workers import _STAGE_CFG

                    _, _, stage_label = _STAGE_CFG.get(current, ("", "", current))
                except Exception:
                    stage_label = current
                return {
                    "gate": f"{current}→{next_state}",
                    "metric": "stage_artifact",
                    "threshold": 1,
                    "actual": 0,
                    "detail": (
                        f"{current}→{next_state} requires the {stage_label} AI work "
                        f"to be completed first (current status: {status_desc}). "
                        f'Click "{stage_label}" to generate it.'
                    ),
                }
        except Exception as exc:
            logger.warning(
                "Artifact gate check failed for pipeline %s stage %s: %s",
                pipeline_id[:8] if pipeline_id else "?",
                current,
                exc,
            )
            # Fail-open: skip the artifact gate if the check itself errors

    # ── B0 → B1: Work must have at least one active (non-deleted) document ───────
    if gate_key == ("B0", "B1"):
        try:
            with db._lock:
                doc_count = db._conn.execute(
                    """SELECT COUNT(*) FROM documents d
                       JOIN objects o ON o.id = d.id
                       WHERE d.work_id = ? AND o.lifecycle != 'deleted'""",
                    (work_id,),
                ).fetchone()[0]
            if doc_count < 1:
                return {
                    "gate": f"{current}→{next_state}",
                    "metric": "doc_count",
                    "threshold": 1,
                    "actual": 0,
                    "detail": "B0→B1 requires at least 1 active document in the Work (none imported yet).",
                }
        except Exception:
            pass
        return None

    # ── Completeness-based gates ───────────────────────────────────────────────
    # threshold is a percentage (0-100); op is ">" or ">="
    _COMPLETENESS_GATES: dict[tuple, tuple] = {
        ("B1", "B2"): ("structural_pct", ">", 0, "at least 1 chapter extracted"),
        ("B2", "B3"): ("research_pct", ">=", 40, "40% research coverage"),
        ("B3", "B4"): ("research_pct", ">=", 60, "60% research coverage"),
        ("B4", "B5"): ("structural_pct", ">=", 80, "80% chapter extraction"),
        ("B6", "B7"): ("content_pct", ">=", 50, "50% content coverage"),
        ("B7", "B8"): ("editorial_pct", ">=", 30, "30% editorial review"),
        ("B16", "B17"): ("editorial_pct", ">=", 80, "80% editorial review"),
    }

    if gate_key not in _COMPLETENESS_GATES:
        return None  # no readiness gate for this transition

    metric, op, threshold, label = _COMPLETENESS_GATES[gate_key]

    # Fetch completeness data from book_intelligence (already computed elsewhere)
    actual: float = 0.0
    try:
        from orivellum.capabilities.book_intelligence import build_book_intelligence

        intel = build_book_intelligence(work_id, db)
        actual = float(intel.get("completeness", {}).get(metric, 0))
    except Exception:
        return None  # data unavailable — skip gate rather than block

    gate_met = (actual > threshold) if op == ">" else (actual >= threshold)

    if not gate_met:
        return {
            "gate": f"{current}→{next_state}",
            "metric": metric,
            "threshold": threshold,
            "actual": round(actual, 1),
            "detail": (
                f"{current}→{next_state} requires {label} — currently at {round(actual, 1)}%."
            ),
        }

    # ── Additional "no high gaps" check for B3→B4 and B16→B17 ────────────────
    if gate_key in {("B3", "B4"), ("B16", "B17")}:
        gaps: list = []
        evaluated = False
        try:
            from orivellum.capabilities.corpus_hygiene import detect_hygiene

            # Try a fresh-enough cache entry first (avoids a slow LLM call when
            # results are recent), then fall back to live detection.
            cached = db.get_cached_gaps(work_id, max_age_seconds=3600)
            if cached is not None:
                gaps = cached.get("gaps", [])
                evaluated = True
            else:
                # No cache or stale — run detection now so the gate is authoritative.
                report = detect_hygiene(work_id, db)
                gaps = [
                    {
                        "kind": g.kind,
                        "severity": g.severity,
                        "title": g.title,
                        "description": g.description,
                    }
                    for g in report.findings
                ]
                # Write result back to cache for subsequent requests.
                try:
                    db.cache_work_gaps(
                        work_id,
                        gaps,
                        report.coverage_pct,
                        suggested_queries=report.suggested_queries,
                    )
                except Exception:
                    pass
                evaluated = True
        except Exception as exc:
            # Genuine evaluation failure — log and skip (fail-open).
            # This must not be used as a bypass: log so it is visible.
            logger.warning(
                "Gap detection failed for work %s during gate check %s→%s: %s",
                work_id[:8],
                current,
                next_state,
                exc,
            )

        if evaluated:
            high_gaps = [g for g in gaps if (g.get("severity") or "").lower() == "high"]
            if high_gaps:
                return {
                    "gate": f"{current}→{next_state}",
                    "metric": "high_gaps",
                    "threshold": 0,
                    "actual": len(high_gaps),
                    "detail": (
                        f"{current}→{next_state} requires no high-severity gaps — "
                        f"{len(high_gaps)} open. Resolve them in the Gaps view first."
                    ),
                }

    return None


@router.post("/works/{work_id}/pipeline/advance")
def advance_pipeline(work_id: str):
    """Advance the book pipeline one stage forward through the B0–B17 lifecycle.

    Checks stage-specific readiness gates (completeness, gap severity, document
    count) before allowing the transition.  Returns 409 with a structured body
    ``{detail, gate, metric, threshold, actual}`` when a gate is not met.
    Returns 409 with ``{detail, blockers}`` when MONARCH findings block it.
    Returns 422 if the pipeline is already at a terminal state.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    pipeline = db.get_book_pipeline_for_work(work_id)
    if not pipeline:
        raise HTTPException(404, "No pipeline for this Work — call POST /pipeline first")

    from orivellum.capabilities.state_machine import (
        BOOK_SM,
        BlockedTransitionError,
        InvalidTransitionError,
        TransitionConflictError,
        apply_transition,
    )

    current = pipeline["status"]
    allowed = BOOK_SM.allowed_from(current)
    if not allowed:
        raise HTTPException(
            422, f"Pipeline is at terminal state {current!r} — no further transitions"
        )

    # BOOK_SM is strictly sequential; exactly one next state
    next_state = next(iter(allowed))

    # ── Stage gate check ───────────────────────────────────────────────────────
    # Use JSONResponse so the body is a flat dict — HTTPException(409, dict) would
    # nest it under {"detail": dict} which the frontend cannot destructure cleanly.
    gate_fail = _check_stage_gate(current, next_state, work_id, db, pipeline_id=pipeline["id"])
    if gate_fail:
        return JSONResponse(status_code=409, content=gate_fail)

    try:
        apply_transition(
            db,
            BOOK_SM,
            object_id=pipeline["id"],
            object_type="book_pipeline",
            table="book_pipelines",
            state_col="status",
            from_state=current,
            to_state=next_state,
            actor="user",
            detail=f"Manual advance: {current}→{next_state}",
        )
    except InvalidTransitionError as exc:
        raise HTTPException(422, str(exc)) from exc
    except BlockedTransitionError as exc:
        # Flat JSON so frontend can read body.detail (string) + body.blockers (list)
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "blockers": exc.blockers},
        )
    except TransitionConflictError as exc:
        # Someone else advanced the pipeline between our read and write.
        raise HTTPException(409, str(exc)) from exc

    return {"pipeline": db.get_book_pipeline_for_work(work_id)}


# ─── Pipeline stage worker ────────────────────────────────────────────────────


@router.post("/works/{work_id}/pipeline/run-stage")
async def run_pipeline_stage(work_id: str):
    """Run the AI stage worker for the pipeline's current stage.

    Compiles context from the Work's documents and knowledge, calls the LLM,
    stores the result as a ``pipeline_artifact``, and returns it.

    For B6 (Continuity Review) and B7 (Fact Check), also creates governance
    findings on the pipeline for each detected issue; those findings will block
    ``advance_pipeline`` via the state-machine blocker check until resolved.

    Returns 409 when the current stage has no worker (e.g. B4 Chapter
    Extraction, B5 Chapter Drafting, and B8 onward).
    The endpoint blocks until the LLM call completes (up to 45 s).
    """
    from starlette.concurrency import run_in_threadpool

    from orivellum.api._deps import get_config

    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    pipeline = db.get_book_pipeline_for_work(work_id)
    if not pipeline:
        raise HTTPException(404, "No pipeline for this Work — call POST /pipeline first")

    stage = pipeline["status"]

    try:
        from orivellum.capabilities.pipeline_workers import (
            _STAGE_CFG,
        )
        from orivellum.capabilities.pipeline_workers import (
            run_stage_worker as _run_worker,
        )
    except ImportError as exc:
        raise internal_error(logger, exc, "load pipeline_workers module") from exc

    if stage not in _STAGE_CFG:
        available = ", ".join(sorted(_STAGE_CFG))
        raise HTTPException(
            409,
            f"No AI worker defined for stage {stage!r}. Workers are available for: {available}.",
        )

    cfg = get_config()

    try:
        await run_in_threadpool(_run_worker, pipeline["id"], stage, db, cfg)
    except Exception as exc:
        # Artifact is already stored with status='failed'; return 422 so the
        # frontend can show the error without clobbering previous state.
        artifact = db.get_pipeline_artifact(pipeline["id"], stage)
        return JSONResponse(
            status_code=422,
            content={
                "detail": f"Stage worker failed: {exc}",
                "stage": stage,
                "status": "failed",
                "artifact": artifact,
            },
        )

    artifact = db.get_pipeline_artifact(pipeline["id"], stage)
    return {"stage": stage, "status": "done", "artifact": artifact}


# ─── Divergent Thinking (Brainstorm) ─────────────────────────────────────────


class BrainstormRequest(BaseModel):
    """FA-09: typed request body for a brainstorm session."""

    model_config = ConfigDict(extra="forbid")
    seed_prompt: str = Field(default="", max_length=4000)
    context_type: str = Field(default="general", max_length=100)
    n_domains: int = 5


@router.get("/works/{work_id}/brainstorm")
async def list_brainstorm_sessions(work_id: str, limit: int = 20):
    """Return the brainstorm session history for a Work (newest first)."""
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    sessions = db.list_brainstorm_sessions(work_id, limit=limit)
    return sessions


@router.post("/works/{work_id}/brainstorm")
async def run_brainstorm(
    work_id: str,
    payload: BrainstormRequest = BrainstormRequest(),
):
    """Start and complete a divergent thinking brainstorm session.

    Runs ``n_domains`` (default 5) parallel domain-shift LLM workers, scores
    originality against the Work's knowledge baseline, rates usefulness with a
    secondary LLM judge, and returns the Pareto-front ideas.

    Blocks until complete (up to ~45 s). The session is persisted so history
    is available via ``GET /api/works/{id}/brainstorm``.
    """
    from datetime import datetime

    from starlette.concurrency import run_in_threadpool

    from orivellum.api._deps import get_config
    from orivellum.capabilities.brainstorm import run_brainstorm_session

    seed_prompt = (payload.seed_prompt or "").strip()
    context_type = payload.context_type or "general"
    n_domains = int(payload.n_domains or 5)

    if not seed_prompt:
        raise HTTPException(422, "seed_prompt is required")

    db = get_db()
    cfg = get_config()

    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    # Create the session record immediately (status='running')
    session = db.create_brainstorm_session(
        work_id=work_id,
        seed_prompt=seed_prompt,
        context_type=context_type,
        n_domains=n_domains,
    )
    session_id = session["id"]

    try:
        ideas = await run_in_threadpool(
            run_brainstorm_session,
            session_id,
            work_id,
            seed_prompt,
            context_type,
            db,
            cfg,
            n_domains,
        )
        db.update_brainstorm_session(
            session_id,
            status="done",
            ideas=ideas,
            completed_at=datetime.now(UTC).isoformat(),
        )
    except Exception as exc:
        db.update_brainstorm_session(session_id, status="failed", ideas=[])
        raise HTTPException(502, f"Brainstorm failed: {exc}") from exc

    return db.get_brainstorm_session(session_id)


@router.get("/works/{work_id}/brainstorm/{session_id}")
async def get_brainstorm_session(work_id: str, session_id: str):
    """Return a specific brainstorm session."""
    db = get_db()
    session = db.get_brainstorm_session(session_id)
    if not session or session["work_id"] != work_id:
        raise HTTPException(404, "Brainstorm session not found")
    return session


@router.post("/works/{work_id}/brainstorm/{session_id}/ideas/{idea_id}/approve")
async def approve_brainstorm_idea(work_id: str, session_id: str, idea_id: str):
    """Promote an idea from a brainstorm session into a knowledge item.

    Idempotent: if the idea already has a knowledge_item_id, returns it as-is.
    The knowledge item is created with review_status='approved' so it appears
    in the standard knowledge list immediately.
    """

    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    session = db.get_brainstorm_session(session_id)
    if not session or session["work_id"] != work_id:
        raise HTTPException(404, "Brainstorm session not found")

    # Find the idea
    ideas = session["ideas"]
    idea = next((i for i in ideas if i["id"] == idea_id), None)
    if not idea:
        raise HTTPException(404, f"Idea {idea_id!r} not found in session")

    # Idempotent — already promoted
    if idea.get("knowledge_item_id"):
        return {"knowledge_item_id": idea["knowledge_item_id"]}

    # Create knowledge item
    ki_id = db.create_knowledge_item(
        work_id=work_id,
        text=idea["text"],
        kind="insight",
        review_status="approved",
        confidence=round(
            min(1.0, (idea.get("originality", 0.5) + idea.get("usefulness", 3) / 5) / 2 + 0.25), 2
        ),
        meta={
            "source": "brainstorm",
            "brainstorm_session_id": session_id,
            "brainstorm_idea_id": idea_id,
            "domain": idea.get("domain", ""),
            "originality": idea.get("originality", 0.5),
            "usefulness": idea.get("usefulness", 3),
        },
    )

    # Update the ideas list in the session with the new knowledge_item_id
    updated_ideas = [{**i, "knowledge_item_id": ki_id} if i["id"] == idea_id else i for i in ideas]
    db.update_brainstorm_session(
        session_id,
        status=session["status"],
        ideas=updated_ideas,
        completed_at=session.get("completed_at"),
    )

    return {"knowledge_item_id": ki_id, "session_id": session_id, "idea_id": idea_id}


# ─── Evidence rescore ─────────────────────────────────────────────────────────


@router.post("/works/{work_id}/evidence/rescore")
async def evidence_rescore(work_id: str):
    """Re-score confidence and detect contradictions for a Work's knowledge items.

    Returns the number of items whose confidence changed, the number of new
    conflict pairs detected, and elapsed wall-clock time.
    """
    import time

    from starlette.concurrency import run_in_threadpool

    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    items = db.list_knowledge(work_id=work_id, limit=1)
    if not items:
        raise HTTPException(422, "This Work has no knowledge items to rescore.")

    t0 = time.monotonic()
    from orivellum.capabilities.evidence import detect_contradictions, rescore_work

    rescored = await run_in_threadpool(rescore_work, work_id, db)
    conflict_count = await run_in_threadpool(detect_contradictions, work_id, db)

    # Stamp the rescore time so the nightshift pass can skip recently-rescored works
    from datetime import datetime

    db.set_setting(f"evidence_rescore:{work_id}", datetime.now(UTC).isoformat())

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    return {
        "work_id": work_id,
        "rescored_count": rescored,
        "conflict_count": conflict_count,
        "elapsed_ms": elapsed_ms,
    }


# ─── Trailer Architect ────────────────────────────────────────────────────────


@router.post("/works/{work_id}/trailer")
async def create_trailer(
    work_id: str,
    format: str = Query(
        default="all",
        description=(
            "Package format: "
            "'full' (75 s 16:9 landscape), "
            "'short' (30 s 9:16 social — Reels/TikTok/Shorts), "
            "'square' (30 s 1:1 — Instagram Feed/LinkedIn), "
            "'both' (full + short, legacy), or "
            "'all' (all three formats in one job — default)."
        ),
        pattern="^(full|short|square|both|all)$",
    ),
):
    """Enqueue a Trailer Architect job for a Work.

    Returns immediately with the new trailer record (status='running').
    The pipeline runs in the background via the thread-pool executor.

    format='full'   → standard 75 s 16:9 landscape package only
    format='short'  → 30 s 9:16 social clip only (Reels/TikTok/Shorts)
    format='square' → 30 s 1:1 square crop (Instagram Feed/LinkedIn/Twitter/X)
    format='both'   → full + short in one job (legacy; prefer 'all')
    format='all'    → all three formats in one job (default)

    The Work must have at least one document with readiness='ready' and
    non-empty extracted_text so the pipeline has content to analyse.
    Deleted works are also rejected.
    """
    from starlette.concurrency import run_in_threadpool

    db = get_db()
    work = db.get_work(work_id)
    if not work:
        raise HTTPException(404, f"Work {work_id!r} not found")

    # Eligibility guard: work must have at least one ready, text-bearing document
    with db._lock:
        row = db._conn.execute(
            """SELECT COUNT(*) FROM documents d
               JOIN objects o ON o.id = d.id
               WHERE d.work_id = ?
                 AND d.readiness = 'ready'
                 AND d.extracted_text IS NOT NULL
                 AND LENGTH(d.extracted_text) > 0
                 AND o.lifecycle != 'deleted'""",
            (work_id,),
        ).fetchone()
    eligible_docs = row[0] if row else 0
    if eligible_docs == 0:
        raise HTTPException(
            422,
            "Trailers require at least one processed document with extracted text. "
            "Import and process a document for this Work first.",
        )

    # Create the trailer record at 'running' immediately
    trailer = db.create_trailer(work_id)
    trailer_id = trailer["id"]
    fmt = format  # avoid shadowing builtin after this point

    # Launch the pipeline in the background (fire-and-forget)
    async def _run_bg() -> None:
        try:
            from orivellum.capabilities.trailer import run_trailer_pipeline

            await run_in_threadpool(run_trailer_pipeline, db, work_id, trailer_id, fmt)
        except Exception:
            import traceback

            logger.error("Trailer background task crashed:\n%s", traceback.format_exc())

    import asyncio

    asyncio.create_task(_run_bg())

    return {
        "trailer_id": trailer_id,
        "work_id": work_id,
        "status": "running",
        "phase": "loading",
        "format": fmt,
        "message": (
            f"Trailer Architect pipeline started (format={fmt!r}). "
            "Poll GET /works/{id}/trailers/{pkg_id} for progress."
        ),
    }


@router.get("/works/{work_id}/trailers")
def list_trailers(work_id: str):
    """Return all trailer packages for a Work, newest first.

    Each item includes id, status, phase, created_at, updated_at.
    The full package_json is NOT included in list responses — fetch
    GET /works/{id}/trailers/{pkg_id} for the full production package.
    """
    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    trailers = db.list_trailers(work_id)
    # Strip heavy package_json from list response
    slim = [
        {
            "id": t["id"],
            "work_id": t["work_id"],
            "status": t["status"],
            "phase": t["phase"],
            "has_package": bool(t.get("package_json")),
            "error": t.get("error"),
            "created_at": t["created_at"],
            "updated_at": t["updated_at"],
        }
        for t in trailers
    ]
    return {"work_id": work_id, "trailers": slim, "count": len(slim)}


@router.get("/works/{work_id}/trailers/{trailer_id}")
def get_trailer(work_id: str, trailer_id: str):
    """Return the full production package for a trailer.

    When status='running' the package_json will be null and phase will
    indicate which pipeline stage is in progress.
    """
    import json as _json

    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")

    trailer = db.get_trailer(trailer_id)
    if not trailer or trailer["work_id"] != work_id:
        raise HTTPException(404, f"Trailer {trailer_id!r} not found for this Work")

    pkg = None
    if trailer.get("package_json"):
        try:
            pkg = _json.loads(trailer["package_json"])
        except Exception:
            pkg = None

    return {
        "id": trailer["id"],
        "work_id": trailer["work_id"],
        "status": trailer["status"],
        "phase": trailer["phase"],
        "error": trailer.get("error"),
        "created_at": trailer["created_at"],
        "updated_at": trailer["updated_at"],
        "package": pkg,
    }


@router.get("/works/{work_id}/trailers/{trailer_id}/export")
def export_trailer(work_id: str, trailer_id: str):
    """Download the trailer production package as a ZIP.

    Contains the production documents (script, shot list, narration, …) as
    Markdown, shot prompts as plain text, and the full package as JSON —
    per format (full/short/square) when the package is a combined envelope.
    """
    import io
    import json as _json
    import zipfile

    from fastapi.responses import Response

    db = get_db()
    if not db.get_work(work_id):
        raise HTTPException(404, f"Work {work_id!r} not found")
    trailer = db.get_trailer(trailer_id)
    if not trailer or trailer["work_id"] != work_id:
        raise HTTPException(404, f"Trailer {trailer_id!r} not found for this Work")
    if not trailer.get("package_json"):
        raise HTTPException(
            409,
            f"No package to export — trailer status is {trailer['status']!r}"
            + (f": {trailer['error']}" if trailer.get("error") else ""),
        )
    try:
        pkg = _json.loads(trailer["package_json"])
    except Exception as e:
        raise HTTPException(500, "Stored package is corrupt") from e

    # Combined envelopes carry sub-packages per format; flat ones are single.
    fmt = pkg.get("format")
    if fmt in ("both", "all"):
        subs = {k: pkg[k] for k in ("full", "short", "square") if pkg.get(k)}
    else:
        subs = {fmt or "full": pkg}

    def _write_sub(zf: zipfile.ZipFile, prefix: str, sub: dict) -> None:
        for name, text in (sub.get("docs") or {}).items():
            if isinstance(text, str):
                zf.writestr(f"{prefix}docs/{name}.md", text)
        # shot_prompts is a mapping (shot_00 → prompt) in current packages;
        # keep a list fallback for any historical data.
        prompts = sub.get("shot_prompts") or {}
        lines: list[str] = []
        if isinstance(prompts, dict):
            for label, p in prompts.items():
                lines.append(f"[{label}] {p if isinstance(p, str) else _json.dumps(p)}")
        elif isinstance(prompts, list):
            for i, p in enumerate(prompts, start=1):
                lines.append(f"[{i:02d}] {p if isinstance(p, str) else _json.dumps(p)}")
        if lines:
            zf.writestr(f"{prefix}shot-prompts.txt", "\n\n".join(lines) + "\n")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("package.json", _json.dumps(pkg, indent=2))
        if len(subs) == 1:
            _write_sub(zf, "", next(iter(subs.values())))
        else:
            for key, sub in subs.items():
                _write_sub(zf, f"{key}/", sub)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="trailer-{trailer_id[:8]}-package.zip"'
        },
    )
