"""Built-in operation steps — honest checkpoints and safe scheduling.

Proves the review-driven guarantees around the flagship playbook steps:

- wait_for_extraction enumerates the whole Work and judges the outcome
  honestly: documents in 'error' fail the step (unless allow_failed), and
  zero 'ready' documents fails instead of letting a later render step fail
  more confusingly
- the studio work-render start route never leaves a phantom 'starting' job
  when the executor rejects the background submission (it cleans up and
  answers 503), so the render_audiobook step fails fast instead of polling
  an unscheduled job forever
"""

from __future__ import annotations

from pathlib import Path

import pytest

from orivellum.capabilities.operations.registry import OpContext, get_op_registry
from orivellum.database.db import OrivellumDB


@pytest.fixture()
def db(tmp_path):
    d = OrivellumDB(str(Path(tmp_path) / "test.db"))
    yield d
    d.close()


def _ctx(db, work_id):
    return OpContext(
        db=db,
        cfg=None,
        operation_id="op-test",
        work_id=work_id,
        params={},
        results={},
        should_stop=lambda: False,
    )


def _make_doc(db, work_id, title, readiness):
    doc = db.create_document(title=title, work_id=work_id, kind="text")
    if readiness != "imported":
        db.update_document_extracted(
            doc["id"],
            extracted_text="hello world" if readiness == "ready" else "",
            word_count=2 if readiness == "ready" else 0,
            readiness=readiness,
            error_message="boom" if readiness == "error" else None,
        )
    return doc


def _wait_action():
    return get_op_registry()["wait_for_extraction"]


def test_wait_for_extraction_succeeds_when_docs_ready(db):
    work = db.create_work("Test Work")
    _make_doc(db, work["id"], "A", "ready")
    _make_doc(db, work["id"], "B", "ready")

    result = _wait_action().execute(_ctx(db, work["id"]), {})
    assert result["documents"] == 2
    assert result["by_readiness"] == {"ready": 2}
    assert "2 of 2" in result["summary"]


def test_wait_for_extraction_fails_on_error_docs(db):
    work = db.create_work("Test Work")
    _make_doc(db, work["id"], "Good", "ready")
    _make_doc(db, work["id"], "Broken", "error")

    with pytest.raises(RuntimeError, match="failed extraction"):
        _wait_action().execute(_ctx(db, work["id"]), {})

    # allow_failed lets a partially-usable Work proceed, and says so.
    result = _wait_action().execute(_ctx(db, work["id"]), {"allow_failed": True})
    assert result["by_readiness"] == {"ready": 1, "error": 1}
    assert "failed, continuing" in result["summary"]


def test_wait_for_extraction_fails_when_nothing_ready(db):
    work = db.create_work("Test Work")
    _make_doc(db, work["id"], "Scan", "no_text")

    with pytest.raises(RuntimeError, match="No documents are ready"):
        _wait_action().execute(_ctx(db, work["id"]), {})

    # An empty Work is equally not-success.
    empty = db.create_work("Empty Work")
    with pytest.raises(RuntimeError, match="No documents are ready"):
        _wait_action().execute(_ctx(db, empty["id"]), {})


# ── Studio scheduling rejection ───────────────────────────────────────────────


@pytest.fixture()
def studio_client(tmp_path):
    from fastapi.testclient import TestClient

    from orivellum.api import _deps
    from orivellum.api.app import create_app
    from orivellum.api.routes import studio
    from orivellum.configuration.config import OrivellumConfig, ServingConfig
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

    with studio._work_tts_jobs_lock:
        saved = dict(studio._work_tts_jobs)
        studio._work_tts_jobs.clear()
    yield client, studio, db
    with studio._work_tts_jobs_lock:
        studio._work_tts_jobs.clear()
        studio._work_tts_jobs.update(saved)


def test_render_start_rejected_submit_cleans_up_and_503s(studio_client, monkeypatch):
    """If the executor refuses the job, no phantom 'starting' entry may remain."""
    client, studio, db = studio_client

    work = db.create_work("Render Work")
    doc = db.create_document(title="Chapter 1", work_id=work["id"], kind="text")
    db.update_document_extracted(doc["id"], "Some narratable text. " * 30, 120, "ready")
    db.add_chunk(doc["id"], "Some narratable text. " * 30)

    from orivellum.api import executor

    monkeypatch.setattr(executor, "submit_bg", lambda *a, **k: False)

    resp = client.post("/api/studio/tts/work/start", json={"work_id": work["id"]})
    assert resp.status_code == 503
    assert "too busy" in resp.json()["detail"]

    # The registry must be clean: nothing to poll, and a retry is not blocked
    # by the duplicate-render (409) guard.
    with studio._work_tts_jobs_lock:
        assert studio._work_tts_jobs == {}
