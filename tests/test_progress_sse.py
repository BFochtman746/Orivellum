"""Confirm that the SSE progress stream delivers live extraction events.

Task #523: GET /api/library/{doc_id}/progress must emit correctly-shaped events
during extraction so the web EventSource and mobile streaming-fetch handlers
receive live progress updates — not a single buffered payload at completion.

Proving live delivery
---------------------
The live-transition tests (Phase B) start a real Uvicorn server in a daemon
thread and connect with ``httpx.Client`` over a real TCP socket.  Uvicorn runs
the ASGI app with a real event loop; Starlette wraps the sync generator in
``iterate_in_threadpool``; each yielded chunk is sent to the client immediately,
not buffered until the stream closes.

Sync ``TestClient`` or ``httpx.AsyncClient`` with ASGI transport both buffer
the complete response body before returning a single byte, so they cannot prove
liveness.  Only a real TCP server delivers SSE progressively.

DB / lifespan alignment
-----------------------
The FastAPI app's startup ``lifespan`` re-initialises ``_deps`` with a fresh
``OrivellumDB`` opened at ``cfg.db_path`` (= ``data_dir/orivellum.db``).  The
test factory ``_make_app`` therefore uses the same filename so that:

1. The test inserts documents through ``db`` (opens ``tmp/orivellum.db``).
2. The lifespan opens the same SQLite file at startup.
3. Background-thread mutations via ``db._conn`` are visible to the API's
   connection, which reads the same on-disk rows.

``_live_server`` sets ``ORIVELLUM_DATA_DIR=tmp_dir`` before starting Uvicorn so
``load_config()`` inside the lifespan resolves to the temp directory.

Live-delivery assertion
-----------------------
A background thread flips the document's readiness state at T = _FLIP_DELAY s.
The SSE generator polls every 0.5 s, so the first ``data:`` frame arrives at the
``iter_lines()`` iterator at ≈ 0.5 s — before the T = 1.3 s flip.

    event_times[0] < flip_time

fails if the HTTP layer buffers the body (all timestamps would be ≥ flip_time).

Phases
------
A  Terminal states — sync TestClient, stream closes in one pass.
B  Live transition (Uvicorn + real TCP, timestamp proof).
C  Stage inference — word_count / chunk_count / knowledge_count → stage/pct.
D  Event schema — every event matches the TypeScript ``ProgressInfo`` interface.
E  Route guard — 404 for unknown doc_id.
F  Deduplication and terminal-finality — unconditional assertions.
G  Web UI rendering contract — field types and safety.
"""

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager

import httpx
import uvicorn
from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

# ── SSE parsing helper ─────────────────────────────────────────────────────────


def _parse_sse(body: str) -> list[dict]:
    """Decode all ``data: …`` lines from a buffered SSE body string."""
    events: list[dict] = []
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


def _consume_live(
    base_url: str, path: str, *, timeout: float = 20.0
) -> tuple[list[dict], list[float]]:
    """Open a live TCP connection to *base_url* + *path* and collect SSE events.

    Each ``data:`` frame is received and timestamped as it arrives from the
    server (progressive delivery).  Returns ``(events, timestamps)`` where
    ``timestamps[i]`` is the monotonic time event *i* was received.
    """
    events: list[dict] = []
    timestamps: list[float] = []
    deadline = time.monotonic() + timeout

    with httpx.Client(timeout=timeout) as client:
        with client.stream("GET", base_url + path, headers=AUTH_HEADERS) as resp:
            for raw_line in resp.iter_lines():
                if time.monotonic() > deadline:
                    break
                line = raw_line.strip()
                if line.startswith("data: "):
                    try:
                        events.append(json.loads(line[6:]))
                        timestamps.append(time.monotonic())
                    except json.JSONDecodeError:
                        pass

    return events, timestamps


# ── App / DB factory ───────────────────────────────────────────────────────────


def _make_app(tmp: str):
    """Create a FastAPI app wired to a fresh SQLite DB in *tmp*.

    Uses ``cfg.db_path`` (= ``tmp/orivellum.db``) so that the Uvicorn lifespan
    (which resolves its own DB path via ``load_config()`` + ``ORIVELLUM_DATA_DIR``)
    opens the same file.  Mutations through the returned ``db`` object are
    therefore visible to the API's internal DB connection.
    """
    from orivellum.api import _deps
    from orivellum.api.app import create_app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(cfg.db_path)  # tmp/orivellum.db — same file the lifespan opens
    _deps.init(db=db, cfg=cfg)
    return create_app(), db


def _make_sync_client(tmp: str) -> tuple[TestClient, object]:
    """Convenience wrapper that returns a sync TestClient for non-live tests."""
    app, db = _make_app(tmp)
    return TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS), db


# ── Uvicorn live-server fixture ────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def _live_server(app, tmp_dir: str):
    """Start a real Uvicorn server in a daemon thread; yield its base URL.

    Sets ``ORIVELLUM_DATA_DIR=tmp_dir`` before starting so the app's lifespan
    calls ``load_config(data_dir=tmp_dir)`` and opens ``tmp_dir/orivellum.db``
    — the same file created by ``_make_app(tmp_dir)``.
    """
    port = _free_port()
    old_data_dir = os.environ.get("ORIVELLUM_DATA_DIR")
    os.environ["ORIVELLUM_DATA_DIR"] = tmp_dir

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for Uvicorn to accept connections (max 8 s)
    deadline = time.monotonic() + 8.0
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=3.0)
        _restore_env("ORIVELLUM_DATA_DIR", old_data_dir)
        raise RuntimeError("Uvicorn did not start within 8 s")

    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=8.0)
        _restore_env("ORIVELLUM_DATA_DIR", old_data_dir)


def _restore_env(key: str, old_value: str | None) -> None:
    if old_value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = old_value


# ── Stage / field constants ────────────────────────────────────────────────────

_KNOWN_STAGES = frozenset(
    {
        "extracting",
        "chunking",
        "indexing",
        "harvesting",
        "transcribing",
        "complete",
        "error",
        "no_text",
    }
)

# Fields the TypeScript ``ProgressInfo`` interface requires (detail.tsx 775-781)
_REQUIRED_FIELDS = {"stage", "pct", "items_found", "readiness", "chunk_count"}

# Background thread flips readiness at this offset (s).
# Must be > 2 × SSE poll interval (0.5 s) = 1.0 s so at least two in-progress
# events are emitted before the flip.
_FLIP_DELAY = 1.3


# ── Phase A — terminal states already set ─────────────────────────────────────


class TestTerminalStates(unittest.TestCase):
    """Sync TestClient is sufficient here: the doc is already terminal so the
    generator closes after one iteration — no live-delivery concern."""

    def _stream(self, tmp: str, readiness: str, **kw) -> tuple[object, list[dict]]:
        client, db = _make_sync_client(tmp)
        doc = db.create_document(title="t.txt", kind="text")
        db.update_document_extracted(
            doc["id"],
            kw.get("text", ""),
            kw.get("words", 0),
            readiness=readiness,
            error_message=kw.get("err"),
        )
        resp = client.get(f"/api/library/{doc['id']}/progress")
        db.close()
        return resp, _parse_sse(resp.text)

    def test_ready_doc_emits_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            resp, events = self._stream(tmp, "ready", text="hello", words=2)
        self.assertEqual(resp.status_code, 200)
        self.assertGreater(len(events), 0)
        last = events[-1]
        self.assertEqual(last["stage"], "complete")
        self.assertEqual(last["pct"], 100)
        self.assertEqual(last["readiness"], "ready")

    def test_error_doc_emits_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, events = self._stream(tmp, "error", err="boom")
        self.assertGreater(len(events), 0)
        last = events[-1]
        self.assertEqual(last["stage"], "error")
        self.assertEqual(last["pct"], 0)
        self.assertEqual(last["readiness"], "error")

    def test_no_text_doc_emits_no_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, events = self._stream(tmp, "no_text")
        self.assertGreater(len(events), 0)
        self.assertEqual(events[-1]["stage"], "no_text")
        self.assertEqual(events[-1]["readiness"], "no_text")

    def test_content_type_is_event_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            resp, _ = self._stream(tmp, "ready", text="x", words=1)
        self.assertIn("text/event-stream", resp.headers.get("content-type", ""))

    def test_cache_control_no_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            resp, _ = self._stream(tmp, "ready", text="x", words=1)
        self.assertIn("no-cache", resp.headers.get("cache-control", ""))


# ── Phase B — live delivery proof (Uvicorn + real TCP) ───────────────────────


class TestLiveDelivery(unittest.TestCase):
    """A document starts ``imported`` and a background thread flips its readiness
    to a terminal state at T = _FLIP_DELAY s.  The SSE generator polls every
    0.5 s, so the first event arrives at the real-TCP ``iter_lines()`` iterator
    at ≈ 0.5 s — BEFORE the T = 1.3 s flip.

    The assertion ``event_times[0] < flip_time`` fails if the HTTP layer
    buffers the response — all timestamps would be ≥ flip_time.
    """

    def _run(
        self,
        db,
        base_url: str,
        doc_id: str,
        *,
        terminal_readiness: str,
        extracted_text: str = "done",
        word_count: int = 10,
    ) -> tuple[list[dict], list[float], float]:
        flip_time_holder: list[float] = []

        def _flip():
            time.sleep(_FLIP_DELAY)
            flip_time_holder.append(time.monotonic())
            if terminal_readiness == "error":
                db.update_document_extracted(
                    doc_id, "", 0, readiness="error", error_message="simulated error"
                )
            elif terminal_readiness == "no_text":
                db.update_document_extracted(doc_id, "", 0, readiness="no_text")
            else:
                db.update_document_extracted(
                    doc_id, extracted_text, word_count, readiness=terminal_readiness
                )

        t = threading.Thread(target=_flip, daemon=True)
        t.start()
        events, event_times = _consume_live(base_url, f"/api/library/{doc_id}/progress")
        t.join(timeout=10.0)

        self.assertTrue(flip_time_holder, "Background flip thread must have run")
        return events, event_times, flip_time_holder[0]

    def test_imported_to_ready_core_liveness_guarantee(self):
        """'extracting' delivered before 'complete', AND first event arrives
        before the state flip — proves SSE frames are not buffered."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            doc = db.create_document(title="processing.txt", kind="text")

            with _live_server(app, tmp) as base_url:
                events, event_times, flip_time = self._run(
                    db, base_url, doc["id"], terminal_readiness="ready"
                )
            db.close()

        self.assertGreaterEqual(
            len(events), 2, f"Expected ≥2 events (extracting + complete); got {events}"
        )

        stages = [e["stage"] for e in events]
        self.assertIn(
            "extracting", stages, f"At least one 'extracting' event required; got {stages}"
        )
        self.assertEqual(stages[-1], "complete", f"Last event must be 'complete'; got {stages}")

        self.assertLess(
            event_times[0],
            flip_time,
            f"First event at {event_times[0]:.3f}s arrived at/after the readiness flip "
            f"at {flip_time:.3f}s (+{event_times[0] - flip_time:+.3f}s). "
            f"SSE delivery appears buffered, not live.",
        )

    def test_imported_to_error_yields_extracting_then_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            doc = db.create_document(title="fails.pdf", kind="pdf")

            with _live_server(app, tmp) as base_url:
                events, event_times, flip_time = self._run(
                    db, base_url, doc["id"], terminal_readiness="error"
                )
            db.close()

        stages = [e["stage"] for e in events]
        self.assertIn("extracting", stages, f"Must emit 'extracting' before 'error'; got {stages}")
        self.assertEqual(stages[-1], "error", f"Last stage must be 'error'; got {stages}")
        self.assertGreater(len(event_times), 0)
        self.assertLess(
            event_times[0],
            flip_time,
            f"First event at {event_times[0]:.3f}s must precede flip at {flip_time:.3f}s",
        )

    def test_pct_starts_below_100_and_ends_at_100(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            doc = db.create_document(title="pct.txt", kind="text")
            with _live_server(app, tmp) as base_url:
                events, _, _ = self._run(db, base_url, doc["id"], terminal_readiness="ready")
            db.close()

        pcts = [e["pct"] for e in events]
        self.assertLess(pcts[0], 100, "First pct must be < 100 (still in-progress)")
        self.assertEqual(pcts[-1], 100, "Final pct must be 100 (complete)")

    def test_readiness_field_updates_progressively(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            doc = db.create_document(title="track.txt", kind="text")
            with _live_server(app, tmp) as base_url:
                events, _, _ = self._run(db, base_url, doc["id"], terminal_readiness="ready")
            db.close()

        readiness = [e["readiness"] for e in events]
        self.assertIn("imported", readiness, "Early events must carry readiness='imported'")
        self.assertEqual(readiness[-1], "ready", "Final event must carry readiness='ready'")


# ── Phase C — stage inference ─────────────────────────────────────────────────


class TestStageInference(unittest.TestCase):
    """Stage label inferred from DB counters.  Uses a live Uvicorn server so
    events from an ``imported`` doc are delivered before the background flip."""

    def _first_event(self, app, db, doc_id: str, tmp: str, flip_delay: float = 0.9) -> dict:
        def _flip():
            time.sleep(flip_delay)
            with db._lock:
                db._conn.execute("UPDATE documents SET readiness='ready' WHERE id=?", (doc_id,))
                db._conn.commit()

        t = threading.Thread(target=_flip, daemon=True)
        t.start()
        with _live_server(app, tmp) as base_url:
            events, _ = _consume_live(base_url, f"/api/library/{doc_id}/progress")
        t.join(timeout=10.0)
        self.assertGreater(len(events), 0, "Stream must emit at least one event")
        return events[0]

    def test_imported_no_text_no_chunks_is_extracting(self):
        """word_count=0, chunk_count=0 → stage='extracting', pct=10."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            doc = db.create_document(title="e.txt", kind="text")
            with db._lock:
                db._conn.execute(
                    "UPDATE documents SET readiness='imported', word_count=0 WHERE id=?",
                    (doc["id"],),
                )
                db._conn.commit()
            first = self._first_event(app, db, doc["id"], tmp)
            db.close()

        self.assertEqual(first["stage"], "extracting", f"Got {first}")
        self.assertEqual(first["pct"], 10)

    def test_imported_has_text_no_chunks_is_chunking(self):
        """word_count>0, chunk_count=0 → stage='chunking', pct=45."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            doc = db.create_document(title="c.txt", kind="text")
            with db._lock:
                db._conn.execute(
                    "UPDATE documents SET readiness='imported', word_count=200 WHERE id=?",
                    (doc["id"],),
                )
                db._conn.commit()
            first = self._first_event(app, db, doc["id"], tmp)
            db.close()

        self.assertEqual(first["stage"], "chunking", f"Got {first}")
        self.assertEqual(first["pct"], 45)

    def test_has_chunks_no_knowledge_is_indexing(self):
        """chunk_count>0, knowledge_count=0 → stage='indexing', pct=70."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            doc = db.create_document(title="idx.txt", kind="text")
            db.add_chunk(doc["id"], "chunk text here", page=1)
            with db._lock:
                db._conn.execute(
                    "UPDATE documents SET readiness='imported', word_count=100 WHERE id=?",
                    (doc["id"],),
                )
                db._conn.commit()
            first = self._first_event(app, db, doc["id"], tmp)
            db.close()

        self.assertEqual(first["stage"], "indexing", f"Got {first}")
        self.assertEqual(first["pct"], 70)
        self.assertGreaterEqual(first["chunk_count"], 1)

    def test_has_knowledge_is_harvesting(self):
        """knowledge_count>0 → stage='harvesting', pct in 70-95."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            work = db.create_work(title="Test Work")
            doc = db.create_document(title="harv.txt", kind="text", work_id=work["id"])
            db.add_chunk(doc["id"], "some text", page=1)
            db.create_knowledge_item(
                work_id=work["id"],
                kind="fact",
                text="A unique fact for harvesting stage test",
                source_doc_id=doc["id"],
            )
            with db._lock:
                db._conn.execute(
                    "UPDATE documents SET readiness='imported', word_count=100 WHERE id=?",
                    (doc["id"],),
                )
                db._conn.commit()
            first = self._first_event(app, db, doc["id"], tmp)
            db.close()

        self.assertEqual(first["stage"], "harvesting", f"Got {first}")
        self.assertGreaterEqual(first["pct"], 70)
        self.assertLessEqual(first["pct"], 95)
        self.assertGreaterEqual(first["items_found"], 1)

    def test_transcribing_readiness_is_transcribing_stage(self):
        """readiness='transcribing' → stage='transcribing', pct=25."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            doc = db.create_document(title="audio.mp3", kind="audio")
            with db._lock:
                db._conn.execute(
                    "UPDATE documents SET readiness='transcribing' WHERE id=?",
                    (doc["id"],),
                )
                db._conn.commit()
            first = self._first_event(app, db, doc["id"], tmp)
            db.close()

        self.assertEqual(first["stage"], "transcribing", f"Got {first}")
        self.assertEqual(first["pct"], 25)


# ── Phase D — event schema ─────────────────────────────────────────────────────


class TestEventSchema(unittest.TestCase):
    """Every SSE event must satisfy the TypeScript ``ProgressInfo`` interface.
    Sync TestClient is fine here — schema, not liveness, is under test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._app, self.db = _make_app(self._tmp.name)
        self._client = TestClient(self._app, raise_server_exceptions=True, headers=AUTH_HEADERS)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _all_events(self, doc_id: str) -> list[dict]:
        def _flip():
            time.sleep(_FLIP_DELAY)
            with self.db._lock:
                self.db._conn.execute(
                    "UPDATE documents SET readiness='ready' WHERE id=?", (doc_id,)
                )
                self.db._conn.commit()

        t = threading.Thread(target=_flip, daemon=True)
        t.start()
        resp = self._client.get(f"/api/library/{doc_id}/progress")
        t.join(timeout=10.0)
        return _parse_sse(resp.text)

    def test_all_required_fields_present(self):
        doc = self.db.create_document(title="schema.txt", kind="text")
        events = self._all_events(doc["id"])
        self.assertGreater(len(events), 0, "Must emit at least one event")
        for i, evt in enumerate(events):
            for field in _REQUIRED_FIELDS:
                self.assertIn(field, evt, f"Event[{i}] missing '{field}': {evt}")

    def test_pct_is_number_in_0_to_100(self):
        doc = self.db.create_document(title="pct.txt", kind="text")
        events = self._all_events(doc["id"])
        self.assertGreater(len(events), 0)
        for i, evt in enumerate(events):
            pct = evt.get("pct")
            self.assertIsInstance(pct, (int, float), f"Event[{i}].pct must be numeric: {evt}")
            self.assertGreaterEqual(pct, 0)
            self.assertLessEqual(pct, 100)

    def test_stage_is_known_value(self):
        doc = self.db.create_document(title="stg.txt", kind="text")
        events = self._all_events(doc["id"])
        self.assertGreater(len(events), 0)
        for i, evt in enumerate(events):
            self.assertIn(
                evt.get("stage"), _KNOWN_STAGES, f"Event[{i}].stage unknown: {evt.get('stage')!r}"
            )

    def test_items_found_is_non_negative_number(self):
        doc = self.db.create_document(title="items.txt", kind="text")
        events = self._all_events(doc["id"])
        self.assertGreater(len(events), 0)
        for i, evt in enumerate(events):
            n = evt.get("items_found")
            self.assertIsInstance(n, (int, float), f"Event[{i}].items_found not numeric: {evt}")
            self.assertGreaterEqual(n, 0)

    def test_chunk_count_is_non_negative_number(self):
        doc = self.db.create_document(title="chk.txt", kind="text")
        events = self._all_events(doc["id"])
        self.assertGreater(len(events), 0)
        for i, evt in enumerate(events):
            n = evt.get("chunk_count")
            self.assertIsInstance(n, (int, float), f"Event[{i}].chunk_count not numeric: {evt}")
            self.assertGreaterEqual(n, 0)

    def test_complete_event_carries_readiness_ready(self):
        doc = self.db.create_document(title="rs.txt", kind="text")
        events = self._all_events(doc["id"])
        for evt in events:
            if evt["stage"] == "complete":
                self.assertEqual(
                    evt["readiness"], "ready", f"complete must carry readiness=ready: {evt}"
                )

    def test_error_stage_carries_readiness_error(self):
        doc = self.db.create_document(title="err.txt", kind="text")
        self.db.update_document_extracted(
            doc["id"], "", 0, readiness="error", error_message="test error"
        )
        resp = self._client.get(f"/api/library/{doc['id']}/progress")
        for evt in _parse_sse(resp.text):
            if evt["stage"] == "error":
                self.assertEqual(
                    evt["readiness"], "error", f"error stage must carry readiness=error: {evt}"
                )


# ── Phase E — route guard ─────────────────────────────────────────────────────


class TestRouteGuard(unittest.TestCase):
    def test_missing_doc_returns_404(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, db = _make_sync_client(tmp)
            resp = client.get("/api/library/does-not-exist-xyzzy/progress")
            db.close()
        self.assertEqual(resp.status_code, 404)

    def test_404_is_not_event_stream(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, db = _make_sync_client(tmp)
            resp = client.get("/api/library/ghost-doc-id/progress")
            db.close()
        self.assertNotIn("text/event-stream", resp.headers.get("content-type", ""))


# ── Phase F — deduplication and terminal-finality ─────────────────────────────


class TestDeduplication(unittest.TestCase):
    """Both assertions are unconditional — tests fail if ``complete`` never
    appears, or if consecutive payloads are identical."""

    def test_complete_is_the_last_event(self):
        """``complete`` must exist and be the last event in the stream."""
        with tempfile.TemporaryDirectory() as tmp:
            client, db = _make_sync_client(tmp)
            doc = db.create_document(title="fin.txt", kind="text")
            db.update_document_extracted(doc["id"], "done", 5, readiness="ready")
            events = _parse_sse(client.get(f"/api/library/{doc['id']}/progress").text)
            db.close()

        self.assertGreater(len(events), 0, "Stream must emit at least one event")
        complete_idx = [i for i, e in enumerate(events) if e["stage"] == "complete"]
        self.assertGreater(len(complete_idx), 0, "Stream must emit at least one 'complete' event")
        self.assertEqual(
            complete_idx[-1],
            len(events) - 1,
            f"'complete' must be the last event; got {[e['stage'] for e in events]}",
        )

    def test_error_is_the_last_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, db = _make_sync_client(tmp)
            doc = db.create_document(title="err.txt", kind="text")
            db.update_document_extracted(doc["id"], "", 0, readiness="error", error_message="boom")
            events = _parse_sse(client.get(f"/api/library/{doc['id']}/progress").text)
            db.close()

        self.assertGreater(len(events), 0)
        self.assertEqual(
            events[-1]["stage"],
            "error",
            f"Last event must be 'error'; got {[e['stage'] for e in events]}",
        )

    def test_no_consecutive_duplicate_events(self):
        """Two adjacent events with identical payloads must never appear — the
        generator deduplicates before emitting."""
        with tempfile.TemporaryDirectory() as tmp:
            app, db = _make_app(tmp)
            doc = db.create_document(title="dedup.txt", kind="text")
            doc_id = doc["id"]

            def _flip():
                time.sleep(_FLIP_DELAY)
                db.update_document_extracted(doc_id, "text", 10, readiness="ready")

            t = threading.Thread(target=_flip, daemon=True)
            t.start()
            with _live_server(app, tmp) as base_url:
                events, _ = _consume_live(base_url, f"/api/library/{doc_id}/progress")
            t.join(timeout=10.0)
            db.close()

        self.assertGreater(len(events), 0, "Must collect at least one event")
        payloads = [json.dumps(e, sort_keys=True) for e in events]
        for i in range(1, len(payloads)):
            self.assertNotEqual(
                payloads[i],
                payloads[i - 1],
                f"Event[{i}] identical to Event[{i - 1}] — dedup broken: {events[i]}",
            )


# ── Phase G — web UI rendering contract ───────────────────────────────────────


class TestWebUIRenderingContract(unittest.TestCase):
    """Confirm the backend event shape matches what the progress bar reads.

    Web component (detail.tsx lines 1666-1683):
        processingProgress.stage       → label text (capitalised, _ → space)
        processingProgress.pct         → ``width: {pct}%``
        processingProgress.items_found → "N knowledge items found"
        processingProgress.readiness   → terminal-state EventSource.close()
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._app, self.db = _make_app(self._tmp.name)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _in_progress_event(self) -> dict:
        """Return the first event emitted while the doc is still 'imported'."""
        doc = self.db.create_document(title="ui.txt", kind="text")
        doc_id = doc["id"]

        def _flip():
            time.sleep(_FLIP_DELAY)
            with self.db._lock:
                self.db._conn.execute(
                    "UPDATE documents SET readiness='ready' WHERE id=?", (doc_id,)
                )
                self.db._conn.commit()

        t = threading.Thread(target=_flip, daemon=True)
        t.start()
        with _live_server(self._app, self._tmp.name) as base_url:
            events, _ = _consume_live(base_url, f"/api/library/{doc_id}/progress")
        t.join(timeout=10.0)
        self.assertGreater(len(events), 0, "Must emit at least one event")
        return events[0]

    def test_stage_is_non_empty_display_safe_string(self):
        evt = self._in_progress_event()
        stage = evt.get("stage", "")
        self.assertIsInstance(stage, str)
        self.assertGreater(len(stage), 0)
        self.assertNotIn("<", stage)
        self.assertNotIn(">", stage)

    def test_pct_usable_as_css_width(self):
        evt = self._in_progress_event()
        pct = evt.get("pct")
        self.assertIsInstance(pct, (int, float))
        self.assertGreaterEqual(pct, 0)
        self.assertLessEqual(pct, 100)

    def test_items_found_is_non_negative(self):
        evt = self._in_progress_event()
        n = evt.get("items_found")
        self.assertIsInstance(n, (int, float))
        self.assertGreaterEqual(n, 0)

    def test_readiness_field_present_and_non_empty(self):
        evt = self._in_progress_event()
        self.assertIn("readiness", evt)
        self.assertIsInstance(evt["readiness"], str)
        self.assertGreater(len(evt["readiness"]), 0)

    def test_in_progress_event_readiness_is_not_terminal(self):
        """Non-terminal events must not carry terminal readiness — that would
        prevent the web progress bar from rendering."""
        _TERMINAL = {"ready", "error", "no_text"}
        evt = self._in_progress_event()
        if evt["stage"] != "complete":
            self.assertNotIn(
                evt["readiness"], _TERMINAL, f"Non-complete event has terminal readiness: {evt}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
