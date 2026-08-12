"""Tests for automatic near-duplicate resolution (an unattended nightshift pass).

Floor-rule coverage: auto_dedup runs nightly with nobody watching and
supersedes documents.  Pins:

  * _pick_canonical rule order — deleted skip, dual-canonical skip,
    lifecycle priority, recency, richness, full tie → human queue
  * a user-set canonical document is never auto-superseded
  * per-pair error isolation and counter accounting in the batch runner
"""

from __future__ import annotations

from orivellum.capabilities import auto_dedup


def _doc(doc_id, lifecycle="draft", created_at="2026-01-01", word_count=100):
    return {
        "id": doc_id,
        "lifecycle": lifecycle,
        "created_at": created_at,
        "word_count": word_count,
    }


# ── Canonical selection rules ─────────────────────────────────────────────────


def test_deleted_docs_are_never_touched():
    assert auto_dedup._pick_canonical(_doc("a", "deleted"), _doc("b")) is None
    assert auto_dedup._pick_canonical(_doc("a"), _doc("b", "deleted")) is None


def test_dual_canonical_is_a_human_decision():
    assert auto_dedup._pick_canonical(_doc("a", "canonical"), _doc("b", "canonical")) is None


def test_user_set_canonical_always_survives():
    """Rule 1: a document the user made canonical is never auto-superseded —
    even against a newer, richer draft."""
    older_canonical = _doc("keep", "canonical", "2020-01-01", word_count=10)
    newer_rich_draft = _doc("lose", "draft", "2026-06-01", word_count=99999)
    assert auto_dedup._pick_canonical(older_canonical, newer_rich_draft) == "keep"
    assert auto_dedup._pick_canonical(newer_rich_draft, older_canonical) == "keep"


def test_lifecycle_priority_order():
    assert auto_dedup._pick_canonical(_doc("r", "reference"), _doc("d", "draft")) == "r"
    assert auto_dedup._pick_canonical(_doc("d", "draft"), _doc("s", "superseded")) == "d"
    # legacy 'active' ties with 'draft' → falls through to recency
    assert (
        auto_dedup._pick_canonical(
            _doc("a", "active", "2026-02-01"), _doc("d", "draft", "2026-01-01")
        )
        == "a"
    )


def test_recency_then_richness_then_tie():
    assert (
        auto_dedup._pick_canonical(_doc("new", created_at="2026-02-01"), _doc("old")) == "new"
    )
    assert (
        auto_dedup._pick_canonical(_doc("rich", word_count=500), _doc("thin", word_count=5))
        == "rich"
    )
    assert auto_dedup._pick_canonical(_doc("a"), _doc("b")) is None  # full tie → human


# ── Batch runner accounting ───────────────────────────────────────────────────


class _StubDB:
    """Just enough surface for auto_resolve_duplicates."""

    def __init__(self, rows, docs, resolve_result=None):
        import threading

        self._lock = threading.Lock()
        self._rows = rows
        self._docs = docs
        self._resolve_result = resolve_result or {"resolved": True}
        self.resolved_calls: list[tuple] = []
        self._conn = self

    def execute(self, sql, params=()):
        rows = self._rows

        class _Cur:
            def fetchall(self):
                return rows

        return _Cur()

    def get_setting(self, key, default=""):
        return default

    def get_document(self, doc_id):
        return self._docs.get(doc_id)

    def resolve_near_duplicate(self, dupe_id, action, canonical_doc_id=None, actor=""):
        self.resolved_calls.append((dupe_id, action, canonical_doc_id, actor))
        return self._resolve_result


def test_near_duplicate_pair_superseded_with_system_actor():
    rows = [("dupe1", "a", "b", 0.92, "near_duplicate")]
    docs = {"a": _doc("a", "canonical"), "b": _doc("b", "draft")}
    db = _StubDB(rows, docs)
    out = auto_dedup.auto_resolve_duplicates(db, max_pairs=10)
    assert out["superseded"] == 1
    dupe_id, action, canonical_id, actor = db.resolved_calls[0]
    assert (dupe_id, action, canonical_id, actor) == ("dupe1", "mark_superseded", "a", "system")


def test_likely_revision_pair_version_linked():
    rows = [("dupe1", "a", "b", 0.7, "likely_revision")]
    docs = {"a": _doc("a"), "b": _doc("b")}
    db = _StubDB(rows, docs)
    out = auto_dedup.auto_resolve_duplicates(db, max_pairs=10)
    assert out["versioned"] == 1
    assert db.resolved_calls[0][1] == "mark_versions"


def test_missing_doc_and_unpickable_pairs_are_skipped():
    rows = [
        ("d1", "a", "gone", 0.9, "near_duplicate"),  # missing doc
        ("d2", "a", "b", 0.9, "near_duplicate"),  # dual canonical → unpickable
        ("d3", "a", "b", 0.9, "weird_kind"),  # unknown kind
    ]
    docs = {"a": _doc("a", "canonical"), "b": _doc("b", "canonical")}
    db = _StubDB(rows, docs)
    out = auto_dedup.auto_resolve_duplicates(db, max_pairs=10)
    assert out["skipped"] == 3
    assert db.resolved_calls == []


def test_already_resolved_pairs_count_as_skipped():
    rows = [("d1", "a", "b", 0.9, "near_duplicate")]
    docs = {"a": _doc("a", "reference"), "b": _doc("b")}
    db = _StubDB(rows, docs, resolve_result={"already_resolved": True})
    out = auto_dedup.auto_resolve_duplicates(db, max_pairs=10)
    assert out["superseded"] == 0
    assert out["skipped"] == 1


def test_per_pair_errors_never_abort_the_run():
    rows = [
        ("d1", "boom", "b", 0.9, "near_duplicate"),
        ("d2", "a", "b", 0.7, "likely_revision"),
    ]

    class _ExplodingDB(_StubDB):
        def get_document(self, doc_id):
            if doc_id == "boom":
                raise RuntimeError("db hiccup")
            return super().get_document(doc_id)

    db = _ExplodingDB(rows, {"a": _doc("a"), "b": _doc("b")})
    out = auto_dedup.auto_resolve_duplicates(db, max_pairs=10)
    assert out["errors"] == 1
    assert out["versioned"] == 1, "an error in one pair must not stop the next"
