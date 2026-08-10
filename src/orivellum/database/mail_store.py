"""Mail Steward database operations.

Kept separate from the 5000-line db.py to isolate the mail feature.
All methods accept the main OrivellumDB instance and operate on its
connection/lock.  Graph IDs are stored encrypted; only domains and
display metadata are stored in the clear.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC
from typing import Any

logger = logging.getLogger("orivellum.mail.store")


class MailStore:
    def __init__(self, db: Any) -> None:
        self._db = db

    def _conn(self):
        return self._db._conn

    def _read(self):
        return self._db.read_conn()

    def _lock(self):
        return self._db._lock

    def _now(self) -> str:
        from datetime import datetime

        return datetime.now(UTC).isoformat()

    # ── Delta links ────────────────────────────────────────────────────────────

    def get_delta_link(self, folder_id: str) -> str | None:
        row = (
            self._read()
            .execute("SELECT delta_link FROM mail_delta_links WHERE folder_id=?", (folder_id,))
            .fetchone()
        )
        return row["delta_link"] if row else None

    def save_delta_link(self, folder_id: str, delta_link: str) -> None:
        with self._lock():
            self._conn().execute(
                """INSERT INTO mail_delta_links(folder_id, delta_link, updated_at)
                   VALUES(?,?,?)
                   ON CONFLICT(folder_id) DO UPDATE SET
                     delta_link=excluded.delta_link,
                     updated_at=excluded.updated_at""",
                (folder_id, delta_link, self._now()),
            )
            self._conn().commit()

    def clear_delta_link(self, folder_id: str) -> None:
        with self._lock():
            self._conn().execute("DELETE FROM mail_delta_links WHERE folder_id=?", (folder_id,))
            self._conn().commit()

    # ── Mail records ───────────────────────────────────────────────────────────

    def upsert_mail_record(self, msg: dict[str, Any], folder_id: str) -> bool:
        """Insert or update a mail record from a Graph delta message.

        Returns True if this is a new record, False if updated.
        """
        from orivellum.capabilities.mail.token_vault import encrypt_str

        graph_id = msg.get("id", "")
        change_key = msg.get("changeKey", "")
        conv_id = msg.get("conversationId", "")
        subject = (msg.get("subject") or "")[:500]
        received_at = msg.get("receivedDateTime", "")
        has_attach = int(bool(msg.get("hasAttachments")))
        importance = msg.get("importance", "normal")
        is_read = int(bool(msg.get("isRead")))

        sender_obj = msg.get("sender") or msg.get("from") or {}
        sender_ea = sender_obj.get("emailAddress") or {} if isinstance(sender_obj, dict) else {}
        sender_name = (sender_ea.get("name") or "")[:200]
        sender_addr = sender_ea.get("address") or ""
        # Store domain only — not the full address
        sender_domain = sender_addr.split("@")[-1].lower() if "@" in sender_addr else ""

        existing = (
            self._read()
            .execute(
                "SELECT id FROM mail_records WHERE graph_message_id_hash=?",
                (self._id_hash(graph_id),),
            )
            .fetchone()
        )

        now = self._now()
        if existing:
            with self._lock():
                self._conn().execute(
                    """UPDATE mail_records SET
                         graph_change_key_enc=?,
                         graph_folder_id_enc=?,
                         is_read=?,
                         updated_at=?
                       WHERE id=?""",
                    (encrypt_str(change_key), encrypt_str(folder_id), is_read, now, existing["id"]),
                )
                self._conn().commit()
            return False
        record_id = str(uuid.uuid4())
        with self._lock():
            self._conn().execute(
                """INSERT INTO mail_records(
                         id, graph_message_id_enc, graph_message_id_hash,
                         graph_change_key_enc, graph_folder_id_enc,
                         conversation_id, subject, sender_name, sender_domain,
                         received_at, has_attachments, importance, is_read,
                         lifecycle_state, created_at, updated_at, meta
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'METADATA_SYNCED',?,?,?)""",
                (
                    record_id,
                    encrypt_str(graph_id),
                    self._id_hash(graph_id),
                    encrypt_str(change_key),
                    encrypt_str(folder_id),
                    conv_id[:255],
                    subject,
                    sender_name,
                    sender_domain[:255],
                    received_at,
                    has_attach,
                    importance,
                    is_read,
                    now,
                    now,
                    "{}",
                ),
            )
            self._conn().commit()
        return True

    def mark_record_deleted(self, graph_id: str) -> None:
        """Mark a mail record as deleted in Outlook (soft delete locally)."""
        id_hash = self._id_hash(graph_id)
        with self._lock():
            self._conn().execute(
                """UPDATE mail_records SET lifecycle_state='DELETED', updated_at=?
                   WHERE graph_message_id_hash=?""",
                (self._now(), id_hash),
            )
            self._conn().commit()

    def get_mail_record(self, record_id: str) -> dict[str, Any] | None:
        row = self._read().execute("SELECT * FROM mail_records WHERE id=?", (record_id,)).fetchone()
        return dict(row) if row else None

    def list_mail_records(
        self,
        lifecycle_state: str | None = None,
        attention_level: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where = ["lifecycle_state != 'DELETED'"]
        params: list[Any] = []
        if lifecycle_state:
            where.append("lifecycle_state=?")
            params.append(lifecycle_state)
        rows = (
            self._read()
            .execute(
                f"""SELECT r.*, a.attention_level, a.needs_reply, a.recommended_action,
                       a.confidence, a.is_high_risk, a.rationale
                FROM mail_records r
                LEFT JOIN mail_assessments a ON a.id = r.assessment_id
                WHERE {" AND ".join(where)}
                ORDER BY r.received_at DESC
                LIMIT ? OFFSET ?""",
                (*params, limit, offset),
            )
            .fetchall()
        )
        result = [dict(row) for row in rows]
        if attention_level:
            result = [r for r in result if (r.get("attention_level") or "") == attention_level]
        return result

    def update_lifecycle(
        self,
        record_id: str,
        state: str,
        assessment_id: str | None = None,
        action_request_id: str | None = None,
    ) -> None:
        updates = ["lifecycle_state=?", "updated_at=?"]
        params: list[Any] = [state, self._now()]
        if assessment_id:
            updates.append("assessment_id=?")
            params.append(assessment_id)
        if action_request_id:
            updates.append("action_request_id=?")
            params.append(action_request_id)
        params.append(record_id)
        with self._lock():
            self._conn().execute(
                f"UPDATE mail_records SET {', '.join(updates)} WHERE id=?",
                params,
            )
            self._conn().commit()

    def update_record_after_move(
        self,
        record_id: str,
        new_graph_id_enc: str,
        new_folder_id_enc: str,
        action_request_id: str | None,
    ) -> None:
        with self._lock():
            self._conn().execute(
                """UPDATE mail_records SET
                     graph_message_id_enc=?,
                     graph_message_id_hash=?,
                     graph_folder_id_enc=?,
                     action_request_id=?,
                     updated_at=?
                   WHERE id=?""",
                (
                    new_graph_id_enc,
                    "(post-move)",  # hash invalidated; re-derive on next sync
                    new_folder_id_enc,
                    action_request_id,
                    self._now(),
                    record_id,
                ),
            )
            self._conn().commit()

    # ── Assessments ────────────────────────────────────────────────────────────

    def create_assessment(
        self,
        assessment_id: str,
        mail_record_id: str,
        assessment: Any,  # models.Assessment
        threat_evidence: list,
    ) -> None:
        signals_json = json.dumps(assessment.signals)
        json.dumps(
            [
                {"feed": e.feed, "indicator": e.indicator, "type": e.indicator_type}
                for e in threat_evidence
            ]
        )
        with self._lock():
            self._conn().execute(
                """INSERT INTO mail_assessments(
                     id, mail_record_id, attention_level, needs_reply, rationale,
                     suggested_reply, recommended_action, confidence,
                     is_high_risk, injection_flagged, model_id,
                     signals_json, created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    assessment_id,
                    mail_record_id,
                    assessment.attention_level,
                    int(assessment.needs_reply),
                    assessment.rationale,
                    assessment.suggested_reply,
                    assessment.recommended_action,
                    assessment.confidence,
                    int(assessment.is_high_risk),
                    int(assessment.injection_flagged),
                    assessment.model_id,
                    signals_json,
                    self._now(),
                ),
            )
            self._conn().commit()

    def get_latest_assessment(self, mail_record_id: str) -> dict[str, Any] | None:
        row = (
            self._read()
            .execute(
                """SELECT * FROM mail_assessments
               WHERE mail_record_id=? ORDER BY created_at DESC LIMIT 1""",
                (mail_record_id,),
            )
            .fetchone()
        )
        return dict(row) if row else None

    # ── Nonces ─────────────────────────────────────────────────────────────────

    def issue_nonce(self, mail_record_id: str, action_type: str) -> str:
        """Create and store a single-use approval nonce."""
        nonce = str(uuid.uuid4())
        with self._lock():
            self._conn().execute(
                """INSERT INTO mail_action_requests(
                     id, mail_record_id, assessment_id, action_type,
                     nonce, status, actor, created_at
                   ) VALUES(?,?,NULL,?,?,'PENDING','user',?)""",
                (str(uuid.uuid4()), mail_record_id, action_type, nonce, self._now()),
            )
            self._conn().commit()
        return nonce

    def consume_nonce(self, nonce: str, mail_record_id: str, action_type: str) -> bool:
        """Validate and consume a single-use nonce.  Returns True if valid."""
        row = (
            self._read()
            .execute(
                """SELECT id FROM mail_action_requests
               WHERE nonce=? AND mail_record_id=? AND action_type=? AND status='PENDING'""",
                (nonce, mail_record_id, action_type),
            )
            .fetchone()
        )
        if not row:
            return False
        with self._lock():
            self._conn().execute(
                "UPDATE mail_action_requests SET status='APPROVED' WHERE id=?",
                (row["id"],),
            )
            self._conn().commit()
        return True

    # ── Action requests ────────────────────────────────────────────────────────

    def create_action_request(
        self,
        mail_record_id: str,
        assessment_id: str | None,
        action_type: str,
        graph_draft_id_enc: str | None = None,
        destination_folder_id_enc: str | None = None,
        original_folder_id_enc: str | None = None,
        result_message_id_enc: str | None = None,
        status: str = "APPLIED",
    ) -> str:
        action_id = str(uuid.uuid4())
        with self._lock():
            self._conn().execute(
                """INSERT INTO mail_action_requests(
                     id, mail_record_id, assessment_id, action_type,
                     destination_folder_id_enc, graph_draft_id_enc,
                     nonce, status, result_message_id_enc,
                     original_folder_id_enc, actor, created_at, applied_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    action_id,
                    mail_record_id,
                    assessment_id,
                    action_type,
                    destination_folder_id_enc,
                    graph_draft_id_enc,
                    str(uuid.uuid4()),  # consumed nonce placeholder
                    status,
                    result_message_id_enc,
                    original_folder_id_enc,
                    "user",
                    self._now(),
                    self._now(),
                ),
            )
            self._conn().commit()
        return action_id

    def get_action_request(self, action_id: str) -> dict[str, Any] | None:
        row = (
            self._read()
            .execute("SELECT * FROM mail_action_requests WHERE id=?", (action_id,))
            .fetchone()
        )
        return dict(row) if row else None

    def update_action_status(self, action_id: str, status: str) -> None:
        with self._lock():
            self._conn().execute(
                "UPDATE mail_action_requests SET status=?, applied_at=? WHERE id=?",
                (status, self._now(), action_id),
            )
            self._conn().commit()

    # ── Audit events ───────────────────────────────────────────────────────────

    def create_audit_event(
        self,
        mail_record_id: str | None,
        event_type: str,
        *,
        action_request_id: str | None = None,
        signals: list[str] | None = None,
        model_id: str = "",
        before: dict | None = None,
        after: dict | None = None,
        result: str = "SUCCESS",
    ) -> str:
        from orivellum.capabilities.mail.action_policy import policy_version_tag

        event_id = str(uuid.uuid4())
        with self._lock():
            self._conn().execute(
                """INSERT INTO mail_audit_events(
                     id, mail_record_id, action_request_id, at, actor, event_type,
                     policy_version, model_id, signals_json,
                     before_json, after_json, result
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    mail_record_id,
                    action_request_id,
                    self._now(),
                    "system",
                    event_type,
                    policy_version_tag(),
                    model_id,
                    json.dumps(signals or []),
                    json.dumps(before or {}),
                    json.dumps(after or {}),
                    result,
                ),
            )
            self._conn().commit()
        return event_id

    def list_audit_events(
        self, mail_record_id: str | None = None, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        if mail_record_id:
            rows = (
                self._read()
                .execute(
                    "SELECT * FROM mail_audit_events WHERE mail_record_id=? ORDER BY at DESC LIMIT ? OFFSET ?",
                    (mail_record_id, limit, offset),
                )
                .fetchall()
            )
        else:
            rows = (
                self._read()
                .execute(
                    "SELECT * FROM mail_audit_events ORDER BY at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
                .fetchall()
            )
        return [dict(r) for r in rows]

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _id_hash(graph_id: str) -> str:
        """SHA-256 prefix used for dedup lookups without storing the raw ID."""
        import hashlib

        return hashlib.sha256(graph_id.encode()).hexdigest()[:32]

    def list_mail_context_records(self, limit: int = 5, days: int = 30) -> list[dict[str, Any]]:
        """Return redacted mail records for chat context injection.

        Only subject, sender_domain, received_at, attention_level, and
        rationale are returned — never the message body or full addresses.
        High-attention records come first; medium after.

        Args:
            limit: Maximum number of records to return.
            days:  Only include messages received within the last *days* days
                   (default 30).  Pass 0 to disable the recency cap.
        """
        if days > 0:
            recency_clause = f"AND r.received_at >= datetime('now', '-{int(days)} days')"
        else:
            recency_clause = ""

        rows = (
            self._read()
            .execute(
                f"""SELECT r.id, r.subject, r.sender_name, r.sender_domain, r.received_at,
                      a.attention_level, a.rationale, a.needs_reply
               FROM mail_records r
               LEFT JOIN mail_assessments a ON a.id = r.assessment_id
               WHERE r.lifecycle_state NOT IN ('DELETED','ACTION_APPLIED','VERIFIED')
                 AND a.attention_level IN ('high','medium')
                 {recency_clause}
               ORDER BY
                 CASE a.attention_level WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                 r.received_at DESC
               LIMIT ?""",
                (limit,),
            )
            .fetchall()
        )
        return [dict(row) for row in rows]

    def summary(self) -> dict[str, Any]:
        """Return counts for the /api/mail/summary endpoint."""
        r = self._read()
        total = r.execute(
            "SELECT COUNT(*) c FROM mail_records WHERE lifecycle_state!='DELETED'"
        ).fetchone()["c"]
        attention = r.execute(
            "SELECT COUNT(*) c FROM mail_records r JOIN mail_assessments a ON a.id=r.assessment_id "
            "WHERE a.attention_level='high' AND r.lifecycle_state NOT IN ('DELETED','ACTION_APPLIED','VERIFIED')"
        ).fetchone()["c"]
        pending = r.execute(
            "SELECT COUNT(*) c FROM mail_action_requests WHERE status='PENDING'"
        ).fetchone()["c"]
        unread = r.execute(
            "SELECT COUNT(*) c FROM mail_records WHERE is_read=0 AND lifecycle_state!='DELETED'"
        ).fetchone()["c"]
        return {
            "total_synced": total,
            "high_attention": attention,
            "pending_actions": pending,
            "unread": unread,
        }
