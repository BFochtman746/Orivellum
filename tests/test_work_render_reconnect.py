"""Reconnecting to a running Work audiobook render (task: reconnect path).

A Work render deliberately keeps going when the Studio UI unmounts. These
tests prove the server side of re-attaching:

- GET /studio/tts/work/active lists only non-terminal jobs, with the
  work_id/started_at fields the UI needs to rediscover them
- internal bookkeeping (cancel_requested, result payload) is never exposed
- POST /studio/tts/work/start returns 409 with the live job id when a render
  for the same Work is already in progress (duplicate-render guard)
- terminal jobs do NOT block a new render for the same Work
"""

import time

import pytest


@pytest.fixture()
def reconnect_client(tmp_path):
    from fastapi.testclient import TestClient

    from orivellum.api import _deps
    from orivellum.api.app import create_app
    from orivellum.api.routes import studio
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

    client = TestClient(create_app(), raise_server_exceptions=False, headers=AUTH_HEADERS)

    # The job registry is module-global — snapshot and restore it so seeded
    # jobs can never leak into other tests in the same process.
    with studio._work_tts_jobs_lock:
        saved = dict(studio._work_tts_jobs)
        studio._work_tts_jobs.clear()
    yield client, studio
    with studio._work_tts_jobs_lock:
        studio._work_tts_jobs.clear()
        studio._work_tts_jobs.update(saved)


def _seed(studio, job_id: str, work_id: str, state: str, **extra):
    with studio._work_tts_jobs_lock:
        studio._work_tts_jobs[job_id] = {
            "state": state,
            "work_id": work_id,
            "started_at": time.time(),
            "chapter_idx": 1,
            "total_chapters": 3,
            "chapter_title": "Chapter Two",
            "work_title": "Seeded Work",
            "spatial": False,
            "segments_done": 4,
            "cached_segments": 2,
            "total_segments": 12,
            "cancel_requested": False,
            **extra,
        }


BODY = {"voice": "af_heart", "speed": 1.0, "include_credits": False, "acx_mastering": False}


# ── GET /studio/tts/work/active ───────────────────────────────────────────────


def test_active_empty(reconnect_client):
    client, _studio = reconnect_client
    r = client.get("/api/studio/tts/work/active")
    assert r.status_code == 200, r.text[:300]
    assert r.json() == {"jobs": []}


def test_active_lists_only_non_terminal(reconnect_client):
    client, studio = reconnect_client
    _seed(studio, "job-running", "work-a", "running")
    _seed(studio, "job-starting", "work-b", "starting")
    for i, state in enumerate(("done", "failed", "cancelled")):
        _seed(studio, f"job-{state}", f"work-t{i}", state)

    r = client.get("/api/studio/tts/work/active")
    assert r.status_code == 200, r.text[:300]
    jobs = r.json()["jobs"]
    assert {j["job_id"] for j in jobs} == {"job-running", "job-starting"}


def test_active_exposes_reattach_fields_and_hides_internals(reconnect_client):
    client, studio = reconnect_client
    _seed(studio, "job-1", "work-a", "running", result={"path": "secret.mp3"})

    job = client.get("/api/studio/tts/work/active").json()["jobs"][0]
    # Everything the UI needs to re-attach its progress view:
    assert job["work_id"] == "work-a"
    assert job["work_title"] == "Seeded Work"
    assert job["chapter_idx"] == 1
    assert job["total_chapters"] == 3
    assert job["segments_done"] == 4
    assert job["total_segments"] == 12
    assert job["cached_segments"] == 2
    assert isinstance(job["started_at"], float)
    # Internal bookkeeping stays internal:
    assert "cancel_requested" not in job
    assert "result" not in job


def test_active_sorted_newest_first(reconnect_client):
    client, studio = reconnect_client
    _seed(studio, "job-old", "work-a", "running")
    _seed(studio, "job-new", "work-b", "running")
    with studio._work_tts_jobs_lock:
        studio._work_tts_jobs["job-old"]["started_at"] -= 100.0

    jobs = client.get("/api/studio/tts/work/active").json()["jobs"]
    assert [j["job_id"] for j in jobs] == ["job-new", "job-old"]


# ── duplicate-render guard on /studio/tts/work/start ─────────────────────────


def test_start_conflicts_with_active_job_for_same_work(reconnect_client):
    client, studio = reconnect_client
    _seed(studio, "job-live", "work-a", "running")

    r = client.post("/api/studio/tts/work/start", json={"work_id": "work-a", **BODY})
    assert r.status_code == 409, r.text[:300]
    detail = r.json()["detail"]
    # The client re-attaches to this id instead of showing an error.
    assert detail["job_id"] == "job-live"
    assert detail["work_id"] == "work-a"


def test_start_not_blocked_by_terminal_job(reconnect_client):
    client, studio = reconnect_client
    _seed(studio, "job-finished", "work-a", "done")

    # The guard must not fire — the request proceeds to the Work lookup,
    # which 404s because this Work doesn't exist in the test DB.
    r = client.post("/api/studio/tts/work/start", json={"work_id": "work-a", **BODY})
    assert r.status_code == 404, r.text[:300]


def test_start_not_blocked_by_other_works_job(reconnect_client):
    client, studio = reconnect_client
    _seed(studio, "job-other", "work-b", "running")

    r = client.post("/api/studio/tts/work/start", json={"work_id": "work-a", **BODY})
    assert r.status_code == 404, r.text[:300]


def test_active_route_not_shadowed_by_job_id_status(reconnect_client):
    """'active' must never be parsed as a job_id by the status route."""
    client, _studio = reconnect_client
    r = client.get("/api/studio/tts/work/active/status")
    assert r.status_code == 404
