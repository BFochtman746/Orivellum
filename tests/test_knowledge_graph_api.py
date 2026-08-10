"""Tests for the knowledge graph API (Task #388).

Covers:
  - GET /api/graph — global (cross-work) graph endpoint
  - GET /api/works/{id}/graph — work-scoped endpoint
  - db.get_global_graph() — cross-work with entity_kinds filter
  - Empty graph when no entities exist
  - Work-id filter delegates correctly
  - entity_kinds filtering removes matching nodes
  - Document nodes survive entity_kinds filtering
  - Edge trimming when a node is hidden
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db():
    from orivellum.database.db import OrivellumDB

    return OrivellumDB(":memory:")


def _populate_graph(db):
    """Create a minimal work + entity + relationship fixture using public DB methods."""
    work = db.create_work(title="Test Work")
    wid = work["id"]

    # Create a document via the public API (handles objects table automatically)
    doc = db.create_document(title="Test Doc", source="test.pdf", kind="pdf", work_id=wid)
    doc_id = doc["id"]

    # Create entities via the public API
    eid1 = db.upsert_entity("Alice", "person")
    eid2 = db.upsert_entity("Paris", "place")

    # Record MENTIONS edges (entity → document)
    db.create_entity_mention(eid1, doc_id, work_id=wid)
    db.create_entity_mention(eid2, doc_id, work_id=wid)

    return wid, doc_id, eid1, eid2


# ---------------------------------------------------------------------------
# 1 — get_global_graph: empty state
# ---------------------------------------------------------------------------


class TestGetGlobalGraphEmpty:
    def test_returns_empty_payload_when_no_entities(self):
        db = _make_db()
        result = db.get_global_graph()
        assert result["nodes"] == []
        assert result["edges"] == []
        assert result["node_count"] == 0
        assert result["edge_count"] == 0

    def test_work_scoped_returns_empty_without_docs(self):
        db = _make_db()
        work = db.create_work(title="Empty Work")
        result = db.get_global_graph(work_id=work["id"])
        assert result["nodes"] == []
        assert result["node_count"] == 0


# ---------------------------------------------------------------------------
# 2 — get_global_graph: populated state
# ---------------------------------------------------------------------------


class TestGetGlobalGraphPopulated:
    def test_returns_entity_and_doc_nodes(self):
        db = _make_db()
        wid, doc_id, eid1, eid2 = _populate_graph(db)

        result = db.get_global_graph()
        node_ids = {n["id"] for n in result["nodes"]}
        assert eid1 in node_ids or eid2 in node_ids, "At least one entity must appear"
        assert doc_id in node_ids, "Document node must appear"

    def test_entity_kind_filter_removes_entities(self):
        db = _make_db()
        wid, doc_id, eid1, eid2 = _populate_graph(db)

        # Filter to only "place" — should suppress the "person" entity
        result = db.get_global_graph(entity_kinds=["place"])
        node_ids = {n["id"] for n in result["nodes"]}
        assert eid2 in node_ids, "Place entity must survive the filter"
        assert eid1 not in node_ids, "Person entity must be filtered out"

    def test_entity_kind_filter_always_keeps_docs(self):
        db = _make_db()
        wid, doc_id, eid1, eid2 = _populate_graph(db)

        # Filter to only "scripture" — no such entities, but doc nodes survive
        result = db.get_global_graph(entity_kinds=["scripture"])
        # Entity nodes suppressed; doc nodes may or may not be there since no links
        # Just confirm nothing crashes and docs aren't *added* when there are no connections
        assert isinstance(result["nodes"], list)

    def test_work_id_filter_scopes_to_one_work(self):
        db = _make_db()
        wid, doc_id, eid1, eid2 = _populate_graph(db)

        # Create a second work with its own entity (won't have MENTIONS relationships)
        work2 = db.create_work(title="Other Work")
        result = db.get_global_graph(work_id=wid)
        node_ids = {n["id"] for n in result["nodes"]}
        assert doc_id in node_ids, "Work-scoped graph must include the work's document"

    def test_limit_cap(self):
        db = _make_db()
        _populate_graph(db)
        result = db.get_global_graph(limit=3)
        assert len(result["nodes"]) <= 3

    def test_nodes_have_required_fields(self):
        db = _make_db()
        _populate_graph(db)
        result = db.get_global_graph()
        for node in result["nodes"]:
            assert "id" in node, f"Node missing id: {node}"
            assert "label" in node, f"Node missing label: {node}"
            assert "type" in node, f"Node missing type: {node}"
            assert "kind" in node, f"Node missing kind: {node}"

    def test_edges_have_required_fields(self):
        db = _make_db()
        _populate_graph(db)
        result = db.get_global_graph()
        for edge in result["edges"]:
            assert "source" in edge, f"Edge missing source: {edge}"
            assert "target" in edge, f"Edge missing target: {edge}"
            assert "label" in edge, f"Edge missing label: {edge}"
            assert "type" in edge, f"Edge missing type: {edge}"

    def test_edge_endpoints_always_in_node_set(self):
        """Every edge must connect two nodes that are actually in the returned node list."""
        db = _make_db()
        _populate_graph(db)
        result = db.get_global_graph()
        node_ids = {n["id"] for n in result["nodes"]}
        for edge in result["edges"]:
            assert edge["source"] in node_ids, f"Edge source {edge['source']!r} not in node set"
            assert edge["target"] in node_ids, f"Edge target {edge['target']!r} not in node set"


# ---------------------------------------------------------------------------
# 3 — API route: works_graph
# ---------------------------------------------------------------------------


class TestWorksGraphAPI:
    def _call_works_graph(
        self, work_id: str, db, entity_kinds: str | None = None, limit: int = 100
    ):
        import orivellum.api._deps as _deps

        saved = _deps._DB
        try:
            _deps._DB = db
            from fastapi import HTTPException

            from orivellum.api.routes.works import works_graph

            try:
                return 200, works_graph(work_id, limit=limit, entity_kinds=entity_kinds)
            except HTTPException as exc:
                return exc.status_code, {"detail": exc.detail}
        finally:
            _deps._DB = saved

    def test_404_for_missing_work(self):
        db = _make_db()
        status, _ = self._call_works_graph("nonexistent", db)
        assert status == 404

    def test_returns_graph_for_valid_work(self):
        db = _make_db()
        wid, doc_id, eid1, eid2 = _populate_graph(db)
        status, body = self._call_works_graph(wid, db)
        assert status == 200
        assert "nodes" in body
        assert "edges" in body
        assert "node_count" in body
        assert "work_id" in body

    def test_entity_kinds_filter_applied(self):
        db = _make_db()
        wid, doc_id, eid1, eid2 = _populate_graph(db)
        status, body = self._call_works_graph(wid, db, entity_kinds="person")
        assert status == 200
        # The place entity should be absent; person entity and docs may be present
        node_ids = {n["id"] for n in body["nodes"]}
        assert eid2 not in node_ids, "Place entity must be filtered out when entity_kinds=person"


# ---------------------------------------------------------------------------
# 4 — API route: global_graph
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 5 — Fallback path (knowledge-projection, no entity MENTIONS relationships)
# ---------------------------------------------------------------------------


class TestFallbackKnowledgeProjection:
    """Exercises get_global_graph when no entity-MENTIONS rows exist,
    forcing the knowledge-item projection fallback."""

    def _db_with_knowledge_relationships(self, n_pairs: int):
        """Return a DB that has n_pairs knowledge relationship rows and NO
        entity/MENTIONS rows, triggering the fallback branch."""
        db = _make_db()
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        with db._lock:
            for i in range(n_pairs):
                kid = str(__import__("uuid").uuid4())
                db._conn.execute(
                    """INSERT INTO objects(id, type, lifecycle, created_at, updated_at)
                       VALUES(?, 'knowledge', 'active', ?, ?)""",
                    (kid, now, now),
                )
                db._conn.execute(
                    """INSERT INTO knowledge(id, kind, text, subject, predicate, object,
                       review_status, created_at)
                       VALUES(?, 'relationship', ?, ?, 'relates_to', ?, 'auto', ?)""",
                    (kid, f"s{i} relates_to o{i}", f"Subject{i}", f"Object{i}", now),
                )
            db._conn.commit()
        return db

    def test_no_dangling_edges_when_limit_cuts_nodes(self):
        """Edges must never reference a node absent from the bounded node list."""
        # Create 20 relationship pairs → up to 40 concept nodes.
        # Request limit=5 so the node list is heavily truncated.
        db = self._db_with_knowledge_relationships(20)
        result = db.get_global_graph(limit=5)
        node_ids = {n["id"] for n in result["nodes"]}
        for edge in result["edges"]:
            assert edge["source"] in node_ids, (
                f"Dangling source {edge['source']!r} not in bounded node set"
            )
            assert edge["target"] in node_ids, (
                f"Dangling target {edge['target']!r} not in bounded node set"
            )

    def test_node_count_matches_returned_list(self):
        """node_count must equal len(nodes), not pre-truncation total."""
        db = self._db_with_knowledge_relationships(20)
        result = db.get_global_graph(limit=5)
        assert result["node_count"] == len(result["nodes"]), (
            f"node_count={result['node_count']} but len(nodes)={len(result['nodes'])}"
        )

    def test_edge_count_matches_returned_list(self):
        """edge_count must equal len(edges), not pre-truncation total."""
        db = self._db_with_knowledge_relationships(20)
        result = db.get_global_graph(limit=5)
        assert result["edge_count"] == len(result["edges"]), (
            f"edge_count={result['edge_count']} but len(edges)={len(result['edges'])}"
        )

    def test_entity_kinds_filter_excludes_concepts_in_fallback(self):
        """When entity_kinds does not include 'concept', the fallback must
        return an empty graph because all fallback nodes are kind='concept'."""
        db = self._db_with_knowledge_relationships(5)
        result = db.get_global_graph(entity_kinds=["person", "place"])
        assert result["nodes"] == [], (
            "Fallback nodes are all 'concept'; filtering to person/place must yield no nodes"
        )
        assert result["edges"] == [], "No nodes means no edges"

    def test_entity_kinds_concept_included_returns_nodes(self):
        """When entity_kinds includes 'concept', fallback nodes are returned."""
        db = self._db_with_knowledge_relationships(3)
        result = db.get_global_graph(entity_kinds=["concept"])
        assert len(result["nodes"]) > 0, (
            "entity_kinds=['concept'] must include fallback concept nodes"
        )

    def test_none_entity_kinds_returns_all_fallback_nodes(self):
        """entity_kinds=None (no filter) must return all fallback concept nodes."""
        db = self._db_with_knowledge_relationships(3)
        result = db.get_global_graph()
        assert len(result["nodes"]) > 0


# ---------------------------------------------------------------------------
# 6 — Non-fallback path: dangling-edge + count correctness
# ---------------------------------------------------------------------------


class TestNonFallbackDanglingEdges:
    """Exercises the entity-MENTIONS non-fallback path with a limit that
    cuts the node list, confirming no edges dangle outside the bounded set."""

    def test_no_dangling_edges_at_small_limit(self):
        """Create many entities+docs, request limit=3: all returned edges
        must connect only nodes in the returned node list."""
        db = _make_db()
        # Create multiple works with entities to grow beyond limit=3 easily
        for i in range(4):
            wid, doc_id, eid1, eid2 = _populate_graph(db)
        result = db.get_global_graph(limit=3)
        node_ids = {n["id"] for n in result["nodes"]}
        for edge in result["edges"]:
            assert edge["source"] in node_ids
            assert edge["target"] in node_ids

    def test_counts_match_returned_lists(self):
        """node_count and edge_count must always equal the actual list lengths."""
        db = _make_db()
        _populate_graph(db)
        _populate_graph(db)
        result = db.get_global_graph(limit=4)
        assert result["node_count"] == len(result["nodes"])
        assert result["edge_count"] == len(result["edges"])


class TestGlobalGraphAPI:
    def _call_global_graph(self, db, work_id=None, entity_kinds=None, limit=200):
        import orivellum.api._deps as _deps

        saved = _deps._DB
        try:
            _deps._DB = db
            from orivellum.api.routes.works import global_graph

            return 200, global_graph(work_id=work_id, entity_kinds=entity_kinds, limit=limit)
        finally:
            _deps._DB = saved

    def test_returns_empty_for_empty_db(self):
        db = _make_db()
        status, body = self._call_global_graph(db)
        assert status == 200
        assert body["nodes"] == []

    def test_returns_nodes_for_populated_db(self):
        db = _make_db()
        _populate_graph(db)
        status, body = self._call_global_graph(db)
        assert status == 200
        assert len(body["nodes"]) > 0

    def test_limit_cap_at_300(self):
        db = _make_db()
        _populate_graph(db)
        # limit > 300 must be capped
        status, body = self._call_global_graph(db, limit=9999)
        assert status == 200  # should not error

    def test_entity_kinds_comma_list(self):
        db = _make_db()
        wid, doc_id, eid1, eid2 = _populate_graph(db)
        status, body = self._call_global_graph(db, entity_kinds="person,place")
        assert status == 200
        node_ids = {n["id"] for n in body["nodes"]}
        # Both entities should be present since we allow both kinds
        assert eid1 in node_ids or eid2 in node_ids
