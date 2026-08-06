"""
GENESIS Book Origination System — API routes.

All routes are scoped under /api/works/{work_id}/genesis/...

Endpoints:
  POST   /api/works/{id}/genesis                    init (or fetch existing) genesis book
  GET    /api/works/{id}/genesis                    full status: book + all stages + ledger length
  GET    /api/works/{id}/genesis/stages/{code}      artifact content + stage status
  PATCH  /api/works/{id}/genesis/stages/{code}      save artifact content
  POST   /api/works/{id}/genesis/stages/{code}/gate record gate decision (pass/fail)
  POST   /api/works/{id}/genesis/seal               seal the package (G9 final gate)
  GET    /api/works/{id}/genesis/verify             verify ledger hash chain
  GET    /api/works/{id}/genesis/techniques         brainstorm codex (all or per stage)
"""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from orivellum.api._deps import get_db
from orivellum.capabilities.genesis import (
    STAGES, STAGE_CODES, STAGE_BY_CODE, TEMPLATE_CONTENT,
    ledger_append, sha256_text, canonical, now_iso,
    get_stage_status, next_open_stage,
    compute_seal, verify_ledger,
)
from orivellum.capabilities.genesis.codex import CODEX_TEXT, get_codex_for_stage

router = APIRouter(prefix="/api/works", tags=["genesis"])


# ── Pydantic models ────────────────────────────────────────────────────────────

class GenesisInitRequest(BaseModel):
    mode: str = "cold"          # 'cold' | 'library'
    length: int = 80
    acts: int = 4


class ArtifactSaveRequest(BaseModel):
    content: str


class GateRequest(BaseModel):
    decision: str       # 'pass' | 'fail'
    author: str
    note: str = ""


class SealRequest(BaseModel):
    author: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_work_or_404(db, work_id: str):
    w = db._conn.execute("SELECT id, title FROM works WHERE id=?", (work_id,)).fetchone()
    if not w:
        raise HTTPException(404, f"Work {work_id!r} not found")
    return w


def _get_book_or_404(db, work_id: str):
    b = db._conn.execute(
        "SELECT * FROM genesis_books WHERE work_id=?", (work_id,)
    ).fetchone()
    if not b:
        raise HTTPException(404, "No GENESIS book for this Work. POST to initialize it.")
    return b


def _book_response(db, book, stage_status: dict[str, str]) -> dict:
    """Build the full status payload returned by GET /genesis."""
    nxt = next_open_stage(stage_status)
    ledger_count = db._conn.execute(
        "SELECT COUNT(*) FROM genesis_ledger WHERE book_id=?", (book["id"],)
    ).fetchone()[0]
    sealed = book["state"] in ("READY_FOR_B0", "PARKED", "KILLED")
    stages_out = []
    for code, name, tmpl_slug, gate_desc in STAGES:
        stages_out.append({
            "code": code,
            "name": name,
            "status": stage_status.get(code, "PENDING"),
            "gate_description": gate_desc,
            "is_current": code == nxt and not sealed,
        })
    return {
        "id": book["id"],
        "work_id": book["work_id"],
        "mode": book["mode"],
        "length": book["length"],
        "acts": book["acts"],
        "state": book["state"],
        "sealed": sealed,
        "manifest": book["manifest_json"],
        "created_at": book["created_at"],
        "updated_at": book["updated_at"],
        "stages": stages_out,
        "next_stage": nxt,
        "ledger_entries": ledger_count,
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("/{work_id}/genesis")
def init_genesis(work_id: str, req: GenesisInitRequest, db=Depends(get_db)):
    """Create a GENESIS book for this Work (idempotent — returns existing if already created)."""
    _get_work_or_404(db, work_id)
    if req.mode not in ("cold", "library"):
        raise HTTPException(422, "mode must be 'cold' or 'library'")
    if not (10 <= req.length <= 500):
        raise HTTPException(422, "length must be 10–500")
    if req.acts not in (3, 4, 5):
        raise HTTPException(422, "acts must be 3, 4, or 5")

    # Idempotent
    existing = db._conn.execute(
        "SELECT * FROM genesis_books WHERE work_id=?", (work_id,)
    ).fetchone()
    if existing:
        ss = get_stage_status(db._conn, existing["id"])
        return _book_response(db, existing, ss)

    book_id = str(uuid.uuid4())
    at = now_iso()
    db._conn.execute(
        "INSERT INTO genesis_books (id, work_id, mode, length, acts, state, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (book_id, work_id, req.mode, req.length, req.acts, "G0", at, at),
    )

    # Pre-create stage rows and artifacts from templates
    for code, _name, tmpl_slug, _gate in STAGES:
        stage_id = str(uuid.uuid4())
        db._conn.execute(
            "INSERT INTO genesis_stages (id, book_id, stage_code, status) VALUES (?,?,?,?)",
            (stage_id, book_id, code, "PENDING"),
        )
        art_content = TEMPLATE_CONTENT.get(tmpl_slug, "")
        art_sha = sha256_text(art_content)
        art_id = str(uuid.uuid4())
        db._conn.execute(
            "INSERT INTO genesis_artifacts (id, book_id, stage_code, content, sha256, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (art_id, book_id, code, art_content, art_sha, at),
        )

    # Seed the ledger
    w = _get_work_or_404(db, work_id)
    ledger_append(db._conn, book_id, "book.created", {
        "book_id": book_id,
        "work_id": work_id,
        "title": w["title"],
        "mode": req.mode,
        "length": req.length,
        "acts": req.acts,
    })

    db._conn.commit()
    book = db._conn.execute("SELECT * FROM genesis_books WHERE id=?", (book_id,)).fetchone()
    ss = get_stage_status(db._conn, book_id)
    return _book_response(db, book, ss)


@router.get("/{work_id}/genesis")
def get_genesis(work_id: str, db=Depends(get_db)):
    """Full GENESIS status: book metadata + all 10 stage statuses."""
    _get_work_or_404(db, work_id)
    book = _get_book_or_404(db, work_id)
    ss = get_stage_status(db._conn, book["id"])
    return _book_response(db, book, ss)


@router.get("/{work_id}/genesis/stages/{code}")
def get_stage(work_id: str, code: str, db=Depends(get_db)):
    """Return artifact content and status for one stage."""
    _get_work_or_404(db, work_id)
    book = _get_book_or_404(db, work_id)
    code = code.upper()
    if code not in STAGE_BY_CODE:
        raise HTTPException(422, f"Unknown stage {code!r}. Valid: {', '.join(STAGE_CODES)}")

    ss = get_stage_status(db._conn, book["id"])
    art = db._conn.execute(
        "SELECT content, sha256, updated_at FROM genesis_artifacts WHERE book_id=? AND stage_code=?",
        (book["id"], code),
    ).fetchone()
    content = art["content"] if art else TEMPLATE_CONTENT.get(STAGE_BY_CODE[code][2], "")
    has_fills = "<<FILL>>" in content

    # Gate decisions for this stage (append-only log)
    decisions = [
        dict(r) for r in db._conn.execute(
            "SELECT kind, payload, at FROM genesis_ledger "
            "WHERE book_id=? AND kind IN ('gate.pass','gate.fail') "
            "ORDER BY seq",
            (book["id"],),
        ).fetchall()
        if f'"code":"{code}"' in r["payload"] or f'"code": "{code}"' in r["payload"]
    ]

    _, name, _, gate_desc = STAGE_BY_CODE[code]
    return {
        "code": code,
        "name": name,
        "gate_description": gate_desc,
        "status": ss.get(code, "PENDING"),
        "content": content,
        "has_unfilled_placeholders": has_fills,
        "sha256": art["sha256"] if art else "",
        "updated_at": art["updated_at"] if art else None,
        "decisions": decisions,
    }


@router.patch("/{work_id}/genesis/stages/{code}")
def save_stage(work_id: str, code: str, req: ArtifactSaveRequest, db=Depends(get_db)):
    """Save (upsert) the artifact markdown for a stage."""
    _get_work_or_404(db, work_id)
    book = _get_book_or_404(db, work_id)
    code = code.upper()
    if code not in STAGE_BY_CODE:
        raise HTTPException(422, f"Unknown stage {code!r}")
    if book["state"] in ("READY_FOR_B0",):
        raise HTTPException(409, "Book is sealed; artifacts are read-only.")

    at = now_iso()
    content_sha = sha256_text(req.content)
    art_id = str(uuid.uuid4())
    db._conn.execute(
        "INSERT INTO genesis_artifacts (id, book_id, stage_code, content, sha256, updated_at) "
        "VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(book_id, stage_code) DO UPDATE SET "
        "content=excluded.content, sha256=excluded.sha256, updated_at=excluded.updated_at",
        (art_id, book["id"], code, req.content, content_sha, at),
    )
    db._conn.execute(
        "UPDATE genesis_books SET updated_at=? WHERE id=?", (at, book["id"])
    )
    db._conn.commit()
    return {"ok": True, "sha256": content_sha, "updated_at": at}


@router.post("/{work_id}/genesis/stages/{code}/gate")
def record_gate(work_id: str, code: str, req: GateRequest, db=Depends(get_db)):
    """Record a gate decision (pass/fail) for a stage."""
    _get_work_or_404(db, work_id)
    book = _get_book_or_404(db, work_id)
    code = code.upper()
    if code not in STAGE_BY_CODE:
        raise HTTPException(422, f"Unknown stage {code!r}")
    if book["state"] in ("READY_FOR_B0", "KILLED"):
        raise HTTPException(409, f"Book is {book['state']}; gates are closed.")
    if req.decision not in ("pass", "fail"):
        raise HTTPException(422, "decision must be 'pass' or 'fail'")
    if not req.author.strip():
        raise HTTPException(422, "author is required")

    ss = get_stage_status(db._conn, book["id"])

    # Order enforcement — cannot pass until all prior stages are PASSED
    if req.decision == "pass":
        idx = STAGE_CODES.index(code)
        for prior in STAGE_CODES[:idx]:
            if ss.get(prior) != "PASSED":
                raise HTTPException(
                    422,
                    f"Blocked: {prior} must be PASSED before {code} can pass.",
                )

        # Check artifact is filled (no <<FILL>> placeholders)
        art = db._conn.execute(
            "SELECT content FROM genesis_artifacts WHERE book_id=? AND stage_code=?",
            (book["id"], code),
        ).fetchone()
        if not art or not art["content"] or "<<FILL>>" in art["content"]:
            raise HTTPException(
                422,
                f"Blocked: {code} artifact still contains <<FILL>> placeholders or is empty. "
                "Fill the template before passing the gate.",
            )

    # Append-only gate record via ledger
    ledger_append(db._conn, book["id"], f"gate.{req.decision}", {
        "code": code,
        "author": req.author,
        "note": req.note,
    })

    # Update stage status
    new_status = "PASSED" if req.decision == "pass" else "FAILED"
    db._conn.execute(
        "INSERT INTO genesis_stages (id, book_id, stage_code, status) VALUES (?,?,?,?) "
        "ON CONFLICT(book_id, stage_code) DO UPDATE SET status=excluded.status",
        (str(uuid.uuid4()), book["id"], code, new_status),
    )

    # Advance book state pointer
    ss[code] = new_status
    nxt = next_open_stage(ss)
    new_book_state = nxt if nxt else "ALL_GATES_PASSED"
    db._conn.execute(
        "UPDATE genesis_books SET state=?, updated_at=? WHERE id=?",
        (new_book_state, now_iso(), book["id"]),
    )
    db._conn.commit()

    return {
        "ok": True,
        "code": code,
        "decision": req.decision,
        "new_status": new_status,
        "book_state": new_book_state,
        "next_stage": nxt,
    }


@router.post("/{work_id}/genesis/seal")
def seal_book(work_id: str, req: SealRequest, db=Depends(get_db)):
    """Seal the origination package — final G9 gate + manifest + READY_FOR_B0 state."""
    _get_work_or_404(db, work_id)
    book = _get_book_or_404(db, work_id)
    if book["state"] == "READY_FOR_B0":
        raise HTTPException(409, "Book is already sealed.")
    if not req.author.strip():
        raise HTTPException(422, "author is required")

    w = _get_work_or_404(db, work_id)
    try:
        import json
        manifest = compute_seal(
            db._conn,
            book["id"],
            w["title"],
            book["length"],
            book["acts"],
            req.author,
        )
    except ValueError as e:
        raise HTTPException(422, str(e))

    at = now_iso()
    db._conn.execute(
        "UPDATE genesis_books SET state='READY_FOR_B0', manifest_json=?, updated_at=? WHERE id=?",
        (json.dumps(manifest), at, book["id"]),
    )
    db._conn.commit()
    return {"ok": True, "state": "READY_FOR_B0", "manifest": manifest}


@router.get("/{work_id}/genesis/verify")
def verify_chain(work_id: str, db=Depends(get_db)):
    """Verify the tamper-evident ledger hash chain for this book."""
    _get_work_or_404(db, work_id)
    book = _get_book_or_404(db, work_id)
    ok, message = verify_ledger(db._conn, book["id"])
    return {"ok": ok, "message": message}


@router.get("/{work_id}/genesis/techniques")
def techniques(work_id: str, stage: Optional[str] = None, db=Depends(get_db)):
    """Return the brainstorm codex (all stages or filtered to one)."""
    _get_work_or_404(db, work_id)
    if stage:
        s = stage.upper()
        if s not in STAGE_BY_CODE:
            raise HTTPException(422, f"Unknown stage {s!r}")
        return {"stage": s, "techniques": get_codex_for_stage(s)}
    return {"techniques": CODEX_TEXT}
