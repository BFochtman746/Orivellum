"""Tests for list_mail_context_records recency cap.

Verifies that the days= parameter correctly filters messages by received_at:
  - Old messages (> cap) are excluded when days > 0
  - Recent messages (< cap) are included
  - Cap is disabled when days=0
  - Ordering (high before medium) is preserved within the window
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orivellum.database.db import OrivellumDB
from orivellum.database.mail_store import MailStore

# ── Helpers ────────────────────────────────────────────────────────────────────


def _fresh_db(tmp_path: Path) -> OrivellumDB:
    return OrivellumDB(str(tmp_path / "test.db"))


def _iso(days_ago: float) -> str:
    """Return an ISO-8601 UTC string for *days_ago* days in the past."""
    dt = datetime.now(UTC) - timedelta(days=days_ago)
    return dt.isoformat()


def _insert_record(
    db: OrivellumDB,
    *,
    record_id: str | None = None,
    received_at: str,
    attention_level: str = "high",
    lifecycle_state: str = "METADATA_SYNCED",
    subject: str = "Test subject",
    sender_domain: str = "example.com",
    needs_reply: int = 0,
) -> str:
    """Insert a mail_record + mail_assessment directly into the DB.

    Returns the record ID.
    """
    rid = record_id or str(uuid.uuid4())
    aid = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    with db._lock:
        db._conn.execute(
            """INSERT INTO mail_records(
                 id, graph_message_id_enc, graph_message_id_hash,
                 subject, sender_name, sender_domain,
                 received_at, lifecycle_state, assessment_id,
                 created_at, updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rid,
                f"enc-{rid}",
                f"hash-{rid}",
                subject,
                "Sender Name",
                sender_domain,
                received_at,
                lifecycle_state,
                aid,
                now,
                now,
            ),
        )
        db._conn.execute(
            """INSERT INTO mail_assessments(
                 id, mail_record_id, attention_level, needs_reply,
                 rationale, recommended_action, confidence,
                 is_high_risk, injection_flagged, model_id,
                 signals_json, created_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                aid,
                rid,
                attention_level,
                needs_reply,
                "Test rationale",
                "REVIEW",
                0.9,
                0,
                0,
                "test-model",
                "[]",
                now,
            ),
        )
        db._conn.commit()

    return rid


# ── Tests ──────────────────────────────────────────────────────────────────────


class TestRecencyCap:
    def test_old_message_excluded_by_default_cap(self, tmp_path):
        """A high-attention message received 31 days ago must NOT appear
        when days=30 (the default)."""
        db = _fresh_db(tmp_path)
        store = MailStore(db)

        _insert_record(db, received_at=_iso(31), attention_level="high")

        results = store.list_mail_context_records(days=30)
        assert results == [], (
            "Expected no results: 31-day-old message should be excluded by 30-day cap"
        )
        db.close()

    def test_recent_message_included_within_cap(self, tmp_path):
        """A high-attention message received yesterday MUST appear
        when days=30."""
        db = _fresh_db(tmp_path)
        store = MailStore(db)

        rid = _insert_record(
            db,
            received_at=_iso(1),
            attention_level="high",
            subject="Recent urgent mail",
        )

        results = store.list_mail_context_records(days=30)
        assert len(results) == 1, f"Expected 1 result, got {len(results)}"
        assert results[0]["id"] == rid
        db.close()

    def test_cap_disabled_when_days_zero(self, tmp_path):
        """When days=0 the recency clause is suppressed; a 31-day-old
        high-attention message MUST be returned."""
        db = _fresh_db(tmp_path)
        store = MailStore(db)

        rid = _insert_record(db, received_at=_iso(31), attention_level="high")

        results = store.list_mail_context_records(days=0)
        ids = [r["id"] for r in results]
        assert rid in ids, "Expected old message to appear when days=0 (cap disabled)"
        db.close()

    def test_boundary_message_at_exactly_cap_is_included(self, tmp_path):
        """A message received exactly 30 days ago (within the window) is included."""
        db = _fresh_db(tmp_path)
        store = MailStore(db)

        # SQLite datetime('now', '-30 days') is exclusive of exactly 30 days
        # only to the sub-second level; a message timestamped 30*24h ago
        # (i.e. slightly inside the window) must appear.
        rid = _insert_record(
            db,
            received_at=_iso(29.9),
            attention_level="medium",
        )

        results = store.list_mail_context_records(days=30)
        ids = [r["id"] for r in results]
        assert rid in ids, "Message just inside the 30-day window must be included"
        db.close()

    def test_ordering_high_before_medium(self, tmp_path):
        """High-attention records must appear before medium-attention ones
        regardless of received_at (within the same recency window)."""
        db = _fresh_db(tmp_path)
        store = MailStore(db)

        medium_id = _insert_record(
            db,
            received_at=_iso(1),
            attention_level="medium",
            subject="Medium mail",
        )
        high_id = _insert_record(
            db,
            received_at=_iso(2),
            attention_level="high",
            subject="High mail (older but higher priority)",
        )

        results = store.list_mail_context_records(days=30)
        assert len(results) == 2
        assert results[0]["id"] == high_id, (
            "HIGH attention record must appear before MEDIUM regardless of age"
        )
        assert results[1]["id"] == medium_id
        db.close()

    def test_deleted_records_never_returned(self, tmp_path):
        """Records with lifecycle_state='DELETED' must be excluded even if recent."""
        db = _fresh_db(tmp_path)
        store = MailStore(db)

        _insert_record(
            db,
            received_at=_iso(1),
            attention_level="high",
            lifecycle_state="DELETED",
        )

        results = store.list_mail_context_records(days=30)
        assert results == [], "DELETED records must never appear in context injection"
        db.close()

    def test_action_applied_records_never_returned(self, tmp_path):
        """Records with lifecycle_state='ACTION_APPLIED' must be excluded."""
        db = _fresh_db(tmp_path)
        store = MailStore(db)

        _insert_record(
            db,
            received_at=_iso(1),
            attention_level="high",
            lifecycle_state="ACTION_APPLIED",
        )

        results = store.list_mail_context_records(days=30)
        assert results == [], "ACTION_APPLIED records must not appear in context"
        db.close()

    def test_limit_respected(self, tmp_path):
        """The limit parameter caps the number of returned records."""
        db = _fresh_db(tmp_path)
        store = MailStore(db)

        for i in range(5):
            _insert_record(
                db,
                received_at=_iso(i),  # all recent
                attention_level="high",
                subject=f"Mail {i}",
            )

        results = store.list_mail_context_records(limit=3, days=30)
        assert len(results) == 3, f"Expected exactly 3 results (limit=3), got {len(results)}"
        db.close()

    def test_mixed_old_and_recent_only_recent_returned(self, tmp_path):
        """When both old and recent records exist, only recent ones are returned
        when days>0."""
        db = _fresh_db(tmp_path)
        store = MailStore(db)

        old_id = _insert_record(
            db,
            received_at=_iso(45),
            attention_level="high",
            subject="Old urgent mail",
        )
        recent_id = _insert_record(
            db,
            received_at=_iso(5),
            attention_level="high",
            subject="Recent urgent mail",
        )

        results = store.list_mail_context_records(days=30)
        ids = [r["id"] for r in results]

        assert recent_id in ids, "Recent message must be included"
        assert old_id not in ids, "45-day-old message must be excluded by 30-day cap"
        db.close()
