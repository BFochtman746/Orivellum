"""Starter playbooks — data-defined multi-step operations.

A playbook is just a title plus an ordered list of step definitions; starting
one copies its steps into the operations tables, so edits here never affect
runs already in flight.
"""

from __future__ import annotations

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


def get_playbook(playbook_id: str) -> dict | None:
    for p in PLAYBOOKS:
        if p["id"] == playbook_id:
            return p
    return None
