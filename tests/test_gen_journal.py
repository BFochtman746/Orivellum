"""Generation-event journal (iPhone continuity core, schema v151/v152).

Covers:
  1. DB layer — gen job lifecycle, monotonic event sequences, stale-running
     detection, active-only listing, opportunistic pruning.
  2. genjournal pump — journalling of a fake SSE generator: coalesced token
     chunks, meta/sources passthrough, done/failed terminal states, and the
     key guarantee that dropping the HTTP tail does NOT stop the pump.
  3. HTTP endpoints — jobs listing, events-after-seq replay, 404s.
  4. Durable notification ledger + push subscription endpoints (webpush
     library mocked — no network).
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import AUTH_HEADERS


def _make_db(tmp: str):
    from orivellum.api import _deps
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=tmp)
    db = OrivellumDB(str(Path(tmp) / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return db


@pytest.fixture()
def db():
    with tempfile.TemporaryDirectory() as tmp:
        database = _make_db(tmp)
        yield database
        database.close()


@pytest.fixture()
def client(db):
    """TestClient whose lifespan startup is re-pointed at the temp DB.

    App startup re-inits _deps with the real data dir, so (matching the
    pattern in test_library_retranscribe) we re-init after entering the
    context so route handlers see the test DB.
    """
    from fastapi.testclient import TestClient

    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig

    with TestClient(app, headers=AUTH_HEADERS) as c:
        _deps.init(db=db, cfg=OrivellumConfig(data_dir=str(Path(db._path).parent)))
        yield c


# ─── 1. DB layer ─────────────────────────────────────────────────────────────


class TestGenJobDb:
    def test_job_lifecycle(self, db):
        conv = db.create_conversation(title="c")
        job_id = db.create_gen_job(conv["id"], client_msg_id="op-1")
        job = db.get_gen_job(job_id)
        assert job["state"] == "running"
        assert job["client_msg_id"] == "op-1"

        db.finish_gen_job(job_id, "done")
        assert db.get_gen_job(job_id)["state"] == "done"

    def test_event_sequences_are_monotonic(self, db):
        conv = db.create_conversation(title="c")
        job_id = db.create_gen_job(conv["id"])
        seqs = [db.append_gen_event(job_id, "chunk", "{}") for _ in range(5)]
        assert seqs == [1, 2, 3, 4, 5]

        events = db.list_gen_events(job_id, after_seq=2)
        assert [e["seq"] for e in events] == [3, 4, 5]

    def test_active_only_listing(self, db):
        conv = db.create_conversation(title="c")
        j1 = db.create_gen_job(conv["id"])
        j2 = db.create_gen_job(conv["id"])
        db.finish_gen_job(j1, "done")

        active = db.list_gen_jobs(conv["id"], active_only=True)
        assert [j["id"] for j in active] == [j2]
        assert len(db.list_gen_jobs(conv["id"])) == 2

    def test_stale_running_job_reports_failed(self, db):
        conv = db.create_conversation(title="c")
        job_id = db.create_gen_job(conv["id"])
        # Backdate updated_at beyond the 10-minute silence threshold.
        with db._lock:
            db._conn.execute(
                "UPDATE gen_jobs SET updated_at = updated_at - 700 WHERE id=?",
                (job_id,),
            )
            db._maybe_commit()
        assert db.get_gen_job(job_id)["state"] == "failed"

    def test_pruning_removes_old_jobs_and_events(self, db):
        conv = db.create_conversation(title="c")
        old_job = db.create_gen_job(conv["id"])
        db.append_gen_event(old_job, "chunk", "{}")
        db.finish_gen_job(old_job, "done")
        # Backdate beyond the 24 h completed-job window.
        with db._lock:
            db._conn.execute(
                "UPDATE gen_jobs SET updated_at = updated_at - 90000 WHERE id=?",
                (old_job,),
            )
            db._maybe_commit()
        db.create_gen_job(conv["id"])  # triggers opportunistic pruning
        assert db.get_gen_job(old_job) is None
        assert db.list_gen_events(old_job) == []


# ─── 2. Pump / wrap ──────────────────────────────────────────────────────────


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _collect(gen, limit=None):
    out = []
    async for frame in gen:
        out.append(frame)
        if limit is not None and len(out) >= limit:
            break
    return out


class TestPump:
    def test_wrap_relays_and_journals(self, db):
        from orivellum.api import genjournal

        conv = db.create_conversation(title="c")

        async def fake_gen():
            yield _sse({"message_id": "m-1"})
            yield _sse({"token": "Hello "})
            yield _sse({"token": "world"})
            yield _sse({"sources": [{"id": "k1"}]})
            yield "data: [DONE]\n\n"

        async def run():
            frames = await _collect(genjournal.wrap(db, conv["id"], fake_gen()))
            # Let the pump's finally block run.
            await asyncio.sleep(0.05)
            return frames

        frames = asyncio.run(run())

        # First frame announces the job id; remaining frames are relayed as-is.
        first = json.loads(frames[0][len("data: "):])
        job_id = first["job_id"]
        assert len(frames) == 6  # job_id + 5 original frames

        job = db.get_gen_job(job_id)
        assert job["state"] == "done"
        assert job["message_id"] == "m-1"

        events = db.list_gen_events(job_id)
        kinds = [e["kind"] for e in events]
        assert kinds[-1] == "done"
        assert "sources" in kinds
        # Tokens were coalesced into chunk events that reassemble the text.
        text = "".join(
            json.loads(e["payload"])["token"] for e in events if e["kind"] == "chunk"
        )
        assert text == "Hello world"

    def test_pump_survives_dropped_tail(self, db):
        """The continuity guarantee: closing the SSE tail must not stop generation."""
        from orivellum.api import genjournal

        conv = db.create_conversation(title="c")
        released = asyncio.Event()

        async def fake_gen():
            yield _sse({"token": "part1"})
            await released.wait()
            yield _sse({"token": "part2"})
            yield "data: [DONE]\n\n"

        async def run():
            tail = genjournal.wrap(db, conv["id"], fake_gen())
            frames = await _collect(tail, limit=2)  # job_id + first token
            await tail.aclose()  # client disconnects mid-stream
            job_id = json.loads(frames[0][len("data: "):])["job_id"]
            assert db.get_gen_job(job_id)["state"] == "running"
            released.set()
            # Wait for the pump task to finish on its own.
            for _ in range(100):
                if db.get_gen_job(job_id)["state"] != "running":
                    break
                await asyncio.sleep(0.02)
            return job_id

        job_id = asyncio.run(run())
        job = db.get_gen_job(job_id)
        assert job["state"] == "done"
        text = "".join(
            json.loads(e["payload"]).get("token", "")
            for e in db.list_gen_events(job_id)
            if e["kind"] == "chunk"
        )
        assert text == "part1part2"

    def test_generator_exception_journals_failed(self, db):
        from orivellum.api import genjournal

        conv = db.create_conversation(title="c")

        async def fake_gen():
            yield _sse({"token": "hi"})
            raise RuntimeError("engine died")

        async def run():
            frames = await _collect(genjournal.wrap(db, conv["id"], fake_gen()))
            await asyncio.sleep(0.05)
            return frames

        frames = asyncio.run(run())
        job_id = json.loads(frames[0][len("data: "):])["job_id"]
        job = db.get_gen_job(job_id)
        assert job["state"] == "failed"
        kinds = [e["kind"] for e in db.list_gen_events(job_id)]
        assert "failed" in kinds


# ─── 3. HTTP endpoints ───────────────────────────────────────────────────────


class TestJournalEndpoints:
    def test_jobs_and_events_replay(self, db, client):
        conv = db.create_conversation(title="c")
        job_id = db.create_gen_job(conv["id"], client_msg_id="op-9")
        db.append_gen_event(job_id, "chunk", json.dumps({"token": "abc"}))
        db.append_gen_event(job_id, "done", "")
        db.finish_gen_job(job_id, "done")

        r = client.get(f"/api/conversations/{conv['id']}/jobs")
        assert r.status_code == 200
        jobs = r.json()["jobs"]
        assert [j["id"] for j in jobs] == [job_id]

        r = client.get(f"/api/conversations/jobs/{job_id}/events?after=0")
        assert r.status_code == 200
        data = r.json()
        assert data["job"]["state"] == "done"
        assert [e["seq"] for e in data["events"]] == [1, 2]

        r = client.get(f"/api/conversations/jobs/{job_id}/events?after=1")
        assert [e["seq"] for e in r.json()["events"]] == [2]

    def test_404s(self, db, client):
        assert client.get("/api/conversations/nope/jobs").status_code == 404
        assert client.get("/api/conversations/jobs/nope/events").status_code == 404


# ─── 4. Notification ledger + push endpoints ────────────────────────────────


class TestNotifLedger:
    def test_dedupe_key_suppresses_duplicates(self, db):
        first = db.add_notification("document_ready", "t", dedupe_key="doc-1-ready")
        dup = db.add_notification("document_ready", "t", dedupe_key="doc-1-ready")
        assert first is not None
        assert dup is None
        events, latest = db.list_notifications(0)
        assert len(events) == 1
        assert latest == first

    def test_null_dedupe_keys_do_not_collide(self, db):
        assert db.add_notification("k", "a") is not None
        assert db.add_notification("k", "b") is not None
        events, _ = db.list_notifications(0)
        assert len(events) == 2

    def test_emit_writes_to_ledger_when_configured(self, db):
        from orivellum.api import notifications as notif

        notif.configure(db)
        try:
            notif.emit("document_ready", "Doc", "ready", url="/library/x")
            events, latest = db.list_notifications(0)
            assert latest >= 1
            assert events[-1]["kind"] == "document_ready"
        finally:
            notif._reset_for_tests()


class TestPushEndpoints:
    def test_config_provisions_vapid_keys(self, db, client):
        r = client.get("/api/system/push/config")
        assert r.status_code == 200
        data = r.json()
        assert data["vapid_public_key"]
        assert data["subscription_count"] == 0
        # Second call returns the SAME key (persisted, not regenerated).
        assert client.get("/api/system/push/config").json()["vapid_public_key"] == (
            data["vapid_public_key"]
        )

    def test_subscribe_unsubscribe_roundtrip(self, db, client):
        sub = {
            "endpoint": "https://push.example/ep1",
            "keys": {"p256dh": "pk", "auth": "ak"},
        }
        r = client.post("/api/system/push/subscribe", json=sub)
        assert r.status_code == 200
        assert r.json()["subscription_count"] == 1

        # Upsert on same endpoint — no duplicate row.
        r = client.post("/api/system/push/subscribe", json=sub)
        assert r.json()["subscription_count"] == 1

        r = client.post(
            "/api/system/push/unsubscribe", json={"endpoint": sub["endpoint"]}
        )
        assert r.json()["removed"] is True
        assert db.list_push_subscriptions() == []

    def test_subscribe_rejects_incomplete_payload(self, db, client):
        r = client.post(
            "/api/system/push/subscribe",
            json={"endpoint": "https://push.example/ep", "keys": {}},
        )
        assert r.status_code == 422

    def test_test_push_409_without_subscriptions(self, db, client):
        r = client.post("/api/system/push/test")
        assert r.status_code == 409

    def test_test_push_sends_via_webpush(self, db, client):
        client.get("/api/system/push/config")  # provision VAPID keys
        db.save_push_subscription("https://push.example/ep2", "pk", "ak")
        with patch("pywebpush.webpush") as wp:
            r = client.post("/api/system/push/test")
        assert r.status_code == 200
        assert r.json()["sent"] == 1
        assert wp.call_count == 1
        # Payload carries only kind + url + id — never content.
        payload = json.loads(wp.call_args.kwargs.get("data") or wp.call_args[0][1])
        assert set(payload) <= {"id", "kind", "url"}

    def test_dead_subscription_is_pruned(self, db, client):
        from pywebpush import WebPushException

        client.get("/api/system/push/config")  # provision VAPID keys
        db.save_push_subscription("https://push.example/gone", "pk", "ak")

        class _Resp:
            status_code = 410

        with patch(
            "pywebpush.webpush",
            side_effect=WebPushException("gone", response=_Resp()),
        ):
            client.post("/api/system/push/test")
        assert db.list_push_subscriptions() == []
