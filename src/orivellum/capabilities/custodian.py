"""Proactive custodian — nightshift pass that surfaces real work nudges.

Checks each Work for staleness signals (untouched for N days at a known pipeline
stage) and writes nudge records to ``work_nudges``.  The dashboard fetches the
top nudge and surfaces it as an actionable card.

Nudge triggers
--------------
* ``stalled`` — Work has documents + knowledge but zero activity in > STALL_DAYS days.
* ``no_docs``  — Work created > NEWBORN_DAYS days ago but still has zero documents.
* ``pipeline_stuck`` — Work has a book pipeline but hasn't advanced in > STUCK_DAYS days.

Idempotent: re-running within the same day does not duplicate nudges.
Old resolved nudges are pruned after PRUNE_DAYS days.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.custodian")

# ── Thresholds ─────────────────────────────────────────────────────────────────
STALL_DAYS    = 14   # days of silence → "stalled" nudge
NEWBORN_DAYS  = 7    # days after creation with no docs → "no_docs" nudge
STUCK_DAYS    = 10   # days at same pipeline stage → "pipeline_stuck" nudge
PRUNE_DAYS    = 30   # days before resolved nudges are pruned
SUPPRESS_DAYS = 30   # days to honour an explicit user dismissal before re-nudging


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _uid() -> str:
    return str(uuid.uuid4())


def _days_since(iso: str | None) -> int | None:
    """Return days since an ISO timestamp, or None if timestamp is None."""
    if not iso:
        return None
    try:
        ts = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return max(0, (_now_utc() - ts).days)
    except Exception:
        return None


# ── Last-activity query ────────────────────────────────────────────────────────

def _get_work_last_activity(db: "OrivellumDB", work_id: str) -> str | None:
    """Return the ISO timestamp of the most recent activity for a Work.

    Checks:
    - Latest message in any conversation linked to this Work
    - Latest document added to this Work (via objects table)
    - Latest task update on this Work
    """
    candidates: list[str] = []
    with db._lock:
        # Latest conversation message
        row = db._conn.execute(
            """SELECT MAX(m.created_at) as last_msg
               FROM messages m
               JOIN conversations c ON c.id = m.conversation_id
               WHERE c.work_id = ?""",
            (work_id,),
        ).fetchone()
        if row and row["last_msg"]:
            candidates.append(row["last_msg"])

        # Latest document linked to this work
        row = db._conn.execute(
            """SELECT MAX(o.created_at) as last_doc
               FROM objects o
               JOIN documents d ON d.id = o.id
               WHERE d.work_id = ?""",
            (work_id,),
        ).fetchone()
        if row and row["last_doc"]:
            candidates.append(row["last_doc"])

        # Latest task activity on this work (completed_at wins over created_at)
        row = db._conn.execute(
            """SELECT MAX(COALESCE(completed_at, created_at)) as last_task
               FROM tasks WHERE work_id = ?""",
            (work_id,),
        ).fetchone()
        if row and row["last_task"]:
            candidates.append(row["last_task"])

    if not candidates:
        return None
    return max(candidates)


def _get_active_nudge(db: "OrivellumDB", work_id: str, kind: str) -> dict | None:
    """Return the existing unresolved nudge for this (work, kind) pair, or None."""
    with db._lock:
        row = db._conn.execute(
            """SELECT * FROM work_nudges
               WHERE work_id=? AND kind=? AND resolved_at IS NULL
               LIMIT 1""",
            (work_id, kind),
        ).fetchone()
    return dict(row) if row else None


def _is_user_suppressed(db: "OrivellumDB", work_id: str, kind: str) -> bool:
    """Return True if the user explicitly dismissed this (work, kind) nudge recently.

    Suppression lasts SUPPRESS_DAYS days from the dismissal timestamp.  After
    that the nightly pass is free to re-nudge if the condition still holds.
    """
    cutoff = (_now_utc() - timedelta(days=SUPPRESS_DAYS)).isoformat()
    with db._lock:
        row = db._conn.execute(
            """SELECT id FROM work_nudges
               WHERE work_id=? AND kind=? AND user_dismissed=1
                 AND resolved_at >= ?
               LIMIT 1""",
            (work_id, kind, cutoff),
        ).fetchone()
    return bool(row)


def _upsert_nudge(
    db: "OrivellumDB",
    work_id: str,
    kind: str,
    message: str,
    stage: str | None,
    days_stalled: int | None,
    priority: int,
) -> tuple[str, bool]:
    """Create or refresh the one unresolved nudge for this (work, kind).

    Returns (nudge_id, was_created).

    Policy enforced here:
    - At most ONE unresolved nudge per (work_id, kind) at any time.
    - If an active (unresolved) nudge exists → refresh its message/metrics in-place.
    - If the user dismissed the nudge within SUPPRESS_DAYS → skip entirely.
    - Otherwise → insert a new nudge.
    """
    # 1. Honour explicit user dismissal (suppression window)
    if _is_user_suppressed(db, work_id, kind):
        # Return a sentinel id; was_created=False signals the caller to count as skipped
        return "", False

    # 2. Refresh existing active nudge
    existing = _get_active_nudge(db, work_id, kind)
    now = _now_utc().isoformat()
    if existing:
        with db._lock:
            db._conn.execute(
                """UPDATE work_nudges
                   SET message=?, stage=?, days_stalled=?, priority=?, created_at=?
                   WHERE id=?""",
                (message, stage, days_stalled, priority, now, existing["id"]),
            )
            db._conn.commit()
        return existing["id"], False

    # 3. Create a new nudge
    nid = _uid()
    with db._lock:
        db._conn.execute(
            """INSERT INTO work_nudges
               (id, work_id, kind, message, stage, days_stalled, priority, created_at, user_dismissed)
               VALUES (?,?,?,?,?,?,?,?,0)""",
            (nid, work_id, kind, message, stage, days_stalled, priority, now),
        )
        db._conn.commit()
    return nid, True


def _auto_resolve_nudge(db: "OrivellumDB", work_id: str, kind: str) -> bool:
    """Auto-resolve a nudge when its trigger condition has naturally cleared.

    Sets user_dismissed=0 so the record is distinguishable from an explicit
    user dismissal — the next nightly pass can re-nudge without the suppression
    window applying.
    Returns True if a nudge was resolved.
    """
    now = _now_utc().isoformat()
    with db._lock:
        cur = db._conn.execute(
            """UPDATE work_nudges
               SET resolved_at=?, user_dismissed=0
               WHERE work_id=? AND kind=? AND resolved_at IS NULL""",
            (now, work_id, kind),
        )
        db._conn.commit()
    return cur.rowcount > 0


def _get_pipeline_last_advanced(db: "OrivellumDB", pipeline_id: str) -> str | None:
    """Return the ISO timestamp of the most recent pipeline stage artifact.

    Using pipeline_artifacts.created_at is authoritative for 'when was the
    pipeline last advanced' because each stage write creates/replaces an artifact.
    Falls back to None if no artifacts exist yet.
    """
    with db._lock:
        row = db._conn.execute(
            "SELECT MAX(created_at) as last_adv FROM pipeline_artifacts WHERE pipeline_id=?",
            (pipeline_id,),
        ).fetchone()
    return row["last_adv"] if row else None


def _prune_old_nudges(db: "OrivellumDB") -> int:
    """Delete resolved nudges older than PRUNE_DAYS days."""
    cutoff = (_now_utc() - timedelta(days=PRUNE_DAYS)).isoformat()
    with db._lock:
        cur = db._conn.execute(
            "DELETE FROM work_nudges WHERE resolved_at IS NOT NULL AND resolved_at < ?",
            (cutoff,),
        )
        db._conn.commit()
    return cur.rowcount


def _check_one_work(db: "OrivellumDB", work: dict) -> tuple[int, int]:
    """Check a single Work for staleness signals and write/refresh any nudges.

    Policy: at most ONE unresolved nudge per (work_id, kind) at any time.
    - If the condition HOLDS   → upsert (create or refresh the existing nudge).
    - If the condition CLEARS  → auto-resolve the existing nudge.

    Returns (nudges_created, nudges_skipped) for this Work.
    Isolated so any exception here is caught by the outer loop.
    """
    created = 0
    skipped = 0

    title           = work.get("title") or "Untitled Work"
    work_id         = work["id"]
    doc_count       = work.get("doc_count") or 0
    knowledge_count = work.get("knowledge_count") or 0
    work_created    = work.get("obj_created") or work.get("created_at")

    # ── Signal 1: No documents after N days ────────────────────────────────
    if doc_count == 0:
        age = _days_since(work_created)
        if age is not None and age >= NEWBORN_DAYS:
            msg = (
                f'"{title}" was created {age} day{"s" if age != 1 else ""} ago '
                f"but has no documents yet. Import a file to get started."
            )
            _, was_created = _upsert_nudge(db, work_id, "no_docs", msg, None, age, priority=2)
            if was_created:
                created += 1
            else:
                skipped += 1
        else:
            # Condition cleared — auto-resolve if a nudge exists
            _auto_resolve_nudge(db, work_id, "no_docs")
        return created, skipped  # skip other checks — no docs → nothing to stall

    # Condition never applies when docs exist — clean up any stale no_docs nudge
    _auto_resolve_nudge(db, work_id, "no_docs")

    # ── Signal 2: Stalled with existing content ────────────────────────────
    last_activity = _get_work_last_activity(db, work_id)
    stall_days = _days_since(last_activity)
    if stall_days is not None and stall_days >= STALL_DAYS:
        msg = (
            f'"{title}" has been untouched for {stall_days} day{"s" if stall_days != 1 else ""}. '
            f"It has {doc_count} document{'s' if doc_count != 1 else ''} "
            f"and {knowledge_count} knowledge item{'s' if knowledge_count != 1 else ''}."
        )
        # Enrich with current pipeline stage if available
        stage_label: str | None = None
        try:
            pipeline = db.get_book_pipeline_for_work(work_id)
            if pipeline:
                stage_label = pipeline.get("status") or pipeline.get("current_stage")
                if stage_label:
                    msg += f" Currently at pipeline stage {stage_label}."
        except Exception:
            pass
        _, was_created = _upsert_nudge(
            db, work_id, "stalled", msg, stage_label, stall_days,
            priority=3 if stall_days >= STALL_DAYS * 2 else 2,
        )
        if was_created:
            created += 1
        else:
            skipped += 1
    else:
        # Condition cleared (user became active again) — auto-resolve
        _auto_resolve_nudge(db, work_id, "stalled")

    # ── Signal 3: Pipeline stuck ────────────────────────────────────────────
    # Authoritative advancement time = most recent pipeline_artifacts.created_at.
    # This correctly reflects when the user last advanced a stage, unlike
    # book_pipelines.updated_at which is not updated by stage transitions.
    try:
        pipeline = db.get_book_pipeline_for_work(work_id)
        if pipeline:
            pipeline_id = pipeline.get("id")
            last_adv = _get_pipeline_last_advanced(db, pipeline_id) if pipeline_id else None
            # Fall back to pipeline creation time when no artifacts exist yet
            last_adv = last_adv or pipeline.get("created_at")
            stuck_days = _days_since(last_adv)
            stage = pipeline.get("status") or "unknown"
            if stuck_days is not None and stuck_days >= STUCK_DAYS:
                msg = (
                    f'"{title}" book pipeline has been at stage {stage} '
                    f'for {stuck_days} day{"s" if stuck_days != 1 else ""}. '
                    f"Open the Work to advance it."
                )
                _, was_created = _upsert_nudge(
                    db, work_id, "pipeline_stuck", msg, stage, stuck_days, priority=2
                )
                if was_created:
                    created += 1
                else:
                    skipped += 1
            else:
                # Pipeline was recently advanced — clear any stale stuck nudge
                _auto_resolve_nudge(db, work_id, "pipeline_stuck")
        else:
            # No pipeline — clear any stale stuck nudge
            _auto_resolve_nudge(db, work_id, "pipeline_stuck")
    except Exception:
        pass  # pipeline table may not exist on old schemas

    return created, skipped


# ── Main entry point ───────────────────────────────────────────────────────────

def run_custodian(db: "OrivellumDB") -> dict:
    """Check all Works for staleness signals; write new nudges.

    Returns a summary dict for the nightshift report.
    """
    nudges_written = 0
    nudges_skipped = 0

    try:
        works = db.list_works(limit=200)
    except Exception as exc:
        logger.warning("Custodian: could not list works: %s", exc)
        return {"status": "error", "nudges_written": 0}

    for work in works:
        work_id = work.get("id")
        if not work_id:
            continue
        # ── Per-Work isolation: one bad work never aborts the whole pass ───────
        try:
            w, s = _check_one_work(db, work)
            nudges_written += w
            nudges_skipped += s
        except Exception as _wex:
            logger.warning("Custodian: skipping work %s due to error: %s", work_id, _wex)
            nudges_skipped += 1

    # Prune old resolved nudges
    pruned = _prune_old_nudges(db)

    logger.info(
        "Custodian complete: %d new, %d skipped, %d pruned",
        nudges_written, nudges_skipped, pruned,
    )
    return {
        "status": "ok",
        "nudges_written": nudges_written,
        "nudges_skipped": nudges_skipped,
        "pruned": pruned,
    }


def get_top_nudges(db: "OrivellumDB", limit: int = 5) -> list[dict]:
    """Return top unresolved nudges ordered by priority descending, then newest first."""
    with db._lock:
        rows = db._conn.execute(
            """SELECT n.*, w.title as work_title
               FROM work_nudges n
               LEFT JOIN works w ON w.id = n.work_id
               WHERE n.resolved_at IS NULL
               ORDER BY n.priority DESC, n.created_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def resolve_nudge(db: "OrivellumDB", nudge_id: str) -> bool:
    """Mark a nudge as user-dismissed.

    Sets user_dismissed=1 so the suppression window (SUPPRESS_DAYS) is honoured
    by subsequent nightly custodian passes.  The nudge will not reappear until
    SUPPRESS_DAYS days after dismissal even if the trigger condition persists.

    Returns True if the nudge was found and updated.
    """
    now = _now_utc().isoformat()
    with db._lock:
        cur = db._conn.execute(
            """UPDATE work_nudges
               SET resolved_at=?, user_dismissed=1
               WHERE id=? AND resolved_at IS NULL""",
            (now, nudge_id),
        )
        db._conn.commit()
    return cur.rowcount > 0
