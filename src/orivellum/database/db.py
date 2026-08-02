"""OrivellumDB — single authoritative SQLite connection.

Features:
- WAL mode for concurrent readers
- Foreign keys enforced
- Busy timeout for lock contention
- Append-only migration ledger (37 migrations from schema.py)
- Thread-safe with RLock
- Helpers for common query patterns
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import MIGRATIONS

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


def _jdump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _jload(s: str | None, default: Any = None) -> Any:
    if s is None:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


class OrivellumDB:
    """Thread-safe SQLite database with governed migration runner."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = threading.RLock()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._configure()
        self._run_migrations()

    @classmethod
    def open(cls, path: str) -> "OrivellumDB":
        return cls(path)

    def _configure(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")

    def _run_migrations(self) -> None:
        """Apply any pending migrations in version order."""
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL DEFAULT 'global',
                    key TEXT NOT NULL,
                    value TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE(scope, key)
                )
            """)
            self._conn.commit()

            current = self._get_setting("schema_version", "0")
            current_v = int(current)

            pending = [(v, d, s) for v, d, s in MIGRATIONS if v > current_v]
            if not pending:
                return

            logger.info("Applying %d pending migrations (current v%d)", len(pending), current_v)
            for version, description, sql in sorted(pending, key=lambda x: x[0]):
                try:
                    with self._lock:
                        for stmt in sql.split(";"):
                            stmt = stmt.strip()
                            if stmt:
                                try:
                                    self._conn.execute(stmt)
                                except sqlite3.OperationalError as e:
                                    # Ignore "duplicate column" errors (idempotent ALTER TABLE)
                                    if "duplicate column" in str(e).lower():
                                        pass
                                    else:
                                        raise
                        self._set_setting("schema_version", str(version))
                        self._conn.commit()
                    logger.info("  Applied migration v%d: %s", version, description)
                except Exception as exc:
                    logger.error("Migration v%d failed: %s", version, exc)
                    raise

    # -------------------------------------------------------------------------
    # Settings helpers
    # -------------------------------------------------------------------------

    def _get_setting(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM settings WHERE scope='global' AND key=?", (key,)
        ).fetchone()
        return row["value"] if row and row["value"] is not None else default

    def _set_setting(self, key: str, value: str) -> None:
        now = _now()
        self._conn.execute(
            """INSERT INTO settings(id, scope, key, value, updated_at) VALUES(?,?,?,?,?)
               ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
            (_uuid(), "global", key, value, now),
        )

    def get_setting(self, key: str, default: str = "") -> str:
        with self._lock:
            return self._get_setting(key, default)

    # Keys whose values must never appear in audit detail (secrets/tokens)
    _AUDIT_SECRET_KEYS: frozenset[str] = frozenset({"api_key", "session_secret", "token"})

    def set_setting(self, key: str, value: str, actor: str = "system") -> None:
        with self._lock:
            self._set_setting(key, value)
            self._conn.commit()
        # Never include the value for secret keys
        safe_detail = None if key in self._AUDIT_SECRET_KEYS else f"{key}={value[:40]}"
        self.audit("setting.updated", object_id=key, object_type="setting",
                   actor=actor, detail=safe_detail)

    # -------------------------------------------------------------------------
    # Audit log
    # -------------------------------------------------------------------------

    def audit(
        self,
        operation: str,
        object_id: str | None = None,
        object_type: str | None = None,
        actor: str = "system",
        result: str = "ok",
        detail: str | None = None,
        before_hash: str | None = None,
        after_hash: str | None = None,
    ) -> None:
        """Append a single audit-log entry.  Never raises — audit failures are
        logged as warnings, never allowed to break the calling operation."""
        try:
            entry_id = _uuid()
            now = _now()
            with self._lock:
                self._conn.execute(
                    """INSERT INTO audit_log(id, timestamp, actor, operation,
                       object_id, object_type, before_hash, after_hash,
                       result, detail, app_version)
                       VALUES(?,?,?,?,?,?,?,?,?,?,'0.1.0')""",
                    (entry_id, now, actor, operation, object_id,
                     object_type, before_hash, after_hash, result, detail),
                )
                self._conn.commit()
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).warning("audit write failed: %s", exc)

    def list_audit_log(
        self,
        limit: int = 100,
        object_id: str | None = None,
        object_type: str | None = None,
        actor: str | None = None,
        operation: str | None = None,
        since: str | None = None,
    ) -> list[dict]:
        """Return recent audit-log entries, newest first.

        since: ISO-8601 timestamp lower bound (inclusive).
        """
        q = "SELECT * FROM audit_log WHERE 1=1"
        args: list = []
        if object_id:
            q += " AND object_id=?"
            args.append(object_id)
        if object_type:
            q += " AND object_type=?"
            args.append(object_type)
        if actor:
            q += " AND actor=?"
            args.append(actor)
        if operation:
            q += " AND operation LIKE ?"
            args.append(f"%{operation}%")
        if since:
            q += " AND timestamp>=?"
            args.append(since)
        q += " ORDER BY timestamp DESC LIMIT ?"
        args.append(min(limit, 1000))
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Near-duplicate tracking (referenced by capabilities/dedup.py)
    # -------------------------------------------------------------------------

    def list_near_duplicates(self, resolved: bool = False,
                             work_id: str | None = None) -> list[dict]:
        """Return doc_dupes rows with document titles joined in.

        By default returns only unresolved pairs (resolved=False).
        Pass resolved=True to fetch already-actioned pairs instead.
        Pass work_id to restrict to pairs where at least one document
        belongs to that Work.
        """
        args: list = [1 if resolved else 0]
        q = """SELECT dd.*, da.title as doc_a_title, db2.title as doc_b_title,
                      da.work_id as doc_a_work_id, db2.work_id as doc_b_work_id
               FROM doc_dupes dd
               JOIN documents da  ON da.id  = dd.doc_a_id
               JOIN documents db2 ON db2.id = dd.doc_b_id
               WHERE dd.resolved=?"""
        if work_id:
            q += " AND (da.work_id=? OR db2.work_id=?)"
            args.extend([work_id, work_id])
        q += " ORDER BY dd.similarity DESC"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def resolve_near_duplicate(self, dupe_id: str, action: str,
                               canonical_doc_id: str | None = None) -> dict | None:
        """Mark a near-duplicate pair as resolved.

        action options:
          keep_both        — dismiss the alert; keep both documents as-is
          mark_versions    — create a DERIVED_FROM relationship between the pair
          mark_superseded  — set the non-canonical doc lifecycle to 'superseded';
                             pass canonical_doc_id to specify which survives
                             (defaults to doc_a if omitted)

        Returns the original doc_dupes row dict, or None if not found.
        """
        _VALID = {"keep_both", "mark_versions", "mark_superseded"}
        if action not in _VALID:
            raise ValueError(f"action must be one of {sorted(_VALID)}")

        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM doc_dupes WHERE id=?", (dupe_id,)
            ).fetchone()
        if not row:
            return None

        dupe = dict(row)
        now = _now()

        with self._lock:
            self._conn.execute(
                "UPDATE doc_dupes SET resolved=1, resolution=? WHERE id=?",
                (action, dupe_id),
            )
            self._conn.commit()

        if action == "mark_versions":
            # Create DERIVED_FROM relationship: doc_b is derived from doc_a.
            # relationships.id is a FK to objects, so we must create the object row first.
            try:
                rel_oid = self._create_object("relationship")
                with self._lock:
                    self._conn.execute(
                        """INSERT OR IGNORE INTO relationships
                           (id, source_id, target_id, kind, weight, meta, created_at)
                           VALUES(?,?,?,'DERIVED_FROM',1.0,'{}',?)""",
                        (rel_oid, dupe["doc_b_id"], dupe["doc_a_id"], now),
                    )
                    self._conn.commit()
                self.audit("document.version_linked", object_id=dupe["doc_a_id"],
                           object_type="document", actor="user",
                           detail=f"DERIVED_FROM {dupe['doc_b_id'][:8]}")
            except Exception as exc:
                logger.debug("mark_versions relationship insert failed: %s", exc)

        elif action == "mark_superseded":
            # Mark the non-canonical document as superseded.
            # canonical_doc_id identifies the survivor; the other one is superseded.
            # Defaults to doc_a as canonical (doc_b gets superseded) when not supplied.
            if canonical_doc_id and canonical_doc_id == dupe["doc_b_id"]:
                superseded_id = dupe["doc_a_id"]  # user chose doc_b as canonical
            else:
                superseded_id = dupe["doc_b_id"]  # default: doc_a is canonical
            try:
                self.update_document_lifecycle(superseded_id, "superseded")
            except Exception as exc:
                logger.debug("mark_superseded lifecycle update failed: %s", exc)

        self.audit("document.dupe_resolved", object_id=dupe_id,
                   object_type="doc_dupe", actor="user",
                   detail=f"action={action}")
        return dupe

    # -------------------------------------------------------------------------
    # Object creation helper
    # -------------------------------------------------------------------------

    def _create_object(self, obj_type: str, extra: dict | None = None,
                       lifecycle: str = "active") -> str:
        oid = _uuid()
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO objects(id, type, version, lifecycle, provenance, permissions,
                   created_at, updated_at, created_by)
                   VALUES(?,?,1,?,'{}','{}',?,?,'user')""",
                (oid, obj_type, lifecycle, now, now),
            )
        return oid

    # -------------------------------------------------------------------------
    # Works
    # -------------------------------------------------------------------------

    def list_works(self, status: str | None = None, work_type: str | None = None,
                   limit: int = 200) -> list[dict]:
        q = """SELECT w.*, o.created_at as obj_created, o.lifecycle,
                      (SELECT COUNT(*) FROM documents d WHERE d.work_id=w.id) as doc_count,
                      (SELECT COUNT(*) FROM documents d WHERE d.work_id=w.id AND d.readiness='ready') as ready_doc_count,
                      (SELECT COUNT(*) FROM documents d WHERE d.work_id=w.id AND d.readiness IN ('error','no_text')) as error_doc_count,
                      (SELECT COUNT(*) FROM documents d WHERE d.work_id=w.id AND d.readiness='imported') as processing_doc_count,
                      (SELECT COUNT(*) FROM tasks t WHERE t.work_id=w.id AND t.status='pending') as pending_tasks,
                      (SELECT COUNT(*) FROM knowledge k WHERE k.work_id=w.id) as knowledge_count,
                      (SELECT COUNT(*) FROM conversations c WHERE c.work_id=w.id) as conv_count
               FROM works w JOIN objects o ON o.id=w.id
               WHERE o.lifecycle != 'deleted'"""
        args: list = []
        if status:
            q += " AND w.status=?"
            args.append(status)
        if work_type:
            q += " AND w.work_type=?"
            args.append(work_type)
        q += " ORDER BY o.updated_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [self._work_dict(r) for r in rows]

    def get_work(self, work_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT w.*, o.created_at as obj_created, o.updated_at as obj_updated,
                          o.lifecycle,
                          (SELECT COUNT(*) FROM documents d WHERE d.work_id=w.id) as doc_count,
                          (SELECT COUNT(*) FROM tasks t WHERE t.work_id=w.id AND t.status='pending') as pending_tasks,
                          (SELECT COUNT(*) FROM knowledge k WHERE k.work_id=w.id) as knowledge_count,
                          (SELECT COUNT(*) FROM conversations c WHERE c.work_id=w.id) as conv_count
                   FROM works w JOIN objects o ON o.id=w.id
                   WHERE w.id=? AND o.lifecycle != 'deleted'""",
                (work_id,),
            ).fetchone()
        return self._work_dict(row) if row else None

    def create_work(self, title: str, work_type: str = "research",
                    description: str | None = None, meta: dict | None = None) -> dict:
        oid = _uuid()
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,created_at,updated_at,created_by) VALUES(?,?,1,'active','{}','{}',?,?,'user')",
                (oid, "work", now, now),
            )
            self._conn.execute(
                "INSERT INTO works(id,title,work_type,description,status,meta) VALUES(?,?,?,?,?,?)",
                (oid, title, work_type, description, "active", _jdump(meta or {})),
            )
            self._conn.commit()
        self.audit("work.created", object_id=oid, object_type="work",
                   after_hash=hashlib.sha256(f"{title}:{work_type}".encode()).hexdigest(),
                   detail=title[:120] if title else None)
        return self.get_work(oid)  # type: ignore[return-value]

    def update_work(self, work_id: str, **kwargs: Any) -> dict | None:
        now = _now()
        allowed = {"title", "description", "status", "meta"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return self.get_work(work_id)
        if "meta" in updates:
            updates["meta"] = _jdump(updates["meta"])
        set_clause = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [work_id]
        _before_row: dict | None = None
        _rowcount = 0
        with self._lock:
            _br = self._conn.execute(
                "SELECT title, status, description, meta FROM works WHERE id=?", (work_id,)
            ).fetchone()
            if _br:
                _before_row = {"title": _br["title"], "status": _br["status"],
                               "description": _br["description"], "meta": _br["meta"]}
            cur = self._conn.execute(f"UPDATE works SET {set_clause} WHERE id=?", vals)
            _rowcount = cur.rowcount
            self._conn.execute("UPDATE objects SET updated_at=? WHERE id=?", (now, work_id))
            self._conn.commit()
        if _rowcount > 0 and _before_row is not None:
            _bh = hashlib.sha256(json.dumps(_before_row, sort_keys=True).encode()).hexdigest()
            # Fetch the same canonical fields AFTER the update for a comparable after-hash
            with self._lock:
                _ar = self._conn.execute(
                    "SELECT title, status, description, meta FROM works WHERE id=?", (work_id,)
                ).fetchone()
            _after_row = {"title": _ar["title"], "status": _ar["status"],
                          "description": _ar["description"], "meta": _ar["meta"]} if _ar else {}
            _ah = hashlib.sha256(json.dumps(_after_row, sort_keys=True).encode()).hexdigest()
            self.audit("work.updated", object_id=work_id, object_type="work",
                       before_hash=_bh, after_hash=_ah,
                       detail=",".join(updates.keys()))
        return self.get_work(work_id)

    def delete_work(self, work_id: str) -> bool:
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE objects SET lifecycle='deleted', updated_at=? WHERE id=? AND lifecycle!='deleted'",
                (now, work_id),
            )
            self._conn.commit()
        if cur.rowcount > 0:
            self.audit("work.deleted", object_id=work_id, object_type="work")
        return cur.rowcount > 0

    @staticmethod
    def _work_dict(row: Any) -> dict:
        d = dict(row)
        d["meta"] = _jload(d.get("meta"), {})
        return d

    # -------------------------------------------------------------------------
    # Conversations
    # -------------------------------------------------------------------------

    def list_conversations(self, work_id: str | None = None, archived: bool = False,
                           limit: int = 100) -> list[dict]:
        q = """SELECT c.*,
                      (SELECT text FROM messages WHERE conversation_id=c.id ORDER BY created_at DESC LIMIT 1) as last_message,
                      (SELECT COUNT(*) FROM messages WHERE conversation_id=c.id) as message_count
               FROM conversations c WHERE c.archived=?"""
        args: list = [1 if archived else 0]
        if work_id:
            q += " AND c.work_id=?"
            args.append(work_id)
        q += " ORDER BY c.updated_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def get_conversation(self, conv_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM conversations WHERE id=?", (conv_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_messages(self, conv_id: str, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at ASC LIMIT ?",
                (conv_id, limit),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["meta"] = _jload(d.get("meta"), {})
            result.append(d)
        return result

    def create_conversation(self, title: str | None = None, work_id: str | None = None,
                            model: str | None = None) -> dict:
        cid = _uuid()
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO conversations(id,work_id,title,archived,model,created_at,updated_at) VALUES(?,?,?,0,?,?,?)",
                (cid, work_id, title, model, now, now),
            )
            self._conn.commit()
        self.audit("conversation.created", object_id=cid, object_type="conversation",
                   detail=title[:120] if title else work_id)
        return self.get_conversation(cid)  # type: ignore[return-value]

    def add_message(self, conv_id: str, role: str, text: str,
                    meta: dict | None = None) -> dict:
        mid = _uuid()
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO messages(id,conversation_id,role,text,meta,created_at) VALUES(?,?,?,?,?,?)",
                (mid, conv_id, role, text, _jdump(meta or {}), now),
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (now, conv_id)
            )
            self._conn.commit()
        _wc = len(text.split()) if text else 0
        self.audit("message.created", object_id=mid, object_type="message",
                   detail=f"{role} {_wc}w")
        return {"id": mid, "conversation_id": conv_id, "role": role, "text": text,
                "meta": meta or {}, "created_at": now}

    def update_conversation(self, conv_id: str, title: str | None = None,
                            archived: bool | None = None,
                            model: str | None = None) -> dict | None:
        now = _now()
        updates: dict[str, Any] = {"updated_at": now}
        if title is not None:
            updates["title"] = title
        if archived is not None:
            updates["archived"] = 1 if archived else 0
        if model is not None:
            updates["model"] = model
        set_clause = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [conv_id]
        _rowcount = 0
        with self._lock:
            cur = self._conn.execute(f"UPDATE conversations SET {set_clause} WHERE id=?", vals)
            _rowcount = cur.rowcount
            self._conn.commit()
        if _rowcount > 0:
            meaningful = {k: v for k, v in updates.items() if k != "updated_at"}
            if meaningful:
                self.audit("conversation.updated", object_id=conv_id, object_type="conversation",
                           detail=",".join(meaningful.keys()))
        return self.get_conversation(conv_id)

    def delete_conversation(self, conv_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
            self._conn.commit()
        if cur.rowcount > 0:
            self.audit("conversation.deleted", object_id=conv_id, object_type="conversation")
        return cur.rowcount > 0

    # -------------------------------------------------------------------------
    # Documents
    # -------------------------------------------------------------------------

    # Valid lifecycle states for documents
    _DOC_LIFECYCLES: frozenset[str] = frozenset(
        {"draft", "canonical", "superseded", "reference", "active"}
    )

    def list_documents(self, work_id: str | None = None, kind: str | None = None,
                       readiness: str | None = None, lifecycle: str | None = None,
                       limit: int = 200) -> list[dict]:
        q = """SELECT d.*, COALESCE(o.lifecycle, 'draft') AS lifecycle
               FROM documents d LEFT JOIN objects o ON o.id = d.id
               WHERE 1=1"""
        args: list = []
        if work_id:
            q += " AND d.work_id=?"
            args.append(work_id)
        if kind:
            q += " AND d.kind=?"
            args.append(kind)
        if readiness:
            q += " AND d.readiness=?"
            args.append(readiness)
        if lifecycle:
            q += " AND COALESCE(o.lifecycle, 'draft')=?"
            args.append(lifecycle)
        q += " ORDER BY d.created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [self._doc_dict(r) for r in rows]

    def get_document(self, doc_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT d.*, COALESCE(o.lifecycle, 'draft') AS lifecycle
                   FROM documents d LEFT JOIN objects o ON o.id = d.id
                   WHERE d.id=?""",
                (doc_id,),
            ).fetchone()
        return self._doc_dict(row) if row else None

    def update_document_lifecycle(self, doc_id: str, lifecycle: str) -> bool:
        """Set the lifecycle state for a document.

        When marking 'canonical', all other docs in the same Work/kind group are
        moved to 'draft' unless they are already 'superseded' or 'deleted'.
        """
        if lifecycle not in self._DOC_LIFECYCLES:
            raise ValueError(f"Invalid lifecycle: {lifecycle!r}. "
                             f"Valid values: {sorted(self._DOC_LIFECYCLES)}")
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE objects SET lifecycle=?, updated_at=? WHERE id=?",
                (lifecycle, now, doc_id),
            )
            if lifecycle == "canonical" and cur.rowcount > 0:
                row = self._conn.execute(
                    "SELECT work_id, kind FROM documents WHERE id=?", (doc_id,)
                ).fetchone()
                if row and row["work_id"]:
                    # Demote all other same-work docs of same kind to 'draft'
                    # (skip superseded and deleted so they stay as-is)
                    self._conn.execute(
                        """UPDATE objects SET lifecycle='draft', updated_at=?
                           WHERE id IN (
                               SELECT id FROM documents
                               WHERE work_id=? AND kind=? AND id!=?
                           ) AND lifecycle NOT IN ('superseded','deleted')""",
                        (now, row["work_id"], row["kind"], doc_id),
                    )
            self._conn.commit()
        if cur.rowcount > 0:
            self.audit("document.lifecycle_updated", object_id=doc_id,
                       object_type="document", detail=lifecycle)
        return cur.rowcount > 0

    def create_document(self, title: str, source: str | None = None, sha256: str | None = None,
                        kind: str | None = None, work_id: str | None = None,
                        content_path: str | None = None, meta: dict | None = None) -> dict:
        oid = self._create_object("document", lifecycle="draft")
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO documents(id,work_id,title,source,sha256,kind,readiness,
                   content_path,meta,created_at) VALUES(?,?,?,?,?,?,'imported',?,?,?)""",
                (oid, work_id, title, source, sha256, kind, content_path, _jdump(meta or {}), now),
            )
            self._conn.commit()
        self.audit("document.imported", object_id=oid, object_type="document",
                   after_hash=sha256,
                   detail=title[:120] if title else source)
        return self.get_document(oid)  # type: ignore[return-value]

    def update_document_work(self, doc_id: str, work_id: str | None) -> bool:
        """Re-assign (or unlink) a document from a work."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE documents SET work_id=? WHERE id=?", (work_id, doc_id)
            )
            self._conn.commit()
        if cur.rowcount > 0:
            op = "document.work_assigned" if work_id else "document.work_unlinked"
            self.audit(op, object_id=doc_id, object_type="document", detail=work_id)
        return cur.rowcount > 0

    def delete_document(self, doc_id: str) -> bool:
        with self._lock:
            # Capture title before deletion for audit detail
            _row = self._conn.execute(
                "SELECT title FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
            _title = _row["title"] if _row else None
            cur = self._conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            self._conn.execute(
                "UPDATE objects SET lifecycle='deleted', updated_at=? WHERE id=?",
                (_now(), doc_id),
            )
            self._conn.commit()
        if cur.rowcount > 0:
            self.audit("document.deleted", object_id=doc_id, object_type="document",
                       detail=_title)
        return cur.rowcount > 0

    @staticmethod
    def _doc_dict(row: Any) -> dict:
        d = dict(row)
        d["meta"] = _jload(d.get("meta"), {})
        return d

    def search_chunks(self, query: str, work_id: str | None = None,
                      limit: int = 10) -> list[dict]:
        """BM25-ranked full-text search over document chunks.

        Returns results ordered by relevance (bm25_score, lower = better).
        Each result includes a ``snippet`` field with matched context wrapped
        in ``[[`` / ``]]`` markers, and a ``bm25_score`` float.
        Falls back to unranked FTS if the ranked query fails.
        """
        cap = min(limit, 50)
        args: list = [query]
        work_clause = ""
        if work_id:
            work_clause = " AND d.work_id=?"
            args.append(work_id)

        # BM25 + snippet query (SQLite FTS5 auxiliary functions)
        q_ranked = f"""
            SELECT c.id, c.doc_id, c.page, c.text, c.created_at,
                   d.title as doc_title, d.kind as doc_kind, d.work_id,
                   bm25(chunks_fts) as bm25_score,
                   snippet(chunks_fts, 0, '[[', ']]', '…', 24) as snippet
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.chunk_id
            JOIN documents d ON d.id = c.doc_id
            WHERE chunks_fts MATCH ?{work_clause}
            ORDER BY bm25(chunks_fts)
            LIMIT {cap}"""

        # Plain FTS fallback (no BM25/snippet) for older SQLite builds
        q_plain = f"""
            SELECT c.id, c.doc_id, c.page, c.text, c.created_at,
                   d.title as doc_title, d.kind as doc_kind, d.work_id,
                   NULL as bm25_score, NULL as snippet
            FROM chunks_fts
            JOIN chunks c ON c.id = chunks_fts.chunk_id
            JOIN documents d ON d.id = c.doc_id
            WHERE chunks_fts MATCH ?{work_clause}
            LIMIT {cap}"""

        with self._lock:
            try:
                rows = self._conn.execute(q_ranked, args).fetchall()
            except Exception:
                try:
                    rows = self._conn.execute(q_plain, args).fetchall()
                except Exception:
                    rows = []
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Entity graph
    # -------------------------------------------------------------------------

    def upsert_entity(self, name: str, kind: str,
                      meta: dict | None = None) -> str:
        """Find or create an entity by normalised name + kind.

        Returns the entity ID. Thread-safe; deduplicates on (name, kind).
        """
        norm = name.strip()
        if not norm:
            raise ValueError("entity name cannot be empty")
        now = _now()
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM entities WHERE name=? AND kind=?",
                (norm, kind),
            ).fetchone()
            if existing:
                return existing["id"]
            eid = _uuid()
            self._conn.execute(
                """INSERT INTO entities(id, name, kind, canonical, aliases, meta, created_at)
                   VALUES(?,?,?,1,'{}',?,?)""",
                (eid, norm, kind, _jdump(meta or {}), now),
            )
            self._conn.commit()
        return eid

    def create_entity_mention(
        self,
        entity_id: str,
        doc_id: str,
        work_id: str | None = None,
        knowledge_id: str | None = None,
    ) -> None:
        """Record a MENTIONS edge from entity to document in the relationships table.

        Idempotent — silently skips if the pair already exists.
        Non-fatal on any DB error so it never breaks the pipeline.
        """
        try:
            with self._lock:
                existing = self._conn.execute(
                    """SELECT id FROM relationships
                       WHERE source_id=? AND target_id=? AND kind='MENTIONS'""",
                    (entity_id, doc_id),
                ).fetchone()
                if existing:
                    return
            # relationships.id is FK → objects, so we must create the object row first.
            rel_oid = self._create_object("relationship")
            meta_val: dict[str, Any] = {}
            if work_id:
                meta_val["work_id"] = work_id
            if knowledge_id:
                meta_val["knowledge_id"] = knowledge_id
            now = _now()
            with self._lock:
                self._conn.execute(
                    """INSERT OR IGNORE INTO relationships
                       (id, source_id, target_id, kind, weight, meta, created_at)
                       VALUES(?,?,?,'MENTIONS',1.0,?,?)""",
                    (rel_oid, entity_id, doc_id, _jdump(meta_val), now),
                )
                self._conn.commit()
        except Exception as exc:
            logger.debug("create_entity_mention failed: %s", exc)

    def create_entity_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str,
        weight: float = 1.0,
    ) -> None:
        """Create a typed edge between two entities (entity-to-entity).

        Idempotent — skips if the (source, target, relation) triple already exists.
        Non-fatal on any DB error.
        """
        try:
            with self._lock:
                existing = self._conn.execute(
                    "SELECT id FROM edges WHERE source_id=? AND target_id=? AND relation=?",
                    (source_id, target_id, relation),
                ).fetchone()
                if existing:
                    return
                eid = _uuid()
                now = _now()
                self._conn.execute(
                    """INSERT INTO edges(id, source_id, target_id, relation, weight, meta, created_at)
                       VALUES(?,?,?,?,?,'{}',?)""",
                    (eid, source_id, target_id, relation, weight, now),
                )
                self._conn.commit()
        except Exception as exc:
            logger.debug("create_entity_edge failed: %s", exc)

    def get_work_graph(self, work_id: str, limit: int = 100) -> dict:
        """Build a graph payload for a Work.

        Budget allocation:
        - Up to DOC_CAP document nodes (capped so entity nodes always have room).
        - Up to (limit - len(doc_nodes)) entity nodes.
        - Edges are filtered to only include nodes that made it into the output set.

        Falls back to a knowledge-item projection when no entities have been
        written yet (works imported before this feature was activated).
        """
        nodes: list[dict] = []
        edges: list[dict] = []
        seen: set[str] = set()

        # Reserve at most 20% of the budget for document nodes so entities dominate.
        DOC_CAP = max(10, limit // 5)

        with self._lock:
            doc_rows = self._conn.execute(
                "SELECT id, title, kind FROM documents WHERE work_id=? LIMIT ?",
                (work_id, DOC_CAP),
            ).fetchall()

        if not doc_rows:
            return {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0}

        doc_ids = [r["id"] for r in doc_rows]

        # Add document nodes (capped)
        for r in doc_rows:
            nid = r["id"]
            if nid not in seen:
                seen.add(nid)
                nodes.append({
                    "id": nid,
                    "label": r["title"] or "Untitled",
                    "type": "document",
                    "kind": r["kind"] or "file",
                })

        # Reserve slots for supplementary doc nodes (entities outside the initial
        # DOC_CAP window may mention docs not yet in `seen`; we need room to add them).
        SUPP_DOC_RESERVE = max(5, limit // 10)
        # Entity budget = remaining capacity minus the reserved supplement slots
        entity_limit = max(0, limit - len(nodes) - SUPP_DOC_RESERVE)

        # Use ALL doc ids from the work (not just the capped set) to find entities
        # so that highly-mentioned entities aren't missed due to the doc cap.
        with self._lock:
            all_doc_ids_rows = self._conn.execute(
                "SELECT id FROM documents WHERE work_id=?", (work_id,)
            ).fetchall()
        all_doc_ids = [r["id"] for r in all_doc_ids_rows]

        # Find entities mentioned across all the work's documents
        all_ph = ",".join("?" * len(all_doc_ids))
        with self._lock:
            entity_rows = self._conn.execute(
                f"""SELECT DISTINCT e.id, e.name, e.kind,
                           COUNT(r.id) AS mention_count
                    FROM entities e
                    JOIN relationships r ON r.source_id = e.id
                    WHERE r.target_id IN ({all_ph}) AND r.kind='MENTIONS'
                    GROUP BY e.id
                    ORDER BY mention_count DESC
                    LIMIT ?""",
                (*all_doc_ids, entity_limit),
            ).fetchall()

        entity_ids: list[str] = []
        for r in entity_rows:
            nid = r["id"]
            if nid not in seen:
                seen.add(nid)
                nodes.append({
                    "id": nid,
                    "label": r["name"],
                    "type": "entity",
                    "kind": r["kind"],
                })
            entity_ids.append(nid)

        if not entity_ids:
            # Fall back to knowledge-item projection
            with self._lock:
                kn_rows = self._conn.execute(
                    """SELECT id, kind, text, subject, predicate, object, confidence
                       FROM knowledge
                       WHERE work_id=? AND kind IN ('entity','relationship')
                       LIMIT ?""",
                    (work_id, limit * 2),
                ).fetchall()
            for row in kn_rows:
                r = dict(row)
                if r["kind"] == "entity" and r["text"]:
                    key = r["id"]
                    if key not in seen:
                        seen.add(key)
                        nodes.append({"id": key, "label": r["text"],
                                      "type": "entity", "kind": "concept"})
                elif r["kind"] == "relationship" and r["subject"] and r["object"]:
                    for label in (r["subject"], r["object"]):
                        nk = f"kn-{label.lower()[:32]}"
                        if nk not in seen:
                            seen.add(nk)
                            nodes.append({"id": nk, "label": label,
                                          "type": "entity", "kind": "concept"})
                    edges.append({
                        "source": f"kn-{r['subject'].lower()[:32]}",
                        "target": f"kn-{r['object'].lower()[:32]}",
                        "label": r["predicate"] or "relates to",
                        "type": "RELATES",
                    })
            return {"nodes": nodes[:limit], "edges": edges[:limit],
                    "node_count": len(nodes), "edge_count": len(edges)}

        # Supplement doc nodes: for each entity that mentions a doc not yet in
        # `seen`, add that doc so the entity always has at least one visible edge.
        # This handles the case where an entity's mention target lies outside the
        # initial DOC_CAP window.
        if entity_ids:
            ent_ph_supp = ",".join("?" * len(entity_ids))
            remaining_doc_budget = max(0, limit - len(nodes))
            if remaining_doc_budget > 0:
                with self._lock:
                    supp_rows = self._conn.execute(
                        f"""SELECT DISTINCT d.id, d.title, d.kind
                            FROM relationships r
                            JOIN documents d ON d.id = r.target_id
                            WHERE r.source_id IN ({ent_ph_supp}) AND r.kind='MENTIONS'
                            LIMIT ?""",
                        (*entity_ids, remaining_doc_budget + len(seen)),
                    ).fetchall()
                added = 0
                for r in supp_rows:
                    if added >= remaining_doc_budget:
                        break
                    nid = r["id"]
                    if nid not in seen:
                        seen.add(nid)
                        nodes.append({
                            "id": nid,
                            "label": r["title"] or "Untitled",
                            "type": "document",
                            "kind": r["kind"] or "file",
                        })
                        added += 1

        # MENTIONS edges: query against all the work's docs so entities found
        # via uncapped docs get connected to whichever doc nodes ARE now in `seen`.
        with self._lock:
            mention_rows = self._conn.execute(
                f"""SELECT source_id, target_id
                    FROM relationships
                    WHERE target_id IN ({all_ph}) AND kind='MENTIONS'""",
                (*all_doc_ids,),
            ).fetchall()
        for r in mention_rows:
            # Both endpoints must be in the returned node set
            if r["source_id"] in seen and r["target_id"] in seen:
                edges.append({
                    "source": r["source_id"],
                    "target": r["target_id"],
                    "label": "mentions",
                    "type": "MENTIONS",
                })

        # Entity-entity edges from the edges table
        if entity_ids:
            ent_ph = ",".join("?" * len(entity_ids))
            with self._lock:
                edge_rows = self._conn.execute(
                    f"""SELECT source_id, target_id, relation FROM edges
                        WHERE source_id IN ({ent_ph}) OR target_id IN ({ent_ph})
                        LIMIT ?""",
                    (*entity_ids, *entity_ids, limit),
                ).fetchall()
            for r in edge_rows:
                if r["source_id"] in seen and r["target_id"] in seen:
                    edges.append({
                        "source": r["source_id"],
                        "target": r["target_id"],
                        "label": r["relation"],
                        "type": r["relation"],
                    })

        return {
            "nodes": nodes[:limit],
            "edges": edges[:limit],
            "node_count": len(nodes),
            "edge_count": len(edges),
        }

    def list_entities(
        self,
        kind: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Return all entities with mention counts, most-mentioned first."""
        q = """SELECT e.*,
                      COUNT(r.id) AS mention_count
               FROM entities e
               LEFT JOIN relationships r ON r.source_id = e.id AND r.kind='MENTIONS'
               WHERE 1=1"""
        args: list = []
        if kind:
            q += " AND e.kind=?"
            args.append(kind)
        q += " GROUP BY e.id ORDER BY mention_count DESC LIMIT ?"
        args.append(min(limit, 1000))
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["meta"] = _jload(d.get("meta"), {})
            result.append(d)
        return result

    # -------------------------------------------------------------------------
    # Knowledge
    # -------------------------------------------------------------------------

    def list_knowledge(self, work_id: str | None = None, kind: str | None = None,
                       limit: int = 200) -> list[dict]:
        q = "SELECT * FROM knowledge WHERE 1=1"
        args: list = []
        if work_id:
            q += " AND work_id=?"
            args.append(work_id)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [self._k_dict(r) for r in rows]

    def search_knowledge(self, query: str, work_id: str | None = None,
                         limit: int = 20) -> list[dict]:
        q = """SELECT k.* FROM knowledge_fts f
               JOIN knowledge k ON k.id = f.knowledge_id
               WHERE knowledge_fts MATCH ?"""
        args: list = [query]
        if work_id:
            q += " AND k.work_id=?"
            args.append(work_id)
        q += f" LIMIT {min(limit, 50)}"
        with self._lock:
            try:
                rows = self._conn.execute(q, args).fetchall()
            except Exception:
                rows = []
        return [self._k_dict(r) for r in rows]

    @staticmethod
    def _k_dict(row: Any) -> dict:
        d = dict(row)
        d["meta"] = _jload(d.get("meta"), {})
        return d

    # -------------------------------------------------------------------------
    # Tasks
    # -------------------------------------------------------------------------

    def list_tasks(self, work_id: str | None = None, status: str | None = None,
                   limit: int = 100) -> list[dict]:
        q = "SELECT * FROM tasks WHERE 1=1"
        args: list = []
        if work_id:
            q += " AND work_id=?"
            args.append(work_id)
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY priority DESC, created_at ASC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def create_task(self, work_id: str, text: str, priority: int = 0) -> dict:
        oid = self._create_object("task")
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks(id,work_id,text,status,priority,meta,created_at) VALUES(?,?,?,'pending',?,?,?)",
                (oid, work_id, text, priority, "{}", now),
            )
            self._conn.commit()
        self.audit("task.created", object_id=oid, object_type="task",
                   detail=text[:120] if text else None)
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (oid,)).fetchone()
        return dict(row) if row else {}

    def update_task(self, task_id: str, status: str | None = None,
                    text: str | None = None, priority: int | None = None) -> dict | None:
        updates: dict[str, Any] = {}
        if status is not None:
            updates["status"] = status
            if status == "done":
                updates["completed_at"] = _now()
        if text is not None:
            updates["text"] = text
        if priority is not None:
            updates["priority"] = priority
        if not updates:
            with self._lock:
                row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            return dict(row) if row else None
        set_clause = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [task_id]
        _rowcount = 0
        with self._lock:
            cur = self._conn.execute(f"UPDATE tasks SET {set_clause} WHERE id=?", vals)
            _rowcount = cur.rowcount
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if _rowcount > 0:
            self.audit("task.updated", object_id=task_id, object_type="task",
                       detail=",".join(k for k in updates if k != "completed_at"))
        return dict(row) if row else None

    # -------------------------------------------------------------------------
    # Chunks (extracted text segments, FTS-indexed)
    # -------------------------------------------------------------------------

    def add_chunk(self, doc_id: str, text: str, page: int = 0) -> str:
        """Insert a text chunk and update the FTS index. Returns chunk id.

        chunks.id is a FK to objects(id), so we must register it there first.
        """
        cid = self._create_object("chunk")
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO chunks(id,doc_id,page,text,created_at) VALUES(?,?,?,?,?)",
                (cid, doc_id, page, text, now),
            )
            self._conn.execute(
                "INSERT INTO chunks_fts(chunk_id,doc_id,text) VALUES(?,?,?)",
                (cid, doc_id, text),
            )
            self._conn.commit()
        self.audit("document.chunk_added", object_id=doc_id, object_type="document",
                   detail=f"page={page}")
        return cid

    def delete_chunks(self, doc_id: str) -> None:
        """Remove all chunks for a document (e.g. before re-extracting)."""
        _count = 0
        with self._lock:
            _row = self._conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE doc_id=?", (doc_id,)
            ).fetchone()
            _count = _row[0] if _row else 0
            self._conn.execute("DELETE FROM chunks_fts WHERE doc_id=?", (doc_id,))
            self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            self._conn.commit()
        if _count > 0:
            self.audit("document.chunks_cleared", object_id=doc_id, object_type="document",
                       detail=f"{_count} chunks")

    def get_extraction_warnings(self, doc_id: str) -> list[dict]:
        """Return all extraction warnings for a document, ordered oldest-first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, doc_id, kind, detail, created_at"
                " FROM extraction_warnings WHERE doc_id=? ORDER BY created_at ASC",
                (doc_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_extraction_warnings(self, doc_id: str) -> None:
        """Remove all prior warnings for a document (call before re-queuing extraction)."""
        _count = 0
        with self._lock:
            _row = self._conn.execute(
                "SELECT COUNT(*) FROM extraction_warnings WHERE doc_id=?", (doc_id,)
            ).fetchone()
            _count = _row[0] if _row else 0
            self._conn.execute(
                "DELETE FROM extraction_warnings WHERE doc_id=?", (doc_id,)
            )
            self._conn.commit()
        if _count > 0:
            self.audit("document.warnings_cleared", object_id=doc_id, object_type="document",
                       detail=f"{_count} warnings")

    def add_extraction_warning(self, doc_id: str, kind: str,
                               detail: str | None = None) -> str:
        """Persist a single extraction warning. Returns the warning id."""
        wid = _uuid()
        now = _now()
        with self._lock:
            self._conn.execute(
                "INSERT INTO extraction_warnings(id, doc_id, kind, detail, created_at)"
                " VALUES(?,?,?,?,?)",
                (wid, doc_id, kind, detail, now),
            )
            self._conn.commit()
        self.audit("document.warning_added", object_id=doc_id, object_type="document",
                   detail=f"{kind}: {(detail or '')[:80]}")
        return wid

    def update_document_extracted(self, doc_id: str, extracted_text: str,
                                  word_count: int, readiness: str = "ready",
                                  error_message: str | None = None) -> None:
        """Persist extraction results back on the document row."""
        _rowcount = 0
        with self._lock:
            cur = self._conn.execute(
                "UPDATE documents SET extracted_text=?, word_count=?, readiness=?, error_message=? WHERE id=?",
                (extracted_text, word_count, readiness, error_message, doc_id),
            )
            _rowcount = cur.rowcount
            self._conn.commit()
        if _rowcount > 0:
            _op = "document.extraction_failed" if readiness in ("error", "no_text") else "document.extracted"
            self.audit(_op, object_id=doc_id, object_type="document",
                       result="error" if readiness in ("error", "no_text") else "ok",
                       detail=error_message or f"{word_count}w {readiness}")

    def upsert_book_chapters(self, doc_id: str, work_id: str | None,
                             chapters: list[dict]) -> int:
        """Replace all book_chapters rows for a document with new extractions.

        Each chapter dict must contain: seq, level, title, text.
        Old rows (and their objects entries) are deleted first so the
        operation is fully idempotent — safe to call on reprocess.
        Returns the count of chapters written.
        """
        now = _now()
        with self._lock:
            # Clean up old extraction
            existing = self._conn.execute(
                "SELECT id FROM book_chapters WHERE source_doc_id=?", (doc_id,)
            ).fetchall()
            for row in existing:
                self._conn.execute("DELETE FROM objects WHERE id=?", (row["id"],))
            self._conn.execute(
                "DELETE FROM book_chapters WHERE source_doc_id=?", (doc_id,)
            )

            for ch in chapters:
                cid = _uuid()
                self._conn.execute(
                    """INSERT INTO objects(id,type,version,lifecycle,provenance,
                       permissions,created_at,updated_at,created_by)
                       VALUES(?,?,1,'active','{}','{}',?,?,'system')""",
                    (cid, "chapter", now, now),
                )
                self._conn.execute(
                    """INSERT INTO book_chapters(id,pipeline_id,work_id,seq,level,title,
                       text,source_doc_id,citations,status,meta,created_at,updated_at,
                       citation_count,extraction_method)
                       VALUES(?,NULL,?,?,?,?,?,?,'[]','extracted','{}',?,?,0,'heading_parser')""",
                    (cid, work_id, ch["seq"], ch.get("level", 1), ch["title"],
                     ch.get("text", ""), doc_id, now, now),
                )
            self._conn.commit()
        n = len(chapters)
        self.audit("document.chapters_updated", object_id=doc_id, object_type="document",
                   detail=f"{n} chapters")
        return n

    def get_book_chapters(self, doc_id: str) -> list[dict]:
        """Return all chapter rows for a document, ordered by seq."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, seq, COALESCE(level, 1) as level, title, status,
                          extraction_method, created_at, citation_count, meta,
                          (length(text) - length(replace(coalesce(text,''), ' ', '')) + 1) as word_count
                   FROM book_chapters WHERE source_doc_id=? ORDER BY seq""",
                (doc_id,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["meta"] = _jload(d.get("meta"), {})
            result.append(d)
        return result

    # -------------------------------------------------------------------------
    # Knowledge items
    # -------------------------------------------------------------------------

    def create_knowledge_item(self, work_id: str | None, kind: str, text: str,
                              subject: str | None = None, predicate: str | None = None,
                              obj: str | None = None, confidence: float = 0.7,
                              source_doc_id: str | None = None,
                              source_chunk_id: str | None = None,
                              review_status: str = "auto",
                              meta: dict | None = None) -> str:
        """Insert a knowledge item and update FTS. Returns item id.

        review_status:
          'auto'     — rule-based, unreviewed
          'ai_auto'  — LLM-extracted, unreviewed
          'approved' — human confirmed
          'rejected' — human dismissed

        meta: optional dict for provenance and other attributes, e.g.
          {"source": "llm"} to durably mark LLM-extracted items so grouping
          survives after review_status changes to 'approved'/'rejected'.
        """
        kid = self._create_object("knowledge")
        now = _now()
        # Dedup by text_hash within same work
        text_hash = hashlib.sha256(f"{work_id}:{text}".encode()).hexdigest()
        meta_json = _jdump(meta or {})
        _inserted = False
        _existing_id: str | None = None
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM knowledge WHERE text_hash=? AND work_id IS ?",
                (text_hash, work_id),
            ).fetchone()
            if existing:
                _existing_id = existing["id"]
            else:
                self._conn.execute(
                    """INSERT INTO knowledge(id,work_id,kind,text,subject,predicate,object,
                       confidence,source_doc_id,source_chunk_id,review_status,meta,
                       created_at,text_hash)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (kid, work_id, kind, text, subject, predicate, obj, confidence,
                     source_doc_id, source_chunk_id, review_status, meta_json, now, text_hash),
                )
                self._conn.execute(
                    "INSERT INTO knowledge_fts(knowledge_id,work_id,text,subject,object) VALUES(?,?,?,?,?)",
                    (kid, work_id, text, subject or "", obj or ""),
                )
                self._conn.commit()
                _inserted = True
        if _existing_id:
            return _existing_id
        if _inserted:
            self.audit("knowledge.created", object_id=kid, object_type="knowledge",
                       detail=f"{kind}: {text[:80]}")
        return kid

    def update_knowledge_review_status(self, item_id: str, status: str) -> bool:
        """Set review_status on a knowledge item. Returns True if found."""
        valid = {"auto", "ai_auto", "approved", "rejected"}
        if status not in valid:
            raise ValueError(f"review_status must be one of {valid}")
        _before_status: str | None = None
        with self._lock:
            _row = self._conn.execute(
                "SELECT review_status FROM knowledge WHERE id=?", (item_id,)
            ).fetchone()
            _before_status = _row["review_status"] if _row else None
            cur = self._conn.execute(
                "UPDATE knowledge SET review_status=? WHERE id=?",
                (status, item_id),
            )
            self._conn.commit()
        if cur.rowcount > 0:
            _bh = hashlib.sha256(json.dumps({"review_status": _before_status}).encode()).hexdigest() if _before_status else None
            _ah = hashlib.sha256(json.dumps({"review_status": status}).encode()).hexdigest()
            self.audit("knowledge.review_updated", object_id=item_id, object_type="knowledge",
                       before_hash=_bh, after_hash=_ah,
                       detail=f"{_before_status}→{status}")
        return cur.rowcount > 0

    def update_knowledge_confidence(self, item_id: str, confidence: float,
                                    evidence: dict | None = None) -> bool:
        """Set confidence (and optional meta.evidence components) on a knowledge item."""
        confidence = max(0.0, min(1.0, float(confidence)))
        with self._lock:
            row = self._conn.execute(
                "SELECT meta FROM knowledge WHERE id=?", (item_id,)).fetchone()
            if not row:
                return False
            if evidence is not None:
                try:
                    meta = json.loads(row["meta"] or "{}")
                except Exception:
                    meta = {}
                meta["evidence"] = evidence
                self._conn.execute(
                    "UPDATE knowledge SET confidence=?, meta=? WHERE id=?",
                    (confidence, json.dumps(meta), item_id))
            else:
                self._conn.execute(
                    "UPDATE knowledge SET confidence=? WHERE id=?",
                    (confidence, item_id))
            self._conn.commit()
        return True

    # -------------------------------------------------------------------------
    # Conflicts (contradiction detection)
    # -------------------------------------------------------------------------

    def create_conflict(self, claim_a_id: str, claim_b_id: str,
                        conflict_type: str) -> str | None:
        """Record a conflict between two knowledge items.

        Returns the new conflict id, or None when this pair (either order)
        is already recorded.
        """
        with self._lock:
            existing = self._conn.execute(
                """SELECT id FROM conflicts
                   WHERE (claim_a_id=? AND claim_b_id=?)
                      OR (claim_a_id=? AND claim_b_id=?)""",
                (claim_a_id, claim_b_id, claim_b_id, claim_a_id),
            ).fetchone()
            if existing:
                return None
            cid = str(uuid.uuid4())
            self._conn.execute(
                """INSERT INTO conflicts(id, claim_a_id, claim_b_id,
                   conflict_type, resolution, created_at)
                   VALUES(?,?,?,?,NULL,?)""",
                (cid, claim_a_id, claim_b_id, conflict_type, _now()),
            )
            self._conn.commit()
        self.audit("conflict.detected", object_id=cid, object_type="conflict",
                   actor="system", detail=f"{conflict_type}: {claim_a_id[:8]} vs {claim_b_id[:8]}")
        return cid

    def create_conflicts_batch(self, pairs: list[tuple[str, str, str]]) -> int:
        """Batch-insert conflicts [(claim_a_id, claim_b_id, conflict_type), ...].

        Skips pairs already recorded (either order). Single commit + single
        audit entry for the whole batch. Returns number inserted.
        """
        if not pairs:
            return 0
        inserted = 0
        with self._lock:
            for a_id, b_id, ctype in pairs:
                exists = self._conn.execute(
                    """SELECT 1 FROM conflicts
                       WHERE (claim_a_id=? AND claim_b_id=?)
                          OR (claim_a_id=? AND claim_b_id=?)""",
                    (a_id, b_id, b_id, a_id)).fetchone()
                if exists:
                    continue
                self._conn.execute(
                    """INSERT INTO conflicts(id, claim_a_id, claim_b_id,
                       conflict_type, resolution, created_at)
                       VALUES(?,?,?,?,NULL,?)""",
                    (str(uuid.uuid4()), a_id, b_id, ctype, _now()))
                inserted += 1
            self._conn.commit()
        if inserted:
            self.audit("conflict.detected", object_id="batch", object_type="conflict",
                       actor="system", detail=f"batch: {inserted} new conflict(s)")
        return inserted

    def list_conflicts(self, resolved: bool = False, limit: int = 100) -> list[dict]:
        """Return conflicts with both claims joined for display."""
        cond = "c.resolution IS NOT NULL" if resolved else "c.resolution IS NULL"
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT c.id, c.conflict_type, c.resolution, c.created_at,
                           ka.id AS a_id, ka.text AS a_text, ka.subject AS a_subject,
                           ka.confidence AS a_confidence, ka.review_status AS a_status,
                           kb.id AS b_id, kb.text AS b_text, kb.subject AS b_subject,
                           kb.confidence AS b_confidence, kb.review_status AS b_status,
                           w.id AS work_id, w.title AS work_title
                    FROM conflicts c
                    JOIN knowledge ka ON ka.id = c.claim_a_id
                    JOIN knowledge kb ON kb.id = c.claim_b_id
                    LEFT JOIN works w ON w.id = ka.work_id
                    WHERE {cond}
                    ORDER BY c.created_at DESC LIMIT ?""",
                (min(limit, 500),),
            ).fetchall()
        return [dict(r) for r in rows]

    def resolve_conflict(self, conflict_id: str, resolution: str,
                         keep_id: str | None = None) -> bool:
        """Resolve a conflict: 'keep_a' | 'keep_b' | 'keep_both'.

        The losing claim (if any) is marked review_status='rejected'.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT claim_a_id, claim_b_id FROM conflicts WHERE id=? AND resolution IS NULL",
                (conflict_id,)).fetchone()
            if not row:
                return False
            self._conn.execute(
                "UPDATE conflicts SET resolution=? WHERE id=?",
                (resolution, conflict_id))
            self._conn.commit()
        loser = None
        if resolution == "keep_a":
            loser = row["claim_b_id"]
        elif resolution == "keep_b":
            loser = row["claim_a_id"]
        if loser:
            self.update_knowledge_review_status(loser, "rejected")
        self.audit("conflict.resolved", object_id=conflict_id, object_type="conflict",
                   actor="user", detail=resolution)
        return True

    # -------------------------------------------------------------------------
    # Vectors (semantic embeddings)
    # -------------------------------------------------------------------------

    def store_vector(self, object_id: str, object_type: str,
                     embedding: bytes, dim: int) -> None:
        """Insert or replace the embedding for an object."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM vectors WHERE object_id=? AND object_type=?",
                (object_id, object_type))
            self._conn.execute(
                """INSERT INTO vectors(id, object_id, object_type, embedding, dim, created_at)
                   VALUES(?,?,?,?,?,?)""",
                (str(uuid.uuid4()), object_id, object_type, embedding, dim, _now()))
            self._conn.commit()

    def count_vectors(self, object_type: str | None = None) -> int:
        with self._lock:
            if object_type:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM vectors WHERE object_type=?",
                    (object_type,)).fetchone()
            else:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM vectors").fetchone()
        return row["n"] if row else 0

    # -------------------------------------------------------------------------
    # Document versions (#146)
    # -------------------------------------------------------------------------

    def create_document_version(
        self, doc_id: str, sha256: str | None = None,
        word_count: int = 0, notes: str | None = None,
        is_canonical: bool = False, created_by: str = "user",
    ) -> dict:
        """Snapshot the current state of a document as a new version row."""
        vid = _uuid()
        now = _now()
        # Find next version_num
        with self._lock:
            last = self._conn.execute(
                "SELECT MAX(version_num) FROM doc_versions WHERE doc_id=?", (doc_id,)
            ).fetchone()[0]
            version_num = (last or 0) + 1
            if is_canonical:
                # Unset previous canonical
                self._conn.execute(
                    "UPDATE doc_versions SET is_canonical=0 WHERE doc_id=?", (doc_id,)
                )
            self._conn.execute(
                """INSERT INTO doc_versions(id, doc_id, version_num, sha256,
                   word_count, notes, is_canonical, created_at, created_by)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (vid, doc_id, version_num, sha256, word_count,
                 notes, 1 if is_canonical else 0, now, created_by),
            )
            self._conn.commit()
        _canonical_flag = " canonical" if is_canonical else ""
        self.audit("document.version_created", object_id=doc_id, object_type="document",
                   after_hash=sha256,
                   detail=f"v{version_num}{_canonical_flag}")
        return {"id": vid, "doc_id": doc_id, "version_num": version_num,
                "sha256": sha256, "word_count": word_count, "notes": notes,
                "is_canonical": is_canonical, "created_at": now}

    def list_document_versions(self, doc_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM doc_versions WHERE doc_id=? ORDER BY version_num DESC",
                (doc_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def set_canonical_version(self, doc_id: str, version_id: str) -> bool:
        with self._lock:
            self._conn.execute("UPDATE doc_versions SET is_canonical=0 WHERE doc_id=?", (doc_id,))
            cur = self._conn.execute(
                "UPDATE doc_versions SET is_canonical=1 WHERE id=? AND doc_id=?",
                (version_id, doc_id),
            )
            self._conn.commit()
        if cur.rowcount > 0:
            self.audit("document.version_canonical", object_id=doc_id, object_type="document",
                       detail=version_id[:36])
        return cur.rowcount > 0

    # -------------------------------------------------------------------------
    # Dashboard / aggregations
    # -------------------------------------------------------------------------

    def dashboard_summary(self) -> dict:
        with self._lock:
            work_count = self._conn.execute(
                "SELECT COUNT(*) FROM works w JOIN objects o ON o.id=w.id WHERE o.lifecycle='active'"
            ).fetchone()[0]
            doc_count = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            doc_ready = self._conn.execute(
                "SELECT COUNT(*) FROM documents WHERE readiness='ready'"
            ).fetchone()[0]
            knowledge_count = self._conn.execute("SELECT COUNT(*) FROM knowledge WHERE review_status != 'rejected'").fetchone()[0]
            conv_count = self._conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE archived=0"
            ).fetchone()[0]
            task_count = self._conn.execute(
                "SELECT COUNT(*) FROM tasks WHERE status='pending'"
            ).fetchone()[0]
            recent_works = self._conn.execute(
                """SELECT w.id, w.title, w.work_type, w.status, w.description, o.updated_at,
                          (SELECT COUNT(*) FROM documents d WHERE d.work_id=w.id) AS document_count,
                          (SELECT COUNT(*) FROM knowledge k WHERE k.work_id=w.id) AS knowledge_count,
                          (SELECT COUNT(*) FROM tasks t WHERE t.work_id=w.id AND t.status='pending') AS pending_tasks
                   FROM works w JOIN objects o ON o.id=w.id
                   WHERE o.lifecycle='active' ORDER BY o.updated_at DESC LIMIT 5"""
            ).fetchall()
            recent_docs = self._conn.execute(
                """SELECT id, COALESCE(title, source) as title, kind, readiness, created_at
                   FROM documents ORDER BY created_at DESC LIMIT 5"""
            ).fetchall()
            recent_convs = self._conn.execute(
                """SELECT id, title, model, updated_at,
                          (SELECT text FROM messages WHERE conversation_id=conversations.id
                           ORDER BY created_at DESC LIMIT 1) as last_message
                   FROM conversations WHERE archived=0
                   ORDER BY updated_at DESC LIMIT 5"""
            ).fetchall()
        return {
            "work_count": work_count,
            "document_count": doc_count,
            "documents_ready": doc_ready,
            "knowledge_count": knowledge_count,
            "conversation_count": conv_count,
            "pending_task_count": task_count,
            "recent_works": [dict(r) for r in recent_works],
            "recent_documents": [dict(r) for r in recent_docs],
            "recent_conversations": [dict(r) for r in recent_convs],
        }

    def recent_activity(self, limit: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT 'document' as kind, id,
                          COALESCE(title, source) as label,
                          created_at FROM documents
                   UNION ALL
                   SELECT 'work', w.id, w.title, o.created_at
                     FROM works w JOIN objects o ON o.id=w.id
                   UNION ALL
                   SELECT 'knowledge', k.id,
                          '[' || UPPER(k.kind) || '] ' || SUBSTR(k.text, 1, 80) as label,
                          k.created_at FROM knowledge k
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Gap cache (v50)
    # -------------------------------------------------------------------------

    def cache_work_gaps(self, work_id: str, gaps: list[dict],
                        coverage_pct: float | None = None) -> None:
        """Persist the most-recent gap detection result for *work_id*.

        Subsequent calls overwrite the previous row so each Work has at most
        one cache entry.  The caller is responsible for serialising ``gaps``
        via ``json.dumps`` / passing a list of dicts.
        """
        import json as _json
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO work_gap_cache (work_id, gaps_json, coverage_pct, evaluated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(work_id) DO UPDATE SET
                     gaps_json    = excluded.gaps_json,
                     coverage_pct = excluded.coverage_pct,
                     evaluated_at = excluded.evaluated_at""",
                (work_id, _json.dumps(gaps), coverage_pct, now),
            )
            self._conn.commit()

    def get_cached_gaps(self, work_id: str,
                        max_age_seconds: int = 3600) -> dict | None:
        """Return the cached gap result for *work_id* if it is not stale.

        Returns ``None`` when no cache entry exists or the entry is older than
        *max_age_seconds* (default 1 h).
        """
        import json as _json
        with self._lock:
            row = self._conn.execute(
                "SELECT gaps_json, coverage_pct, evaluated_at "
                "FROM work_gap_cache WHERE work_id=?",
                (work_id,),
            ).fetchone()
        if not row:
            return None
        import datetime
        evaluated = row["evaluated_at"]
        try:
            ts = datetime.datetime.fromisoformat(evaluated)
            age = (datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - ts.replace(tzinfo=None)).total_seconds()
            if age > max_age_seconds:
                return None
        except Exception:
            return None
        return {
            "gaps": _json.loads(row["gaps_json"] or "[]"),
            "coverage_pct": row["coverage_pct"],
            "evaluated_at": evaluated,
        }

    def get_all_cached_gaps(self, max_age_seconds: int = 3600) -> list[dict]:
        """Return all non-stale cached gap rows as a flat list.

        Each entry includes ``work_id``, ``gaps`` (list), ``coverage_pct``,
        and ``evaluated_at``.  Stale rows are silently excluded.
        """
        import json as _json, datetime
        cutoff = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=max_age_seconds)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with self._lock:
            rows = self._conn.execute(
                "SELECT work_id, gaps_json, coverage_pct, evaluated_at "
                "FROM work_gap_cache WHERE evaluated_at >= ?",
                (cutoff,),
            ).fetchall()
        result = []
        for row in rows:
            result.append({
                "work_id":      row["work_id"],
                "gaps":         _json.loads(row["gaps_json"] or "[]"),
                "coverage_pct": row["coverage_pct"],
                "evaluated_at": row["evaluated_at"],
            })
        return result

    # -------------------------------------------------------------------------
    # Health / diagnostics
    # -------------------------------------------------------------------------

    def health(self) -> dict:
        try:
            with self._lock:
                version = self._get_setting("schema_version", "0")
                count = self._conn.execute("SELECT COUNT(*) FROM objects").fetchone()[0]
            return {"status": "ok", "schema_version": int(version), "object_count": count,
                    "path": self._path}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
