"""Pause/resume for long Work audiobook renders (task: resume path).

An interrupted render keeps every finished segment in the persistent
tts-cache, so a re-run with the same voice/speed fast-forwards through them
instead of starting over. These tests prove:

- POST /studio/tts/work/resume-info reports per-chapter cached progress
- a render paused midway leaves its finished segments in the cache
- a second render reuses those segments (engine is NOT called again for them)
  and reports the reuse through cached_segments in the job status

The local neural engine (Kokoro) is faked with a sine-wave generator that
passes the QA gate; ffmpeg is required (installed in CI) for QA + concat.
"""

import math
import os
import time

import pytest

os.environ.setdefault("SESSION_SECRET", "test-orivellum-api-key-1234567890abcdef")

CH1_TEXT = "Once upon a time there was a resumable render."
CH2_TEXT = "The second chapter continues where the first left off."


class _FakeKokoro:
    """Sine-wave stand-in for the Kokoro engine; counts synth calls."""

    def __init__(self, cancel_after: int | None = None):
        self.calls = 0
        self.cancel_after = cancel_after

    def create(self, text, voice=None, speed=1.0, lang="en-us"):
        import numpy as np

        self.calls += 1
        if self.cancel_after is not None and self.calls == self.cancel_after:
            # Simulate the user pressing Pause while this segment renders:
            # the flag is observed before the NEXT segment starts.
            from orivellum.api.routes import studio

            with studio._work_tts_jobs_lock:
                for job in studio._work_tts_jobs.values():
                    job["cancel_requested"] = True
        sr = 24000
        t = np.arange(int(sr * 0.4), dtype=np.float32) / sr
        return (0.3 * np.sin(2 * math.pi * 220.0 * t)).astype(np.float32), sr


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

    work = db.create_work(title="Resume Regression", work_type="writing")
    for title, text in (("ch1", CH1_TEXT), ("ch2", CH2_TEXT)):
        doc = db.create_document(title=title, work_id=work["id"], kind="text")
        with db._lock:
            db._conn.execute(
                "UPDATE documents SET readiness='ready' WHERE id=?", (doc["id"],)
            )
            db._conn.commit()
        db.add_chunk(doc["id"], text, page=0)

    client = TestClient(create_app(), raise_server_exceptions=False,
                        headers=AUTH_HEADERS)
    return client, cfg, work["id"]


def _wait_job(client, job_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    status: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/studio/tts/work/{job_id}/status")
        assert r.status_code == 200, r.text[:300]
        status = r.json()
        if status["state"] in ("done", "failed", "cancelled"):
            return status
        time.sleep(0.2)
    raise AssertionError(f"work TTS job did not finish in time: {status}")


BODY = {"voice": "af_heart", "speed": 1.0,
        "include_credits": False, "acx_mastering": False}


# ── resume-info endpoint ──────────────────────────────────────────────────────

def test_resume_info_missing_work_404(work_client):
    client, _cfg, _wid = work_client
    r = client.post("/api/studio/tts/work/resume-info",
                    json={"work_id": "nope", **BODY})
    assert r.status_code == 404


def test_resume_info_fresh_work_not_resumable(work_client):
    client, _cfg, wid = work_client
    r = client.post("/api/studio/tts/work/resume-info",
                    json={"work_id": wid, **BODY})
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert data["resumable"] is False
    assert data["cached_segments"] == 0
    assert data["complete_chapters"] == 0
    assert [c["title"] for c in data["chapters"]] == ["ch1", "ch2"]
    # Each short chapter = title intro + one body segment
    assert all(c["segments"] == 2 for c in data["chapters"])
    assert data["total_segments"] == 4


def test_resume_info_includes_credits_segments(work_client):
    client, _cfg, wid = work_client
    r = client.post("/api/studio/tts/work/resume-info",
                    json={"work_id": wid, **BODY, "include_credits": True})
    assert r.status_code == 200
    assert r.json()["total_segments"] == 6  # 4 chapter + opening/closing credits


def test_resume_info_reports_cached_chapter_complete(work_client):
    """Seeding cache entries for every segment of ch1 marks it done."""
    from orivellum.api.routes.studio import (
        _chapter_segment_texts,
        _seg_cache_path,
    )

    client, cfg, wid = work_client
    for seg_text in _chapter_segment_texts("ch1", CH1_TEXT):
        p = _seg_cache_path(cfg, seg_text, "kokoro", BODY["voice"], BODY["speed"])
        p.write_bytes(b"RIFFfake")

    r = client.post("/api/studio/tts/work/resume-info",
                    json={"work_id": wid, **BODY})
    data = r.json()
    assert data["resumable"] is True
    assert data["complete_chapters"] == 1
    ch1, ch2 = data["chapters"]
    assert ch1["complete"] is True and ch1["cached_segments"] == 2
    assert ch2["complete"] is False and ch2["cached_segments"] == 0
    assert data["cached_segments"] == 2


# ── pause → resume through a real (fake-engine) render ───────────────────────

def test_pause_keeps_progress_and_resume_skips_finished_segments(
    work_client, monkeypatch
):
    from orivellum.api.routes import studio

    client, _cfg, wid = work_client

    # Run 1: "Pause" lands while segment 2 renders → chapter 1 finishes,
    # the job stops before chapter 2 ever starts.
    pausing = _FakeKokoro(cancel_after=2)
    monkeypatch.setattr(studio, "_get_kokoro", lambda: pausing)
    r = client.post("/api/studio/tts/work/start", json={"work_id": wid, **BODY})
    assert r.status_code == 200, r.text[:300]
    status = _wait_job(client, r.json()["job_id"])
    assert status["state"] == "cancelled"
    assert pausing.calls == 2

    # The paused progress is visible to the resume-info endpoint.
    info = client.post("/api/studio/tts/work/resume-info",
                       json={"work_id": wid, **BODY}).json()
    assert info["resumable"] is True
    assert info["cached_segments"] == 2
    assert info["chapters"][0]["complete"] is True

    # Run 2: resumes — the engine is only called for chapter 2's segments,
    # chapter 1 is served from the cache, and the render completes.
    resumed = _FakeKokoro()
    monkeypatch.setattr(studio, "_get_kokoro", lambda: resumed)
    r = client.post("/api/studio/tts/work/start", json={"work_id": wid, **BODY})
    assert r.status_code == 200, r.text[:300]
    status = _wait_job(client, r.json()["job_id"])
    assert status["state"] == "done", status
    assert resumed.calls == 2, "cached chapter must not be re-synthesized"
    assert status["cached_segments"] == 2
    assert status["segments_done"] == 4
    assert status["total_segments"] == 4
    assert status["result"]["path"]

    # A third render is a pure cache replay — zero engine calls.
    replay = _FakeKokoro()
    monkeypatch.setattr(studio, "_get_kokoro", lambda: replay)
    r = client.post("/api/studio/tts/work/start", json={"work_id": wid, **BODY})
    status = _wait_job(client, r.json()["job_id"])
    assert status["state"] == "done", status
    assert replay.calls == 0
    assert status["cached_segments"] == 4
