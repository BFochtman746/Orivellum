"""Per-chapter quality report for Work audiobook renders.

The QA gate (ffmpeg volumedetect) already measures every segment; these tests
prove the measurements now survive the render as a ``quality_report`` on the
job status: per chapter — segments, cache reuse, loudness, retries, and the
reason any segment was flagged and re-synthesized.
"""

import math
import os
import time

import pytest

os.environ.setdefault("SESSION_SECRET", "test-orivellum-api-key-1234567890abcdef")

CH1_TEXT = "A first chapter with a perfectly healthy narration segment."
CH2_TEXT = "A second chapter that also renders without any trouble at all."


def _sine(amplitude: float):
    import numpy as np

    sr = 24000
    t = np.arange(int(sr * 0.4), dtype=np.float32) / sr
    return (amplitude * np.sin(2 * math.pi * 220.0 * t)).astype(np.float32), sr


class _FakeKokoro:
    """Healthy sine-wave engine; counts synth calls."""

    def __init__(self):
        self.calls = 0

    def create(self, text, voice=None, speed=1.0, lang="en-us"):
        self.calls += 1
        return _sine(0.3)


class _FlakyKokoro(_FakeKokoro):
    """First call yields near-silent audio (fails QA), later calls are fine."""

    def create(self, text, voice=None, speed=1.0, lang="en-us"):
        self.calls += 1
        return _sine(0.0001 if self.calls == 1 else 0.3)


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

    work = db.create_work(title="Quality Report Regression", work_type="writing")
    for title, text in (("ch1", CH1_TEXT), ("ch2", CH2_TEXT)):
        doc = db.create_document(title=title, work_id=work["id"], kind="text")
        with db._lock:
            db._conn.execute("UPDATE documents SET readiness='ready' WHERE id=?", (doc["id"],))
            db._conn.commit()
        db.add_chunk(doc["id"], text, page=0)

    client = TestClient(create_app(), raise_server_exceptions=False, headers=AUTH_HEADERS)
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


def _render(client, wid: str, **overrides) -> dict:
    body = {
        "work_id": wid,
        "voice": "af_heart",
        "speed": 1.0,
        "include_credits": False,
        "acx_mastering": False,
        **overrides,
    }
    r = client.post("/api/studio/tts/work/start", json=body)
    assert r.status_code == 200, r.text[:300]
    return _wait_job(client, r.json()["job_id"])


def test_quality_report_after_clean_render(work_client, monkeypatch):
    from orivellum.api.routes import studio

    client, _cfg, wid = work_client
    monkeypatch.setattr(studio, "_get_kokoro", lambda: _FakeKokoro())

    status = _render(client, wid, include_credits=True)
    assert status["state"] == "done", status
    report = status["quality_report"]

    # Chapter rows in book order, credits row last
    assert [c["title"] for c in report["chapters"]] == ["ch1", "ch2", "Credits"]
    assert [c["kind"] for c in report["chapters"]] == ["chapter", "chapter", "credits"]
    for ch in report["chapters"]:
        assert ch["segments"] == 2  # intro + body / opening + closing
        assert ch["retries"] == 0 and ch["flagged"] == []
        # Sine at 0.3 amplitude ≈ -13 dB mean; just assert plausible audio
        assert ch["mean_db"] is not None and -40 < ch["mean_db"] < -5
        assert ch["peak_db"] is not None and ch["peak_db"] < -0.1

    totals = report["totals"]
    assert totals["segments"] == 6
    assert totals["retries"] == 0 and totals["flagged_segments"] == 0
    assert totals["mean_db"] is not None and totals["peak_db"] is not None


def test_quality_report_records_retried_segment(work_client, monkeypatch):
    from orivellum.api.routes import studio

    client, _cfg, wid = work_client
    flaky = _FlakyKokoro()
    monkeypatch.setattr(studio, "_get_kokoro", lambda: flaky)

    status = _render(client, wid)
    assert status["state"] == "done", status
    report = status["quality_report"]

    ch1 = report["chapters"][0]
    assert ch1["retries"] == 1
    assert len(ch1["flagged"]) == 1
    assert ch1["flagged"][0]["segment"] == 1  # the chapter intro
    assert "near-silent" in ch1["flagged"][0]["reason"]
    assert report["chapters"][1]["retries"] == 0
    assert report["totals"]["retries"] == 1
    assert report["totals"]["flagged_segments"] == 1
    # 4 shipped segments + 1 extra synthesis for the retry
    assert flaky.calls == 5


def test_quality_report_covers_cache_reused_segments(work_client, monkeypatch):
    from orivellum.api.routes import studio

    client, _cfg, wid = work_client
    monkeypatch.setattr(studio, "_get_kokoro", lambda: _FakeKokoro())
    assert _render(client, wid)["state"] == "done"

    replay = _FakeKokoro()
    monkeypatch.setattr(studio, "_get_kokoro", lambda: replay)
    status = _render(client, wid)
    assert status["state"] == "done", status
    assert replay.calls == 0

    report = status["quality_report"]
    totals = report["totals"]
    assert totals["cached_segments"] == totals["segments"] == 4
    # Loudness must be reported for reused segments too (no blind spots)
    for ch in report["chapters"]:
        assert ch["cached_segments"] == ch["segments"] == 2
        assert ch["mean_db"] is not None and ch["peak_db"] is not None
