"""Orivellum lifecycle enums — M0.2 (Sovereign Platform).

Every status / state field in the system should use one of these enums.
String-valued (``str, enum.Enum``) so SQLite stores readable text and the
values can be compared directly with stored strings.

Adding a new state:
  1. Append it to the relevant enum.
  2. Update the ``TRANSITIONS`` dict in ``state_machine.py``.
  3. Add a schema migration if the DB column has a CHECK constraint.

Never remove a value — old rows in the DB may still reference it.
"""

from __future__ import annotations

import enum

# ---------------------------------------------------------------------------
# Message states (chat messages)
# ---------------------------------------------------------------------------


class MessageState(str, enum.Enum):
    """Lifecycle of a single assistant message from creation to completion.

    Graph::
        queued → running → streaming → done
        running → failed
        streaming → failed
    """

    queued = "queued"  # placeholder written before any model call
    running = "running"  # inference has started
    streaming = "streaming"  # tokens are flowing to the client
    done = "done"  # message is complete and immutable
    failed = "failed"  # unrecoverable error; surface reason + Retry


# ---------------------------------------------------------------------------
# Job states (background / async jobs)
# ---------------------------------------------------------------------------


class JobState(str, enum.Enum):
    """State of a background processing job (document pipeline, batch ops …).

    Graph::
        queued → running → done
        running → failed
        running → cancelled
        queued  → cancelled
    """

    queued = "queued"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


# ---------------------------------------------------------------------------
# Finding states (governance blockers)
# ---------------------------------------------------------------------------


class FindingState(str, enum.Enum):
    """State of a governance finding (blocker on a lifecycle transition).

    An ``open`` finding blocks all forward transitions on its target object
    until a human resolves it.  ``resolved`` findings are kept for audit.
    """

    open = "open"
    resolved = "resolved"


# ---------------------------------------------------------------------------
# Finding severity
# ---------------------------------------------------------------------------


class FindingSeverity(str, enum.Enum):
    """How urgently a finding must be resolved."""

    info = "info"  # informational only, does not block
    warning = "warning"  # advisory; does not block by default
    high = "high"  # blocks all forward transitions
    critical = "critical"  # blocks all transitions including backward


# ---------------------------------------------------------------------------
# Book production states (B0–B17)
# ---------------------------------------------------------------------------


class BookState(str, enum.Enum):
    """18-stage production lifecycle for a book chapter or full manuscript.

    The first 14 stages (B0–B13) match the writing_architect_pkg's verified
    pipeline.  B14–B17 cover post-publication phases.

    Forward transitions advance exactly one stage.  Backward transitions
    (returns) are always allowed but must carry a reason and are recorded.
    Any open BLOCKER-severity finding blocks forward advancement.

    ::
        B0  → B1  Intake complete
        B1  → B2  Outline approved
        B2  → B3  Research complete
        B3  → B4  First draft complete
        B4  → B5  Self-review passed
        B5  → B6  Peer review received
        B6  → B7  Revision complete
        B7  → B8  Copy edit complete
        B8  → B9  Proof read
        B9  → B10 Layout complete
        B10 → B11 Final check passed
        B11 → B12 Production approval
        B12 → B13 Published
        B13 → B14 Distributed
        B14 → B15 Errata period
        B15 → B16 Revision open
        B16 → B17 Archived / superseded
    """

    B0 = "B0"  # Intake
    B1 = "B1"  # Outline
    B2 = "B2"  # Research
    B3 = "B3"  # First Draft
    B4 = "B4"  # Self-Review
    B5 = "B5"  # Peer Review
    B6 = "B6"  # Revision
    B7 = "B7"  # Copy Edit
    B8 = "B8"  # Proof
    B9 = "B9"  # Layout
    B10 = "B10"  # Final Check
    B11 = "B11"  # Production Approval
    B12 = "B12"  # Published
    B13 = "B13"  # Distributed
    B14 = "B14"  # Post-Publication
    B15 = "B15"  # Errata
    B16 = "B16"  # Open Revision
    B17 = "B17"  # Archived / Superseded


# ---------------------------------------------------------------------------
# Document readiness states (existing; mirrored as enum for validation)
# ---------------------------------------------------------------------------


class DocumentReadiness(str, enum.Enum):
    """Processing readiness of a library document."""

    imported = "imported"  # uploaded, not yet processed
    ready = "ready"  # fully processed
    error = "error"  # processing failed
    no_text = "no_text"  # image/scan with no extractable text
    reprocessing = "reprocessing"  # in-flight re-extraction


# ---------------------------------------------------------------------------
# Document lifecycle states (existing; mirrored as enum for validation)
# ---------------------------------------------------------------------------


class DocumentLifecycle(str, enum.Enum):
    """Editorial lifecycle of a library document."""

    draft = "draft"  # default; work in progress
    canonical = "canonical"  # the authoritative version for a role
    superseded = "superseded"  # replaced by a newer canonical
    reference = "reference"  # kept for reference, not authoritative
    active = "active"  # legacy alias for draft
    deleted = "deleted"  # soft-deleted
