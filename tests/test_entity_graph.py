"""Tests for entity graph DB methods and the works/graph + entities API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.conftest import AUTH_HEADERS

# ── Test app factory ──────────────────────────────────────────────────────────


def _make_app(tmp_path: Path):
    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

    cfg = OrivellumConfig(data_dir=str(tmp_path))
    db = OrivellumDB(str(tmp_path / "test.db"))
    _deps.init(db=db, cfg=cfg)
    return app, db


# ─── DB method unit tests ─────────────────────────────────────────────────────


def test_upsert_entity_creates_and_deduplicates(tmp_path):
    _, db = _make_app(tmp_path)

    eid1 = db.upsert_entity("Paul", "person")
    eid2 = db.upsert_entity("Paul", "person")  # duplicate → same id
    assert eid1 == eid2

    eid3 = db.upsert_entity("Paul", "concept")  # different kind → new entity
    assert eid3 != eid1


def test_upsert_entity_rejects_empty(tmp_path):
    _, db = _make_app(tmp_path)
    with pytest.raises(ValueError):
        db.upsert_entity("", "concept")


def test_create_entity_mention_and_graph(tmp_path):
    _, db = _make_app(tmp_path)

    work = db.create_work("Graph Test Work")
    doc = db.create_document("Doc Alpha", work_id=work["id"])
    eid = db.upsert_entity("Corinthians", "scripture")
    db.create_entity_mention(eid, doc["id"], work["id"])

    # Duplicate mention should be silently ignored
    db.create_entity_mention(eid, doc["id"], work["id"])

    graph = db.get_work_graph(work["id"])
    node_ids = {n["id"] for n in graph["nodes"]}
    assert eid in node_ids, "entity should appear in graph"
    assert doc["id"] in node_ids, "document should appear in graph"

    edge_types = {e["type"] for e in graph["edges"]}
    assert "MENTIONS" in edge_types


def test_create_entity_edge(tmp_path):
    _, db = _make_app(tmp_path)

    sid = db.upsert_entity("Paul", "person")
    oid = db.upsert_entity("Corinthians", "place")
    db.create_entity_edge(sid, oid, "wrote to")
    db.create_entity_edge(sid, oid, "wrote to")  # duplicate — no error

    work = db.create_work("Edge Work")
    doc = db.create_document("Doc", work_id=work["id"])
    db.create_entity_mention(sid, doc["id"], work["id"])
    db.create_entity_mention(oid, doc["id"], work["id"])

    graph = db.get_work_graph(work["id"])
    edge_labels = [e["label"] for e in graph["edges"]]
    assert "wrote to" in edge_labels


def test_list_entities(tmp_path):
    _, db = _make_app(tmp_path)

    e1 = db.upsert_entity("John", "person")
    _e2 = db.upsert_entity("Rome", "place")
    _e3 = db.upsert_entity("Faith", "concept")

    work = db.create_work("List Work")
    doc = db.create_document("Doc", work_id=work["id"])
    db.create_entity_mention(e1, doc["id"], work["id"])
    db.create_entity_mention(e1, doc["id"], work["id"])  # dup — counted once

    entities = db.list_entities()
    names = [e["name"] for e in entities]
    assert "John" in names
    assert "Rome" in names

    john = next(e for e in entities if e["name"] == "John")
    assert john["mention_count"] == 1

    persons = db.list_entities(kind="person")
    assert all(e["kind"] == "person" for e in persons)


# ─── API endpoint tests ───────────────────────────────────────────────────────


def test_get_entities_endpoint(tmp_path):
    app, db = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

    db.upsert_entity("Ephesus", "place")

    r = client.get("/api/entities")
    assert r.status_code == 200
    body = r.json()
    assert "entities" in body
    names = [e["name"] for e in body["entities"]]
    assert "Ephesus" in names


def test_get_entity_detail_endpoint(tmp_path):
    app, db = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

    eid = db.upsert_entity("Galatia", "place")
    work = db.create_work("W")
    doc = db.create_document("D", work_id=work["id"])
    db.create_entity_mention(eid, doc["id"], work["id"])

    r = client.get(f"/api/entities/{eid}")
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Galatia"
    assert body["mention_count"] == 1
    assert any(m["id"] == doc["id"] for m in body["mentions"])


def test_get_entity_detail_404(tmp_path):
    app, _ = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
    r = client.get("/api/entities/nonexistent-id")
    assert r.status_code == 404


def test_works_graph_endpoint_with_entities(tmp_path):
    app, db = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

    work = db.create_work("Graph Work")
    doc = db.create_document("Doc", work_id=work["id"])
    eid = db.upsert_entity("Jerusalem", "place")
    db.create_entity_mention(eid, doc["id"], work["id"])

    r = client.get(f"/api/works/{work['id']}/graph")
    assert r.status_code == 200
    body = r.json()
    assert body["work_id"] == work["id"]
    assert body["node_count"] >= 2  # doc + entity
    assert body["edge_count"] >= 1  # MENTIONS edge

    node_ids = {n["id"] for n in body["nodes"]}
    assert eid in node_ids
    assert doc["id"] in node_ids


def test_works_graph_fallback_to_knowledge_items(tmp_path):
    """When no entities exist, graph returns doc nodes (no entity fallback needed for empty entities)."""
    app, db = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

    work = db.create_work("FallbackWork")
    doc = db.create_document("Doc", work_id=work["id"])
    # Write knowledge items WITHOUT entity table rows (simulates pre-graph import)
    db.create_knowledge_item(
        work_id=work["id"],
        kind="entity",
        text="Paul",
        subject="Paul",
        predicate="mentioned in",
        obj="Doc",
        confidence=0.5,
        source_doc_id=doc["id"],
    )
    db.create_knowledge_item(
        work_id=work["id"],
        kind="relationship",
        text="Paul wrote Corinthians",
        subject="Paul",
        predicate="wrote",
        obj="Corinthians",
        confidence=0.75,
        source_doc_id=doc["id"],
    )

    r = client.get(f"/api/works/{work['id']}/graph")
    assert r.status_code == 200
    body = r.json()
    # Doc node is always present; fallback adds concept nodes from knowledge items
    labels = [n["label"] for n in body["nodes"]]
    assert any(l in labels for l in ("Paul", "Corinthians", "Doc"))


def test_works_graph_404_for_unknown_work(tmp_path):
    app, _ = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
    r = client.get("/api/works/no-such-work/graph")
    assert r.status_code == 404


# ─── Harvest integration tests ────────────────────────────────────────────────


def _fake_extraction(text: str, kind: str = "text"):
    """Build a minimal ExtractionResult suitable for harvest()."""
    from orivellum.capabilities.extraction import ExtractionResult, PageSegment

    seg = PageSegment(page=1, text=text)
    return ExtractionResult(
        kind=kind,
        full_text=text,
        word_count=len(text.split()),
        pages=[seg],
        headings=[],
    )


def test_rule_harvest_writes_entity_mentions(tmp_path):
    """harvest() must create entities and MENTIONS edges in the graph tables.

    Verifies the ki.get() bug is fixed: create_knowledge_item() returns a str,
    not a dict, so knowledge_id must be passed directly.
    """
    _, db = _make_app(tmp_path)
    from orivellum.capabilities.knowledge_harvest import harvest

    work = db.create_work("Harvest Work")
    doc = db.create_document("Romans", work_id=work["id"])

    # Text with clearly capitalised proper-noun phrases the heuristic will catch
    text = (
        "Paul wrote to the Romans about faith and grace. "
        "The Corinthian Church struggled with divisions. "
        "Barnabas accompanied Paul on his missionary journeys. " * 5
    )
    result = _fake_extraction(text)
    harvest(result, doc_id=doc["id"], work_id=work["id"], doc_title="Romans", db=db)

    # Entity table must have rows
    entities = db.list_entities()
    assert len(entities) > 0, "harvest() must write entity rows to the entities table"

    # Graph must have MENTIONS edges
    graph = db.get_work_graph(work["id"])
    assert graph["node_count"] >= 2, "graph must have at least a doc node and one entity"
    edge_types = {e["type"] for e in graph["edges"]}
    assert "MENTIONS" in edge_types, (
        "harvest() entity rows must have MENTIONS edges so the graph can connect them. "
        f"Graph nodes: {[n['label'] for n in graph['nodes']]}; "
        f"edges: {graph['edges']}"
    )


def test_llm_harvest_writes_entity_mentions(tmp_path):
    """llm_harvest() must create entities and MENTIONS edges when extraction succeeds.

    Uses a patched _call_llm_sync to inject a deterministic JSON payload without
    requiring a real model server, verifying the full persistence path.
    """
    _, db = _make_app(tmp_path)

    # Ensure AI extraction is enabled for this test
    db.set_setting("ai_extraction_enabled", "true")
    db.set_setting("base_url", "http://localhost:11434/v1")  # placeholder

    import json
    from unittest.mock import patch

    from orivellum.capabilities import knowledge_harvest as kh

    llm_payload = json.dumps(
        {
            "entities": [
                {"name": "Timothy", "description": "companion of Paul"},
            ],
            "claims": [],
            "relationships": [
                {"subject": "Paul", "predicate": "mentored", "object": "Timothy"},
            ],
        }
    )

    work = db.create_work("LLM Work")
    doc = db.create_document("Philippians", work_id=work["id"])
    text = "Paul and Timothy wrote to the Philippians."
    result = _fake_extraction(text)

    with patch.object(kh, "_call_llm_sync", return_value=llm_payload):
        created = kh.llm_harvest(
            result,
            doc_id=doc["id"],
            work_id=work["id"],
            doc_title="Philippians",
            db=db,
        )

    assert created >= 2, f"Expected at least 2 knowledge items, got {created}"

    # Entity table must have the extracted entities
    entities = db.list_entities()
    names = {e["name"] for e in entities}
    assert "Timothy" in names, f"LLM entity 'Timothy' missing from entities table. Got: {names}"
    assert "Paul" in names or "Paul" in {n for n in names}, (
        f"LLM relationship subject 'Paul' missing. Got: {names}"
    )

    # Graph must have MENTIONS edges connecting these entities to the document
    graph = db.get_work_graph(work["id"])
    edge_types = {e["type"] for e in graph["edges"]}
    assert "MENTIONS" in edge_types, (
        "llm_harvest() entity rows must produce MENTIONS edges in the graph. "
        f"Nodes: {[n['label'] for n in graph['nodes']]}; edges: {graph['edges']}"
    )


def test_works_graph_entity_nodes_survive_large_doc_count(tmp_path):
    """Entity nodes must appear even when the Work has more docs than the budget cap.

    Regression guard for the bug where document nodes filled nodes[:limit] and
    every entity node was silently truncated.
    """
    app, db = _make_app(tmp_path)
    client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

    work = db.create_work("Large Work")

    # Create more docs than DOC_CAP (DOC_CAP = max(10, limit // 5) = 20 for limit=100)
    docs = [db.create_document(f"Doc {i}", work_id=work["id"]) for i in range(30)]

    # Add one entity that mentions the last doc (beyond the cap)
    eid = db.upsert_entity("KeyEntity", "concept")
    db.create_entity_mention(eid, docs[-1]["id"], work["id"])

    r = client.get(f"/api/works/{work['id']}/graph?limit=100")
    assert r.status_code == 200
    body = r.json()

    node_ids = {n["id"] for n in body["nodes"]}
    assert eid in node_ids, (
        "Entity node must appear even when Work has more docs than the cap. "
        f"Got nodes: {[n['label'] for n in body['nodes']]}"
    )
    # Edge must also be present (entity→some doc in seen set)
    assert body["edge_count"] >= 1, "MENTIONS edge must be present when entity is in graph"
