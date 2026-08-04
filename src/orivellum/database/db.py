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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from .schema import MIGRATIONS


# ---------------------------------------------------------------------------
# Governed-core exceptions (Sovereign Platform M0.1)
# ---------------------------------------------------------------------------

class _CASConflict(Exception):
    """Internal sentinel: compare-and-set check failed inside governed_write.

    Raised inside a governed_write block so the transaction rolls back
    before the caller can return "conflict".  Never exposed to callers.
    """


class VersionConflictError(Exception):
    """Raised when an optimistic-concurrency update is attempted with a stale
    expected_version.  The caller should re-fetch the object and retry.

    Attributes:
        object_id: the primary key of the row that conflicted.
        expected: the version the caller believed was current.
        actual: the version actually stored in the DB (may be None if the
                row no longer exists).
    """

    def __init__(self, object_id: str, expected: int | None,
                 actual: int | None) -> None:
        super().__init__(
            f"Version conflict on {object_id!r}: "
            f"expected {expected}, got {actual}"
        )
        self.object_id = object_id
        self.expected = expected
        self.actual = actual

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
        # Never include the value for secret keys in the audit detail
        safe_detail = None if key in self._AUDIT_SECRET_KEYS else f"{key}={value[:40]}"
        with self.governed_write(
            operation="setting.updated",
            event_type="setting.updated",
            object_id=key,
            object_type="setting",
            actor=actor,
            detail=safe_detail,
        ):
            self._set_setting(key, value)

    def get_active_prompt(self, slot: str) -> str | None:
        """Return the active prompt content for a slot, or None.

        Never raises — returns None if the prompts table is missing/empty or
        anything goes wrong, so callers can safely fall back to a hardcoded
        default (e.g. the chat base persona).
        """
        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT content FROM prompts WHERE slot=? AND active=1 LIMIT 1",
                    (slot,),
                ).fetchone()
            return row["content"] if row and row["content"] else None
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Audit log
    # -------------------------------------------------------------------------

    # =========================================================================
    # Governed-core — M0.1 (Sovereign Platform)
    # =========================================================================

    def _audit_tx(
        self,
        operation: str,
        object_id: str | None = None,
        object_type: str | None = None,
        actor: str = "system",
        result: str = "ok",
        detail: str | None = None,
        before_hash: str | None = None,
        after_hash: str | None = None,
    ) -> str:
        """Insert one hash-chained audit row WITHOUT committing.

        Must be called with ``self._lock`` already held and inside an open
        transaction (i.e. inside a ``governed_write`` block or an explicit
        ``with self._lock:`` context).  Returns the new row's ``row_hash``
        so callers can thread it through if needed.

        Chain formula::
            row_hash = sha256(prev_hash | operation | object_id | detail | timestamp | id)

        Rows written before schema v55 have NULL ``row_hash`` / ``prev_hash``
        and are skipped by :meth:`verify_audit_chain`.
        """
        entry_id = _uuid()
        now = _now()
        prev_row = self._conn.execute(
            "SELECT row_hash FROM audit_log "
            "WHERE row_hash IS NOT NULL "
            "ORDER BY timestamp DESC, id DESC LIMIT 1"
        ).fetchone()
        prev_hash = prev_row["row_hash"] if prev_row else "0" * 64
        chain_data = "|".join([
            prev_hash, operation, object_id or "", detail or "", now, entry_id
        ])
        row_hash = hashlib.sha256(chain_data.encode()).hexdigest()
        self._conn.execute(
            """INSERT INTO audit_log(id, timestamp, actor, operation,
               object_id, object_type, before_hash, after_hash,
               result, detail, app_version, prev_hash, row_hash)
               VALUES(?,?,?,?,?,?,?,?,?,?,'0.1.0',?,?)""",
            (entry_id, now, actor, operation, object_id, object_type,
             before_hash, after_hash, result, detail, prev_hash, row_hash),
        )
        return row_hash

    def _emit_outbox_tx(
        self,
        event_type: str,
        object_id: str | None = None,
        object_type: str | None = None,
        payload: dict | None = None,
    ) -> None:
        """Insert one outbox event WITHOUT committing.

        Must be called with ``self._lock`` held inside an open transaction.
        """
        self._conn.execute(
            """INSERT INTO outbox(id, event_type, object_id, object_type,
               payload, created_at)
               VALUES(?,?,?,?,?,?)""",
            (_uuid(), event_type, object_id, object_type,
             json.dumps(payload or {}), _now()),
        )

    @contextmanager
    def governed_write(
        self,
        *,
        operation: str,
        event_type: str,
        object_id: str | None = None,
        object_type: str | None = None,
        payload: dict | None = None,
        actor: str = "system",
        detail: str | None = None,
    ) -> Generator[None, None, None]:
        """Context manager for atomic domain-change + audit + outbox writes.

        Acquires the DB lock, yields so the caller can execute domain SQL
        (without committing), then on successful exit inserts one audit row
        and one outbox event and commits everything in a single transaction.
        On any exception the transaction is rolled back and the exception is
        re-raised unchanged.

        **The caller must NOT call** ``self._conn.commit()`` inside the
        ``with`` block — ``governed_write`` is the only committer.

        Example::
            with db.governed_write(
                operation="work.updated",
                event_type="work.updated",
                object_id=wid,
                object_type="work",
                payload={"fields": ["title"]},
                detail="title",
            ):
                db._conn.execute("UPDATE works SET title=? WHERE id=?", (t, wid))
                db._conn.execute("UPDATE objects SET version=version+1 WHERE id=?", (wid,))
                # no commit here — governed_write commits for you

        Raises:
            VersionConflictError: if the caller raises it inside the block
                (the transaction is rolled back before re-raising).
            Any other exception from domain SQL is also rolled back and
                re-raised.
        """
        with self._lock:
            try:
                yield
                self._audit_tx(operation, object_id, object_type,
                               actor=actor, detail=detail)
                self._emit_outbox_tx(event_type, object_id, object_type,
                                     payload or {})
                self._conn.commit()
            except Exception:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                raise

    def verify_audit_chain(self) -> tuple[bool, str]:
        """Walk every hash-chained audit row and verify the chain is intact.

        Rows written before schema v55 (``row_hash IS NULL``) are skipped so
        that pre-existing data does not cause false failures.

        Returns:
            ``(True, "")`` if the chain is intact.
            ``(False, reason)`` if any link is broken or a hash does not match.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, timestamp, operation, object_id, detail,
                          prev_hash, row_hash
                   FROM audit_log
                   WHERE row_hash IS NOT NULL
                   ORDER BY timestamp ASC, id ASC"""
            ).fetchall()
        expected_prev = "0" * 64
        for r in rows:
            # Verify stored prev_hash chains to the last seen row_hash.
            stored_prev = r["prev_hash"] or ("0" * 64)
            if stored_prev != expected_prev:
                return (
                    False,
                    f"Chain break at audit row {r['id']!r}: "
                    f"stored prev_hash={stored_prev[:12]}… "
                    f"expected={expected_prev[:12]}…",
                )
            # Verify row_hash was computed correctly.
            chain_data = "|".join([
                stored_prev, r["operation"], r["object_id"] or "",
                r["detail"] or "", r["timestamp"], r["id"],
            ])
            expected_hash = hashlib.sha256(chain_data.encode()).hexdigest()
            if r["row_hash"] != expected_hash:
                return (
                    False,
                    f"Hash mismatch at audit row {r['id']!r}: "
                    f"stored={r['row_hash'][:12]}… "
                    f"computed={expected_hash[:12]}…",
                )
            expected_prev = r["row_hash"]
        return True, ""

    def list_outbox(
        self,
        pending_only: bool = True,
        limit: int = 200,
    ) -> list[dict]:
        """Return outbox events, newest first.

        Args:
            pending_only: when True (default) only return undispatched events
                (``dispatched_at IS NULL``).
        """
        q = "SELECT * FROM outbox"
        if pending_only:
            q += " WHERE dispatched_at IS NULL"
        q += " ORDER BY created_at DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(q, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def dispatch_outbox_event(self, event_id: str) -> bool:
        """Mark one outbox event as dispatched (idempotent).

        Returns True if the row existed and was updated, False otherwise.
        """
        now = _now()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE outbox SET dispatched_at=? "
                "WHERE id=? AND dispatched_at IS NULL",
                (now, event_id),
            )
            self._conn.commit()
        return cur.rowcount > 0

    # =========================================================================
    # End governed-core
    # =========================================================================

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
        """Append a single hash-chained audit-log entry and commit.

        This is the standalone (auto-committing) variant.  Code that already
        holds the lock and is building an atomic transaction should call
        :meth:`_audit_tx` directly instead.

        Never raises — audit failures are logged as warnings so they cannot
        break the calling operation.
        """
        try:
            with self._lock:
                self._audit_tx(
                    operation, object_id, object_type,
                    actor=actor, result=result, detail=detail,
                    before_hash=before_hash, after_hash=after_hash,
                )
                self._conn.commit()
        except Exception as exc:
            logger.warning("audit write failed: %s", exc)

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

        Claim-first: the pair is claimed with a conditional UPDATE (resolved=0),
        and side effects run only for the successful claimant.  Returns the
        doc_dupes row dict on success, None if not found, or a dict with
        already_resolved=True (no side effects applied) if the pair was
        resolved earlier or concurrently.
        """
        _VALID = {"keep_both", "mark_versions", "mark_superseded"}
        if action not in _VALID:
            raise ValueError(f"action must be one of {sorted(_VALID)}")

        # Pre-read (outside the write transaction)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM doc_dupes WHERE id=?", (dupe_id,)
            ).fetchone()
        if not row:
            return None
        dupe = dict(row)

        # Atomic claim: conditional UPDATE + audit + outbox in one transaction.
        # _CASConflict is raised (and the tx rolled back) when the pair was
        # already resolved by a concurrent caller.
        try:
            with self.governed_write(
                operation="document.dupe_resolved",
                event_type="document.dupe_resolved",
                object_id=dupe_id,
                object_type="doc_dupe",
                actor="user",
                detail=f"action={action}",
            ):
                cur = self._conn.execute(
                    "UPDATE doc_dupes SET resolved=1, resolution=? WHERE id=? AND resolved=0",
                    (action, dupe_id),
                )
                if cur.rowcount == 0:
                    raise _CASConflict("already resolved")
        except _CASConflict:
            dupe["already_resolved"] = True
            return dupe

        now = _now()

        if action == "mark_versions":
            # Create DERIVED_FROM relationship: doc_b is derived from doc_a.
            # relationships.id is a FK to objects, so we must create the object row first.
            try:
                rel_oid = self._create_object("relationship")
                with self.governed_write(
                    operation="document.version_linked",
                    event_type="document.version_linked",
                    object_id=dupe["doc_a_id"],
                    object_type="document",
                    actor="user",
                    detail=f"DERIVED_FROM {dupe['doc_b_id'][:8]}",
                ):
                    self._conn.execute(
                        """INSERT OR IGNORE INTO relationships
                           (id, source_id, target_id, kind, weight, meta, created_at)
                           VALUES(?,?,?,'DERIVED_FROM',1.0,'{}',?)""",
                        (rel_oid, dupe["doc_b_id"], dupe["doc_a_id"], now),
                    )
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
        with self.governed_write(
            operation="work.created",
            event_type="work.created",
            object_id=oid,
            object_type="work",
            payload={"work_type": work_type},
            actor="user",
            detail=title[:120] if title else None,
        ):
            self._conn.execute(
                "INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,created_at,updated_at,created_by) VALUES(?,?,1,'active','{}','{}',?,?,'user')",
                (oid, "work", now, now),
            )
            self._conn.execute(
                "INSERT INTO works(id,title,work_type,description,status,meta) VALUES(?,?,?,?,?,?)",
                (oid, title, work_type, description, "active", _jdump(meta or {})),
            )
        return self.get_work(oid)  # type: ignore[return-value]

    def update_work(self, work_id: str,
                    expected_version: int | None = None,
                    **kwargs: Any) -> dict | None:
        """Update mutable fields on a work.

        Args:
            work_id: the work to update.
            expected_version: when supplied, the current ``objects.version``
                must equal this value or :exc:`VersionConflictError` is raised
                and nothing is written.  The version is incremented on every
                successful update.
            **kwargs: field / value pairs to update (allowed: title,
                description, status, meta).

        Returns:
            The refreshed work dict, or None if the work does not exist.
        """
        now = _now()
        allowed = {"title", "description", "status", "work_type", "meta"}
        updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
        if not updates:
            return self.get_work(work_id)
        if "meta" in updates:
            updates["meta"] = _jdump(updates["meta"])
        set_clause = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [work_id]
        with self.governed_write(
            operation="work.updated",
            event_type="work.updated",
            object_id=work_id,
            object_type="work",
            payload={"fields": list(updates.keys())},
            detail=",".join(updates.keys()),
        ):
            if expected_version is not None:
                row = self._conn.execute(
                    "SELECT version FROM objects WHERE id=?", (work_id,)
                ).fetchone()
                actual = row["version"] if row else None
                if actual != expected_version:
                    raise VersionConflictError(work_id, expected_version, actual)
            self._conn.execute(f"UPDATE works SET {set_clause} WHERE id=?", vals)
            self._conn.execute(
                "UPDATE objects SET updated_at=?, version=version+1 WHERE id=?",
                (now, work_id),
            )
        return self.get_work(work_id)

    def delete_work(self, work_id: str) -> bool:
        now = _now()
        _deleted = False
        with self.governed_write(
            operation="work.deleted",
            event_type="work.deleted",
            object_id=work_id,
            object_type="work",
            actor="user",
        ):
            cur = self._conn.execute(
                "UPDATE objects SET lifecycle='deleted', updated_at=? WHERE id=? AND lifecycle!='deleted'",
                (now, work_id),
            )
            _deleted = cur.rowcount > 0
        return _deleted

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
                      (SELECT COUNT(*) FROM messages WHERE conversation_id=c.id) as message_count,
                      (SELECT w.title FROM works w WHERE w.id=c.work_id LIMIT 1) as work_title
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
        with self.governed_write(
            operation="conversation.created",
            event_type="conversation.created",
            object_id=cid,
            object_type="conversation",
            payload={"work_id": work_id, "model": model},
            actor="user",
            detail=title[:120] if title else work_id,
        ):
            self._conn.execute(
                "INSERT INTO conversations(id,work_id,title,archived,model,created_at,updated_at) VALUES(?,?,?,0,?,?,?)",
                (cid, work_id, title, model, now, now),
            )
        return self.get_conversation(cid)  # type: ignore[return-value]

    def add_message(self, conv_id: str, role: str, text: str,
                    meta: dict | None = None,
                    state: str = "done") -> dict:
        """Insert a message and bump the conversation's updated_at.

        Args:
            conv_id: conversation to append to.
            role:    "user" or "assistant".
            text:    message body (may be empty for a pre-created streaming stub).
            meta:    arbitrary JSON metadata.
            state:   initial MessageState — defaults to "done" (use "queued"
                     when pre-creating an assistant stub for a streaming reply).
        """
        mid = _uuid()
        now = _now()
        _wc = len(text.split()) if text else 0
        with self.governed_write(
            operation="message.created",
            event_type="message.created",
            object_id=mid,
            object_type="message",
            payload={"conversation_id": conv_id, "role": role,
                     "word_count": _wc, "state": state},
            actor="user" if role == "user" else "system",
            detail=f"{role} {state} {_wc}w",
        ):
            self._conn.execute(
                """INSERT INTO messages(id,conversation_id,role,text,meta,created_at,state)
                   VALUES(?,?,?,?,?,?,?)""",
                (mid, conv_id, role, text, _jdump(meta or {}), now, state),
            )
            self._conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?", (now, conv_id)
            )
        return {"id": mid, "conversation_id": conv_id, "role": role, "text": text,
                "state": state, "meta": meta or {}, "created_at": now}

    def transition_message(self, msg_id: str, to_state: str) -> None:
        """Apply a MESSAGE_SM state transition to an existing message.

        Reads the current state, validates via MESSAGE_SM, then atomically
        records the state change (governed_write: audit + outbox).

        Raises:
            InvalidTransitionError: if the transition is not in MESSAGE_SM.
            BlockedTransitionError: if open high/critical findings block it.
        """
        from orivellum.capabilities.state_machine import apply_transition, MESSAGE_SM
        with self._lock:
            row = self._conn.execute(
                "SELECT state FROM messages WHERE id=?", (msg_id,)
            ).fetchone()
        if not row:
            return  # message not found — caller already logged it
        apply_transition(
            self, MESSAGE_SM,
            object_id=msg_id,
            object_type="message",
            table="messages",
            state_col="state",
            from_state=row["state"],
            to_state=to_state,
            actor="system",
            # streaming transitions are never blocked by findings
            check_blockers=False,
        )

    def finalize_message(self, msg_id: str, text: str, state: str) -> None:
        """Write the final text + state to a pre-created assistant message stub.

        Used at the end of the streaming pipeline to atomically commit the
        full reply text and the terminal state ('done' or 'failed') in one
        governed_write transaction.

        Does NOT validate via MESSAGE_SM — the caller is responsible for
        making sure the message is in a state that can reach *state* (i.e.
        transition_message(msg_id, 'streaming') should have been called first).
        If the message is not found, the call is a no-op.
        """
        now = _now()
        _wc = len(text.split()) if text else 0
        with self.governed_write(
            operation="message.finalized",
            event_type="message.finalized",
            object_id=msg_id,
            object_type="message",
            payload={"state": state, "word_count": _wc},
            actor="system",
            detail=f"{state} {_wc}w",
        ):
            self._conn.execute(
                "UPDATE messages SET text=?, state=? WHERE id=?",
                (text, state, msg_id),
            )

    def update_conversation(self, conv_id: str, title: str | None = None,
                            archived: bool | None = None,
                            model: str | None = None,
                            expected_version: int | None = None) -> dict | None:
        """Update mutable fields on a conversation.

        Args:
            conv_id: the conversation to update.
            title: new display title, or None to leave unchanged.
            archived: True/False to archive/unarchive, or None to leave unchanged.
            model: model attribution string, or None to leave unchanged.
            expected_version: when supplied, the current ``conversations.version``
                must equal this value or :exc:`VersionConflictError` is raised
                and nothing is written.  The version is incremented on success.

        Returns:
            The refreshed conversation dict, or None if it does not exist.
        """
        now = _now()
        updates: dict[str, Any] = {"updated_at": now}
        if title is not None:
            updates["title"] = title
        if archived is not None:
            updates["archived"] = 1 if archived else 0
        if model is not None:
            updates["model"] = model
        meaningful = {k: v for k, v in updates.items() if k != "updated_at"}
        if not meaningful and expected_version is None:
            # Nothing to do — skip the governed write entirely.
            with self._lock:
                self._conn.execute(
                    f"UPDATE conversations SET updated_at=? WHERE id=?",
                    (now, conv_id),
                )
                self._conn.commit()
            return self.get_conversation(conv_id)
        updates["version"] = None  # placeholder; actual bump done via SQL below
        set_clause = ", ".join(
            (f"{k}=?" if k != "version" else "version=version+1")
            for k in updates
        )
        vals = [v for k, v in updates.items() if k != "version"] + [conv_id]
        with self.governed_write(
            operation="conversation.updated",
            event_type="conversation.updated",
            object_id=conv_id,
            object_type="conversation",
            payload={"fields": list(meaningful.keys())},
            detail=",".join(meaningful.keys()) or None,
        ):
            if expected_version is not None:
                row = self._conn.execute(
                    "SELECT version FROM conversations WHERE id=?", (conv_id,)
                ).fetchone()
                actual = row["version"] if row else None
                if actual != expected_version:
                    raise VersionConflictError(conv_id, expected_version, actual)
            self._conn.execute(
                f"UPDATE conversations SET {set_clause} WHERE id=?", vals
            )
        return self.get_conversation(conv_id)

    def delete_conversation(self, conv_id: str) -> bool:
        _deleted = False
        with self.governed_write(
            operation="conversation.deleted",
            event_type="conversation.deleted",
            object_id=conv_id,
            object_type="conversation",
            actor="user",
        ):
            cur = self._conn.execute("DELETE FROM conversations WHERE id=?", (conv_id,))
            _deleted = cur.rowcount > 0
        return _deleted

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
        # Read the work/kind before the write transaction (read-only, no lock held).
        with self._lock:
            _meta_row = self._conn.execute(
                "SELECT work_id, kind FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
        _work_id = _meta_row["work_id"] if _meta_row else None
        _kind = _meta_row["kind"] if _meta_row else None
        _changed = False
        with self.governed_write(
            operation="document.lifecycle_updated",
            event_type="document.lifecycle_updated",
            object_id=doc_id,
            object_type="document",
            payload={"lifecycle": lifecycle, "work_id": _work_id},
            actor="user",
            detail=lifecycle,
        ):
            cur = self._conn.execute(
                "UPDATE objects SET lifecycle=?, updated_at=? WHERE id=?",
                (lifecycle, now, doc_id),
            )
            _changed = cur.rowcount > 0
            if lifecycle == "canonical" and _changed and _work_id and _kind:
                # Demote all other same-work docs of same kind to 'draft'
                self._conn.execute(
                    """UPDATE objects SET lifecycle='draft', updated_at=?
                       WHERE id IN (
                           SELECT id FROM documents
                           WHERE work_id=? AND kind=? AND id!=?
                       ) AND lifecycle NOT IN ('superseded','deleted')""",
                    (now, _work_id, _kind, doc_id),
                )
        return _changed

    def create_document(self, title: str, source: str | None = None, sha256: str | None = None,
                        kind: str | None = None, work_id: str | None = None,
                        content_path: str | None = None, meta: dict | None = None,
                        tier: str = "source") -> dict:
        oid = _uuid()
        now = _now()
        with self.governed_write(
            operation="document.imported",
            event_type="document.imported",
            object_id=oid,
            object_type="document",
            payload={"work_id": work_id, "kind": kind, "sha256": sha256, "tier": tier},
            actor="user",
            detail=(title[:120] if title else source),
        ):
            self._conn.execute(
                """INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,
                   created_at,updated_at,created_by) VALUES(?,?,1,'draft','{}','{}',?,?,'user')""",
                (oid, "document", now, now),
            )
            self._conn.execute(
                """INSERT INTO documents(id,work_id,title,source,sha256,kind,readiness,
                   content_path,meta,tier,created_at) VALUES(?,?,?,?,?,?,'imported',?,?,?,?)""",
                (oid, work_id, title, source, sha256, kind, content_path, _jdump(meta or {}), tier, now),
            )
        return self.get_document(oid)  # type: ignore[return-value]

    def update_document_work(self, doc_id: str, work_id: str | None) -> bool:
        """Re-assign (or unlink) a document from a work."""
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
        if not exists:
            return False
        op = "document.work_assigned" if work_id else "document.work_unlinked"
        _updated = False
        with self.governed_write(
            operation=op,
            event_type=op,
            object_id=doc_id,
            object_type="document",
            actor="user",
            detail=work_id,
        ):
            cur = self._conn.execute(
                "UPDATE documents SET work_id=? WHERE id=?", (work_id, doc_id)
            )
            _updated = cur.rowcount > 0
        return _updated

    def delete_document(self, doc_id: str) -> bool:
        # Capture title before entering governed_write (read-only, outside TX)
        with self._lock:
            _row = self._conn.execute(
                "SELECT title FROM documents WHERE id=?", (doc_id,)
            ).fetchone()
        if not _row:
            return False
        _title = _row["title"]
        now = _now()
        # Use governed_write so deletion is audit-logged + outbox-emitted atomically.
        _deleted = False
        with self.governed_write(
            operation="document.deleted",
            event_type="document.deleted",
            object_id=doc_id,
            object_type="document",
            actor="user",
            detail=_title,
        ):
            cur = self._conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
            self._conn.execute(
                "UPDATE objects SET lifecycle='deleted', updated_at=? WHERE id=?",
                (now, doc_id),
            )
            _deleted = cur.rowcount > 0
        return _deleted

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
        # Pre-read dedup check (outside the write transaction)
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM entities WHERE name=? AND kind=?",
                (norm, kind),
            ).fetchone()
        if existing:
            return existing["id"]
        eid = _uuid()
        with self.governed_write(
            operation="entity.created",
            event_type="entity.created",
            object_id=eid,
            object_type="entity",
            actor="system",
            detail=f"{kind}/{norm[:80]}",
        ):
            self._conn.execute(
                """INSERT INTO entities(id, name, kind, canonical, aliases, meta, created_at)
                   VALUES(?,?,?,1,'{}',?,?)""",
                (eid, norm, kind, _jdump(meta or {}), now),
            )
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
            with self.governed_write(
                operation="entity.mention_created",
                event_type="entity.mention_created",
                object_id=entity_id,
                object_type="entity",
                actor="system",
                detail=f"doc={doc_id[:12]}",
            ):
                self._conn.execute(
                    """INSERT OR IGNORE INTO relationships
                       (id, source_id, target_id, kind, weight, meta, created_at)
                       VALUES(?,?,?,'MENTIONS',1.0,?,?)""",
                    (rel_oid, entity_id, doc_id, _jdump(meta_val), now),
                )
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
            with self.governed_write(
                operation="entity.edge_created",
                event_type="entity.edge_created",
                object_id=source_id,
                object_type="entity",
                actor="system",
                detail=f"{relation}/{target_id[:12]}",
            ):
                self._conn.execute(
                    """INSERT INTO edges(id, source_id, target_id, relation, weight, meta, created_at)
                       VALUES(?,?,?,?,?,'{}',?)""",
                    (eid, source_id, target_id, relation, weight, now),
                )
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
        oid = _uuid()
        now = _now()
        with self.governed_write(
            operation="task.created",
            event_type="task.created",
            object_id=oid,
            object_type="task",
            payload={"work_id": work_id, "priority": priority},
            actor="user",
            detail=text[:120] if text else None,
        ):
            self._conn.execute(
                """INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,
                   created_at,updated_at,created_by) VALUES(?,?,1,'active','{}','{}',?,?,'user')""",
                (oid, "task", now, now),
            )
            self._conn.execute(
                "INSERT INTO tasks(id,work_id,text,status,priority,meta,created_at) VALUES(?,?,?,'pending',?,?,?)",
                (oid, work_id, text, priority, "{}", now),
            )
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
        _changed_fields = [k for k in updates if k != "completed_at"]
        with self.governed_write(
            operation="task.updated",
            event_type="task.updated",
            object_id=task_id,
            object_type="task",
            payload={"fields": _changed_fields},
            actor="user",
            detail=",".join(_changed_fields),
        ):
            self._conn.execute(f"UPDATE tasks SET {set_clause} WHERE id=?", vals)
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def delete_task(self, task_id: str) -> bool:
        """Delete a task by id. Returns True if a row was deleted."""
        with self.governed_write(
            operation="task.deleted",
            event_type="task.deleted",
            object_id=task_id,
            object_type="task",
            payload={},
            actor="user",
            detail="task deleted",
        ):
            cur = self._conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        return cur.rowcount > 0

    # -------------------------------------------------------------------------
    # Chunks (extracted text segments, FTS-indexed)
    # -------------------------------------------------------------------------

    def add_chunk(self, doc_id: str, text: str, page: int = 0) -> str:
        """Insert a text chunk and update the FTS index. Returns chunk id.

        chunks.id is a FK to objects(id), so we must register it there first.
        """
        cid = _uuid()
        now = _now()
        with self.governed_write(
            operation="document.chunk_added",
            event_type="document.chunk_added",
            object_id=doc_id,
            object_type="document",
            actor="system",
            detail=f"page={page}",
        ):
            self._conn.execute(
                """INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,
                   created_at,updated_at,created_by) VALUES(?,?,1,'active','{}','{}',?,?,'system')""",
                (cid, "chunk", now, now),
            )
            self._conn.execute(
                "INSERT INTO chunks(id,doc_id,page,text,created_at) VALUES(?,?,?,?,?)",
                (cid, doc_id, page, text, now),
            )
            self._conn.execute(
                "INSERT INTO chunks_fts(chunk_id,doc_id,text) VALUES(?,?,?)",
                (cid, doc_id, text),
            )
        return cid

    def delete_chunks(self, doc_id: str) -> None:
        """Remove all chunks for a document (e.g. before re-extracting)."""
        with self._lock:
            _row = self._conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE doc_id=?", (doc_id,)
            ).fetchone()
        _count = _row[0] if _row else 0
        if _count == 0:
            return
        with self.governed_write(
            operation="document.chunks_cleared",
            event_type="document.chunks_cleared",
            object_id=doc_id,
            object_type="document",
            actor="system",
            detail=f"{_count} chunks",
        ):
            self._conn.execute("DELETE FROM chunks_fts WHERE doc_id=?", (doc_id,))
            self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))

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
        with self._lock:
            _row = self._conn.execute(
                "SELECT COUNT(*) FROM extraction_warnings WHERE doc_id=?", (doc_id,)
            ).fetchone()
        _count = _row[0] if _row else 0
        if _count == 0:
            return
        with self.governed_write(
            operation="document.warnings_cleared",
            event_type="document.warnings_cleared",
            object_id=doc_id,
            object_type="document",
            actor="system",
            detail=f"{_count} warnings",
        ):
            self._conn.execute(
                "DELETE FROM extraction_warnings WHERE doc_id=?", (doc_id,)
            )

    def add_extraction_warning(self, doc_id: str, kind: str,
                               detail: str | None = None) -> str:
        """Persist a single extraction warning. Returns the warning id."""
        wid = _uuid()
        now = _now()
        with self.governed_write(
            operation="document.warning_added",
            event_type="document.warning_added",
            object_id=doc_id,
            object_type="document",
            actor="system",
            detail=f"{kind}: {(detail or '')[:80]}",
        ):
            self._conn.execute(
                "INSERT INTO extraction_warnings(id, doc_id, kind, detail, created_at)"
                " VALUES(?,?,?,?,?)",
                (wid, doc_id, kind, detail, now),
            )
        return wid

    def update_document_extracted(self, doc_id: str, extracted_text: str,
                                  word_count: int, readiness: str = "ready",
                                  error_message: str | None = None) -> None:
        """Persist extraction results back on the document row."""
        _op = "document.extraction_failed" if readiness in ("error", "no_text") else "document.extracted"
        with self.governed_write(
            operation=_op,
            event_type=_op,
            object_id=doc_id,
            object_type="document",
            payload={"readiness": readiness, "word_count": word_count},
            actor="system",
            detail=error_message or f"{word_count}w {readiness}",
        ):
            self._conn.execute(
                "UPDATE documents SET extracted_text=?, word_count=?, readiness=?, error_message=? WHERE id=?",
                (extracted_text, word_count, readiness, error_message, doc_id),
            )

    def upsert_book_chapters(self, doc_id: str, work_id: str | None,
                             chapters: list[dict]) -> int:
        """Replace all book_chapters rows for a document with new extractions.

        Each chapter dict must contain: seq, level, title, text.
        Old rows (and their objects entries) are deleted first so the
        operation is fully idempotent — safe to call on reprocess.
        Returns the count of chapters written.
        """
        now = _now()
        n = len(chapters)
        with self.governed_write(
            operation="document.chapters_updated",
            event_type="document.chapters_updated",
            object_id=doc_id,
            object_type="document",
            actor="system",
            detail=f"{n} chapters",
        ):
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
    # Book pipelines
    # -------------------------------------------------------------------------

    def create_book_pipeline(self, work_id: str, title: str,
                              config: dict | None = None) -> dict:
        """Create a book pipeline for a Work at state B0.

        Idempotent: if a non-deleted pipeline already exists for the Work,
        the existing record is returned unchanged.  Orphan book_chapters
        rows (pipeline_id IS NULL, work_id matches) are linked to the new
        pipeline so they appear in the chapter count immediately.
        """
        import json as _json
        existing = self.get_book_pipeline_for_work(work_id)
        if existing:
            return existing

        oid = _uuid()
        now = _now()
        cfg = _json.dumps(config or {})

        with self.governed_write(
            operation="book_pipeline.created",
            event_type="book_pipeline.created",
            object_id=oid,
            object_type="book_pipeline",
            actor="user",
            detail=f"Pipeline '{title}' initialised at B0",
        ):
            self._conn.execute(
                """INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,
                   created_at,updated_at,created_by) VALUES(?,?,1,'active','{}','{}',?,?,'user')""",
                (oid, "book_pipeline", now, now),
            )
            self._conn.execute(
                """INSERT INTO book_pipelines(id,work_id,title,status,config,meta,
                   created_at,updated_at) VALUES(?,?,?,'B0',?,'{}',?,?)""",
                (oid, work_id, title, cfg, now, now),
            )
            # Link any already-extracted chapters that haven't been assigned yet
            self._conn.execute(
                "UPDATE book_chapters SET pipeline_id=? WHERE work_id=? AND pipeline_id IS NULL",
                (oid, work_id),
            )
        return self.get_book_pipeline_for_work(work_id)  # type: ignore[return-value]

    def get_book_pipeline_for_work(self, work_id: str) -> dict | None:
        """Return the most-recent active book pipeline for a Work.

        The returned dict includes aggregated chapter counts broken down by
        status (extracted / drafted / approved) and a total.
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT bp.id, bp.work_id, bp.title, bp.status, bp.config, bp.meta,
                          bp.created_at, bp.updated_at,
                          (SELECT COUNT(*) FROM book_chapters bc
                           WHERE bc.pipeline_id=bp.id) as chapter_count,
                          (SELECT COUNT(*) FROM book_chapters bc
                           WHERE bc.pipeline_id=bp.id AND bc.status='extracted') as chapters_extracted,
                          (SELECT COUNT(*) FROM book_chapters bc
                           WHERE bc.pipeline_id=bp.id AND bc.status='drafted') as chapters_drafted,
                          (SELECT COUNT(*) FROM book_chapters bc
                           WHERE bc.pipeline_id=bp.id AND bc.status='approved') as chapters_approved
                   FROM book_pipelines bp JOIN objects o ON o.id=bp.id
                   WHERE bp.work_id=? AND o.lifecycle != 'deleted'
                   ORDER BY bp.created_at DESC LIMIT 1""",
                (work_id,),
            ).fetchone()
        if not row:
            return None
        return dict(row)

    # -------------------------------------------------------------------------
    # Brainstorm sessions (divergent thinking engine)
    # -------------------------------------------------------------------------

    def create_brainstorm_session(
        self,
        work_id: str,
        seed_prompt: str,
        context_type: str = "general",
        n_domains: int = 5,
    ) -> dict:
        """Create a new brainstorm session record (status='running')."""
        sid = _uuid()
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO brainstorm_sessions
                   (id, work_id, seed_prompt, context_type, status, ideas, domain_count, created_at)
                   VALUES (?,?,?,?,'running','[]',?,?)""",
                (sid, work_id, seed_prompt, context_type, n_domains, now),
            )
            self._conn.commit()
        return self.get_brainstorm_session(sid)  # type: ignore[return-value]

    def update_brainstorm_session(
        self,
        session_id: str,
        status: str,
        ideas: list,
        completed_at: str | None = None,
    ) -> None:
        """Persist brainstorm results (status='done' or 'failed')."""
        import json as _json
        now = _now()
        ca = completed_at or now
        with self._lock:
            self._conn.execute(
                """UPDATE brainstorm_sessions
                   SET status=?, ideas=?, completed_at=?
                   WHERE id=?""",
                (status, _json.dumps(ideas), ca, session_id),
            )
            self._conn.commit()

    def get_brainstorm_session(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM brainstorm_sessions WHERE id=?",
                (session_id,),
            ).fetchone()
        if not row:
            return None
        import json as _json
        d = dict(row)
        d["ideas"] = _json.loads(d.get("ideas") or "[]")
        return d

    def list_brainstorm_sessions(
        self,
        work_id: str,
        limit: int = 20,
    ) -> list[dict]:
        import json as _json
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM brainstorm_sessions
                   WHERE work_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (work_id, limit),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["ideas"] = _json.loads(d.get("ideas") or "[]")
            result.append(d)
        return result

    # -------------------------------------------------------------------------
    # Pipeline artifacts (B0-B17 stage AI outputs)
    # -------------------------------------------------------------------------

    def get_pipeline_artifact(self, pipeline_id: str, stage: str) -> dict | None:
        """Return the artifact for a specific stage of a pipeline, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM pipeline_artifacts WHERE pipeline_id=? AND stage=?",
                (pipeline_id, stage),
            ).fetchone()
        if not row:
            return None
        r = dict(row)
        r["content"] = _jload(r.get("content"), {})
        return r

    def upsert_pipeline_artifact(
        self,
        pipeline_id: str,
        stage: str,
        artifact_type: str,
        content: dict,
        status: str = "done",
        error: str | None = None,
    ) -> str:
        """Create or replace the artifact for a pipeline stage.

        The UNIQUE(pipeline_id, stage) constraint means calling this twice for
        the same stage replaces the previous result (e.g. when re-running a
        failed worker).  Returns the artifact id.
        """
        now = _now()
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM pipeline_artifacts WHERE pipeline_id=? AND stage=?",
                (pipeline_id, stage),
            ).fetchone()
            artifact_id = existing["id"] if existing else _uuid()
            self._conn.execute(
                """INSERT INTO pipeline_artifacts(id, pipeline_id, stage, artifact_type,
                   content, status, error, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(pipeline_id, stage) DO UPDATE SET
                       artifact_type=excluded.artifact_type,
                       content=excluded.content,
                       status=excluded.status,
                       error=excluded.error,
                       updated_at=excluded.updated_at""",
                (artifact_id, pipeline_id, stage, artifact_type,
                 _jdump(content), status, error, now, now),
            )
            self._conn.commit()
        return artifact_id

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
        now = _now()
        # Dedup by text_hash within same work (checked before acquiring write lock)
        text_hash = hashlib.sha256(f"{work_id}:{text}".encode()).hexdigest()
        meta_json = _jdump(meta or {})
        # Fast dedup check outside governed_write to avoid holding the write lock
        # during the SELECT.
        with self._lock:
            existing = self._conn.execute(
                "SELECT id FROM knowledge WHERE text_hash=? AND work_id IS ?",
                (text_hash, work_id),
            ).fetchone()
        if existing:
            return existing["id"]
        # Not a duplicate — write atomically with audit + outbox.
        kid = _uuid()
        with self.governed_write(
            operation="knowledge.created",
            event_type="knowledge.created",
            object_id=kid,
            object_type="knowledge",
            payload={"work_id": work_id, "kind": kind, "review_status": review_status},
            actor="system",
            detail=f"{kind}: {text[:80]}",
        ):
            self._conn.execute(
                """INSERT INTO objects(id,type,version,lifecycle,provenance,permissions,
                   created_at,updated_at,created_by) VALUES(?,?,1,'active','{}','{}',?,?,'system')""",
                (kid, "knowledge", now, now),
            )
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
        return kid

    def update_knowledge_review_status(self, item_id: str, status: str,
                                       expected_status: tuple[str, ...] | None = None) -> str:
        """Set review_status on a knowledge item.

        When ``expected_status`` is given, the write is a compare-and-set: it
        only applies while the current status is one of the expected values,
        so a stale or concurrent request cannot overturn a decision that was
        already finalized through another surface.

        Returns "updated", "not_found", or "conflict" (CAS failed).
        """
        valid = {"auto", "ai_auto", "approved", "rejected"}
        if status not in valid:
            raise ValueError(f"review_status must be one of {valid}")
        # Read before entering governed_write to support early-exit paths
        # (not_found, CAS conflict) without holding the write lock unnecessarily.
        with self._lock:
            _row = self._conn.execute(
                "SELECT review_status FROM knowledge WHERE id=?", (item_id,)
            ).fetchone()
        if not _row:
            return "not_found"
        _before_status: str = _row["review_status"]
        if expected_status is not None and _before_status not in expected_status:
            return "conflict"
        _bh = hashlib.sha256(
            json.dumps({"review_status": _before_status}).encode()
        ).hexdigest()
        _ah = hashlib.sha256(
            json.dumps({"review_status": status}).encode()
        ).hexdigest()
        _changed = False
        try:
            with self.governed_write(
                operation="knowledge.review_updated",
                event_type="knowledge.review_updated",
                object_id=item_id,
                object_type="knowledge",
                payload={"before": _before_status, "after": status},
                actor="user",
                detail=f"{_before_status}→{status}",
            ):
                # Re-check CAS inside the write lock to close the read→write window.
                if expected_status is not None:
                    _live = self._conn.execute(
                        "SELECT review_status FROM knowledge WHERE id=?", (item_id,)
                    ).fetchone()
                    if not _live or _live["review_status"] not in expected_status:
                        # CAS lost the race — governed_write will rollback on exit.
                        raise _CASConflict()
                cur = self._conn.execute(
                    "UPDATE knowledge SET review_status=? WHERE id=?",
                    (status, item_id),
                )
                _changed = cur.rowcount > 0
        except _CASConflict:
            return "conflict"
        return "updated" if _changed else "not_found"

    def update_knowledge_confidence(self, item_id: str, confidence: float,
                                    evidence: dict | None = None) -> bool:
        """Set confidence (and optional meta.evidence components) on a knowledge item."""
        confidence = max(0.0, min(1.0, float(confidence)))
        with self._lock:
            row = self._conn.execute(
                "SELECT meta FROM knowledge WHERE id=?", (item_id,)).fetchone()
        if not row:
            return False
        with self.governed_write(
            operation="knowledge.confidence_updated",
            event_type="knowledge.confidence_updated",
            object_id=item_id,
            object_type="knowledge",
            actor="system",
            detail=f"{confidence:.2f}",
        ):
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
        with self.governed_write(
            operation="conflict.detected",
            event_type="conflict.detected",
            object_id=cid,
            object_type="conflict",
            actor="system",
            detail=f"{conflict_type}: {claim_a_id[:8]} vs {claim_b_id[:8]}",
        ):
            self._conn.execute(
                """INSERT INTO conflicts(id, claim_a_id, claim_b_id,
                   conflict_type, resolution, created_at)
                   VALUES(?,?,?,?,NULL,?)""",
                (cid, claim_a_id, claim_b_id, conflict_type, _now()),
            )
        return cid

    def create_conflicts_batch(self, pairs: list[tuple[str, str, str]]) -> int:
        """Batch-insert conflicts [(claim_a_id, claim_b_id, conflict_type), ...].

        Skips pairs already recorded (either order). Single governed transaction
        for the whole batch. Returns number inserted.
        """
        if not pairs:
            return 0
        # Pre-filter already-known pairs (read outside transaction)
        new_pairs: list[tuple[str, str, str]] = []
        with self._lock:
            for a_id, b_id, ctype in pairs:
                exists = self._conn.execute(
                    """SELECT 1 FROM conflicts
                       WHERE (claim_a_id=? AND claim_b_id=?)
                          OR (claim_a_id=? AND claim_b_id=?)""",
                    (a_id, b_id, b_id, a_id)).fetchone()
                if not exists:
                    new_pairs.append((a_id, b_id, ctype))
        inserted = len(new_pairs)
        if not inserted:
            return 0
        with self.governed_write(
            operation="conflict.detected",
            event_type="conflict.detected",
            object_id="batch",
            object_type="conflict",
            actor="system",
            detail=f"batch: {inserted} new conflict(s)",
        ):
            for a_id, b_id, ctype in new_pairs:
                self._conn.execute(
                    """INSERT INTO conflicts(id, claim_a_id, claim_b_id,
                       conflict_type, resolution, created_at)
                       VALUES(?,?,?,?,NULL,?)""",
                    (str(uuid.uuid4()), a_id, b_id, ctype, _now()))
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
        with self.governed_write(
            operation="conflict.resolved",
            event_type="conflict.resolved",
            object_id=conflict_id,
            object_type="conflict",
            actor="user",
            detail=resolution,
        ):
            self._conn.execute(
                "UPDATE conflicts SET resolution=? WHERE id=?",
                (resolution, conflict_id))
        # Side effect: reject the losing claim (update_knowledge_review_status is
        # already a governed write internally)
        loser = None
        if resolution == "keep_a":
            loser = row["claim_b_id"]
        elif resolution == "keep_b":
            loser = row["claim_a_id"]
        if loser:
            self.update_knowledge_review_status(loser, "rejected")
        return True

    # -------------------------------------------------------------------------
    # Vectors (semantic embeddings)
    # -------------------------------------------------------------------------

    def store_vector(self, object_id: str, object_type: str,
                     embedding: bytes, dim: int) -> None:
        """Insert or replace the embedding for an object."""
        with self.governed_write(
            operation="vector.stored",
            event_type="vector.stored",
            object_id=object_id,
            object_type=object_type,
            actor="system",
            detail=f"dim={dim}",
        ):
            self._conn.execute(
                "DELETE FROM vectors WHERE object_id=? AND object_type=?",
                (object_id, object_type))
            self._conn.execute(
                """INSERT INTO vectors(id, object_id, object_type, embedding, dim, created_at)
                   VALUES(?,?,?,?,?,?)""",
                (str(uuid.uuid4()), object_id, object_type, embedding, dim, _now()))

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
        # Pre-read: find next version_num (outside the write transaction)
        with self._lock:
            last = self._conn.execute(
                "SELECT MAX(version_num) FROM doc_versions WHERE doc_id=?", (doc_id,)
            ).fetchone()[0]
        version_num = (last or 0) + 1
        _canonical_flag = " canonical" if is_canonical else ""
        with self.governed_write(
            operation="document.version_created",
            event_type="document.version_created",
            object_id=doc_id,
            object_type="document",
            actor=created_by,
            detail=f"v{version_num}{_canonical_flag}",
        ):
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
            exists = self._conn.execute(
                "SELECT 1 FROM doc_versions WHERE id=? AND doc_id=?", (version_id, doc_id)
            ).fetchone()
        if not exists:
            return False
        _updated = False
        with self.governed_write(
            operation="document.version_canonical",
            event_type="document.version_canonical",
            object_id=doc_id,
            object_type="document",
            actor="user",
            detail=version_id[:36],
        ):
            self._conn.execute("UPDATE doc_versions SET is_canonical=0 WHERE doc_id=?", (doc_id,))
            cur = self._conn.execute(
                "UPDATE doc_versions SET is_canonical=1 WHERE id=? AND doc_id=?",
                (version_id, doc_id),
            )
            _updated = cur.rowcount > 0
        return _updated

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
        with self._lock:
            tier_rows = self._conn.execute(
                "SELECT tier, COUNT(*) as cnt FROM documents GROUP BY tier"
            ).fetchall()
        tier_counts = {r["tier"]: r["cnt"] for r in tier_rows}
        return {
            "work_count": work_count,
            "document_count": doc_count,
            "documents_ready": doc_ready,
            "knowledge_count": knowledge_count,
            "conversation_count": conv_count,
            "pending_task_count": task_count,
            "document_tier_counts": tier_counts,
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
        with self.governed_write(
            operation="gaps.cache_updated",
            event_type="gaps.cache_updated",
            object_id=work_id,
            object_type="work",
            actor="system",
            detail=f"coverage={coverage_pct:.1f}%",
        ):
            self._conn.execute(
                """INSERT INTO work_gap_cache (work_id, gaps_json, coverage_pct, evaluated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(work_id) DO UPDATE SET
                     gaps_json    = excluded.gaps_json,
                     coverage_pct = excluded.coverage_pct,
                     evaluated_at = excluded.evaluated_at""",
                (work_id, _json.dumps(gaps), coverage_pct, now),
            )

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
    # Job state transitions (M0.2 — JOB_SM)
    # -------------------------------------------------------------------------

    def update_job_state(
        self,
        job_id: str,
        *,
        from_state: str,
        to_state: str,
        actor: str = "system",
        detail: str | None = None,
        check_blockers: bool = True,
    ) -> None:
        """Apply a JOB_SM state transition atomically via governed_write.

        Raises:
            InvalidTransitionError: if the transition is not declared in JOB_SM.
            BlockedTransitionError: if an open high/critical finding blocks it.
        """
        from orivellum.capabilities.state_machine import JOB_SM, apply_transition
        apply_transition(
            self, JOB_SM,
            object_id=job_id,
            object_type="job",
            table="jobs",
            state_col="state",
            from_state=from_state,
            to_state=to_state,
            actor=actor,
            detail=detail,
            check_blockers=check_blockers,
        )

    def create_job(
        self,
        *,
        job_type: str,
        priority: int = 0,
        max_attempts: int = 3,
        correlation_id: str | None = None,
        input_data: dict | None = None,
    ) -> str:
        """Create a new job in the 'queued' state.  Returns the new job id."""
        jid = str(uuid.uuid4())
        import json as _json
        input_payload = _json.dumps(input_data or {})
        now = _now()
        with self.governed_write(
            operation="job.created",
            event_type="job.created",
            object_id=jid,
            object_type="job",
            actor="system",
            detail=job_type,
        ):
            self._conn.execute(
                """INSERT INTO jobs(id, job_type, state, priority, created_at,
                   max_attempts, input, correlation_id)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (jid, job_type, "queued", priority, now,
                 max_attempts, input_payload, correlation_id),
            )
        return jid

    def get_job(self, job_id: str) -> dict | None:
        """Return a single job row, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_jobs(
        self,
        *,
        state: str | None = None,
        job_type: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Return jobs filtered by state and/or job_type."""
        clauses: list[str] = []
        params: list = []
        if state is not None:
            clauses.append("state=?")
            params.append(state)
        if job_type is not None:
            clauses.append("job_type=?")
            params.append(job_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # -------------------------------------------------------------------------
    # Findings (M0.2 governance blockers)
    # -------------------------------------------------------------------------

    def create_finding(
        self,
        *,
        object_id: str,
        object_type: str,
        description: str,
        kind: str = "issue",
        severity: str = "high",
        meta: dict | None = None,
    ) -> str:
        """Create a governance finding that may block state-machine transitions.

        A finding with severity ``high`` or ``critical`` blocks all forward
        transitions on *object_id* until it is resolved.

        Returns the new finding id.
        """
        fid = str(uuid.uuid4())
        now = _now()
        import json as _json
        payload = _json.dumps(meta or {})
        with self.governed_write(
            operation="finding.created",
            event_type="finding.created",
            object_id=fid,
            object_type="finding",
            actor="system",
            detail=f"{severity}/{kind} on {object_id[:12]}…: {description[:80]}",
        ):
            self._conn.execute(
                """INSERT INTO findings(id, object_id, object_type, kind,
                   description, severity, state, created_at, meta)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (fid, object_id, object_type, kind,
                 description, severity, "open", now, payload),
            )
        return fid

    def list_findings(
        self,
        *,
        object_id: str | None = None,
        state: str | None = None,
        min_severity: tuple[str, ...] | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return findings filtered by *object_id* and/or *state*.

        Args:
            object_id:    Restrict to findings on this object.
            state:        ``"open"`` or ``"resolved"``; None returns all.
            min_severity: Tuple of severities to include, e.g.
                          ``("high", "critical")``.  None includes all.
            limit:        Maximum rows to return.
        """
        clauses: list[str] = []
        params: list = []
        if object_id is not None:
            clauses.append("object_id = ?")
            params.append(object_id)
        if state is not None:
            clauses.append("state = ?")
            params.append(state)
        if min_severity is not None:
            placeholders = ",".join("?" * len(min_severity))
            clauses.append(f"severity IN ({placeholders})")
            params.extend(min_severity)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM findings {where} ORDER BY created_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_finding(self, finding_id: str) -> dict | None:
        """Return a single finding by id, or None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM findings WHERE id=?", (finding_id,)
            ).fetchone()
        return dict(row) if row else None

    def resolve_finding(
        self,
        finding_id: str,
        *,
        resolved_by: str = "system",
    ) -> bool:
        """Mark a finding resolved.

        Returns True if the finding existed and was open (i.e. actually changed).
        """
        now = _now()
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM findings WHERE id=? AND state='open'", (finding_id,)
            ).fetchone()
        if not exists:
            return False
        with self.governed_write(
            operation="finding.resolved",
            event_type="finding.resolved",
            object_id=finding_id,
            object_type="finding",
            actor=resolved_by,
        ):
            self._conn.execute(
                """UPDATE findings SET state='resolved', resolved_at=?,
                   resolved_by=? WHERE id=? AND state='open'""",
                (now, resolved_by, finding_id),
            )
        return True

    # -------------------------------------------------------------------------
    # PKLOS Layer 0 — Claim ledger (VER-INV-001)
    # -------------------------------------------------------------------------

    def upsert_claim(
        self,
        subject: str,
        predicate: str,
        value: str,
        *,
        unit: str | None = None,
        authority_tier: str = "A7",
        source_id: str | None = None,
        conv_id: str | None = None,
        ttl_class: str = "DURABLE",
        evidence_text: str | None = None,
        meta: dict | None = None,
    ) -> str:
        """Insert or update a claim.  Returns the claim id.

        If a CURRENT or UNOBSERVED claim already exists for (subject, predicate)
        at equal or lower authority tier (numerically higher A-number = lower
        authority), update it.  A higher-authority source always wins.

        Authority ordering: A0 > A1 > A2 > … > A8 (lower index = higher authority).
        We update when incoming tier <= existing tier (equal or better).
        """
        import json as _json
        now = _now()
        payload = _json.dumps(meta or {})

        # Determine initial status from authority tier (spec §3.3 state machine).
        # A7/A8 → USER_ASSERTED (not independently verified yet).
        # A0–A6 → RETRIEVED (has a source; verifier must run to reach VERIFIED).
        # Backward-compat: treat legacy 'CURRENT' inputs as VERIFIED.
        new_tier_num = int(authority_tier[1:]) \
            if len(authority_tier) > 1 and authority_tier[1:].isdigit() else 99
        new_status = "USER_ASSERTED" if new_tier_num >= 7 else "RETRIEVED"

        # Statuses considered "live" (can be superseded by a new upsert)
        _LIVE_STATUSES = (
            'USER_ASSERTED', 'RETRIEVED', 'PARTIALLY_VERIFIED',
            'VERIFIED', 'CURRENT', 'UNOBSERVED',
        )

        # Read phase — lock held only for the SELECT
        with self._lock:
            existing = self._conn.execute(
                f"""SELECT id, authority_tier, status FROM claims
                   WHERE subject=? AND predicate=?
                   AND status IN ({','.join('?'*len(_LIVE_STATUSES))})
                   ORDER BY updated_at DESC LIMIT 1""",
                (subject, predicate, *_LIVE_STATUSES),
            ).fetchone()

        if existing:
            cid = existing["id"]
            old_tier_num = int(existing["authority_tier"][1:]) \
                if existing["authority_tier"][1:].isdigit() else 99

            if new_tier_num <= old_tier_num:
                # Update path — governed_write keeps claim + transition + FTS atomic
                old_status = existing["status"]
                with self.governed_write(
                    operation="claim.updated",
                    event_type="claim.updated",
                    object_id=cid,
                    object_type="claim",
                    actor="system",
                    detail=f"{predicate[:40]}={value[:40]}",
                ):
                    self._conn.execute(
                        """UPDATE claims
                           SET value=?, unit=?, authority_tier=?, source_id=?,
                               conv_id=?, ttl_class=?, status=?,
                               updated_at=?, meta=?
                           WHERE id=?""",
                        (value, unit, authority_tier, source_id,
                         conv_id, ttl_class, new_status, now, payload, cid),
                    )
                    if old_status != new_status:
                        self._conn.execute(
                            """INSERT INTO claim_transitions(id,claim_id,from_status,
                               to_status,actor,reason,created_at)
                               VALUES(?,?,?,?,?,?,?)""",
                            (str(uuid.uuid4()), cid, old_status, new_status,
                             "system", "upsert", now),
                        )
                    try:
                        self._conn.execute(
                            "DELETE FROM claims_fts WHERE claim_id=?", (cid,)
                        )
                        self._conn.execute(
                            "INSERT INTO claims_fts(claim_id,subject,predicate,value)"
                            " VALUES(?,?,?,?)",
                            (cid, subject, predicate, value),
                        )
                    except Exception:
                        pass
            # else: existing has higher authority — no write
            return cid

        # No existing claim — insert path
        cid = str(uuid.uuid4())
        with self.governed_write(
            operation="claim.created",
            event_type="claim.created",
            object_id=cid,
            object_type="claim",
            actor="system",
            detail=f"{subject[:40]}/{predicate[:40]}",
        ):
            self._conn.execute(
                """INSERT INTO claims(id,subject,predicate,value,unit,authority_tier,
                   source_id,status,confidence,ttl_class,conv_id,created_at,
                   updated_at,meta)
                   VALUES(?,?,?,?,?,?,?,?,1.0,?,?,?,?,?)""",
                (cid, subject, predicate, value, unit, authority_tier,
                 source_id, new_status, ttl_class, conv_id, now, now, payload),
            )
            self._conn.execute(
                """INSERT INTO claim_transitions(id,claim_id,from_status,to_status,
                   actor,reason,created_at) VALUES(?,?,'UNOBSERVED',?,?,?,?)""",
                (str(uuid.uuid4()), cid, new_status, "system", "initial_capture", now),
            )
            try:
                self._conn.execute(
                    "INSERT INTO claims_fts(claim_id,subject,predicate,value)"
                    " VALUES(?,?,?,?)",
                    (cid, subject, predicate, value),
                )
            except Exception:
                pass

        # Attach evidence if provided (governed internally by add_claim_evidence)
        if evidence_text:
            self.add_claim_evidence(cid, "assertion", evidence_text,
                                    source_id=source_id)
        return cid

    def get_claim(self, claim_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM claims WHERE id=?", (claim_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_claim_by_predicate(
        self, subject: str, predicate: str
    ) -> dict | None:
        """Return the most recent live claim for (subject, predicate).

        Prefers VERIFIED > PARTIALLY_VERIFIED > USER_ASSERTED > RETRIEVED > CURRENT.
        """
        _PRIORITY = {
            "VERIFIED": 0, "PARTIALLY_VERIFIED": 1,
            "USER_ASSERTED": 2, "RETRIEVED": 3, "CURRENT": 4,
        }
        _LIVE = tuple(_PRIORITY.keys())
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT * FROM claims WHERE subject=? AND predicate=?
                   AND status IN ({','.join('?'*len(_LIVE))})
                   ORDER BY updated_at DESC LIMIT 10""",
                (subject, predicate, *_LIVE),
            ).fetchall()
        if not rows:
            return None
        # Return highest-priority status
        best = min(rows, key=lambda r: _PRIORITY.get(r["status"], 99))
        return dict(best)

    def list_claims(
        self,
        *,
        subject: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list = []
        if subject is not None:
            clauses.append("subject=?")
            params.append(subject)
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM claims {where} ORDER BY updated_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def update_claim_status(
        self,
        claim_id: str,
        new_status: str,
        *,
        actor: str = "system",
        reason: str | None = None,
    ) -> bool:
        now = _now()
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM claims WHERE id=?", (claim_id,)
            ).fetchone()
        if not row:
            return False
        old_status = row["status"]
        if old_status == new_status:
            return False
        with self.governed_write(
            operation="claim.status_changed",
            event_type="claim.status_changed",
            object_id=claim_id,
            object_type="claim",
            actor=actor,
            detail=f"{old_status}→{new_status}",
        ):
            self._conn.execute(
                "UPDATE claims SET status=?, updated_at=? WHERE id=?",
                (new_status, now, claim_id),
            )
            self._conn.execute(
                """INSERT INTO claim_transitions(id,claim_id,from_status,to_status,
                   actor,reason,created_at) VALUES(?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), claim_id, old_status, new_status,
                 actor, reason, now),
            )
        return True

    def add_claim_evidence(
        self,
        claim_id: str,
        evidence_type: str,
        content: str,
        *,
        source_id: str | None = None,
    ) -> str:
        eid = str(uuid.uuid4())
        now = _now()
        with self.governed_write(
            operation="claim.evidence_added",
            event_type="claim.evidence_added",
            object_id=claim_id,
            object_type="claim",
            actor="system",
            detail=f"{evidence_type}: {content[:60]}",
        ):
            self._conn.execute(
                """INSERT INTO claim_evidence(id,claim_id,evidence_type,content,
                   source_id,created_at) VALUES(?,?,?,?,?,?)""",
                (eid, claim_id, evidence_type, content, source_id, now),
            )
        return eid

    def search_claims_for_context(
        self,
        query: str,
        *,
        subject: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Find live claims relevant to a query using FTS5 + fallback.

        Returns VERIFIED, PARTIALLY_VERIFIED, USER_ASSERTED, and RETRIEVED claims.
        A8 (model inference) is NEVER surfaced (VER-INV-001 / spec §3.1).
        Backward-compat: also matches legacy 'CURRENT' status.
        """
        results: list[dict] = []
        # A8 predicates are never surfaced (defense in depth for VER-INV-001)
        base_filter = (
            "c.status IN ('VERIFIED','PARTIALLY_VERIFIED','USER_ASSERTED',"
            "'RETRIEVED','CURRENT') AND c.authority_tier != 'A8'"
        )

        # Try FTS5 first
        try:
            tokens = " OR ".join(
                f'"{t}"' for t in query.split() if len(t) > 1
            )
            if tokens:
                sql = f"""
                    SELECT c.* FROM claims c
                    JOIN claims_fts f ON f.claim_id = c.id
                    WHERE {base_filter}
                    AND claims_fts MATCH ?
                    {' AND c.subject=?' if subject else ''}
                    ORDER BY rank LIMIT ?
                """
                params = [tokens]
                if subject:
                    params.append(subject)
                params.append(limit)
                with self._lock:
                    rows = self._conn.execute(sql, params).fetchall()
                results = [dict(r) for r in rows]
        except Exception:
            pass

        # Fallback: scan all live claims (keyword in value/predicate)
        if not results:
            try:
                qlow = query.lower()
                # Fetch all live claims (no status filter → picks up all live statuses)
                all_claims = [
                    c for c in self.list_claims(subject=subject, status=None, limit=200)
                    if c.get("status") in (
                        "VERIFIED", "PARTIALLY_VERIFIED", "USER_ASSERTED",
                        "RETRIEVED", "CURRENT",
                    )
                ]
                results = [
                    c for c in all_claims
                    if c.get("authority_tier") != "A8"
                    and (
                        qlow in (c.get("predicate") or "").lower()
                        or qlow in (c.get("value") or "").lower()
                        or qlow in (c.get("subject") or "").lower()
                        or any(
                            w in (c.get("predicate") or "").lower()
                            or w in (c.get("value") or "").lower()
                            for w in qlow.split()
                            if len(w) > 2
                        )
                    )
                ][:limit]
            except Exception:
                pass

        return results

    def create_capture_stamp(
        self,
        stamp_id: str,
        channel: str,
        source_type: str,
        *,
        claim_id: str | None = None,
        raw_text: str | None = None,
        meta: dict | None = None,
    ) -> str:
        import json as _json
        now = _now()
        payload = _json.dumps(meta or {})
        with self.governed_write(
            operation="capture_stamp.created",
            event_type="capture_stamp.created",
            object_id=stamp_id,
            object_type="capture_stamp",
            actor="system",
            detail=f"{channel}/{source_type}",
        ):
            self._conn.execute(
                """INSERT INTO capture_stamps(id,channel,source_type,claim_id,
                   raw_text,created_at,meta) VALUES(?,?,?,?,?,?,?)""",
                (stamp_id, channel, source_type, claim_id,
                 raw_text, now, payload),
            )
        return stamp_id

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
