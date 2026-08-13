"""Journalled generation jobs — the server half of the iPhone continuity core.

Every streaming chat generation is wrapped in a *job*: a background task pumps
the SSE frames produced by the existing generator into

  1. a live relay queue that the original HTTP response tails (token-level
     latency is unchanged for a connected client), and
  2. the durable ``gen_events`` journal (schema v151), with token/thinking
     frames coalesced into chunk events so the write rate stays sane.

Because the *pump* — not the HTTP response — consumes the generator, a client
disconnect (iOS suspension, dead zone, Wi-Fi handoff) no longer aborts the
generation: the job runs to completion server-side, the assistant message row
is finalized exactly as before, and the client reconstructs the reply later by
replaying journal events after its last acknowledged sequence
(``GET /api/conversations/jobs/{job_id}/events``).

The pump holds a strong reference to its task in ``_TASKS`` (asyncio only keeps
weak refs); entries are removed when the task finishes.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# Strong refs to running pump tasks: {job_id: Task}
_TASKS: dict[str, asyncio.Task] = {}

_DATA_PREFIX = "data: "
# Coalescing thresholds for token/thinking journal chunks.
_FLUSH_CHARS = 512
_FLUSH_SECONDS = 1.0

# Sentinel pushed to the relay queue when the job is over.
_EOF = None

# Live relay bound: enough for bursty token streams to a healthy client, but
# small enough that an abandoned tail cannot hold a whole generation in RAM.
_RELAY_MAXSIZE = 512


class _Relay:
    """Bounded live channel between the pump and the HTTP tail.

    The journal is the durable recovery path — the relay only serves the
    token-level latency of a *connected* client.  When the tail is gone or too
    slow (queue full), the relay detaches: frames are dropped from then on and
    an EOF is left in the queue so a late-waking tail terminates instead of
    blocking forever.  Detaching never affects the pump or the journal.
    """

    def __init__(self, maxsize: int = _RELAY_MAXSIZE) -> None:
        self._q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.attached = True

    def push(self, frame: str | None) -> None:
        if not self.attached:
            return
        try:
            self._q.put_nowait(frame)
        except asyncio.QueueFull:
            # Tail stopped reading (suspended phone, dead connection).
            self.detach()

    def detach(self) -> None:
        """Stop relaying and guarantee the tail sees an EOF."""
        if not self.attached:
            return
        self.attached = False
        if self._q.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._q.get_nowait()
        with contextlib.suppress(asyncio.QueueFull):
            self._q.put_nowait(_EOF)

    def qsize(self) -> int:
        return self._q.qsize()

    async def get(self) -> str | None:
        return await self._q.get()


class _Coalescer:
    """Buffers token/thinking text into periodic journal chunk events."""

    def __init__(self, db: OrivellumDB, job_id: str) -> None:
        self._db = db
        self._job_id = job_id
        self._buf: dict[str, str] = {"token": "", "thinking": ""}
        self._since: dict[str, float] = {}

    def add(self, key: str, text: str) -> None:
        if not self._buf[key]:
            self._since[key] = time.monotonic()
        self._buf[key] += text
        if (
            len(self._buf[key]) >= _FLUSH_CHARS
            or (time.monotonic() - self._since[key]) >= _FLUSH_SECONDS
        ):
            self.flush(key)

    def flush(self, key: str | None = None) -> None:
        keys = [key] if key else ["token", "thinking"]
        for k in keys:
            if self._buf[k]:
                kind = "chunk" if k == "token" else "thinking"
                self._db.append_gen_event(self._job_id, kind, json.dumps({k: self._buf[k]}))
                self._buf[k] = ""


def _classify(payload: dict) -> str:
    """Journal event kind for a non-token SSE frame."""
    for key in (
        "activity",
        "code_progress",
        "sources",
        "clarify",
        "intent",
        "timeout",
        "cut_short",
        "pklos_correction",
        "job_id",
    ):
        if key in payload:
            return key
    if "message_id" in payload:
        return "meta"
    return "meta"


def _journal_frame(db: OrivellumDB, job_id: str, coalescer: _Coalescer, frame: str) -> bool:
    """Journal one SSE frame; returns True when the frame was [DONE]."""
    raw = frame[len(_DATA_PREFIX) :].strip() if frame.startswith(_DATA_PREFIX) else ""
    if raw == "[DONE]":
        coalescer.flush()
        db.append_gen_event(job_id, "done", "")
        return True
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    if "token" in payload:
        coalescer.add("token", str(payload["token"]))
    elif "thinking" in payload:
        coalescer.add("thinking", str(payload["thinking"]))
    else:
        coalescer.flush()
        db.append_gen_event(job_id, _classify(payload), json.dumps(payload))
        if isinstance(payload.get("message_id"), str):
            with contextlib.suppress(Exception):
                db.set_gen_job_message(job_id, payload["message_id"])
    return False


def _settle_idempotency(db: OrivellumDB, job_id: str, conv_id: str, client_msg_id: str) -> None:
    """Complete or release the message_idempotency claim when the job ends.

    The streaming route claims the slot ('processing') before generation, but
    the assistant message is persisted inside the pumped generator — so the
    claim must be settled HERE, when the pump finishes, or retries with the
    same client_msg_id would 409 until the stale timeout and then regenerate
    a duplicate reply.  A persisted terminal message completes the slot; a
    failed job releases it so the client's queued retry can regenerate.
    """
    job = db.get_gen_job(job_id)
    msg_id = (job or {}).get("message_id")
    if msg_id:
        db.complete_idempotency(conv_id, client_msg_id, msg_id)
    else:
        db.release_idempotency(conv_id, client_msg_id)


async def _pump(
    db: OrivellumDB,
    job_id: str,
    gen: AsyncGenerator[str],
    relay: _Relay,
    conv_id: str,
    client_msg_id: str | None,
) -> None:
    """Drain the generator to completion, journalling + relaying every frame.

    Runs as an independent task: the HTTP response tails ``relay``, and
    dropping that response does NOT stop this pump — generation always runs to
    the end so a suspended phone can recover the full reply.
    """
    coalescer = _Coalescer(db, job_id)
    state = "failed"
    try:
        async for frame in gen:
            # Relay first — live clients get token-level latency.
            relay.push(frame)
            if _journal_frame(db, job_id, coalescer, frame):
                state = "done"
    except asyncio.CancelledError:
        # Process shutdown — journal the interruption honestly, then re-raise.
        with contextlib.suppress(Exception):
            coalescer.flush()
            db.append_gen_event(job_id, "failed", json.dumps({"error": "server shutdown"}))
            db.finish_gen_job(job_id, "failed")
        raise
    except Exception as exc:  # noqa: BLE001 — background task must not die silently
        logger.warning("generation pump failed (job=%s): %s", job_id, exc)
        with contextlib.suppress(Exception):
            coalescer.flush()
            db.append_gen_event(job_id, "failed", json.dumps({"error": str(exc)[:300]}))
    finally:
        with contextlib.suppress(Exception):
            coalescer.flush()
            if state == "done":
                db.finish_gen_job(job_id, "done")
            else:
                db.finish_gen_job(job_id, "failed")
        if client_msg_id:
            with contextlib.suppress(Exception):
                _settle_idempotency(db, job_id, conv_id, client_msg_id)
        relay.detach()
        _TASKS.pop(job_id, None)


async def wrap(
    db: OrivellumDB,
    conversation_id: str,
    gen: AsyncGenerator[str],
    client_msg_id: str | None = None,
) -> AsyncGenerator[str]:
    """SSE generator that runs *gen* as a journalled job and tails it live.

    On first iteration (inside the event loop — route handlers may be sync):
    creates the job row, starts the pump task, and announces the job id as the
    first SSE frame so the client can store its replay cursor.  A client
    disconnect closes only this tail — the pump keeps running to completion.
    """
    job_id = db.create_gen_job(conversation_id, client_msg_id=client_msg_id)
    relay = _Relay()
    task = asyncio.get_running_loop().create_task(
        _pump(db, job_id, gen, relay, conversation_id, client_msg_id)
    )
    _TASKS[job_id] = task
    try:
        yield f"data: {json.dumps({'job_id': job_id})}\n\n"
        while True:
            frame = await relay.get()
            if frame is _EOF:
                return
            yield frame
    finally:
        # Tail closed (client disconnect / iOS suspension): detach so the pump
        # stops queueing frames nobody will read.  The pump itself keeps
        # running to completion — recovery happens via the journal.
        relay.detach()
