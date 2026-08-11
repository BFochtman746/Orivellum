"""Playbooks — data-defined multi-step operations.

A playbook is just a title plus an ordered list of step definitions; starting
one copies its steps into the operations tables, so edits here never affect
runs already in flight.

Two kinds exist:
- Starter playbooks: the built-in ``PLAYBOOKS`` list below.
- Custom playbooks: user-saved plans (usually born from an AI-planned job),
  persisted in the ``custom_playbooks`` table. Steps are validated against
  the action registry BEFORE saving, so a stored playbook can never reference
  actions or parameters that don't exist.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

PLAYBOOKS: list[dict] = [
    {
        "id": "work_audiobook",
        "title": "Turn a Work into an audiobook",
        "description": (
            "Waits for every document in the Work to finish processing, renders "
            "the full audiobook, then notifies you when it's ready."
        ),
        "steps": [
            {
                "action_id": "wait_for_extraction",
                "label": "Wait for documents to finish processing",
            },
            {"action_id": "render_audiobook", "label": "Render the audiobook"},
            {
                "action_id": "notify",
                "label": "Let me know it's done",
                "params": {
                    "title": "Audiobook ready",
                    "body": "Your audiobook has finished rendering.",
                },
            },
        ],
    },
    {
        "id": "work_study_pack",
        "title": "Build a study plan for a Work",
        "description": (
            "Waits for the Work's documents to finish processing, then generates "
            "a prioritised study plan and notifies you."
        ),
        "steps": [
            {
                "action_id": "wait_for_extraction",
                "label": "Wait for documents to finish processing",
            },
            {"action_id": "action:study_plan", "label": "Generate the study plan"},
            {
                "action_id": "notify",
                "label": "Let me know it's done",
                "params": {
                    "title": "Study plan ready",
                    "body": "Your study plan is waiting in the Learn home.",
                },
            },
        ],
    },
    {
        "id": "work_book_export",
        "title": "Export a Work as a book",
        "description": (
            "Waits for the Work's documents to finish processing, exports the "
            "book, then notifies you with the download."
        ),
        "steps": [
            {
                "action_id": "wait_for_extraction",
                "label": "Wait for documents to finish processing",
            },
            {"action_id": "action:book_export", "label": "Export the book"},
            {
                "action_id": "notify",
                "label": "Let me know it's done",
                "params": {
                    "title": "Book export ready",
                    "body": "Your book export is ready to download.",
                },
            },
        ],
    },
]


def get_playbook(playbook_id: str, db: Any = None) -> dict | None:
    """Look up a playbook by id — built-ins first, then saved custom ones."""
    for p in PLAYBOOKS:
        if p["id"] == playbook_id:
            return p
    if db is not None and playbook_id.startswith("custom_"):
        for p in list_custom_playbooks(db):
            if p["id"] == playbook_id:
                return p
    return None


# ── Custom (user-saved) playbooks ────────────────────────────────────────────


def _row_to_playbook(row: Any) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "steps": json.loads(row["steps"]),
        "created_at": row["created_at"],
        "custom": True,
    }


def list_custom_playbooks(db: Any) -> list[dict]:
    with db._lock:
        rows = db._conn.execute(
            "SELECT * FROM custom_playbooks ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_playbook(r) for r in rows]


def save_custom_playbook(db: Any, title: str, steps: list[dict], description: str = "") -> dict:
    """Validate *steps* against the action registry, then persist. Raises ValueError."""
    from orivellum.capabilities.operations.planner import validate_steps
    from orivellum.capabilities.operations.registry import get_op_registry

    title = (title or "").strip()
    if not title:
        raise ValueError("A playbook needs a name.")
    problems = validate_steps(steps, get_op_registry())
    if problems:
        raise ValueError("Invalid steps: " + "; ".join(problems))

    pb_id = f"custom_{uuid.uuid4().hex[:10]}"
    now = datetime.now(UTC).isoformat()
    clean_steps = [
        {
            "action_id": s["action_id"],
            "label": (s.get("label") or s["action_id"])[:120],
            "params": s.get("params") or {},
        }
        for s in steps
    ]
    with db._lock:
        db._conn.execute(
            "INSERT INTO custom_playbooks (id, title, description, steps, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (pb_id, title[:120], (description or "")[:400], json.dumps(clean_steps), now),
        )
        db._conn.commit()
    return {
        "id": pb_id,
        "title": title[:120],
        "description": (description or "")[:400],
        "steps": clean_steps,
        "created_at": now,
        "custom": True,
    }


def delete_custom_playbook(db: Any, playbook_id: str) -> bool:
    with db._lock:
        cur = db._conn.execute("DELETE FROM custom_playbooks WHERE id=?", (playbook_id,))
        db._conn.commit()
    return cur.rowcount > 0
