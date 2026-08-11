"""Audiobook mastering pipeline — two-pass loudnorm, QA gate, segment cache,
and per-Work voice casting (task: professionally mastered audiobooks).

Uses real ffmpeg-generated audio (available in this environment) so the QA
thresholds and loudness targets are verified against actual measurements,
not mocks.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orivellum.api.routes.studio import (  # noqa: E402
    _MASTER_I,
    _apply_acx_mastering,
    _finalize_segment,
    _measure_loudness,
    _qa_check_audio,
    _seg_cache_get,
    _seg_cache_path,
    _seg_cache_put,
)

# ── Audio fixtures (real ffmpeg output) ───────────────────────────────────────


def _gen(path: Path, filt: str, duration: float = 2.0) -> Path:
    r = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"{filt}:duration={duration}",
            str(path),
        ],
        capture_output=True,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr.decode()[:300]
    return path


@pytest.fixture()
def tone_wav(tmp_path):
    """Healthy speech-loudness stand-in: -20 dB peak sine."""
    raw = _gen(tmp_path / "raw.wav", "sine=frequency=440")
    out = tmp_path / "tone.wav"
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af", "volume=-20dB", str(out)],
        capture_output=True,
        timeout=60,
    )
    assert r.returncode == 0
    return out


@pytest.fixture()
def silent_wav(tmp_path):
    return _gen(tmp_path / "silent.wav", "anullsrc=sample_rate=22050")


@pytest.fixture()
def clipped_wav(tmp_path):
    """Sine boosted past full scale clips at 0 dBFS — the QA gate must flag it."""
    raw = _gen(tmp_path / "loud_raw.wav", "sine=frequency=440")
    out = tmp_path / "clipped.wav"
    r = subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af", "volume=24dB", str(out)],
        capture_output=True,
        timeout=60,
    )
    assert r.returncode == 0
    return out


# ── QA gate ───────────────────────────────────────────────────────────────────


def test_qa_passes_healthy_audio(tone_wav):
    assert _qa_check_audio(tone_wav) is None


def test_qa_flags_near_silence(silent_wav):
    reason = _qa_check_audio(silent_wav)
    assert reason and "silent" in reason


def test_qa_flags_clipping(clipped_wav):
    reason = _qa_check_audio(clipped_wav)
    assert reason and "clipping" in reason


def test_qa_flags_unreadable_file(tmp_path):
    bad = tmp_path / "garbage.wav"
    bad.write_bytes(b"this is not audio at all")
    reason = _qa_check_audio(bad)
    assert reason and "unreadable" in reason


# ── Two-pass mastering ────────────────────────────────────────────────────────


def test_measure_loudness_returns_stats(tone_wav):
    stats = _measure_loudness(str(tone_wav))
    assert stats is not None
    assert "input_i" in stats and "input_tp" in stats and "input_thresh" in stats


def test_mastering_hits_minus_23_lufs(tmp_path, tone_wav):
    out = tmp_path / "mastered.mp3"
    assert _apply_acx_mastering(str(tone_wav), str(out))
    assert out.exists() and out.stat().st_size > 0
    stats = _measure_loudness(str(out))
    assert stats is not None
    measured = float(stats["input_i"])
    # Two-pass loudnorm should land close to the -23 LUFS target.
    assert abs(measured - _MASTER_I) < 3.0, f"got {measured} LUFS"


def test_mastering_fails_gracefully_on_garbage(tmp_path):
    bad = tmp_path / "garbage.wav"
    bad.write_bytes(b"nope")
    assert _apply_acx_mastering(str(bad), str(tmp_path / "out.mp3")) is False


# ── Deterministic segment cache ───────────────────────────────────────────────


@pytest.fixture()
def cache_cfg(tmp_path):
    return SimpleNamespace(data_dir=str(tmp_path / "data"))


def test_cache_key_varies_with_every_input(cache_cfg):
    base = _seg_cache_path(cache_cfg, "hello", "espeak", "af_heart", 1.0)
    assert base == _seg_cache_path(cache_cfg, "hello", "espeak", "af_heart", 1.0)
    assert base != _seg_cache_path(cache_cfg, "hello!", "espeak", "af_heart", 1.0)
    assert base != _seg_cache_path(cache_cfg, "hello", "kokoro", "af_heart", 1.0)
    assert base != _seg_cache_path(cache_cfg, "hello", "espeak", "bm_george", 1.0)
    assert base != _seg_cache_path(cache_cfg, "hello", "espeak", "af_heart", 1.5)


def test_cache_roundtrip_and_engine_priority(cache_cfg, tone_wav):
    _seg_cache_put(cache_cfg, "hi", "espeak", "af_heart", 1.0, tone_wav)
    # Miss for other voice / engine list
    assert _seg_cache_get(cache_cfg, "hi", "bm_george", 1.0, ["espeak"]) is None
    assert _seg_cache_get(cache_cfg, "hi", "af_heart", 1.0, ["kokoro"]) is None
    # Hit; priority list falls through to espeak
    hit = _seg_cache_get(cache_cfg, "hi", "af_heart", 1.0, ["kokoro", "espeak"])
    assert hit is not None and hit.read_bytes() == tone_wav.read_bytes()


def test_cache_hit_is_qa_validated_and_corrupt_entries_evicted(cache_cfg, tone_wav, silent_wav):
    """The cache is untrusted: a corrupt/silent entry must be evicted on read,
    never served into a render."""
    from orivellum.api.routes.studio import _seg_cache_path as _p

    # Poison the cache with garbage bytes under a valid key
    bad = _p(cache_cfg, "poisoned", "espeak", "af_heart", 1.0)
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"not audio")
    assert _seg_cache_get(cache_cfg, "poisoned", "af_heart", 1.0, ["espeak"]) is None
    assert not bad.exists()  # evicted
    # A silent (QA-failing) entry is also evicted
    silent_entry = _p(cache_cfg, "quiet", "espeak", "af_heart", 1.0)
    silent_entry.write_bytes(silent_wav.read_bytes())
    assert _seg_cache_get(cache_cfg, "quiet", "af_heart", 1.0, ["espeak"]) is None
    assert not silent_entry.exists()
    # A healthy entry survives and is returned
    _seg_cache_put(cache_cfg, "good", "espeak", "af_heart", 1.0, tone_wav)
    assert _seg_cache_get(cache_cfg, "good", "af_heart", 1.0, ["espeak"]) is not None


def test_finalize_retries_once_then_caches(cache_cfg, tone_wav, silent_wav, tmp_path):
    calls = []

    def attempt():
        calls.append(1)
        src = silent_wav if len(calls) == 1 else tone_wav
        dst = tmp_path / f"try{len(calls)}.wav"
        dst.write_bytes(src.read_bytes())
        return dst, "espeak"

    out = _finalize_segment(cache_cfg, "text", "af_heart", 1.0, attempt, "segment 0")
    assert out is not None and len(calls) == 2  # retried exactly once
    assert _seg_cache_get(cache_cfg, "text", "af_heart", 1.0, ["espeak"]) is not None


def test_finalize_fails_render_when_segment_stays_bad(cache_cfg, silent_wav, tmp_path):
    def attempt():
        dst = tmp_path / "bad.wav"
        dst.write_bytes(silent_wav.read_bytes())
        return dst, "espeak"

    with pytest.raises(RuntimeError, match="Audio QA failed on segment 7"):
        _finalize_segment(cache_cfg, "text", "af_heart", 1.0, attempt, "segment 7")
    # Failed segments must never be cached
    assert _seg_cache_get(cache_cfg, "text", "af_heart", 1.0, ["espeak"]) is None


def test_finalize_never_caches_ai_engine(cache_cfg, tone_wav, tmp_path):
    def attempt():
        dst = tmp_path / "ai.wav"
        dst.write_bytes(tone_wav.read_bytes())
        return dst, "ai"

    assert _finalize_segment(cache_cfg, "t", "af_heart", 1.0, attempt, "segment 0")
    assert _seg_cache_get(cache_cfg, "t", "af_heart", 1.0, ["ai"]) is None


# ── Voice casting endpoints + render gates ────────────────────────────────────


@pytest.fixture()
def work_client(tmp_path):
    from fastapi.testclient import TestClient

    from orivellum.api import _deps
    from orivellum.api.app import create_app
    from orivellum.configuration.config import OrivellumConfig, ServingConfig
    from orivellum.database.db import OrivellumDB
    from tests.conftest import AUTH_HEADERS

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db = OrivellumDB(str(data_dir / "test.db"))
    cfg = OrivellumConfig(
        data_dir=str(data_dir),
        serving=ServingConfig(base_url="http://localhost:1/api/v1"),
    )
    _deps.init(db=db, cfg=cfg)

    work = db.create_work(title="Casting Test", work_type="writing")
    doc = db.create_document(title="ch1", work_id=work["id"], kind="text")
    with db._lock:
        db._conn.execute("UPDATE documents SET readiness='ready' WHERE id=?", (doc["id"],))
        db._conn.commit()
    db.add_chunk(doc["id"], "A short chapter for the casting tests.", page=0)

    client = TestClient(create_app(), raise_server_exceptions=False, headers=AUTH_HEADERS)
    return client, db, work["id"], doc["id"]


def test_casting_get_lists_chapters_with_no_casting(work_client):
    client, _db, wid, did = work_client
    r = client.get(f"/api/studio/works/{wid}/casting")
    assert r.status_code == 200
    data = r.json()
    assert data["sections"] == {}
    assert [d["id"] for d in data["documents"]] == [did]
    assert data["documents"][0]["voice"] is None


def test_casting_put_roundtrip_and_clear(work_client):
    client, _db, wid, did = work_client
    r = client.put(f"/api/studio/works/{wid}/casting", json={"sections": {did: "bm_george"}})
    assert r.status_code == 200
    got = client.get(f"/api/studio/works/{wid}/casting").json()
    assert got["sections"] == {did: "bm_george"}
    assert got["documents"][0]["voice"] == "bm_george"
    # Empty string clears the assignment
    r = client.put(f"/api/studio/works/{wid}/casting", json={"sections": {did: ""}})
    assert r.status_code == 200 and r.json()["sections"] == {}
    assert client.get(f"/api/studio/works/{wid}/casting").json()["sections"] == {}


def test_casting_persists_narrator_voice_with_the_work(work_client):
    """Saving a casting can also store the Work's default narrator, and the
    GET pre-fills it so the picker survives leaving the Studio."""
    client, _db, wid, did = work_client
    # No narrator saved yet
    assert client.get(f"/api/studio/works/{wid}/casting").json()["narrator_voice"] is None

    r = client.put(
        f"/api/studio/works/{wid}/casting",
        json={"sections": {did: "bm_george"}, "narrator_voice": "af_heart"},
    )
    assert r.status_code == 200 and r.json()["narrator_voice"] == "af_heart"
    got = client.get(f"/api/studio/works/{wid}/casting").json()
    assert got["narrator_voice"] == "af_heart"
    assert got["sections"] == {did: "bm_george"}

    # Omitting narrator_voice leaves the saved default untouched
    r = client.put(f"/api/studio/works/{wid}/casting", json={"sections": {}})
    assert r.status_code == 200
    assert client.get(f"/api/studio/works/{wid}/casting").json()["narrator_voice"] == "af_heart"

    # Unknown narrator is rejected without wiping anything
    r = client.put(
        f"/api/studio/works/{wid}/casting",
        json={"sections": {}, "narrator_voice": "not_a_voice"},
    )
    assert r.status_code == 422
    assert client.get(f"/api/studio/works/{wid}/casting").json()["narrator_voice"] == "af_heart"

    # Clone narrators are allowed; empty string clears the saved default
    r = client.put(
        f"/api/studio/works/{wid}/casting",
        json={"sections": {}, "narrator_voice": "clone:abc123"},
    )
    assert r.status_code == 200 and r.json()["narrator_voice"] == "clone:abc123"
    r = client.put(f"/api/studio/works/{wid}/casting", json={"sections": {}, "narrator_voice": ""})
    assert r.status_code == 200 and r.json()["narrator_voice"] is None
    assert client.get(f"/api/studio/works/{wid}/casting").json()["narrator_voice"] is None


def test_casting_put_accepts_clone_voice_ids(work_client):
    client, _db, wid, did = work_client
    r = client.put(f"/api/studio/works/{wid}/casting", json={"sections": {did: "clone:abc123"}})
    assert r.status_code == 200


def test_casting_put_rejects_unknown_voice_and_foreign_doc(work_client):
    client, _db, wid, did = work_client
    r = client.put(f"/api/studio/works/{wid}/casting", json={"sections": {did: "not_a_voice"}})
    assert r.status_code == 422
    r = client.put(
        f"/api/studio/works/{wid}/casting", json={"sections": {"not-a-doc": "bm_george"}}
    )
    assert r.status_code == 422
    r = client.get("/api/studio/works/nope/casting")
    assert r.status_code == 404


def test_render_rejects_clone_in_casting_without_premium(work_client):
    """Even with a normal narrator, a cloned CHAPTER voice must 503 up front
    in BOTH work pipelines when the premium engine is off."""
    client, _db, wid, did = work_client
    client.put(f"/api/studio/works/{wid}/casting", json={"sections": {did: "clone:abc123"}})
    for endpoint in ("/api/studio/tts/work", "/api/studio/tts/work/start"):
        r = client.post(endpoint, json={"work_id": wid, "voice": "af_heart"})
        assert r.status_code == 503, endpoint
        assert "premium" in r.json()["detail"].lower()


@pytest.mark.skipif(
    not (Path("kokoro-v0_19.onnx").exists() and Path("voices.bin").exists()),
    reason="Kokoro ONNX model files not present (fetched locally, never committed — absent on CI)",
)
def test_render_with_catalog_casting_produces_audio(work_client):
    """Full sync render with a cast catalog voice: passes the QA gate, comes
    out mastered, and populates the segment cache."""
    client, _db, wid, did = work_client
    client.put(f"/api/studio/works/{wid}/casting", json={"sections": {did: "bm_george"}})
    r = client.post(
        "/api/studio/tts/work",
        json={
            "work_id": wid,
            "voice": "af_heart",
            "speed": 1.0,
            "include_credits": False,
            "acx_mastering": True,
        },
    )
    assert r.status_code == 200, r.text[:300]
    assert r.headers["content-type"].startswith("audio/")
    assert len(r.content) > 1000
    # Deterministic cache now holds the espeak segments
    from orivellum.api import _deps

    cache_dir = Path(_deps.get_config().data_dir) / "tts-cache"
    assert cache_dir.exists() and any(cache_dir.iterdir())


def test_doc_tts_request_masters_by_default():
    from orivellum.api.routes.studio import DocumentTTSRequest

    assert DocumentTTSRequest(doc_id="x").acx_mastering is True
