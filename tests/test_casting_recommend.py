"""AI chapter casting recommender: POST /studio/works/{id}/casting/recommend.

Detects POV/character per chapter via the LLM and returns a casting map that
pre-fills the Chapter Voices editor. Suggestions are never persisted by the
recommender itself — the user accepts them via PUT /casting.
"""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture()
def casting_client(tmp_path):
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

    work = db.create_work(title="Casting Recommend Test", work_type="writing")
    doc_ids = []
    for i, text in enumerate(
        ["Mara crept along the wall.", "Torin sharpened his blade.", "Mara ran."]
    ):
        doc = db.create_document(title=f"ch{i + 1}", work_id=work["id"], kind="text")
        with db._lock:
            db._conn.execute("UPDATE documents SET readiness='ready' WHERE id=?", (doc["id"],))
            db._conn.commit()
        db.add_chunk(doc["id"], text, page=0)
        doc_ids.append(doc["id"])

    client = TestClient(create_app(), raise_server_exceptions=False, headers=AUTH_HEADERS)
    return client, db, work["id"], doc_ids


def _llm_ok(payload: dict):
    return SimpleNamespace(ok=True, text=json.dumps(payload))


def test_recommend_404_for_unknown_work(casting_client):
    client, *_ = casting_client
    r = client.post("/api/studio/works/nope/casting/recommend")
    assert r.status_code == 404


def test_recommend_no_ready_chapters_returns_no_content(casting_client):
    client, db, *_ = casting_client
    empty = db.create_work(title="Empty", work_type="writing")
    r = client.post(f"/api/studio/works/{empty['id']}/casting/recommend")
    assert r.status_code == 200
    data = r.json()
    assert data["no_content"] is True
    assert data["sections"] == {}
    assert data["chapters"] == []


def test_recommend_happy_path_builds_sections_and_hints(casting_client):
    client, _db, wid, doc_ids = casting_client
    payload = {
        "casting_analysis": "Two alternating POV characters.",
        "narrator_voice_id": "bm_george",
        "casting": [
            {
                "doc_id": doc_ids[0],
                "character": "Mara",
                "voice_id": "af_sarah",
                "rationale": "Young, bright voice for Mara.",
            },
            {
                "doc_id": doc_ids[1],
                "character": "Torin",
                "voice_id": "am_adam",
                "rationale": "Gravelly voice for Torin.",
            },
            {
                "doc_id": doc_ids[2],
                "character": "Mara",
                "voice_id": "af_sarah",
                "rationale": "Same voice as her earlier chapter.",
            },
        ],
    }
    with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_ok(payload)):
        r = client.post(f"/api/studio/works/{wid}/casting/recommend")
    assert r.status_code == 200
    data = r.json()
    assert data["fallback"] is False
    assert data["narrator_voice_id"] == "bm_george"
    assert data["sections"] == {
        doc_ids[0]: "af_sarah",
        doc_ids[1]: "am_adam",
        doc_ids[2]: "af_sarah",
    }
    chapters = {c["id"]: c for c in data["chapters"]}
    assert len(chapters) == 3
    assert chapters[doc_ids[0]]["character"] == "Mara"
    assert chapters[doc_ids[1]]["character"] == "Torin"
    # Suggestions are never persisted — the saved casting stays empty
    assert client.get(f"/api/studio/works/{wid}/casting").json()["sections"] == {}


def test_recommend_drops_unknown_voices_and_foreign_docs(casting_client):
    client, _db, wid, doc_ids = casting_client
    payload = {
        "narrator_voice_id": "not_a_voice",
        "casting": [
            {"doc_id": doc_ids[0], "character": "Mara", "voice_id": "not_a_voice"},
            {"doc_id": "foreign-doc", "character": "Ghost", "voice_id": "af_sarah"},
            {"doc_id": doc_ids[1], "character": "Torin", "voice_id": "am_adam"},
        ],
    }
    with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_ok(payload)):
        r = client.post(f"/api/studio/works/{wid}/casting/recommend")
    data = r.json()
    # Unknown narrator and unknown chapter voice fall back to the default
    assert data["narrator_voice_id"] == ""
    assert data["sections"] == {doc_ids[1]: "am_adam"}
    chapters = {c["id"]: c for c in data["chapters"]}
    assert chapters[doc_ids[0]]["voice_id"] == ""  # invalid voice → narrator default
    assert "foreign-doc" not in chapters


def test_recommend_empty_voice_means_narrator_default(casting_client):
    client, _db, wid, doc_ids = casting_client
    payload = {
        "narrator_voice_id": "bm_george",
        "casting": [{"doc_id": d, "character": "Narrator", "voice_id": ""} for d in doc_ids],
    }
    with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_ok(payload)):
        r = client.post(f"/api/studio/works/{wid}/casting/recommend")
    data = r.json()
    assert data["sections"] == {}
    assert all(c["voice_id"] == "" for c in data["chapters"])


def test_recommend_llm_failure_returns_clean_fallback(casting_client):
    client, _db, wid, doc_ids = casting_client
    fail = SimpleNamespace(ok=False, text="")
    with patch("orivellum.capabilities.llm.llm_call", return_value=fail):
        r = client.post(f"/api/studio/works/{wid}/casting/recommend")
    assert r.status_code == 200
    data = r.json()
    assert data["fallback"] is True
    assert data["sections"] == {}
    assert [c["id"] for c in data["chapters"]] == doc_ids


def test_recommend_malformed_json_returns_clean_fallback(casting_client):
    client, _db, wid, _doc_ids = casting_client
    garbage = SimpleNamespace(ok=True, text="I think chapter one should be...")
    with patch("orivellum.capabilities.llm.llm_call", return_value=garbage):
        r = client.post(f"/api/studio/works/{wid}/casting/recommend")
    data = r.json()
    assert data["fallback"] is True
    assert data["sections"] == {}


def test_recommend_non_object_json_returns_clean_fallback(casting_client):
    client, _db, wid, _doc_ids = casting_client
    for text in ('["af_sarah"]', '"af_sarah"', "42"):
        with patch(
            "orivellum.capabilities.llm.llm_call",
            return_value=SimpleNamespace(ok=True, text=text),
        ):
            r = client.post(f"/api/studio/works/{wid}/casting/recommend")
        assert r.status_code == 200
        data = r.json()
        assert data["fallback"] is True
        assert data["sections"] == {}


def test_recommend_survives_wrongly_typed_fields(casting_client):
    client, _db, wid, doc_ids = casting_client
    payload = {
        "narrator_voice_id": {"nested": "object"},
        "casting": [
            {"doc_id": ["list"], "character": "X", "voice_id": "af_sarah"},
            {"doc_id": doc_ids[0], "character": "Mara", "voice_id": {"bad": 1}},
            {"doc_id": doc_ids[1], "character": 42, "voice_id": "am_adam"},
            "not-a-dict",
        ],
    }
    with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_ok(payload)):
        r = client.post(f"/api/studio/works/{wid}/casting/recommend")
    assert r.status_code == 200
    data = r.json()
    assert data["fallback"] is False
    assert data["narrator_voice_id"] == ""
    # Malformed voice → narrator default; malformed doc_id entry dropped entirely
    assert data["sections"] == {doc_ids[1]: "am_adam"}


def test_recommend_casting_not_a_list_is_tolerated(casting_client):
    client, _db, wid, _doc_ids = casting_client
    payload = {"narrator_voice_id": "bm_george", "casting": {"unexpected": "object"}}
    with patch("orivellum.capabilities.llm.llm_call", return_value=_llm_ok(payload)):
        r = client.post(f"/api/studio/works/{wid}/casting/recommend")
    data = r.json()
    assert data["fallback"] is False
    assert data["sections"] == {}
    assert data["narrator_voice_id"] == "bm_george"


def test_recommend_strips_code_fences(casting_client):
    client, _db, wid, doc_ids = casting_client
    payload = {
        "narrator_voice_id": "bm_george",
        "casting": [{"doc_id": doc_ids[0], "character": "Mara", "voice_id": "af_sarah"}],
    }
    fenced = SimpleNamespace(ok=True, text="```json\n" + json.dumps(payload) + "\n```")
    with patch("orivellum.capabilities.llm.llm_call", return_value=fenced):
        r = client.post(f"/api/studio/works/{wid}/casting/recommend")
    data = r.json()
    assert data["fallback"] is False
    assert data["sections"] == {doc_ids[0]: "af_sarah"}
