"""Drafting cockpit routes (task: author contracts + run LOOM from the app).

Covers the HTTP layer added on top of the merged LOOM engine:
  1. /works/{id}/loom/chapters — per-chapter contract status, persona
     readiness, plain-language problems, draft_ready flag, active runs.
  2. GET/PUT chapter contract — validation mirrors the engine's refusals;
     meta is MERGED (other keys survive), never replaced.
  3. POST contract/suggest — deterministic pre-fill, never persisted.
  4. GET /loom/revisions/{id} — full text (list endpoint omits it).
  5. GET /loom/runs/{id} — includes the chapter's escalation findings.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from orivellum.database.db import OrivellumDB, _now
from tests.conftest import AUTH_HEADERS


def _make_db(tmp: str):
    from orivellum.api import _deps
    from orivellum.configuration.config import OrivellumConfig

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
    from fastapi.testclient import TestClient

    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig

    with TestClient(app, headers=AUTH_HEADERS) as c:
        _deps.init(db=db, cfg=OrivellumConfig(data_dir=str(Path(db._path).parent)))
        yield c


@pytest.fixture()
def work_id(db):
    return db.create_work("Cockpit Book", work_type="writing")["id"]


def _persona(db, work_id, name, approve=True):
    pid = db.create_loom_persona(work_id, name, {"role": "traveler"})
    if approve:
        assert db.resolve_loom_persona(pid, decision="approved", author="Brian") == "ok"
    return pid


def _chapter(db, work_id, seq, *, contract=None, status="draft", text=None, extra_meta=None):
    oid = db._create_object("book_chapter")
    meta = dict(extra_meta or {})
    if contract is not None:
        meta["contract"] = contract
    with db._lock:
        db._conn.execute(
            """INSERT INTO book_chapters(id, work_id, seq, level, title, text,
               source_doc_id, status, meta, created_at, updated_at)
               VALUES(?,?,?,1,?,?,NULL,?,?,?,?)""",
            (oid, work_id, seq, f"Chapter {seq}", text, status, json.dumps(meta), _now(), _now()),
        )
        db._conn.commit()
    return oid


def _contract(**over):
    c = {"beat": "Mara counts the wagons", "word_range": [50, 400], "cast": ["Mara"], "act": 1}
    c.update(over)
    return c


BASE = "/api/works/{}/loom/chapters"


# ─── 1. Chapter list / readiness ─────────────────────────────────────────────


class TestChapterList:
    def test_unknown_work_404(self, client):
        assert client.get(BASE.format("nope")).status_code == 404

    def test_no_contract_is_not_ready(self, client, db, work_id):
        _chapter(db, work_id, 1)
        [ch] = client.get(BASE.format(work_id)).json()["chapters"]
        assert ch["draft_ready"] is False
        assert ch["contract"] is None
        assert "no contract yet" in ch["problems"]

    def test_ready_when_contract_and_personas_approved(self, client, db, work_id):
        _persona(db, work_id, "Mara")
        _chapter(db, work_id, 1, contract=_contract())
        [ch] = client.get(BASE.format(work_id)).json()["chapters"]
        assert ch["draft_ready"] is True
        assert ch["problems"] == []
        assert ch["cast_status"] == [{"name": "Mara", "status": "approved"}]

    def test_unapproved_persona_blocks(self, client, db, work_id):
        _persona(db, work_id, "Mara", approve=False)
        _chapter(db, work_id, 1, contract=_contract())
        [ch] = client.get(BASE.format(work_id)).json()["chapters"]
        assert ch["draft_ready"] is False
        assert ch["cast_status"][0]["status"] == "proposed"
        assert any("proposed" in p for p in ch["problems"])

    def test_missing_persona_blocks(self, client, db, work_id):
        _chapter(db, work_id, 1, contract=_contract(cast=["Ghost"]))
        [ch] = client.get(BASE.format(work_id)).json()["chapters"]
        assert ch["cast_status"][0]["status"] == "missing"
        assert ch["draft_ready"] is False

    def test_approved_chapter_never_redrafted(self, client, db, work_id):
        _persona(db, work_id, "Mara")
        _chapter(db, work_id, 1, contract=_contract(), status="approved")
        [ch] = client.get(BASE.format(work_id)).json()["chapters"]
        assert ch["draft_ready"] is False
        assert any("approved" in p for p in ch["problems"])

    def test_active_run_surfaces(self, client, db, work_id):
        _persona(db, work_id, "Mara")
        cid = _chapter(db, work_id, 1, contract=_contract())
        run_id = db.create_loom_run(work_id, cid)
        [ch] = client.get(BASE.format(work_id)).json()["chapters"]
        assert ch["active_run_id"] == run_id


# ─── 2. Contract GET/PUT ─────────────────────────────────────────────────────


class TestContract:
    def _url(self, work_id, cid):
        return f"/api/works/{work_id}/loom/chapters/{cid}/contract"

    def test_404_wrong_work(self, client, db, work_id):
        other = db.create_work("Other", work_type="writing")["id"]
        cid = _chapter(db, work_id, 1)
        assert client.get(self._url(other, cid)).status_code == 404

    def test_put_then_get_roundtrip(self, client, db, work_id):
        cid = _chapter(db, work_id, 1)
        body = _contract(location="the yard")
        r = client.put(self._url(work_id, cid), json=body)
        assert r.status_code == 200
        assert r.json()["problems"] == []
        got = client.get(self._url(work_id, cid)).json()
        assert got["contract"]["beat"] == body["beat"]
        assert got["contract"]["location"] == "the yard"

    def test_meta_is_merged_not_replaced(self, client, db, work_id):
        cid = _chapter(db, work_id, 1, extra_meta={"scene_count": 3})
        client.put(self._url(work_id, cid), json=_contract())
        with db._lock:
            meta = json.loads(
                db._conn.execute(
                    "SELECT meta FROM book_chapters WHERE id=?", (cid,)
                ).fetchone()["meta"]
            )
        assert meta["scene_count"] == 3
        assert meta["contract"]["beat"]

    def test_competing_meta_write_survives_save(self, client, db, work_id):
        """A meta key written AFTER the route would have read the row must
        survive: the merge happens under the same lock as the write, so the
        saved snapshot can never be stale."""
        cid = _chapter(db, work_id, 1)
        # Simulate a pipeline worker landing metadata just before the save.
        with db._lock:
            db._conn.execute(
                "UPDATE book_chapters SET meta=? WHERE id=?",
                (json.dumps({"extraction": {"scenes": 4}}), cid),
            )
            db._conn.commit()
        assert client.put(self._url(work_id, cid), json=_contract()).status_code == 200
        with db._lock:
            meta = json.loads(
                db._conn.execute(
                    "SELECT meta FROM book_chapters WHERE id=?", (cid,)
                ).fetchone()["meta"]
            )
        assert meta["extraction"] == {"scenes": 4}
        assert meta["contract"]["beat"]

    def test_malformed_legacy_meta_is_tolerated(self, client, db, work_id):
        """Legacy rows with non-JSON or non-dict meta: readiness reports
        'no contract yet' and a save replaces the junk rather than crashing."""
        cid = _chapter(db, work_id, 1)
        with db._lock:
            db._conn.execute(
                "UPDATE book_chapters SET meta='not json' WHERE id=?", (cid,)
            )
            db._conn.commit()
        [ch] = client.get(BASE.format(work_id)).json()["chapters"]
        assert ch["contract"] is None
        assert "no contract yet" in ch["problems"]
        assert client.put(self._url(work_id, cid), json=_contract()).status_code == 200
        got = client.get(self._url(work_id, cid)).json()
        assert got["contract"]["beat"]

    @pytest.mark.parametrize(
        "bad",
        [
            _contract(beat="   "),
            _contract(word_range=[400, 50]),
            _contract(word_range=[0, 50]),
            _contract(cast=["  "]),
            _contract(cast=["Mara", "mara"]),
        ],
    )
    def test_invalid_contracts_refused(self, client, db, work_id, bad):
        cid = _chapter(db, work_id, 1)
        assert client.put(self._url(work_id, cid), json=bad).status_code == 422
        # nothing persisted
        assert client.get(self._url(work_id, cid)).json()["contract"] is None


# ─── 3. Suggest ──────────────────────────────────────────────────────────────


class TestSuggest:
    def _url(self, work_id, cid):
        return f"/api/works/{work_id}/loom/chapters/{cid}/contract/suggest"

    def test_cast_from_text_and_range_from_words(self, client, db, work_id):
        _persona(db, work_id, "Mara")
        _persona(db, work_id, "Tobin")
        _persona(db, work_id, "Absent")
        text = "Mara walked. Tobin followed. " * 60  # ~240 words
        cid = _chapter(db, work_id, 1, text=text)
        r = client.post(self._url(work_id, cid)).json()
        s = r["suggestion"]
        assert sorted(s["cast"]) == ["Mara", "Tobin"]
        lo, hi = s["word_range"]
        assert 0 < lo <= hi
        assert r["sources"]["cast"] == "personas_in_text"
        assert r["sources"]["word_range"] == "current_text"

    def test_no_text_offers_approved_personas_and_defaults(self, client, db, work_id):
        _persona(db, work_id, "Mara")
        _persona(db, work_id, "Draft", approve=False)
        cid = _chapter(db, work_id, 1)
        r = client.post(self._url(work_id, cid)).json()
        assert r["suggestion"]["cast"] == ["Mara"]
        assert r["suggestion"]["word_range"] == [1500, 4000]

    def test_existing_contract_values_win(self, client, db, work_id):
        cid = _chapter(db, work_id, 1, contract=_contract(beat="KEEP ME"))
        r = client.post(self._url(work_id, cid)).json()
        assert r["suggestion"]["beat"] == "KEEP ME"

    def test_suggest_persists_nothing(self, client, db, work_id):
        cid = _chapter(db, work_id, 1)
        client.post(self._url(work_id, cid))
        got = client.get(f"/api/works/{work_id}/loom/chapters/{cid}/contract").json()
        assert got["contract"] is None


# ─── 4. Revision text + 5. run escalations ──────────────────────────────────


class TestRevisionAndRun:
    def test_revision_includes_text(self, client, db, work_id):
        cid = _chapter(db, work_id, 1)
        rev = db.create_chapter_revision(
            work_id=work_id, chapter_id=cid, text="The full prose.", origin="ai_generated",
            created_by="loom",
        )
        r = client.get(f"/api/loom/revisions/{rev['id']}").json()
        assert r["revision"]["text"] == "The full prose."
        assert client.get("/api/loom/revisions/nope").status_code == 404

    def test_run_carries_chapter_escalations(self, client, db, work_id):
        cid = _chapter(db, work_id, 1)
        run_id = db.create_loom_run(work_id, cid)
        db.create_finding(
            object_id=cid,
            object_type="book_chapter",
            kind="loom_escalation",
            severity="high",
            description="LOOM chapter 1: word band violated",
            meta={"chapter_id": cid, "chapter_seq": 1},
        )
        r = client.get(f"/api/loom/runs/{run_id}").json()
        assert len(r["escalations"]) == 1
        assert r["escalations"][0]["meta"]["chapter_id"] == cid
