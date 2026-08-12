"""Collections, canon domains, and safe book conversions.

Three DISTINCT layers on top of the merged series model:

- Collection (``book_collection``) — a reader/production family: branding,
  metadata, style inheritance.  It holds whole series AND standalone books
  (many-to-many).  NOT the provenance ``collection`` table (v144), which is
  import bookkeeping and may never become a subject.
- Canon domain (``canon_domain``) — a shared universe / evidence domain.
  It can serve one series, multiple series, a whole collection, or a
  single standalone book.  Facts scoped to a domain bind every served book
  via the single FACT_VISIBILITY_SQL clause in canon_store.py.
- Series — unchanged: strict ordered membership, `volume` is the authority
  order canon flows along.  Chronology/publication orders are separate,
  descriptive columns (never overloaded onto volume).

Conversions (standalone→series, link-to-collection, canon promotion) are
recommend-only, per-item approved, ledgered in ``conversion_ledger`` with
enough payload to reverse explicitly.  Nothing is silently merged,
promoted, or deleted.

Kept separate from db.py like CanonStore/SeriesStore.  All methods accept
the main OrivellumDB instance and operate on its connection/lock.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from orivellum.database.canon_store import CanonFactError, CanonStore
from orivellum.database.series_store import SeriesStore

logger = logging.getLogger("orivellum.structure.store")

COLLECTION_TYPES = (
    "branded-theme",
    "shared-universe",
    "anthology",
    "educational",
    "author-backlist",
    "other",
)
COLLECTION_STATUSES = ("concept", "active", "paused", "archived")
DOMAIN_TYPES = ("fictional", "historical", "biblical", "mixed", "research")
RELATIONSHIP_TYPES = ("volume", "prequel", "sequel", "novella", "companion", "side-story")


class StructureError(ValueError):
    """A collection/domain/conversion operation violated the rules."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ─── Collections (book_collection — reader/production families) ──────────────


class CollectionStore:
    def __init__(self, db: Any) -> None:
        self._db = db

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create(
        self,
        *,
        title: str,
        description: str = "",
        collection_type: str = "branded-theme",
        reader_promise: str = "",
        actor: str = "author",
    ) -> dict:
        title = (title or "").strip()
        if not title:
            raise StructureError("A collection needs a title.")
        if collection_type not in COLLECTION_TYPES:
            raise StructureError(
                f"collection_type must be one of {', '.join(COLLECTION_TYPES)}"
            )
        cid = str(uuid.uuid4())
        now = _now()
        db = self._db
        with db.governed_write(
            operation="collection.created",
            event_type="collection.created",
            object_id=cid,
            object_type="book_collection",
            actor=actor,
            detail=title[:80],
        ):
            db._conn.execute(
                """INSERT INTO book_collection
                   (id, title, description, collection_type, reader_promise,
                    created_by, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    cid,
                    title,
                    (description or "").strip(),
                    collection_type,
                    (reader_promise or "").strip(),
                    actor,
                    now,
                    now,
                ),
            )
        return self.get(cid)  # type: ignore[return-value]

    def update(
        self,
        collection_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        collection_type: str | None = None,
        status: str | None = None,
        reader_promise: str | None = None,
        actor: str = "author",
    ) -> dict | None:
        if not self._exists(collection_id):
            return None
        sets, args = ["updated_at=?"], [_now()]
        if title is not None:
            if not title.strip():
                raise StructureError("A collection title cannot be empty.")
            sets.append("title=?")
            args.append(title.strip())
        if description is not None:
            sets.append("description=?")
            args.append(description.strip())
        if collection_type is not None:
            if collection_type not in COLLECTION_TYPES:
                raise StructureError(
                    f"collection_type must be one of {', '.join(COLLECTION_TYPES)}"
                )
            sets.append("collection_type=?")
            args.append(collection_type)
        if status is not None:
            if status not in COLLECTION_STATUSES:
                raise StructureError(
                    f"status must be one of {', '.join(COLLECTION_STATUSES)}"
                )
            sets.append("status=?")
            args.append(status)
        if reader_promise is not None:
            sets.append("reader_promise=?")
            args.append(reader_promise.strip())
        args.append(collection_id)
        db = self._db
        with db.governed_write(
            operation="collection.updated",
            event_type="collection.updated",
            object_id=collection_id,
            object_type="book_collection",
            actor=actor,
        ):
            db._conn.execute(
                f"UPDATE book_collection SET {', '.join(sets)} WHERE id=?", args
            )
        return self.get(collection_id)

    def delete(self, collection_id: str, *, actor: str = "author") -> str:
        """Delete a collection.  Returns 'ok' | 'not_found' | 'in_domain'.

        Refused while a canon domain lists this collection as a member —
        removing it would silently change which books domain facts bind.
        Membership rows cascade; series and books themselves are untouched
        (a collection is a grouping, never an owner).
        """
        db = self._db
        if not self._exists(collection_id):
            return "not_found"
        conn = db.read_conn()
        row = conn.execute(
            "SELECT 1 FROM canon_domain_member "
            "WHERE member_kind='collection' AND member_id=? LIMIT 1",
            (collection_id,),
        ).fetchone()
        if row:
            return "in_domain"
        with db.governed_write(
            operation="collection.deleted",
            event_type="collection.deleted",
            object_id=collection_id,
            object_type="book_collection",
            actor=actor,
        ):
            db._conn.execute(
                "DELETE FROM book_collection_member WHERE collection_id=?",
                (collection_id,),
            )
            db._conn.execute("DELETE FROM book_collection WHERE id=?", (collection_id,))
        return "ok"

    def get(self, collection_id: str) -> dict | None:
        conn = self._db.read_conn()
        row = conn.execute(
            "SELECT * FROM book_collection WHERE id=?", (collection_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["members"] = self.list_members(collection_id)
        d["domains"] = [
            dict(r)
            for r in conn.execute(
                """SELECT cd.id, cd.title, cd.domain_type FROM canon_domain cd
                   JOIN canon_domain_member dm ON dm.domain_id = cd.id
                   WHERE dm.member_kind='collection' AND dm.member_id=?""",
                (collection_id,),
            ).fetchall()
        ]
        return d

    def list(self) -> list[dict]:
        conn = self._db.read_conn()
        rows = conn.execute(
            """SELECT c.*,
                      (SELECT COUNT(*) FROM book_collection_member m
                        WHERE m.collection_id = c.id AND m.member_kind='series')
                        AS series_count,
                      (SELECT COUNT(*) FROM book_collection_member m
                        WHERE m.collection_id = c.id AND m.member_kind='work')
                        AS work_count
               FROM book_collection c ORDER BY c.created_at""",
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Membership ────────────────────────────────────────────────────────────

    def list_members(self, collection_id: str) -> list[dict]:
        conn = self._db.read_conn()
        out: list[dict] = []
        for r in conn.execute(
            "SELECT * FROM book_collection_member WHERE collection_id=? "
            "ORDER BY position, added_at",
            (collection_id,),
        ).fetchall():
            m = dict(r)
            if m["member_kind"] == "series":
                s = conn.execute(
                    "SELECT title FROM series WHERE id=?", (m["member_id"],)
                ).fetchone()
                m["title"] = s["title"] if s else "(deleted series)"
                m["book_count"] = int(
                    conn.execute(
                        "SELECT COUNT(*) AS n FROM series_member WHERE series_id=?",
                        (m["member_id"],),
                    ).fetchone()["n"]
                )
            else:
                w = conn.execute(
                    "SELECT title FROM works WHERE id=?", (m["member_id"],)
                ).fetchone()
                m["title"] = w["title"] if w else "(deleted work)"
            out.append(m)
        return out

    def _binding_domains(self, conn: Any, collection_id: str) -> list[dict]:
        """Domains that serve this collection AND have active facts —
        membership edits here change which books that canon binds."""
        return [
            dict(r)
            for r in conn.execute(
                """SELECT d.id, d.title,
                          (SELECT COUNT(*) FROM canon_fact f
                            WHERE f.domain_id = d.id AND f.status='active') AS n
                   FROM canon_domain d
                   JOIN canon_domain_member dm ON dm.domain_id = d.id
                   WHERE dm.member_kind='collection' AND dm.member_id=?
                     AND EXISTS (SELECT 1 FROM canon_fact f
                                  WHERE f.domain_id = d.id AND f.status='active')""",
                (collection_id,),
            ).fetchall()
        ]

    @staticmethod
    def _served_without_collection(
        conn: Any, domain_id: str, member_kind: str, member_id: str, collection_id: str
    ) -> bool:
        """Would the member still be served by this domain if it left the
        given collection?  Checks every remaining path: direct work/series
        membership in the domain, and every OTHER collection the domain
        serves that still contains the member."""
        direct = conn.execute(
            "SELECT 1 FROM canon_domain_member "
            "WHERE domain_id=? AND member_kind=? AND member_id=?",
            (domain_id, member_kind, member_id),
        ).fetchone()
        if direct:
            return True
        via_other = conn.execute(
            """SELECT 1 FROM canon_domain_member dm
               JOIN book_collection_member bcm
                 ON bcm.collection_id = dm.member_id
               WHERE dm.domain_id=? AND dm.member_kind='collection'
                 AND dm.member_id != ?
                 AND bcm.member_kind=? AND bcm.member_id=?""",
            (domain_id, collection_id, member_kind, member_id),
        ).fetchone()
        return via_other is not None

    def add_member(
        self,
        collection_id: str,
        *,
        member_kind: str,
        member_id: str,
        position: int = 0,
        actor: str = "author",
        confirm_canon_binding: bool = False,
    ) -> dict:
        db = self._db
        if not self._exists(collection_id):
            raise StructureError(f"Collection {collection_id!r} not found.")
        if member_kind not in ("series", "work"):
            raise StructureError("member_kind must be 'series' or 'work'.")
        conn = db.read_conn()
        if member_kind == "series":
            if not conn.execute(
                "SELECT 1 FROM series WHERE id=?", (member_id,)
            ).fetchone():
                raise StructureError(f"Series {member_id!r} not found.")
        else:
            if not db.get_work(member_id):
                raise StructureError(f"Work {member_id!r} not found.")
            # The provenance `collection` table shares no ids with works, but
            # keep the invariant loud: import provenance is never a subject.
            db.assert_not_collection(member_id, "join a book collection")
        dup = conn.execute(
            "SELECT 1 FROM book_collection_member "
            "WHERE collection_id=? AND member_kind=? AND member_id=?",
            (collection_id, member_kind, member_id),
        ).fetchone()
        if dup:
            raise StructureError("Already a member of this collection.")
        # Joining a collection that a fact-bearing canon domain serves is NOT
        # branding-only — the domain's facts immediately bind the new member's
        # books.  Never do that silently: require explicit confirmation.
        binding = self._binding_domains(conn, collection_id)
        newly_bound = [
            d
            for d in binding
            if not conn.execute(
                "SELECT 1 FROM canon_domain_member "
                "WHERE domain_id=? AND member_kind=? AND member_id=?",
                (d["id"], member_kind, member_id),
            ).fetchone()
        ]
        if newly_bound and not confirm_canon_binding:
            names = ", ".join(f"{d['title']!r} ({d['n']} fact(s))" for d in newly_bound)
            raise StructureError(
                "Joining this collection would bind shared canon: domain(s) "
                f"{names} serve it. Confirm the canon binding explicitly "
                "(confirm_canon_binding) after previewing, or add the member "
                "to a collection no domain serves."
            )
        with db.governed_write(
            operation="collection.member_added",
            event_type="collection.member_added",
            object_id=collection_id,
            object_type="book_collection",
            actor=actor,
            detail=f"{member_kind}={member_id}",
        ):
            db._conn.execute(
                """INSERT INTO book_collection_member
                   (collection_id, member_kind, member_id, position, added_by, added_at)
                   VALUES(?,?,?,?,?,?)""",
                (collection_id, member_kind, member_id, int(position), actor, _now()),
            )
        return self.get(collection_id)  # type: ignore[return-value]

    def remove_member(
        self,
        collection_id: str,
        *,
        member_kind: str,
        member_id: str,
        actor: str = "author",
    ) -> bool:
        """Remove a member.  Refused when the member is only reachable by a
        canon domain THROUGH this collection and domain facts exist — the
        removal would silently unbind established canon."""
        db = self._db
        conn = db.read_conn()
        # EVERY fact-bearing domain serving this collection must keep an
        # independent path to the member, or removal silently unbinds canon.
        for d in self._binding_domains(conn, collection_id):
            if not self._served_without_collection(
                conn, d["id"], member_kind, member_id, collection_id
            ):
                raise StructureError(
                    f"Refused: canon domain {d['title']!r} serves this member "
                    "only through this collection and has established facts — "
                    "removing it here would silently unbind that canon. Add "
                    "the member to the domain directly first, or retract the "
                    "domain's facts."
                )
        with db.governed_write(
            operation="collection.member_removed",
            event_type="collection.member_removed",
            object_id=collection_id,
            object_type="book_collection",
            actor=actor,
            detail=f"{member_kind}={member_id}",
        ):
            cur = db._conn.execute(
                "DELETE FROM book_collection_member "
                "WHERE collection_id=? AND member_kind=? AND member_id=?",
                (collection_id, member_kind, member_id),
            )
            return cur.rowcount > 0

    def collections_for_work(self, work_id: str) -> list[dict]:
        """Collections holding this Work directly OR via its series."""
        conn = self._db.read_conn()
        rows = conn.execute(
            """SELECT DISTINCT c.*, m.member_kind AS via
               FROM book_collection c
               JOIN book_collection_member m ON m.collection_id = c.id
               WHERE (m.member_kind='work' AND m.member_id=?)
                  OR (m.member_kind='series' AND m.member_id IN (
                       SELECT series_id FROM series_member WHERE work_id=?))
               ORDER BY c.created_at""",
            (work_id, work_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def _exists(self, collection_id: str) -> bool:
        return (
            self._db.read_conn()
            .execute("SELECT 1 FROM book_collection WHERE id=?", (collection_id,))
            .fetchone()
            is not None
        )


# ─── Canon domains (shared universes / evidence domains) ─────────────────────


class DomainStore:
    def __init__(self, db: Any) -> None:
        self._db = db

    def create(
        self,
        *,
        title: str,
        description: str = "",
        domain_type: str = "fictional",
        actor: str = "author",
    ) -> dict:
        title = (title or "").strip()
        if not title:
            raise StructureError("A canon domain needs a title.")
        if domain_type not in DOMAIN_TYPES:
            raise StructureError(f"domain_type must be one of {', '.join(DOMAIN_TYPES)}")
        did = str(uuid.uuid4())
        now = _now()
        db = self._db
        with db.governed_write(
            operation="canon_domain.created",
            event_type="canon_domain.created",
            object_id=did,
            object_type="canon_domain",
            actor=actor,
            detail=title[:80],
        ):
            db._conn.execute(
                """INSERT INTO canon_domain
                   (id, title, description, domain_type, created_by,
                    created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (did, title, (description or "").strip(), domain_type, actor, now, now),
            )
        return self.get(did)  # type: ignore[return-value]

    def update(
        self,
        domain_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        actor: str = "author",
    ) -> dict | None:
        if not self._exists(domain_id):
            return None
        sets, args = ["updated_at=?"], [_now()]
        if title is not None:
            if not title.strip():
                raise StructureError("A domain title cannot be empty.")
            sets.append("title=?")
            args.append(title.strip())
        if description is not None:
            sets.append("description=?")
            args.append(description.strip())
        args.append(domain_id)
        db = self._db
        with db.governed_write(
            operation="canon_domain.updated",
            event_type="canon_domain.updated",
            object_id=domain_id,
            object_type="canon_domain",
            actor=actor,
        ):
            db._conn.execute(f"UPDATE canon_domain SET {', '.join(sets)} WHERE id=?", args)
        return self.get(domain_id)

    def delete(self, domain_id: str, *, actor: str = "author") -> str:
        """Returns 'ok' | 'not_found' | 'has_canon'.  Refused while active
        domain-scoped facts exist — authority never loses scope silently."""
        db = self._db
        if not self._exists(domain_id):
            return "not_found"
        n = (
            db.read_conn()
            .execute(
                "SELECT COUNT(*) AS n FROM canon_fact "
                "WHERE domain_id=? AND status='active'",
                (domain_id,),
            )
            .fetchone()["n"]
        )
        if int(n):
            return "has_canon"
        with db.governed_write(
            operation="canon_domain.deleted",
            event_type="canon_domain.deleted",
            object_id=domain_id,
            object_type="canon_domain",
            actor=actor,
        ):
            db._conn.execute("DELETE FROM canon_domain_member WHERE domain_id=?", (domain_id,))
            db._conn.execute(
                "UPDATE canon_fact SET domain_id=NULL WHERE domain_id=? AND status != 'active'",
                (domain_id,),
            )
            db._conn.execute("DELETE FROM canon_domain WHERE id=?", (domain_id,))
        return "ok"

    def get(self, domain_id: str) -> dict | None:
        conn = self._db.read_conn()
        row = conn.execute("SELECT * FROM canon_domain WHERE id=?", (domain_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["members"] = self.list_members(domain_id)
        d["fact_count"] = int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM canon_fact WHERE domain_id=? AND status='active'",
                (domain_id,),
            ).fetchone()["n"]
        )
        return d

    def list(self) -> list[dict]:
        conn = self._db.read_conn()
        rows = conn.execute(
            """SELECT d.*,
                      (SELECT COUNT(*) FROM canon_domain_member m
                        WHERE m.domain_id = d.id) AS member_count,
                      (SELECT COUNT(*) FROM canon_fact f
                        WHERE f.domain_id = d.id AND f.status='active') AS fact_count
               FROM canon_domain d ORDER BY d.created_at""",
        ).fetchall()
        return [dict(r) for r in rows]

    def list_members(self, domain_id: str) -> list[dict]:
        conn = self._db.read_conn()
        out: list[dict] = []
        for r in conn.execute(
            "SELECT * FROM canon_domain_member WHERE domain_id=? ORDER BY added_at",
            (domain_id,),
        ).fetchall():
            m = dict(r)
            table = {"series": "series", "work": "works", "collection": "book_collection"}[
                m["member_kind"]
            ]
            row = conn.execute(
                f"SELECT title FROM {table} WHERE id=?", (m["member_id"],)
            ).fetchone()
            m["title"] = row["title"] if row else f"(deleted {m['member_kind']})"
            out.append(m)
        return out

    def add_member(
        self, domain_id: str, *, member_kind: str, member_id: str, actor: str = "author"
    ) -> dict:
        db = self._db
        if not self._exists(domain_id):
            raise StructureError(f"Canon domain {domain_id!r} not found.")
        if member_kind not in ("series", "work", "collection"):
            raise StructureError("member_kind must be 'series', 'work', or 'collection'.")
        conn = db.read_conn()
        table = {"series": "series", "work": "works", "collection": "book_collection"}[
            member_kind
        ]
        if not conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (member_id,)).fetchone():
            raise StructureError(f"{member_kind} {member_id!r} not found.")
        if member_kind == "work":
            db.assert_not_collection(member_id, "join a canon domain")
        dup = conn.execute(
            "SELECT 1 FROM canon_domain_member "
            "WHERE domain_id=? AND member_kind=? AND member_id=?",
            (domain_id, member_kind, member_id),
        ).fetchone()
        if dup:
            raise StructureError("Already served by this domain.")
        with db.governed_write(
            operation="canon_domain.member_added",
            event_type="canon_domain.member_added",
            object_id=domain_id,
            object_type="canon_domain",
            actor=actor,
            detail=f"{member_kind}={member_id}",
        ):
            db._conn.execute(
                """INSERT INTO canon_domain_member
                   (domain_id, member_kind, member_id, added_by, added_at)
                   VALUES(?,?,?,?,?)""",
                (domain_id, member_kind, member_id, actor, _now()),
            )
        return self.get(domain_id)  # type: ignore[return-value]

    def remove_member(
        self, domain_id: str, *, member_kind: str, member_id: str, actor: str = "author"
    ) -> bool:
        """Refused while the domain has active facts — dropping a member
        would silently unbind canon those books were verified against."""
        db = self._db
        n = (
            db.read_conn()
            .execute(
                "SELECT COUNT(*) AS n FROM canon_fact "
                "WHERE domain_id=? AND status='active'",
                (domain_id,),
            )
            .fetchone()["n"]
        )
        if int(n):
            raise StructureError(
                f"Refused: this domain has {n} active fact(s) that currently "
                "bind the member's books — retract them first, or keep the "
                "membership."
            )
        with db.governed_write(
            operation="canon_domain.member_removed",
            event_type="canon_domain.member_removed",
            object_id=domain_id,
            object_type="canon_domain",
            actor=actor,
            detail=f"{member_kind}={member_id}",
        ):
            cur = db._conn.execute(
                "DELETE FROM canon_domain_member "
                "WHERE domain_id=? AND member_kind=? AND member_id=?",
                (domain_id, member_kind, member_id),
            )
            return cur.rowcount > 0

    def domains_for_work(self, work_id: str) -> list[dict]:
        """Domains serving this Work directly, via its series, or via a
        collection holding it or its series."""
        conn = self._db.read_conn()
        rows = conn.execute(
            """SELECT DISTINCT d.* FROM canon_domain d
               JOIN canon_domain_member dm ON dm.domain_id = d.id
               WHERE (dm.member_kind='work' AND dm.member_id=?)
                  OR (dm.member_kind='series' AND dm.member_id IN (
                       SELECT series_id FROM series_member WHERE work_id=?))
                  OR (dm.member_kind='collection' AND dm.member_id IN (
                       SELECT bcm.collection_id FROM book_collection_member bcm
                       WHERE (bcm.member_kind='work' AND bcm.member_id=?)
                          OR (bcm.member_kind='series' AND bcm.member_id IN (
                               SELECT series_id FROM series_member WHERE work_id=?))))
               ORDER BY d.created_at""",
            (work_id, work_id, work_id, work_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def _exists(self, domain_id: str) -> bool:
        return (
            self._db.read_conn()
            .execute("SELECT 1 FROM canon_domain WHERE id=?", (domain_id,))
            .fetchone()
            is not None
        )


# ─── Classification recommendation (deterministic — recommends, never imposes)


def recommend_classification(answers: dict) -> dict:
    """Deterministic New-Book classification recommendation.

    ``answers`` keys (all optional):
      recurring_cast: bool — reuses characters/world from existing books
      unresolved_arc: bool — a dramatic question continues past this book
      existing_series_id: str — the author already named a series
      before_existing: bool — the story happens BEFORE existing books
      shorter_form: bool — novella/companion length
      shared_world_only: bool — same universe but standalone story
      branding_only: bool — grouped for readers/brand, no shared facts

    Returns {"recommendation", "reasons", "alternatives"}.  The author
    always makes the final call — this only ranks the options.
    """
    reasons: list[str] = []
    rec = "standalone"
    if answers.get("existing_series_id"):
        if answers.get("before_existing"):
            rec = "prequel-novella-companion"
            reasons.append("It happens before the existing books in that series.")
        elif answers.get("shorter_form"):
            rec = "prequel-novella-companion"
            reasons.append("A shorter companion form inside an existing series.")
        else:
            rec = "next-in-series"
            reasons.append("You named an existing series it continues.")
    elif answers.get("recurring_cast") and answers.get("unresolved_arc"):
        rec = "new-series"
        reasons.append(
            "A recurring cast plus an arc that outlives this book is the "
            "classic shape of a first-in-series."
        )
    elif answers.get("shared_world_only"):
        rec = "shared-universe"
        reasons.append(
            "Same world, self-contained story — a canon domain shares the "
            "facts without forcing a reading order."
        )
    elif answers.get("branding_only"):
        rec = "collection-only"
        reasons.append(
            "Grouped for readers and branding with no shared facts — a "
            "collection membership, no canon involved."
        )
    else:
        reasons.append(
            "No recurring cast, continuing arc, or shared world was "
            "indicated — a standalone keeps every option open."
        )
    all_options = [
        "standalone",
        "new-series",
        "next-in-series",
        "prequel-novella-companion",
        "collection-only",
        "shared-universe",
    ]
    return {
        "recommendation": rec,
        "reasons": reasons,
        "alternatives": [o for o in all_options if o != rec],
    }


# ─── Conversions — ledgered, reversible, never silent ─────────────────────────


class ConversionService:
    def __init__(self, db: Any) -> None:
        self._db = db

    # ── Ledger ────────────────────────────────────────────────────────────────

    def _ledger(
        self,
        conn: Any,
        *,
        kind: str,
        subject_kind: str,
        subject_id: str,
        payload: dict,
        actor: str,
    ) -> str:
        lid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO conversion_ledger
               (id, kind, subject_kind, subject_id, payload, actor, created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (lid, kind, subject_kind, subject_id, json.dumps(payload), actor, _now()),
        )
        return lid

    def list_ledger(self, *, subject_id: str | None = None, limit: int = 200) -> list[dict]:
        conn = self._db.read_conn()
        q = "SELECT * FROM conversion_ledger"
        args: list = []
        if subject_id:
            q += " WHERE subject_id=?"
            args.append(subject_id)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 1000)))
        out = []
        for r in conn.execute(q, args).fetchall():
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"] or "{}")
            except Exception:
                d["payload"] = {}
            out.append(d)
        return out

    # ── Standalone → series ───────────────────────────────────────────────────

    def convert_standalone_to_series(
        self,
        work_id: str,
        *,
        series_id: str | None = None,
        series_title: str | None = None,
        volume: int = 1,
        actor: str = "author",
    ) -> dict:
        """Make a standalone Work a series member — nothing else changes.

        The Work keeps every ID, version, note, review, and release link:
        the conversion is ONE membership row (plus the series row when a
        new series is created).  No canon is promoted — book-scoped facts
        stay book-scoped until explicitly promoted per item.  Ledgered and
        reversible.
        """
        db = self._db
        if not db.get_work(work_id):
            raise StructureError(f"Work {work_id!r} not found.")
        store = SeriesStore(db)
        if store.series_for_work(work_id):
            raise StructureError(
                "Refused: this Work already belongs to a series — a Work can "
                "be in at most one series."
            )
        created_series = False
        if series_id is None and not (series_title or "").strip():
            raise StructureError("Pass series_id or a series_title for the new series.")
        # ONE transaction: series creation, membership, and ledger entry all
        # commit together or not at all — a conversion is never half-recorded.
        with db.atomic():
            if series_id is None:
                series = store.create_series(title=series_title or "", actor=actor)
                series_id = series["id"]
                created_series = True
            store.add_member(series_id, work_id, volume=volume, actor=actor)
            with db.governed_write(
                operation="conversion.standalone_to_series",
                event_type="conversion.standalone_to_series",
                object_id=work_id,
                object_type="work",
                actor=actor,
                detail=f"series={series_id} volume={volume}",
            ):
                lid = self._ledger(
                    db._conn,
                    kind="standalone_to_series",
                    subject_kind="work",
                    subject_id=work_id,
                    payload={
                        "series_id": series_id,
                        "volume": int(volume),
                        "created_series": created_series,
                    },
                    actor=actor,
                )
        return {
            "ledger_id": lid,
            "series": store.get_series(series_id),
            "created_series": created_series,
        }

    # ── Link previews (read-only — nothing changes until approved) ────────────

    def link_preview(
        self,
        work_id: str,
        *,
        series_id: str | None = None,
        collection_id: str | None = None,
        domain_id: str | None = None,
    ) -> dict:
        """What WOULD change if this Work joined the given series /
        collection / domain.  Pure read — an explicit merge preview."""
        db = self._db
        if not db.get_work(work_id):
            raise StructureError(f"Work {work_id!r} not found.")
        conn = db.read_conn()
        out: dict = {"work_id": work_id, "conflicts": [], "gained_facts": [], "notes": []}
        own = conn.execute(
            "SELECT COUNT(*) AS n FROM canon_fact WHERE work_id=? AND status='active'",
            (work_id,),
        ).fetchone()
        out["own_fact_count"] = int(own["n"])
        gained_rows: list = []
        if series_id:
            if not conn.execute("SELECT 1 FROM series WHERE id=?", (series_id,)).fetchone():
                raise StructureError(f"Series {series_id!r} not found.")
            existing = SeriesStore(db).series_for_work(work_id)
            if existing:
                out["conflicts"].append(
                    f"This Work already belongs to series {existing['series_title']!r} — "
                    "a Work can be in at most one series."
                )
            members = SeriesStore(db).list_members(series_id)
            next_volume = max((m["volume"] for m in members), default=0) + 1
            out["proposed_volume"] = next_volume
            gained_rows = conn.execute(
                """SELECT id, statement, classification FROM canon_fact
                   WHERE status='active' AND (
                     series_id=? OR work_id IN (
                       SELECT work_id FROM series_member WHERE series_id=?))
                   ORDER BY created_at DESC LIMIT 200""",
                (series_id, series_id),
            ).fetchall()
            if out["own_fact_count"] and members:
                out["notes"].append(
                    f"This Work has {out['own_fact_count']} book-scoped fact(s). "
                    "They stay book-local; nothing is promoted to series canon "
                    "without per-item approval."
                )
        if collection_id:
            if not conn.execute(
                "SELECT 1 FROM book_collection WHERE id=?", (collection_id,)
            ).fetchone():
                raise StructureError(f"Collection {collection_id!r} not found.")
            out["notes"].append(
                "Collection membership is branding/production grouping only — "
                "no canon or reading order changes."
            )
            gained_rows = gained_rows or conn.execute(
                """SELECT f.id, f.statement, f.classification FROM canon_fact f
                   JOIN canon_domain_member dm
                     ON dm.member_kind='collection' AND dm.member_id=?
                    AND f.domain_id = dm.domain_id
                   WHERE f.status='active'
                   ORDER BY f.created_at DESC LIMIT 200""",
                (collection_id,),
            ).fetchall()
        if domain_id:
            if not conn.execute(
                "SELECT 1 FROM canon_domain WHERE id=?", (domain_id,)
            ).fetchone():
                raise StructureError(f"Canon domain {domain_id!r} not found.")
            gained_rows = conn.execute(
                "SELECT id, statement, classification FROM canon_fact "
                "WHERE domain_id=? AND status='active' "
                "ORDER BY created_at DESC LIMIT 200",
                (domain_id,),
            ).fetchall()
        out["gained_facts"] = [dict(r) for r in gained_rows]
        out["gained_fact_count"] = len(out["gained_facts"])
        return out

    # ── Canon promotion (per-item, retract-then-establish) ────────────────────

    def promote_facts(
        self,
        fact_ids: list[str],
        *,
        target_series_id: str | None = None,
        target_domain_id: str | None = None,
        signed_by: str = "",
    ) -> dict:
        """Promote book-scoped facts to series or domain scope, one by one.

        Rescoping is NEVER a supersede (supersede keeps scope): each
        promotion retracts the book fact and establishes a new fact at the
        broader scope in ONE governed transaction, ledgered per item.
        Every item is individually approved — a failed item refuses without
        touching the rest.
        """
        if bool(target_series_id) == bool(target_domain_id):
            raise StructureError("Pass exactly one of target_series_id / target_domain_id.")
        if not signed_by.strip():
            raise StructureError("Canon promotion requires an author signature.")
        db = self._db
        canon = CanonStore(db)
        results: list[dict] = []
        for fid in fact_ids:
            fact = canon.get_fact(fid)
            if not fact:
                results.append({"fact_id": fid, "result": "not_found"})
                continue
            if fact["status"] != "active":
                results.append(
                    {"fact_id": fid, "result": "refused", "reason": f"fact is {fact['status']}"}
                )
                continue
            if not fact["work_id"]:
                results.append(
                    {
                        "fact_id": fid,
                        "result": "refused",
                        "reason": "only book-scoped facts can be promoted",
                    }
                )
                continue
            if fact.get("overrides"):
                results.append(
                    {
                        "fact_id": fid,
                        "result": "refused",
                        "reason": "an override is this book's departure — it never becomes shared canon",
                    }
                )
                continue
            try:
                new_id = str(uuid.uuid4())
                with db.governed_write(
                    operation="conversion.canon_promoted",
                    event_type="conversion.canon_promoted",
                    object_id=fid,
                    object_type="canon_fact",
                    actor=signed_by.strip(),
                    detail=(
                        f"series={target_series_id}"
                        if target_series_id
                        else f"domain={target_domain_id}"
                    ),
                ):
                    conn = db._conn
                    canon._check_series_scope(
                        conn,
                        work_id=None,
                        series_id=target_series_id,
                        overrides=None,
                        domain_id=target_domain_id,
                    )
                    if target_series_id:
                        member = conn.execute(
                            "SELECT 1 FROM series_member WHERE series_id=? AND work_id=?",
                            (target_series_id, fact["work_id"]),
                        ).fetchone()
                        if not member:
                            raise StructureError(
                                "Refused: the fact's book is not a member of the "
                                "target series."
                            )
                    else:
                        from orivellum.database.canon_store import domain_serves_work

                        if not domain_serves_work(conn, target_domain_id, fact["work_id"]):
                            raise StructureError(
                                "Refused: the fact's book is not served by the "
                                "target canon domain."
                            )
                    cur = conn.execute(
                        "UPDATE canon_fact SET status='retracted', retracted_by=?, "
                        "retracted_at=? WHERE id=? AND status='active'",
                        (signed_by.strip(), _now(), fid),
                    )
                    if cur.rowcount == 0:
                        raise StructureError("Refused: the fact is no longer active.")
                    canon._insert_row(
                        conn,
                        new_id,
                        work_id=None,
                        statement=fact["statement"],
                        classification=fact["classification"],
                        source_ref=fact["source_ref"],
                        parent_ids=fact["parent_ids"],
                        signed_by=signed_by,
                        origin="promotion",
                        series_id=target_series_id,
                        domain_id=target_domain_id,
                    )
                    self._ledger(
                        conn,
                        kind="canon_promoted",
                        subject_kind="canon_fact",
                        subject_id=fid,
                        payload={
                            "new_fact_id": new_id,
                            "from_work_id": fact["work_id"],
                            "target_series_id": target_series_id,
                            "target_domain_id": target_domain_id,
                        },
                        actor=signed_by.strip(),
                    )
                results.append({"fact_id": fid, "result": "ok", "new_fact_id": new_id})
            except (StructureError, CanonFactError) as e:
                results.append({"fact_id": fid, "result": "refused", "reason": str(e)})
        return {
            "promoted": sum(1 for r in results if r["result"] == "ok"),
            "refused": sum(1 for r in results if r["result"] != "ok"),
            "results": results,
        }

    # ── Reversal ──────────────────────────────────────────────────────────────

    def reverse(self, ledger_id: str, *, actor: str = "author") -> dict:
        """Reverse a ledgered conversion explicitly.

        - standalone_to_series: remove the membership (guarded by the same
          continuity rules as any removal) and delete the series if this
          conversion created it and it is now empty.
        - canon_promoted: retract the promoted shared fact and re-establish
          the statement at the original book scope (a NEW fact id — history
          is never rewritten).
        """
        db = self._db
        conn = db.read_conn()
        row = conn.execute(
            "SELECT * FROM conversion_ledger WHERE id=?", (ledger_id,)
        ).fetchone()
        if not row:
            raise StructureError(f"Ledger entry {ledger_id!r} not found.")
        entry = dict(row)
        if entry["reversed_by"]:
            raise StructureError("This conversion was already reversed.")
        try:
            payload = json.loads(entry["payload"] or "{}")
        except Exception:
            payload = {}
        kind = entry["kind"]
        if kind == "standalone_to_series":
            store = SeriesStore(db)
            series_id = payload.get("series_id")
            work_id = entry["subject_id"]
            # ONE transaction: removal, series cleanup, and the reversal mark
            # commit together — a reversal is never half-applied.
            with db.atomic():
                removed = store.remove_member(series_id, work_id, actor=actor)
                if not removed:
                    raise StructureError(
                        "Refused: the Work is no longer a member of that series — "
                        "nothing to reverse."
                    )
                deleted_series = False
                if payload.get("created_series") and not store.list_members(series_id):
                    deleted_series = store.delete_series(series_id, actor=actor) == "ok"
                self._mark_reversed(ledger_id, actor)
            return {"result": "ok", "deleted_series": deleted_series}
        if kind == "canon_promoted":
            canon = CanonStore(db)
            new_fid = payload.get("new_fact_id") or ""
            promoted = canon.get_fact(new_fid)
            if not promoted or promoted["status"] != "active":
                raise StructureError(
                    "Refused: the promoted fact is no longer active — reverse "
                    "manually via retract/establish."
                )
            # ONE transaction: if re-establishing the book fact fails (e.g. an
            # INFERRED parent was retracted since), the retraction rolls back
            # too — canon is never lost to a half-finished reversal.
            with db.atomic():
                r = canon.retract_fact(new_fid, signed_by=actor, reason=f"reversal of {ledger_id}")
                if r != "ok":
                    raise StructureError(f"Could not retract the promoted fact ({r}).")
                restored = canon.create_fact(
                    statement=promoted["statement"],
                    classification=promoted["classification"],
                    work_id=payload.get("from_work_id"),
                    source_ref=promoted["source_ref"],
                    parent_ids=promoted["parent_ids"],
                    signed_by=actor,
                    origin="promotion_reversal",
                )
                self._mark_reversed(ledger_id, actor)
            return {"result": "ok", "restored_fact_id": restored["id"]}
        raise StructureError(f"Conversions of kind {kind!r} cannot be auto-reversed.")

    def _mark_reversed(self, ledger_id: str, actor: str) -> None:
        db = self._db
        with db.governed_write(
            operation="conversion.reversed",
            event_type="conversion.reversed",
            object_id=ledger_id,
            object_type="conversion_ledger",
            actor=actor,
        ):
            db._conn.execute(
                "UPDATE conversion_ledger SET reversed_by=?, reversed_at=? "
                "WHERE id=? AND reversed_by IS NULL",
                (actor, _now(), ledger_id),
            )
