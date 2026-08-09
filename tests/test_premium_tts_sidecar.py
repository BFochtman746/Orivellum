"""Premium TTS sidecar — voice store consent gate + HTTP contract.

These tests run WITHOUT chatterbox/torch installed: the engine loads lazily,
so everything except actual synthesis is testable. Synthesis of a consented
clone must fail with 503 (engine unavailable) — never 403 — proving the
consent gate and the engine availability check are independent.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sidecars.premium_tts.voices import (  # noqa: E402
    MIN_REF_BYTES, VoiceStore,
)

FAKE_CLIP = b"RIFF" + b"\x00" * (MIN_REF_BYTES + 1024)


# ── VoiceStore unit tests ─────────────────────────────────────────────────────

def test_store_create_records_sha_and_consent(tmp_path):
    store = VoiceStore(tmp_path)
    v = store.create("Narrator", FAKE_CLIP, consent_ack=True, consent_statement="ok")
    assert len(v.sha256) == 64
    assert v.usable
    assert v.consent.acknowledged_at is not None
    assert (tmp_path / v.file).read_bytes() == FAKE_CLIP


def test_store_unconsented_voice_is_unusable(tmp_path):
    store = VoiceStore(tmp_path)
    v = store.create("Pending", FAKE_CLIP, consent_ack=False, consent_statement="")
    assert not v.usable
    assert v.consent.statement == ""
    # Acknowledging flips it.
    v2 = store.acknowledge_consent(v.id)
    assert v2.usable and v2.consent.acknowledged_at is not None


def test_store_rejects_duplicates_and_bad_input(tmp_path):
    store = VoiceStore(tmp_path)
    store.create("One", FAKE_CLIP, consent_ack=True, consent_statement="ok")
    with pytest.raises(ValueError, match="already registered"):
        store.create("Two", FAKE_CLIP, consent_ack=True, consent_statement="ok")
    with pytest.raises(ValueError, match="too short"):
        store.create("Tiny", b"x" * 100, consent_ack=True, consent_statement="ok")
    with pytest.raises(ValueError, match="name"):
        store.create("   ", FAKE_CLIP[:-1] + b"y", consent_ack=True, consent_statement="ok")


def test_store_delete_removes_clip(tmp_path):
    store = VoiceStore(tmp_path)
    v = store.create("Gone", FAKE_CLIP, consent_ack=True, consent_statement="ok")
    assert store.delete(v.id)
    assert not (tmp_path / v.file).exists()
    assert store.get(v.id) is None
    assert not store.delete(v.id)


# ── HTTP contract tests ───────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sidecars.premium_tts import server
    monkeypatch.setattr(server, "store", VoiceStore(tmp_path))
    return TestClient(server.app)


def test_health_reports_engine_identity(client):
    data = client.get("/health").json()
    assert data["ok"] is True
    assert data["engine"] == "chatterbox"
    assert "consent_statement" in data


def test_clone_lifecycle_and_consent_gate(client):
    # Upload WITHOUT consent → registered but unusable.
    r = client.post(
        "/v1/voices",
        files={"file": ("ref.wav", io.BytesIO(FAKE_CLIP), "audio/wav")},
        data={"name": "Test Voice", "consent_ack": "false"},
    )
    assert r.status_code == 200, r.text
    vid = r.json()["id"]
    assert r.json()["usable"] is False

    # Synthesis with the unconsented clone → 403 (the consent gate).
    r = client.post("/v1/tts", json={"text": "hello", "voice": f"clone:{vid}"})
    assert r.status_code == 403
    assert "consent" in r.json()["detail"].lower()

    # Acknowledge consent → usable.
    r = client.post(f"/v1/voices/{vid}/consent")
    assert r.status_code == 200 and r.json()["usable"] is True

    # Now synthesis passes the gate; without chatterbox installed the engine
    # is unavailable → 503 (cascade falls through), NOT 403.
    r = client.post("/v1/tts", json={"text": "hello", "voice": f"clone:{vid}"})
    assert r.status_code == 503

    # Delete.
    assert client.delete(f"/v1/voices/{vid}").status_code == 200
    assert client.post("/v1/tts", json={"text": "x", "voice": f"clone:{vid}"}).status_code == 404


def test_tts_rejects_empty_text(client):
    assert client.post("/v1/tts", json={"text": "   "}).status_code == 400


# ── Main API breaker unit tests ───────────────────────────────────────────────

def test_premium_breaker_open_close(monkeypatch):
    from orivellum.api.routes import studio
    studio._premium_note_success()
    assert not studio._premium_breaker_open()
    studio._premium_note_failure()
    assert studio._premium_breaker_open()
    st = studio._premium_breaker_status()
    assert st["circuit_open"] and st["retry_in_sec"] > 0
    studio._premium_note_success()
    assert not studio._premium_breaker_open()
    assert studio._premium_breaker_status() == {"circuit_open": False, "retry_in_sec": 0}


def test_breaker_blocks_premium_call(monkeypatch):
    """With the breaker open, _call_premium_tts_sync returns None without
    any network attempt (httpx.post would explode if reached)."""
    from orivellum.api.routes import studio

    class _Serving:
        tts_premium_url = "http://127.0.0.1:9"   # nothing listens here
        tts_premium_ack_license = True

    class _Cfg:
        serving = _Serving()

    import httpx
    def _boom(*a, **k):
        raise AssertionError("network attempted while breaker open")
    studio._premium_note_failure()               # open the breaker
    monkeypatch.setattr(httpx, "post", _boom)
    try:
        assert studio._call_premium_tts_sync("t", "v", 1.0, _Cfg()) is None
    finally:
        studio._premium_note_success()           # leave global state clean


def test_tts_request_quality_defaults_final():
    from orivellum.api.routes.studio import TTSRequest
    assert TTSRequest(text="x").quality == "final"
    assert TTSRequest(text="x", quality="draft").quality == "draft"


def test_breaker_single_flight_until_healthy():
    """Until the sidecar has proven healthy once, only ONE caller may probe;
    concurrent callers must be refused instantly (no 60 s pile-up)."""
    from orivellum.api.routes import studio
    # Reset to cold state: closed breaker, never-healthy, nothing inflight.
    studio._premium_note_failure()
    with studio._premium_breaker_lock:
        studio._premium_unavailable_until = 0.0
        studio._premium_healthy = False
        studio._premium_inflight = False
    try:
        assert studio._premium_try_acquire() is True      # first prober
        assert studio._premium_try_acquire() is False     # concurrent → refused
        studio._premium_note_success()                    # probe succeeded
        assert studio._premium_try_acquire() is True      # healthy → concurrent OK
        assert studio._premium_try_acquire() is True
    finally:
        studio._premium_note_success()


def test_clone_voice_detection():
    from orivellum.api.routes.studio import _is_clone_voice
    assert _is_clone_voice("clone:abc123")
    assert not _is_clone_voice("af_heart")
    assert not _is_clone_voice("")
