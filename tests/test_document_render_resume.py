"""Pause/resume for single-document audiobook renders (task: doc resume path).

Work renders already resume from the persistent tts-cache; these tests prove
the single-document path (POST /studio/tts/document) gets the same guarantee:

- POST /studio/tts/document/resume-info reports cached progress for a doc
- a render cancelled midway leaves its finished segments in the cache
- a second render reuses those segments (engine is NOT called again for them)
  and reports the reuse through cached_segments in the job status

The local neural engine (Kokoro) is faked with a sine-wave generator that
passes the QA gate; ffmpeg is required (installed in CI) for QA + concat.
"""

import math
import time

import pytest

# Two paragraphs, each too long to pack together under the 1500-char segment
# cap -> the document splits into exactly two deterministic segments.
_PARA1 = " ".join("The first part of the story keeps going steadily." for _ in range(18))
_PARA2 = " ".join("The second part continues after the interruption." for _ in range(18))
DOC_TEXT = _PARA1 + "\n\n" + _PARA2


class _FakeKokoro:
    """Sine-wave stand-in for the Kokoro engine; counts synth calls."""

    def __init__(self, cancel_after: int | None = None):
        self.calls = 0
        self.cancel_after = cancel_after

    def create(self, text, voice=None, speed=1.0, lang="en-us"):
        import numpy as np

        self.calls += 1
        if self.cancel_after is not None and self.calls == self.cancel_after:
            # Simulate the user pressing Cancel while this segment renders:
            # the event is observed before the NEXT segment starts.
            from orivellum.api.routes import studio

            with studio._doc_tts_jobs_lock:
                for job in studio._doc_tts_jobs.values():
                    ev = job.get("cancel")
                    if ev is not None:
                        ev.set()
        sr = 24000
        t = np.arange(int(sr * 0.4), dtype=np.float32) / sr
        return (0.3 * np.sin(2 * math.pi * 220.0 * t)).astype(np.float32), sr


@pytest.fixture()
def doc_client(tmp_path):
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

    doc = db.create_document(title="Resumable Doc", kind="text")
    with db._lock:
        db._conn.execute("UPDATE documents SET readiness='ready' WHERE id=?", (doc["id"],))
        db._conn.commit()
    db.add_chunk(doc["id"], DOC_TEXT, page=0)

    client = TestClient(create_app(), raise_server_exceptions=False, headers=AUTH_HEADERS)
    return client, cfg, doc["id"]


def _wait_job(client, job_id: str, timeout: float = 90.0) -> dict:
    deadline = time.time() + timeout
    status: dict = {}
    while time.time() < deadline:
        r = client.get(f"/api/studio/tts/document/{job_id}/status")
        assert r.status_code == 200, r.text[:300]
        status = r.json()
        if status["state"] in ("done", "failed", "cancelled", "error"):
            return status
        time.sleep(0.2)
    raise AssertionError(f"document TTS job did not finish in time: {status}")


BODY = {"voice": "af_heart", "speed": 1.0, "acx_mastering": False}


# ── resume-info endpoint ──────────────────────────────────────────────────────


def test_resume_info_missing_doc_404(doc_client):
    client, _cfg, _did = doc_client
    r = client.post("/api/studio/tts/document/resume-info", json={"doc_id": "nope", **BODY})
    assert r.status_code == 404


def test_resume_info_fresh_doc_not_resumable(doc_client, monkeypatch):
    from orivellum.api.routes import studio

    client, _cfg, did = doc_client
    monkeypatch.setattr(studio, "_kokoro_probably_available", lambda: True)
    r = client.post("/api/studio/tts/document/resume-info", json={"doc_id": did, **BODY})
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert data["resumable"] is False
    assert data["cached_segments"] == 0
    assert data["total_segments"] == 2
    assert data["doc_title"] == "Resumable Doc"


def test_resume_info_counts_seeded_cache_entries(doc_client, monkeypatch):
    from orivellum.api.routes import studio
    from orivellum.api.routes.studio import _seg_cache_path, _split_text_into_segments

    client, cfg, did = doc_client
    # CI runners have no Kokoro engine — resume-info only counts cache
    # entries a reachable engine could reuse, so fake availability just like
    # the render tests fake the engine itself.
    monkeypatch.setattr(studio, "_kokoro_probably_available", lambda: True)
    first_seg = _split_text_into_segments(DOC_TEXT)[0]
    _seg_cache_path(cfg, first_seg, "kokoro", BODY["voice"], BODY["speed"]).write_bytes(b"RIFFfake")

    r = client.post("/api/studio/tts/document/resume-info", json={"doc_id": did, **BODY})
    data = r.json()
    assert data["resumable"] is True
    assert data["cached_segments"] == 1
    assert data["total_segments"] == 2


def test_resume_info_ignores_cache_when_no_engine_reachable(doc_client, monkeypatch):
    """No premium, no AI server, no Kokoro -> nothing is reusable."""
    from orivellum.api.routes import studio
    from orivellum.api.routes.studio import _seg_cache_path, _split_text_into_segments

    client, cfg, did = doc_client
    monkeypatch.setattr(studio, "_kokoro_probably_available", lambda: False)
    first_seg = _split_text_into_segments(DOC_TEXT)[0]
    _seg_cache_path(cfg, first_seg, "kokoro", BODY["voice"], BODY["speed"]).write_bytes(b"RIFFfake")

    r = client.post("/api/studio/tts/document/resume-info", json={"doc_id": did, **BODY})
    assert r.json()["resumable"] is False


# ── cancel → resume through a real (fake-engine) render ─────────────────────


def test_cancel_keeps_progress_and_resume_skips_finished_segments(doc_client, monkeypatch):
    from orivellum.api.routes import studio

    client, _cfg, did = doc_client
    # Resume-info must see Kokoro as reachable (no engine on CI runners).
    monkeypatch.setattr(studio, "_kokoro_probably_available", lambda: True)

    # Run 1: "Cancel" lands while segment 1 renders → the first segment
    # finishes (and is cached), the job stops before segment 2 starts.
    cancelling = _FakeKokoro(cancel_after=1)
    monkeypatch.setattr(studio, "_get_kokoro", lambda: cancelling)
    r = client.post("/api/studio/tts/document", json={"doc_id": did, **BODY})
    assert r.status_code == 200, r.text[:300]
    assert r.json()["total_segments"] == 2
    status = _wait_job(client, r.json()["job_id"])
    assert status["state"] == "cancelled"
    assert cancelling.calls == 1

    # The cancelled progress is visible to the resume-info endpoint.
    info = client.post("/api/studio/tts/document/resume-info", json={"doc_id": did, **BODY}).json()
    assert info["resumable"] is True
    assert info["cached_segments"] == 1

    # Run 2: resumes — the engine is only called for the second segment,
    # the first is served from the cache, and the render completes.
    resumed = _FakeKokoro()
    monkeypatch.setattr(studio, "_get_kokoro", lambda: resumed)
    r = client.post("/api/studio/tts/document", json={"doc_id": did, **BODY})
    assert r.status_code == 200, r.text[:300]
    status = _wait_job(client, r.json()["job_id"])
    assert status["state"] == "done", status
    assert resumed.calls == 1, "cached segment must not be re-synthesized"
    assert status["cached_segments"] == 1
    assert status["segments_done"] == 2
    assert status["mp3_path"]

    # A third render is a pure cache replay — zero engine calls.
    replay = _FakeKokoro()
    monkeypatch.setattr(studio, "_get_kokoro", lambda: replay)
    r = client.post("/api/studio/tts/document", json={"doc_id": did, **BODY})
    status = _wait_job(client, r.json()["job_id"])
    assert status["state"] == "done", status
    assert replay.calls == 0
    assert status["cached_segments"] == 2
