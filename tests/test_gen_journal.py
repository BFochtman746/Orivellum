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

    def test_closed_tail_does_not_accumulate_frames(self, db):
        """A dead tail must never retain the generation in RAM: after the tail
        closes, the relay detaches and drops every subsequent frame."""
        from orivellum.api import genjournal

        conv = db.create_conversation(title="c")
        n_frames = genjournal._RELAY_MAXSIZE * 3

        async def fake_gen():
            for i in range(n_frames):
                yield _sse({"token": f"t{i}"})
                if i % 100 == 0:
                    await asyncio.sleep(0)  # let the tail run
            yield "data: [DONE]\n\n"

        async def run():
            tail = genjournal.wrap(db, conv["id"], fake_gen())
            frames = await _collect(tail, limit=2)  # job_id + first token
            job_id = json.loads(frames[0][len("data: "):])["job_id"]
            relay = None
            # Grab the relay from the running pump task's frame locals.
            task = genjournal._TASKS[job_id]
            relay = task.get_coro().cr_frame.f_locals["relay"]
            await tail.aclose()  # client disconnects
            assert relay.attached is False  # tail close detaches immediately
            for _ in range(200):
                if db.get_gen_job(job_id)["state"] != "running":
                    break
                await asyncio.sleep(0.02)
            return job_id, relay

        job_id, relay = asyncio.run(run())
        assert db.get_gen_job(job_id)["state"] == "done"
        # Detached relay holds at most the EOF sentinel — never the stream.
        assert relay.qsize() <= 1

    def test_relay_overflow_detaches_instead_of_growing(self, db):
        """A tail that stops reading (suspended phone) must not buffer the
        whole generation: the relay caps at maxsize, then detaches."""
        from orivellum.api.genjournal import _EOF, _Relay

        async def run():
            relay = _Relay(maxsize=8)
            for i in range(100):
                relay.push(f"frame{i}")
            assert relay.attached is False
            assert relay.qsize() <= 8
            # A late-waking tail terminates: EOF is reachable.
            seen = []
            for _ in range(8):
                item = await relay.get()
                if item is _EOF:
                    break
                seen.append(item)
            else:
                raise AssertionError("EOF never reached after detach")
            # Frames pushed after detach are dropped silently.
            relay.push("late")
            assert relay.qsize() == 0

        asyncio.run(run())

    def test_stream_idempotency_completed_when_pump_finishes(self, db):
        """Exactly-once for streamed sends: the 'processing' claim taken by the
        route must be completed when the journalled stream persists its
        terminal assistant message — a retry with the same client_msg_id must
        get the stored reply back, never a 409-until-stale or a duplicate."""
        from orivellum.api import genjournal

        conv = db.create_conversation(title="c")
        action, _, _ = db.store_user_msg_and_claim(conv["id"], "hi", None, "cmid-1")
        assert action == "generate"
        ai = db.add_message(conv["id"], "assistant", "reply")

        async def fake_gen():
            yield _sse({"token": "reply"})
            yield _sse({"message_id": ai["id"], "model": "m"})
            yield "data: [DONE]\n\n"

        async def run():
            await _collect(genjournal.wrap(db, conv["id"], fake_gen(), client_msg_id="cmid-1"))
            await asyncio.sleep(0.05)

        asyncio.run(run())
        # Retry (outbox flush after lost response) returns the stored reply.
        action2, ai_id, _ = db.store_user_msg_and_claim(conv["id"], "hi", None, "cmid-1")
        assert action2 == "return"
        assert ai_id == ai["id"]

    def test_stream_idempotency_released_on_failed_generation(self, db):
        """A failed stream (no persisted assistant message) must RELEASE the
        claim so the client's queued retry regenerates immediately instead of
        409-ing until the stale timeout."""
        from orivellum.api import genjournal

        conv = db.create_conversation(title="c")
        action, _, _ = db.store_user_msg_and_claim(conv["id"], "hi", None, "cmid-2")
        assert action == "generate"

        async def fake_gen():
            yield _sse({"token": "par"})
            raise RuntimeError("engine died")

        async def run():
            await _collect(genjournal.wrap(db, conv["id"], fake_gen(), client_msg_id="cmid-2"))
            await asyncio.sleep(0.05)

        asyncio.run(run())
        action2, _, _ = db.store_user_msg_and_claim(conv["id"], "hi", None, "cmid-2")
        assert action2 == "generate"

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

    def test_subscribe_unsubscribe_roundtrip(self, db, client, monkeypatch):
        import socket

        # Pretend the endpoint host resolves publicly — the SSRF validator
        # itself is exercised by test_subscribe_rejects_ssrf_endpoints.
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
        )
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

    def test_subscribe_rejects_ssrf_endpoints(self, db, client):
        """The server sends outbound requests to stored endpoints — private,
        non-HTTPS, or credentialed targets must be refused at intake."""
        keys = {"p256dh": "k", "auth": "a"}
        bad = [
            "http://push.example.com/x",              # not https
            "https://127.0.0.1/x",                    # loopback
            "https://localhost/x",                    # resolves to loopback
            "https://10.0.0.5/x",                     # RFC-1918
            "https://192.168.1.10:9000/x",            # RFC-1918 with port
            "https://user:pw@push.example.com/x",     # embedded credentials
            "https://push.example.com/" + "a" * 1100,  # oversized URL
        ]
        for endpoint in bad:
            r = client.post(
                "/api/system/push/subscribe",
                json={"endpoint": endpoint, "keys": keys},
            )
            assert r.status_code == 422, f"accepted {endpoint!r}"
        assert db.list_push_subscriptions() == []

    def test_send_to_all_revalidates_at_delivery_time(self, db, monkeypatch):
        """DNS rebinding defense: an endpoint accepted at subscribe time must
        be re-validated at DELIVERY time — if it now resolves privately, the
        subscription is pruned and no outbound request is made."""
        import socket

        import pywebpush

        from orivellum.api import webpush

        webpush.ensure_vapid_keys(db)
        db.save_push_subscription("https://push.example.com/x", "pk", "ak")

        # The host now resolves to loopback (rebound after registration).
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        )
        called = []
        monkeypatch.setattr(pywebpush, "webpush", lambda **kw: called.append(kw))

        result = webpush.send_to_all(db, {"id": 0, "kind": "test", "url": "/system"})
        assert result == {"sent": 0, "failed": 0, "pruned": 1}
        assert called == []
        assert db.list_push_subscriptions() == []

    def test_subscribe_rejects_oversized_keys(self, db, client):
        r = client.post(
            "/api/system/push/subscribe",
            json={
                "endpoint": "https://push.example.com/x",
                "keys": {"p256dh": "k" * 600, "auth": "a"},
            },
        )
        assert r.status_code == 422

    def test_subscribe_rejects_incomplete_payload(self, db, client):
        r = client.post(
            "/api/system/push/subscribe",
            json={"endpoint": "https://push.example/ep", "keys": {}},
        )
        assert r.status_code == 422

    def test_test_push_409_without_subscriptions(self, db, client):
        r = client.post("/api/system/push/test")
        assert r.status_code == 409

    def test_test_push_sends_via_webpush(self, db, client, monkeypatch):
        import socket

        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
        )
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

    def test_dead_subscription_is_pruned(self, db, client, monkeypatch):
        import socket

        from pywebpush import WebPushException

        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
        )
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
