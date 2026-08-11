"""Per-chapter voice casting for chapter-first books.

Documents whose extracted book_chapters cover most of their text expand into
one casting/narration unit per structural chapter; other documents keep the
historical per-file behavior. Doc-level castings saved before chapter
extraction still apply to every chapter of that document.
"""

import pytest


@pytest.fixture()
def unit_client(tmp_path):
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

    work = db.create_work(title="Chapter Units Test", work_type="writing")

    ch1 = "Mara crept along the northern wall, counting torches." * 3
    ch2 = "Torin sharpened his blade beside the dying fire." * 3

    # Chapter-first document: two structural chapters covering all its text.
    book_doc = db.create_document(title="novel", work_id=work["id"], kind="text")
    with db._lock:
        db._conn.execute("UPDATE documents SET readiness='ready' WHERE id=?", (book_doc["id"],))
        db._conn.commit()
    db.add_chunk(book_doc["id"], ch1, page=0)
    db.add_chunk(book_doc["id"], ch2, page=1)
    db.upsert_book_chapters(
        book_doc["id"],
        work["id"],
        [
            {"seq": 0, "level": 1, "title": "Chapter 1: Mara", "text": ch1},
            {"seq": 1, "level": 1, "title": "Chapter 2: Torin", "text": ch2},
        ],
    )

    # Plain document: no chapters extracted.
    plain_doc = db.create_document(title="notes", work_id=work["id"], kind="text")
    with db._lock:
        db._conn.execute("UPDATE documents SET readiness='ready' WHERE id=?", (plain_doc["id"],))
        db._conn.commit()
    db.add_chunk(plain_doc["id"], "Appendix notes about the world.", page=0)

    client = TestClient(create_app(), raise_server_exceptions=False, headers=AUTH_HEADERS)
    return client, db, work["id"], book_doc["id"], plain_doc["id"]


def _chapter_ids(db, doc_id):
    return [c["id"] for c in db.get_book_chapters(doc_id)]


def test_get_casting_lists_chapter_units_and_document_units(unit_client):
    client, db, wid, book_doc, plain_doc = unit_client
    r = client.get(f"/api/studio/works/{wid}/casting")
    assert r.status_code == 200
    rows = r.json()["documents"]
    ch_ids = _chapter_ids(db, book_doc)
    assert [d["id"] for d in rows] == ch_ids + [plain_doc]
    assert [d["kind"] for d in rows] == ["chapter", "chapter", "document"]
    assert rows[0]["title"] == "Chapter 1: Mara"
    assert all(d["doc_id"] == book_doc for d in rows[:2])


def test_doc_level_casting_resolves_onto_each_chapter(unit_client):
    client, db, wid, book_doc, _plain = unit_client
    # Old-format save: voice keyed by the parent document id.
    r = client.put(
        f"/api/studio/works/{wid}/casting",
        json={"sections": {book_doc: "af_sarah"}},
    )
    assert r.status_code == 200
    r = client.get(f"/api/studio/works/{wid}/casting")
    data = r.json()
    ch_ids = _chapter_ids(db, book_doc)
    for cid in ch_ids:
        assert data["sections"][cid] == "af_sarah"
    rows = {d["id"]: d for d in data["documents"]}
    assert all(rows[cid]["voice"] == "af_sarah" for cid in ch_ids)


def test_put_accepts_chapter_ids_and_rejects_foreign_ids(unit_client):
    client, db, wid, book_doc, _plain = unit_client
    ch_ids = _chapter_ids(db, book_doc)
    r = client.put(
        f"/api/studio/works/{wid}/casting",
        json={"sections": {ch_ids[0]: "af_sarah", ch_ids[1]: "bm_george"}},
    )
    assert r.status_code == 200
    assert r.json()["sections"] == {ch_ids[0]: "af_sarah", ch_ids[1]: "bm_george"}

    r = client.put(
        f"/api/studio/works/{wid}/casting",
        json={"sections": {"not-a-real-id": "af_sarah"}},
    )
    assert r.status_code == 422


def test_render_plan_uses_chapter_units(unit_client):
    _client, db, wid, book_doc, plain_doc = unit_client
    from orivellum.api.routes.studio import _collect_work_doc_texts

    _title, doc_texts = _collect_work_doc_texts(db, wid)
    ch_ids = _chapter_ids(db, book_doc)
    assert [t[0] for t in doc_texts] == ch_ids + [plain_doc]
    assert doc_texts[0][1] == "Chapter 1: Mara"
    assert "Mara crept" in doc_texts[0][2]
    assert "Torin sharpened" in doc_texts[1][2]


def test_chapter_casting_reaches_render_voice_resolution(unit_client):
    client, db, wid, book_doc, _plain = unit_client
    from orivellum.api.routes.studio import (
        _get_voice_casting,
        _load_work_cast_units,
        _resolve_unit_casting,
    )

    ch_ids = _chapter_ids(db, book_doc)
    client.put(
        f"/api/studio/works/{wid}/casting",
        json={"sections": {ch_ids[1]: "af_sarah"}},
    )
    units = _load_work_cast_units(db, wid)
    casting = _resolve_unit_casting(_get_voice_casting(db, wid), units)
    assert casting == {ch_ids[1]: "af_sarah"}


def test_reextraction_invalidates_chapter_keys_but_keeps_doc_keys(unit_client):
    client, db, wid, book_doc, _plain = unit_client
    old_ch_ids = _chapter_ids(db, book_doc)
    # One chapter-specific assignment + one legacy doc-level assignment.
    client.put(
        f"/api/studio/works/{wid}/casting",
        json={"sections": {old_ch_ids[0]: "af_sarah"}},
    )
    # Re-extraction replaces chapter rows with NEW ids.
    ch1 = "Mara crept along the northern wall, counting torches." * 3
    ch2 = "Torin sharpened his blade beside the dying fire." * 3
    db.upsert_book_chapters(
        book_doc,
        wid,
        [
            {"seq": 0, "level": 1, "title": "Chapter 1: Mara", "text": ch1},
            {"seq": 1, "level": 1, "title": "Chapter 2: Torin", "text": ch2},
        ],
    )
    new_ch_ids = _chapter_ids(db, book_doc)
    assert set(new_ch_ids).isdisjoint(old_ch_ids)
    # Stale chapter key resolves to nothing — chapters fall back to narrator.
    data = client.get(f"/api/studio/works/{wid}/casting").json()
    assert data["sections"] == {}
    # A doc-level assignment survives re-extraction and fans out again.
    client.put(
        f"/api/studio/works/{wid}/casting",
        json={"sections": {book_doc: "bm_george"}},
    )
    data = client.get(f"/api/studio/works/{wid}/casting").json()
    assert all(data["sections"][cid] == "bm_george" for cid in new_ch_ids)


def test_sparse_chapters_keep_per_document_behavior(unit_client):
    client, db, wid, _book_doc, _plain = unit_client
    # A document whose chapters cover only a sliver of its text must NOT
    # switch to chapter units — the render would silently drop content.
    long_text = "The caravan crossed the salt flats under a copper sky. " * 50
    doc = db.create_document(title="sparse", work_id=wid, kind="text")
    with db._lock:
        db._conn.execute("UPDATE documents SET readiness='ready' WHERE id=?", (doc["id"],))
        db._conn.commit()
    db.add_chunk(doc["id"], long_text, page=0)
    db.upsert_book_chapters(
        doc["id"],
        wid,
        [
            {"seq": 0, "level": 1, "title": "Fragment A", "text": "Tiny."},
            {"seq": 1, "level": 1, "title": "Fragment B", "text": "Also tiny."},
        ],
    )
    r = client.get(f"/api/studio/works/{wid}/casting")
    rows = {d["id"]: d for d in r.json()["documents"]}
    assert rows[doc["id"]]["kind"] == "document"
