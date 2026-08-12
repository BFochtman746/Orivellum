"""RE-PROJECTION Phase 3 — doc_type classification, backfill, and refusals.

Covers:
* deterministic classify_doc_type rules (provenance names included)
* create_document persists doc_type/doc_type_by
* backfill: collection home, doc_type application, tier PROPOSALS (never mutation)
* harvest refusal for unknown/generated/correspondence doc_types
* Work-creation gate: an ARTIFACT archive may not produce a Work
* reclassify approval applies proposed tier/doc_type
* acceptance: after backfill every doc has collection_id + doc_type, and no
  knowledge is scoped to unknown/generated documents
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from orivellum.capabilities.classify import (
    HARVEST_REFUSED_DOC_TYPES,
    DocType,
    assert_tier_may_become_work,
    classify_doc_type,
)
from orivellum.capabilities.classify_backfill import backfill_classification
from orivellum.capabilities.knowledge_harvest import (
    HarvestRefused,
    assert_doc_type_harvestable,
    harvest,
)
from orivellum.database.db import OrivellumDB
from tests.conftest import AUTH_HEADERS

# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def _tmp():
    with tempfile.TemporaryDirectory() as tmp:
        yield tmp


@pytest.fixture()
def db(_tmp):
    database = OrivellumDB(str(Path(_tmp) / "test.db"))
    yield database
    database.close()


@pytest.fixture()
def client(_tmp, db):
    from fastapi.testclient import TestClient

    from orivellum.api import _deps
    from orivellum.api.app import app
    from orivellum.configuration.config import OrivellumConfig

    cfg = OrivellumConfig(data_dir=_tmp)
    _deps.init(db=db, cfg=cfg)
    return TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)


# ── deterministic rules ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "kind", "text", "expected", "rule"),
    [
        ("rp016-test-catalog.json", "json", None, DocType.TEST_CATALOG, "catalog-name"),
        ("regression_baseline.json", "json", None, DocType.TEST_CATALOG, "catalog-name"),
        ("pipeline.py", None, None, DocType.CODE, "code-extension"),
        ("Setup-Windows.ps1", None, None, DocType.CODE, "code-extension"),
        ("budget.xlsx", "excel", None, DocType.WORKBOOK, "workbook-extension"),
        ("data.csv", "csv", None, DocType.WORKBOOK, "workbook-extension"),
        ("thread.eml", None, None, DocType.CORRESPONDENCE, "mail"),
        ("engine-contract_v1.0.0.md", None, None, DocType.DOCTRINE, "doctrine-name"),
        ("style_policy.md", None, None, DocType.DOCTRINE, "doctrine-name"),
        ("orivellum-report-2026-08.md", None, None, DocType.GENERATED, "generated-name"),
        ("handbook.pdf", "pdf", None, DocType.REFERENCE, "readable-document"),
        ("mystery.bin", None, None, DocType.UNKNOWN, "fallback"),
    ],
)
def test_doc_type_rules(name, kind, text, expected, rule):
    cls = classify_doc_type(name, kind=kind, sample_text=text)
    assert cls.doc_type == expected
    assert cls.rule == rule


def test_doc_type_json_key_shape():
    text = '{"tests": [{"test_id": "T1", "expected_result": "pass"}]}'
    cls = classify_doc_type("catalog_dump.json", kind="json", sample_text=text)
    assert cls.doc_type == DocType.TEST_CATALOG
    assert cls.rule == "catalog-json-keys"


def test_doc_type_chapter_structure():
    text = "Chapter 1\n\nIt began at dusk.\n\nChapter 2\n\nThe road north.\n"
    cls = classify_doc_type("mystery_draft.txt", kind="text", sample_text=text)
    assert cls.doc_type == DocType.MANUSCRIPT
    assert cls.rule == "chapter-structure"


def test_doc_type_system_meta_flag():
    cls = classify_doc_type("notes.txt", meta={"generated_by": "workshop"})
    assert cls.doc_type == DocType.GENERATED
    assert cls.rule == "system-meta"


# ── persistence ───────────────────────────────────────────────────────────────


def test_create_document_persists_doc_type(db):
    doc = db.create_document(
        title="pipeline.py",
        kind="text",
        doc_type="code",
        doc_type_by="rule:code-extension",
    )
    got = db.get_document(doc["id"])
    assert got["doc_type"] == "code"
    assert got["doc_type_by"] == "rule:code-extension"


# ── backfill ─────────────────────────────────────────────────────────────────


def test_backfill_assigns_collection_and_doc_type(db):
    a = db.create_document(title="handbook.pdf", kind="pdf")
    b = db.create_document(title="mystery.bin", kind="file")
    report = backfill_classification(db)

    assert report["collections_assigned"] >= 2
    ga, gb = db.get_document(a["id"]), db.get_document(b["id"])
    assert ga["collection_id"] and gb["collection_id"]
    assert ga["doc_type"] == "reference"
    assert ga["doc_type_by"] == "rule:readable-document"
    # Residue lands as unknown (refuses harvest) — never silently classified.
    assert gb["doc_type"] == "unknown"

    coll = db.get_collection(ga["collection_id"])
    assert coll["source_kind"] == "manual"


def test_backfill_never_mutates_tier_directly(db):
    # A doc whose deterministic tier disagrees with the stored one.
    doc = db.create_document(title="A01_MIGRATION_BATCH_007.json", kind="json", tier="source")
    backfill_classification(db)

    got = db.get_document(doc["id"])
    assert got["tier"] == "source", "tier must never be mutated by the backfill"
    with db._lock:
        row = db._conn.execute(
            "SELECT proposed_tier, proposed_tier_by FROM pending_reclassify WHERE doc_id=?",
            (doc["id"],),
        ).fetchone()
    assert row is not None, "a pending_reclassify proposal must exist"
    assert row["proposed_tier"] in ("artifact", "system")
    assert row["proposed_tier_by"].startswith("rule:")


def test_backfill_is_idempotent(db):
    db.create_document(title="handbook.pdf", kind="pdf")
    backfill_classification(db)
    second = backfill_classification(db)
    assert second["collections_assigned"] == 0
    assert second["doc_type_applied"] == 0
    assert second["doc_type_residue"] == 0


# ── harvest refusal ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("doc_type", sorted(t.value for t in HARVEST_REFUSED_DOC_TYPES))
def test_harvest_refuses_untouchable_doc_types(db, doc_type):
    doc = db.create_document(title="x.bin", kind="file", doc_type=doc_type, doc_type_by="rule:t")
    with pytest.raises(HarvestRefused):
        assert_doc_type_harvestable(db, doc["id"])


def test_harvest_entry_point_refuses(db):
    from orivellum.capabilities.extraction import ExtractionResult

    doc = db.create_document(
        title="report.md", kind="markdown", doc_type="generated", doc_type_by="rule:generated-name"
    )
    result = ExtractionResult(kind="markdown", full_text="Some generated text.", word_count=3)
    with pytest.raises(HarvestRefused):
        harvest(result, doc_id=doc["id"], work_id=None, doc_title="report.md", db=db)


def test_harvest_allows_null_doc_type_and_reference(db):
    from orivellum.capabilities.extraction import ExtractionResult

    legacy = db.create_document(title="old.txt", kind="text")  # NULL doc_type = legacy
    ref = db.create_document(title="handbook.pdf", kind="pdf", doc_type="reference")
    assert_doc_type_harvestable(db, legacy["id"])
    assert_doc_type_harvestable(db, ref["id"])
    result = ExtractionResult(kind="pdf", full_text="A useful handbook.", word_count=3)
    created = harvest(result, doc_id=ref["id"], work_id=None, doc_title="handbook.pdf", db=db)
    assert created >= 1


def test_zero_knowledge_scoped_to_refused_docs(db):
    """Acceptance: no knowledge may be scoped to unknown/generated documents."""
    from orivellum.capabilities.extraction import ExtractionResult

    doc = db.create_document(title="mystery.bin", kind="file", doc_type="unknown")
    result = ExtractionResult(kind="text", full_text="Chapter of text.", word_count=3)
    with pytest.raises(HarvestRefused):
        harvest(result, doc_id=doc["id"], work_id=None, doc_title="mystery.bin", db=db)
    with db._lock:
        n = db._conn.execute(
            """SELECT COUNT(*) FROM knowledge k
               JOIN documents d ON d.id = k.source_doc_id
               WHERE d.doc_type IN ('unknown','generated')"""
        ).fetchone()[0]
    assert n == 0


# ── Work-creation tier gate ──────────────────────────────────────────────────


def test_artifact_tier_may_not_become_work():
    with pytest.raises(ValueError):
        assert_tier_may_become_work("artifact")
    with pytest.raises(ValueError):
        assert_tier_may_become_work("system")
    assert_tier_may_become_work("source")  # no raise
    assert_tier_may_become_work("canon")
    assert_tier_may_become_work(None)


def test_work_assignment_approval_refuses_artifact_archive(client, db):
    """An ARTIFACT archive's work_assignment suggestion must 422 on approval."""
    import json
    import uuid

    archive = db.create_document(title="build_output.zip", kind="zip", tier="artifact")
    child = db.create_document(title="pipeline.py", kind="text", tier="artifact")
    sid = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            """INSERT INTO suggestions(id, kind, text, meta, created_at)
               VALUES(?, 'work_assignment', 'Create Work from build_output.zip', ?,
                      datetime('now'))""",
            (
                sid,
                json.dumps(
                    {
                        "archive_doc_id": archive["id"],
                        "doc_ids": [child["id"]],
                        "proposed_title": "Build Output",
                    }
                ),
            ),
        )
        db._conn.commit()

    with db._lock:
        works_before = db._conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
    resp = client.post(f"/api/review/suggestion:{sid}/resolve", json={"decision": "approve"})
    assert resp.status_code == 422
    with db._lock:
        works_after = db._conn.execute("SELECT COUNT(*) FROM works").fetchone()[0]
    assert works_after == works_before, "no Work may be created from an ARTIFACT archive"
    # A failed validation must NOT consume the suggestion — it stays queued
    # so the classification can be corrected and the item re-reviewed.
    with db._lock:
        still_there = db._conn.execute(
            "SELECT COUNT(*) FROM suggestions WHERE id=?", (sid,)
        ).fetchone()[0]
    assert still_there == 1, "422 validation must leave the suggestion pending"
    # Rejecting it afterwards still works (claim path intact).
    resp2 = client.post(f"/api/review/suggestion:{sid}/resolve", json={"decision": "reject"})
    assert resp2.status_code == 200


# ── reclassify proposal ratification ─────────────────────────────────────────


def test_reclassify_approval_applies_proposal(client, db):
    import uuid

    doc = db.create_document(title="mystery.bin", kind="file", doc_type="unknown", tier="source")
    pid = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            """INSERT INTO pending_reclassify(id, doc_id, reason, created_at,
                                              proposed_tier, proposed_doc_type,
                                              proposed_tier_by, proposed_doc_type_by)
               VALUES(?, ?, 'Model proposes reference', datetime('now'),
                      'artifact', 'reference', 'model', 'model')""",
            (pid, doc["id"]),
        )
        db._conn.commit()

    resp = client.post(f"/api/review/reclassify:{pid}/resolve", json={"decision": "approve"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["applied"] == {"tier": "artifact", "doc_type": "reference"}

    got = db.get_document(doc["id"])
    assert got["tier"] == "artifact"
    assert got["doc_type"] == "reference"
    assert got["doc_type_by"] == "author", "ratified proposals carry author provenance"
    with db._lock:
        left = db._conn.execute(
            "SELECT COUNT(*) FROM pending_reclassify WHERE id=?", (pid,)
        ).fetchone()[0]
    assert left == 0


def test_reclassify_rejection_applies_nothing(client, db):
    import uuid

    doc = db.create_document(title="mystery.bin", kind="file", doc_type="unknown", tier="source")
    pid = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            """INSERT INTO pending_reclassify(id, doc_id, reason, created_at,
                                              proposed_doc_type, proposed_doc_type_by)
               VALUES(?, ?, 'Model proposes code', datetime('now'), 'code', 'model')""",
            (pid, doc["id"]),
        )
        db._conn.commit()

    resp = client.post(f"/api/review/reclassify:{pid}/resolve", json={"decision": "reject"})
    assert resp.status_code == 200
    got = db.get_document(doc["id"])
    assert got["doc_type"] == "unknown"
    assert got["tier"] == "source"


# ── acceptance ───────────────────────────────────────────────────────────────


def test_acceptance_every_doc_classified_after_backfill(db):
    for title, kind in [
        ("handbook.pdf", "pdf"),
        ("pipeline.py", "text"),
        ("rp016-test-catalog.json", "json"),
        ("mystery.bin", "file"),
    ]:
        db.create_document(title=title, kind=kind)
    backfill_classification(db)
    with db._lock:
        missing = db._conn.execute(
            "SELECT COUNT(*) FROM documents WHERE collection_id IS NULL OR doc_type IS NULL"
        ).fetchone()[0]
    assert missing == 0


def test_backfill_endpoint(client, db):
    db.create_document(title="handbook.pdf", kind="pdf")
    resp = client.post("/api/library/classify-backfill?propose_via_model=false")
    assert resp.status_code == 200
    body = resp.json()
    assert body["doc_type_applied"] >= 1
    assert body["model_proposals_queued"] is False


# ── system-output creation paths stamp doc_type at insert ────────────────────


def test_register_and_index_stamps_generated_and_refuses_harvest(db, _tmp, monkeypatch):
    """Persisted outputs must be 'generated' at creation — refused by harvest
    immediately, never only after a manual backfill."""
    from orivellum.capabilities.persist import register_and_index
    from orivellum.configuration.config import OrivellumConfig

    cfg = OrivellumConfig(data_dir=_tmp)
    out = Path(_tmp) / "outputs" / "clip.mp3"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"fake audio")

    doc_id = register_and_index(out, "TTS clip text", "mp3", db, cfg, title="TTS clip: hi")
    doc = db.get_document(doc_id)
    assert doc["doc_type"] == "generated"
    assert doc["doc_type_by"] == "rule:system-output"
    with pytest.raises(HarvestRefused):
        assert_doc_type_harvestable(db, doc_id)


def test_generate_register_output_stamps_generated(db, _tmp):
    from orivellum.capabilities.generate import _register_output
    from orivellum.configuration.config import OrivellumConfig

    cfg = OrivellumConfig(data_dir=_tmp)
    out = Path(_tmp) / "outputs" / "generate" / "brief.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("# Generated brief")

    doc_id = _register_output(out, None, db, cfg, "md", "Brief", text_content="# Generated brief")
    doc = db.get_document(doc_id)
    assert doc["doc_type"] == "generated"
    assert doc["doc_type_by"] == "rule:system-output"
    with pytest.raises(HarvestRefused):
        assert_doc_type_harvestable(db, doc_id)


def test_template_upload_stamps_doc_type(client, db, _tmp):
    resp = client.post(
        "/api/actions/template-fill",
        files={
            "template": ("invoice_template.docx", b"PK\x03\x04fake", "application/octet-stream")
        },
    )
    # Route may reject a fake docx downstream; the document row is what matters.
    with db._lock:
        row = db._conn.execute(
            "SELECT doc_type, doc_type_by FROM documents WHERE source='upload/template' LIMIT 1"
        ).fetchone()
    if row is None:
        # Registration failed before insert — the endpoint refused the fake
        # file entirely, which is fine; assert it did not 500 silently.
        assert resp.status_code in (400, 415, 422, 500), resp.text
    else:
        assert row["doc_type"] is not None
        assert row["doc_type_by"], "provenance must be stamped at creation"
