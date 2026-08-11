"""Series storage — ordered groups of Works (M18).

A series groups Works in reading order (volume 1, 2, 3 …).  It is the
scope that lets canon, voice, personas, and continuity span a trilogy:

- A Work belongs to AT MOST ONE series (unique index on work_id), so
  authority resolution is never ambiguous.
- Volumes are 1-based and unique within a series — the order IS the
  continuity order: book N is verified against the accumulated state of
  volumes 1..N-1, never the reverse.
- Deleting a series that still carries series-scoped canon is refused —
  authority records never lose their scope silently.

Kept separate from db.py like CanonStore/MailStore.  All methods accept
the main OrivellumDB instance and operate on its connection/lock.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("orivellum.series.store")


class SeriesError(ValueError):
    """A series operation violated the membership/authority rules."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SeriesStore:
    def __init__(self, db: Any) -> None:
        self._db = db

    # ── Series CRUD ────────────────────────────────────────────────────────────

    def create_series(self, *, title: str, description: str = "", actor: str = "author") -> dict:
        title = (title or "").strip()
        if not title:
            raise SeriesError("A series needs a title.")
        sid = str(uuid.uuid4())
        now = _now()
        db = self._db
        with db.governed_write(
            operation="series.created",
            event_type="series.created",
            object_id=sid,
            object_type="series",
            actor=actor,
            detail=title[:80],
        ):
            db._conn.execute(
                "INSERT INTO series(id, title, description, created_at, updated_at) "
                "VALUES(?,?,?,?,?)",
                (sid, title, (description or "").strip(), now, now),
            )
        return self.get_series(sid)  # type: ignore[return-value]

    def update_series(
        self,
        series_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        actor: str = "author",
    ) -> dict | None:
        if not self._exists(series_id):
            return None
        sets, args = ["updated_at=?"], [_now()]
        if title is not None:
            if not title.strip():
                raise SeriesError("A series title cannot be empty.")
            sets.append("title=?")
            args.append(title.strip())
        if description is not None:
            sets.append("description=?")
            args.append(description.strip())
        args.append(series_id)
        db = self._db
        with db.governed_write(
            operation="series.updated",
            event_type="series.updated",
            object_id=series_id,
            object_type="series",
            actor=actor,
        ):
            db._conn.execute(f"UPDATE series SET {', '.join(sets)} WHERE id=?", args)
        return self.get_series(series_id)

    def delete_series(self, series_id: str, *, actor: str = "author") -> str:
        """Delete a series.  Returns 'ok' | 'not_found' | 'has_canon'.

        Refused while series-scoped canon facts exist — retire or rescope
        the facts first, so authority records never dangle.
        """
        db = self._db
        conn = db.read_conn()
        if not self._exists(series_id):
            return "not_found"
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM canon_fact WHERE series_id=?", (series_id,)
        ).fetchone()
        if int(row["n"]):
            return "has_canon"
        with db.governed_write(
            operation="series.deleted",
            event_type="series.deleted",
            object_id=series_id,
            object_type="series",
            actor=actor,
        ):
            db._conn.execute("DELETE FROM series_member WHERE series_id=?", (series_id,))
            db._conn.execute("DELETE FROM series WHERE id=?", (series_id,))
        return "ok"

    # ── Reads ──────────────────────────────────────────────────────────────────

    def _exists(self, series_id: str) -> bool:
        return (
            self._db.read_conn()
            .execute("SELECT 1 FROM series WHERE id=?", (series_id,))
            .fetchone()
            is not None
        )

    def get_series(self, series_id: str) -> dict | None:
        conn = self._db.read_conn()
        row = conn.execute("SELECT * FROM series WHERE id=?", (series_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["members"] = self.list_members(series_id)
        return d

    def list_series(self) -> list[dict]:
        conn = self._db.read_conn()
        rows = conn.execute(
            """SELECT s.*, COUNT(m.id) AS member_count
               FROM series s LEFT JOIN series_member m ON m.series_id=s.id
               GROUP BY s.id ORDER BY s.created_at""",
        ).fetchall()
        return [dict(r) for r in rows]

    def list_members(self, series_id: str) -> list[dict]:
        """Members in volume order, joined with the Work's title/status."""
        conn = self._db.read_conn()
        rows = conn.execute(
            """SELECT m.work_id, m.volume, m.created_at,
                      w.title AS work_title, w.work_type, w.status AS work_status
               FROM series_member m JOIN works w ON w.id=m.work_id
               WHERE m.series_id=? ORDER BY m.volume""",
            (series_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def series_for_work(self, work_id: str) -> dict | None:
        """The series a Work belongs to (with this Work's volume), or None."""
        conn = self._db.read_conn()
        row = conn.execute(
            """SELECT s.id AS series_id, s.title AS series_title, m.volume
               FROM series_member m JOIN series s ON s.id=m.series_id
               WHERE m.work_id=?""",
            (work_id,),
        ).fetchone()
        return dict(row) if row else None

    def prior_volume_work_ids(self, work_id: str) -> list[str]:
        """Work ids of EARLIER volumes in this Work's series, in volume order.

        Empty when the Work is not in a series or is volume 1.  This is the
        continuity direction: book N inherits from 1..N-1, never the reverse.
        """
        conn = self._db.read_conn()
        rows = conn.execute(
            """SELECT m2.work_id FROM series_member m1
               JOIN series_member m2
                 ON m2.series_id=m1.series_id AND m2.volume < m1.volume
               WHERE m1.work_id=? ORDER BY m2.volume""",
            (work_id,),
        ).fetchall()
        return [r["work_id"] for r in rows]

    # ── Membership ─────────────────────────────────────────────────────────────

    def add_member(
        self, series_id: str, work_id: str, *, volume: int, actor: str = "author"
    ) -> dict:
        db = self._db
        if not self._exists(series_id):
            raise SeriesError(f"Series {series_id!r} not found.")
        if not db.get_work(work_id):
            raise SeriesError(f"Work {work_id!r} not found.")
        if int(volume) < 1:
            raise SeriesError("volume must be >= 1")
        mid = str(uuid.uuid4())
        try:
            with db.governed_write(
                operation="series.member_added",
                event_type="series.member_added",
                object_id=series_id,
                object_type="series",
                actor=actor,
                detail=f"work={work_id} volume={volume}",
            ):
                db._conn.execute(
                    "INSERT INTO series_member(id, series_id, work_id, volume, created_at) "
                    "VALUES(?,?,?,?,?)",
                    (mid, series_id, work_id, int(volume), _now()),
                )
        except sqlite3.IntegrityError as exc:
            msg = str(exc).lower()
            if "series_member.work_id" in msg and "series_member.series_id" not in msg:
                raise SeriesError(
                    "Refused: this Work already belongs to a series — a Work "
                    "can be in at most one series."
                ) from exc
            if "volume" in msg:
                raise SeriesError(
                    f"Refused: volume {volume} is already taken in this series."
                ) from exc
            raise SeriesError(
                "Refused: this Work is already a member of this series."
            ) from exc
        return self.get_series(series_id)  # type: ignore[return-value]

    def remove_member(self, series_id: str, work_id: str, *, actor: str = "author") -> bool:
        db = self._db
        with db.governed_write(
            operation="series.member_removed",
            event_type="series.member_removed",
            object_id=series_id,
            object_type="series",
            actor=actor,
            detail=f"work={work_id}",
        ):
            cur = db._conn.execute(
                "DELETE FROM series_member WHERE series_id=? AND work_id=?",
                (series_id, work_id),
            )
        return bool(cur.rowcount)

    def set_member_volume(
        self, series_id: str, work_id: str, *, volume: int, actor: str = "author"
    ) -> dict:
        if int(volume) < 1:
            raise SeriesError("volume must be >= 1")
        db = self._db
        try:
            with db.governed_write(
                operation="series.member_reordered",
                event_type="series.member_reordered",
                object_id=series_id,
                object_type="series",
                actor=actor,
                detail=f"work={work_id} volume={volume}",
            ):
                cur = db._conn.execute(
                    "UPDATE series_member SET volume=? WHERE series_id=? AND work_id=?",
                    (int(volume), series_id, work_id),
                )
                if not cur.rowcount:
                    raise SeriesError("Refused: that Work is not a member of this series.")
        except sqlite3.IntegrityError as exc:
            raise SeriesError(
                f"Refused: volume {volume} is already taken in this series."
            ) from exc
        return self.get_series(series_id)  # type: ignore[return-value]


# ── Inherited baselines (voice envelopes and friends) ─────────────────────────


def resolve_assay_baseline(db: Any, work_id: str, key: str) -> dict | None:
    """A Work's baseline, inheriting from earlier volumes of its series.

    Resolution order: the Work's own baseline (an explicit per-book
    override) wins; otherwise the NEAREST earlier volume that has one.
    Returns {"payload", "source_work_id", "inherited"} or None.  Later
    volumes never leak backward.
    """
    own = db.get_assay_baseline(work_id, key)
    if own is not None:
        return {"payload": own, "source_work_id": work_id, "inherited": False}
    for prior_id in reversed(SeriesStore(db).prior_volume_work_ids(work_id)):
        payload = db.get_assay_baseline(prior_id, key)
        if payload is not None:
            return {"payload": payload, "source_work_id": prior_id, "inherited": True}
    return None
