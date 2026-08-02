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

    def list_near_duplicates(self, resolved: bool = False) -> list[dict]:
        """Return doc_dupes rows with document titles joined in."""
        q = """SELECT dd.*, da.title as doc_a_title, db2.title as doc_b_title
               FROM doc_dupes dd
               JOIN documents da  ON da.id  = dd.doc_a_id
               JOIN documents db2 ON db2.id = dd.doc_b_id
               ORDER BY dd.similarity DESC"""
        with self._lock:
            rows = self._conn.execute(q).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Object creation helper
    # -------------------------------------------------------------------------

    def _create_object(self, obj_type: str, extra: dict | None = None) -> str:
        oid = _uuid()
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO objects(id, type, version, lifecycle, provenance, permissions,
                   created_at, updated_at, created_by)
                   VALUES(?,?,1,'active','{}','{}',?,?,'user')""",
                (oid, obj_type, now, now),
            )
        return oid

    # -------------------------------------------------------------------------
    # Works
    # -------------------------------------------------------------------------

    def list_works(self, status: str | None = None, work_type: str | None = None,
                   limit: int = 200) -> list[dict]:
        q = """SELECT w.*, o.created_at as obj_created, o.lifecycle,
                      (SELECT COUNT(*) FROM documents d WHERE d.work_id=w.id) as doc_count,
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

    def list_documents(self, work_id: str | None = None, kind: str | None = None,
                       readiness: str | None = None, limit: int = 200) -> list[dict]:
        q = "SELECT * FROM documents WHERE 1=1"
        args: list = []
        if work_id:
            q += " AND work_id=?"
            args.append(work_id)
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if readiness:
            q += " AND readiness=?"
            args.append(readiness)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [self._doc_dict(r) for r in rows]

    def get_document(self, doc_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        return self._doc_dict(row) if row else None

    def create_document(self, title: str, source: str | None = None, sha256: str | None = None,
                        kind: str | None = None, work_id: str | None = None,
                        content_path: str | None = None, meta: dict | None = None) -> dict:
        oid = self._create_object("document")
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
        q = """SELECT c.*, d.title as doc_title, d.kind as doc_kind, d.work_id
               FROM chunks_fts f
               JOIN chunks c ON c.id = f.chunk_id
               JOIN documents d ON d.id = f.doc_id
               WHERE chunks_fts MATCH ?"""
        args: list = [query]
        if work_id:
            q += " AND d.work_id=?"
            args.append(work_id)
        q += f" LIMIT {min(limit, 50)}"
        with self._lock:
            try:
                rows = self._conn.execute(q, args).fetchall()
            except Exception:
                rows = []
        return [dict(r) for r in rows]

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
