"""Writing Architect decomposition storage (Pipeline M0 / DECOMPOSE).

Kept separate from db.py like MailStore.  All methods accept the main
OrivellumDB instance and operate on its connection/lock.

Persistence rules:
- Inventory (wa_archive_docs) and doctrine records (wa_records) are
  wipe-and-rebuild per decompose run — they are pure derivations of the
  archive and must never drift from it.
- Canon proposals (wa_canon_proposals) are INSERT OR IGNORE on a
  deterministic content-hash id, so re-running the decomposer never
  clobbers an author's ratification decision (approved / rejected).
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("orivellum.wa.store")

PROPOSAL_STATUSES = ("proposed", "approved", "rejected")


class WAStore:
    def __init__(self, db: Any) -> None:
        self._db = db

    def _conn(self):
        return self._db._conn

    def _read(self):
        return self._db.read_conn()

    def _lock(self):
        return self._db._lock

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    # ── Write (one transaction per decompose run) ─────────────────────────────

    def replace_run(
        self,
        run_id: str,
        inventory: list[dict],
        records: list[dict],
        proposals: list[dict],
    ) -> dict:
        """Persist a full decompose run atomically.

        Wipes and rewrites inventory + records; upserts proposals with
        INSERT OR IGNORE (preserving prior ratification decisions).
        Returns counts, including how many proposals were new.
        """
        now = self._now()
        new_proposals = 0
        with self._lock():
            c = self._conn()
            try:
                c.execute("DELETE FROM wa_archive_docs")
                c.execute("DELETE FROM wa_records")
                for d in inventory:
                    c.execute(
                        """INSERT INTO wa_archive_docs
                           (id, rel_path, filename, layer, sha256, size_bytes,
                            duplicate_of, status, reason, target_kind, run_id, created_at)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            d["id"],
                            d["rel_path"],
                            d["filename"],
                            d["layer"],
                            d["sha256"],
                            d["size_bytes"],
                            d.get("duplicate_of"),
                            d["status"],
                            d.get("reason"),
                            d.get("target_kind"),
                            run_id,
                            now,
                        ),
                    )
                for r in records:
                    c.execute(
                        """INSERT INTO wa_records
                           (id, record_type, name, payload, source_path,
                            source_note, run_id, created_at)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (
                            r["id"],
                            r["record_type"],
                            r["name"],
                            json.dumps(r["payload"], ensure_ascii=False),
                            r["source_path"],
                            r.get("source_note"),
                            run_id,
                            now,
                        ),
                    )
                for p in proposals:
                    cur = c.execute(
                        """INSERT OR IGNORE INTO wa_canon_proposals
                           (id, fact_title, fact_text, classification, scope,
                            source_path, source_location, status, created_at)
                           VALUES (?,?,?,?,?,?,?,'proposed',?)""",
                        (
                            p["id"],
                            p["fact_title"],
                            p["fact_text"],
                            p["classification"],
                            p["scope"],
                            p["source_path"],
                            p["source_location"],
                            now,
                        ),
                    )
                    new_proposals += cur.rowcount if cur.rowcount > 0 else 0
                c.commit()
            except Exception:
                c.rollback()
                raise
        return {
            "inventory": len(inventory),
            "records": len(records),
            "proposals_seen": len(proposals),
            "proposals_new": new_proposals,
        }

    # ── Read ──────────────────────────────────────────────────────────────────

    def list_inventory(self, status: str | None = None) -> list[dict]:
        q = "SELECT * FROM wa_archive_docs"
        args: list = []
        if status:
            q += " WHERE status=?"
            args.append(status)
        q += " ORDER BY rel_path"
        return [dict(r) for r in self._read().execute(q, args).fetchall()]

    def list_records(self, record_type: str | None = None) -> list[dict]:
        q = "SELECT id, record_type, name, source_path, source_note, created_at FROM wa_records"
        args: list = []
        if record_type:
            q += " WHERE record_type=?"
            args.append(record_type)
        q += " ORDER BY record_type, name"
        return [dict(r) for r in self._read().execute(q, args).fetchall()]

    def get_record(self, record_id: str) -> dict | None:
        row = self._read().execute("SELECT * FROM wa_records WHERE id=?", (record_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        with contextlib.suppress(Exception):
            d["payload"] = json.loads(d["payload"])
        return d

    def list_proposals(
        self, status: str | None = None, classification: str | None = None
    ) -> list[dict]:
        q = "SELECT * FROM wa_canon_proposals WHERE 1=1"
        args: list = []
        if status:
            q += " AND status=?"
            args.append(status)
        if classification:
            q += " AND classification=?"
            args.append(classification)
        q += " ORDER BY source_path, source_location"
        return [dict(r) for r in self._read().execute(q, args).fetchall()]

    def decide_proposal(self, proposal_id: str, status: str) -> dict | None:
        """Author ratification: atomically move proposed → approved/rejected.

        Conditional UPDATE claims the row; a decided proposal can be
        re-decided (author may change their mind) but the transition is
        always explicit and audited by the returned row.
        """
        if status not in ("approved", "rejected", "proposed"):
            raise ValueError(f"invalid proposal status {status!r}")
        with self._lock():
            c = self._conn()
            cur = c.execute(
                "UPDATE wa_canon_proposals SET status=?, decided_at=? WHERE id=?",
                (status, self._now() if status != "proposed" else None, proposal_id),
            )
            c.commit()
            if cur.rowcount == 0:
                return None
            row = c.execute(
                "SELECT * FROM wa_canon_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
        return dict(row) if row else None

    # ── Coverage ──────────────────────────────────────────────────────────────

    def coverage(self) -> dict:
        """Aggregate coverage: every archive doc accounted for, by disposition."""
        conn = self._read()
        by_status = {
            r["status"]: r["n"]
            for r in conn.execute(
                "SELECT status, COUNT(*) AS n FROM wa_archive_docs GROUP BY status"
            ).fetchall()
        }
        by_layer = [
            dict(r)
            for r in conn.execute(
                """SELECT layer, status, COUNT(*) AS n FROM wa_archive_docs
                   GROUP BY layer, status ORDER BY layer, status"""
            ).fetchall()
        ]
        records_by_type = {
            r["record_type"]: r["n"]
            for r in conn.execute(
                "SELECT record_type, COUNT(*) AS n FROM wa_records GROUP BY record_type"
            ).fetchall()
        }
        proposals_by = {
            f"{r['classification']}/{r['status']}": r["n"]
            for r in conn.execute(
                """SELECT classification, status, COUNT(*) AS n
                   FROM wa_canon_proposals GROUP BY classification, status"""
            ).fetchall()
        }
        total = sum(by_status.values())
        return {
            "total_docs": total,
            "by_status": by_status,
            "by_layer": by_layer,
            "records_by_type": records_by_type,
            "proposals": proposals_by,
            "fully_accounted": total > 0
            and total == sum(by_status.get(s, 0) for s in ("extracted", "deduped", "deferred")),
        }
