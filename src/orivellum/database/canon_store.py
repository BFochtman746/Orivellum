"""Canon authority storage — the classified, sourced fact substrate.

Kept separate from db.py like MailStore/WAStore.  All methods accept the
main OrivellumDB instance and operate on its connection/lock.

Authority rules (LAW 3 + LAW 4 of the Masterpiece Pipeline):
- Every fact carries a classification: HISTORICAL / INFERRED / INVENTED.
- The insert path REFUSES:
    * HISTORICAL facts without a non-empty source_ref
    * INFERRED facts without parent fact ids (each parent must be active)
    * INVENTED facts without an author signature
- Revisions are explicit: a new fact must name the active fact it
  supersedes; the old fact flips to 'superseded' in the same transaction.
  There is no UPDATE path for statement/classification — silent overwrite
  is structurally impossible.
- work_id NULL means the fact holds series-wide (the whole trilogy).
- Machine-proposed facts (wa_canon_proposals) never become canon
  automatically: ratify_proposal claims the proposal row first and writes
  the fact with the ratifying author's signature.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("orivellum.canon.store")

CLASSIFICATIONS = ("HISTORICAL", "INFERRED", "INVENTED")
FACT_STATUSES = ("active", "superseded", "retracted")


class CanonFactError(ValueError):
    """A fact violated the authority rules and was refused."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _fact_dict(row: Any) -> dict:
    d = dict(row)
    try:
        d["parent_ids"] = json.loads(d.get("parent_ids") or "[]")
    except Exception:
        d["parent_ids"] = []
    return d


class CanonStore:
    def __init__(self, db: Any) -> None:
        self._db = db

    # ── Validation (shared by create_fact and ratify_proposal) ────────────────

    def _validate(
        self,
        *,
        statement: str,
        classification: str,
        source_ref: str,
        parent_ids: list[str],
        signed_by: str,
        conn: Any = None,
        extra_active: set[str] | None = None,
    ) -> None:
        """Raise CanonFactError when the fact violates the authority rules.

        ``conn`` selects where parent liveness is checked (defaults to the
        read connection; pass the write connection when validating inside an
        open transaction).  ``extra_active`` names fact ids created earlier in
        the same uncommitted batch, which count as live parents.
        """
        if not statement or not statement.strip():
            raise CanonFactError("A canon fact needs a statement.")
        if classification not in CLASSIFICATIONS:
            raise CanonFactError(
                f"classification must be one of {', '.join(CLASSIFICATIONS)} "
                f"(got {classification!r})"
            )
        if classification == "HISTORICAL" and not source_ref.strip():
            raise CanonFactError(
                "Refused: a HISTORICAL fact requires a source_ref "
                "(scripture reference, source document, or archive location)."
            )
        if classification == "INFERRED":
            self._check_parents(parent_ids, conn=conn, extra_active=extra_active)
        if classification == "INVENTED" and not signed_by.strip():
            raise CanonFactError(
                "Refused: an INVENTED fact requires an author signature (signed_by)."
            )

    def _check_parents(
        self,
        parent_ids: list[str],
        *,
        conn: Any = None,
        extra_active: set[str] | None = None,
    ) -> None:
        """INFERRED parents must exist and be active (in-batch ids count)."""
        if not parent_ids:
            raise CanonFactError(
                "Refused: an INFERRED fact requires the ids of the facts "
                "it was inferred from (parent_ids)."
            )
        c = conn if conn is not None else self._db.read_conn()
        for pid in parent_ids:
            if extra_active and pid in extra_active:
                continue
            row = c.execute("SELECT status FROM canon_fact WHERE id=?", (pid,)).fetchone()
            if not row:
                raise CanonFactError(f"Refused: parent fact {pid!r} does not exist.")
            if row["status"] != "active":
                raise CanonFactError(
                    f"Refused: parent fact {pid!r} is {row['status']} — an "
                    "INFERRED fact must trace to live parents."
                )

    @staticmethod
    def _insert_row(
        conn: Any,
        fact_id: str,
        *,
        work_id: str | None,
        statement: str,
        classification: str,
        source_ref: str,
        parent_ids: list[str],
        established_chapter: int | None = None,
        established_offset: int | None = None,
        supersedes: str | None = None,
        signed_by: str,
        origin: str,
        proposal_id: str | None = None,
    ) -> None:
        conn.execute(
            """INSERT INTO canon_fact
               (id, work_id, statement, classification, source_ref, parent_ids,
                established_chapter, established_offset, supersedes, status,
                signed_by, origin, proposal_id, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,'active',?,?,?,?)""",
            (
                fact_id,
                work_id,
                statement.strip(),
                classification,
                source_ref.strip(),
                json.dumps(parent_ids),
                established_chapter,
                established_offset,
                supersedes,
                signed_by.strip(),
                origin,
                proposal_id,
                _now(),
            ),
        )

    # ── Create / supersede ─────────────────────────────────────────────────────

    def create_fact(
        self,
        *,
        statement: str,
        classification: str,
        work_id: str | None = None,
        source_ref: str = "",
        parent_ids: list[str] | None = None,
        signed_by: str = "",
        established_chapter: int | None = None,
        established_offset: int | None = None,
        supersedes: str | None = None,
        origin: str = "author",
        proposal_id: str | None = None,
    ) -> dict:
        """Insert one canon fact, enforcing the authority rules.

        When ``supersedes`` is given, the referenced fact must be active; it
        flips to 'superseded' in the same transaction (explicit revision —
        never a silent overwrite).  Raises CanonFactError on refusal.
        """
        parents = list(parent_ids or [])
        classification = (classification or "").strip().upper()
        self._validate(
            statement=statement,
            classification=classification,
            source_ref=source_ref,
            parent_ids=parents,
            signed_by=signed_by,
        )
        fact_id = str(uuid.uuid4())
        db = self._db
        with db.governed_write(
            operation="canon.fact_created",
            event_type="canon.fact_created",
            object_id=fact_id,
            object_type="canon_fact",
            actor=signed_by.strip() or "system",
            detail=f"{classification} {statement[:80]}"
            + (f" supersedes={supersedes}" if supersedes else ""),
        ):
            if supersedes:
                cur = db._conn.execute(
                    "UPDATE canon_fact SET status='superseded' WHERE id=? AND status='active'",
                    (supersedes,),
                )
                if cur.rowcount == 0:
                    raise CanonFactError(
                        f"Refused: fact {supersedes!r} is not an active fact — a "
                        "revision must explicitly supersede a live fact."
                    )
            self._insert_row(
                db._conn,
                fact_id,
                work_id=work_id,
                statement=statement,
                classification=classification,
                source_ref=source_ref,
                parent_ids=parents,
                established_chapter=established_chapter,
                established_offset=established_offset,
                supersedes=supersedes,
                signed_by=signed_by,
                origin=origin,
                proposal_id=proposal_id,
            )
        return self.get_fact(fact_id)  # type: ignore[return-value]

    def create_facts_batch(
        self,
        rows: list[dict],
        *,
        signed_by: str,
        origin: str,
    ) -> dict:
        """Insert many facts in ONE transaction — all or nothing.

        Each row: {statement, classification, work_id, source_ref,
        parent_rows (1-based indexes into this batch)}.  Rows identical to an
        existing active fact are skipped (idempotent re-seed).  Any refusal
        rolls back the whole batch, so a failed seed never leaves partial
        canon behind.  Returns {"created", "skipped", "fact_ids"}.
        """
        db = self._db
        created = 0
        skipped = 0
        fact_ids: list[str] = []
        with db.governed_write(
            operation="canon.facts_seeded",
            event_type="canon.facts_seeded",
            object_type="canon_fact",
            actor=signed_by.strip() or "system",
            detail=f"{origin}: {len(rows)} fact rows",
        ):
            batch_ids: set[str] = set()
            for row in rows:
                classification = str(row["classification"]).strip().upper()
                statement = str(row["statement"])
                work_id = row.get("work_id")
                existing = db._conn.execute(
                    "SELECT id FROM canon_fact WHERE statement=? AND classification=? "
                    "AND status='active' AND work_id IS ?",
                    (statement.strip(), classification, work_id),
                ).fetchone()
                if existing:
                    fact_ids.append(existing["id"])
                    skipped += 1
                    continue
                parent_ids = [fact_ids[p - 1] for p in row.get("parent_rows", [])]
                self._validate(
                    statement=statement,
                    classification=classification,
                    source_ref=str(row.get("source_ref", "")),
                    parent_ids=parent_ids,
                    signed_by=signed_by,
                    conn=db._conn,
                    extra_active=batch_ids,
                )
                fact_id = str(uuid.uuid4())
                self._insert_row(
                    db._conn,
                    fact_id,
                    work_id=work_id,
                    statement=statement,
                    classification=classification,
                    source_ref=str(row.get("source_ref", "")),
                    parent_ids=parent_ids,
                    signed_by=signed_by,
                    origin=origin,
                )
                fact_ids.append(fact_id)
                batch_ids.add(fact_id)
                created += 1
        return {"created": created, "skipped": skipped, "fact_ids": fact_ids}

    # ── Read ───────────────────────────────────────────────────────────────────

    def get_fact(self, fact_id: str) -> dict | None:
        conn = self._db.read_conn()
        row = conn.execute("SELECT * FROM canon_fact WHERE id=?", (fact_id,)).fetchone()
        if not row:
            return None
        d = _fact_dict(row)
        successor = conn.execute(
            "SELECT id FROM canon_fact WHERE supersedes=? ORDER BY created_at DESC LIMIT 1",
            (fact_id,),
        ).fetchone()
        d["superseded_by"] = successor["id"] if successor else None
        return d

    def list_facts(
        self,
        *,
        work_id: str | None = None,
        include_series: bool = True,
        series_only: bool = False,
        classification: str | None = None,
        status: str | None = None,
        limit: int = 1000,
    ) -> list[dict]:
        """List facts.  A book's canon includes series-wide facts by default."""
        q = "SELECT * FROM canon_fact WHERE 1=1"
        args: list = []
        if series_only:
            q += " AND work_id IS NULL"
        elif work_id:
            if include_series:
                q += " AND (work_id=? OR work_id IS NULL)"
            else:
                q += " AND work_id=?"
            args.append(work_id)
        if classification:
            q += " AND classification=?"
            args.append(classification)
        if status:
            q += " AND status=?"
            args.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(max(1, min(limit, 5000)))
        rows = self._db.read_conn().execute(q, args).fetchall()
        return [_fact_dict(r) for r in rows]

    def counts(self) -> dict:
        rows = (
            self._db.read_conn()
            .execute(
                "SELECT classification, status, COUNT(*) AS n "
                "FROM canon_fact GROUP BY classification, status"
            )
            .fetchall()
        )
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["classification"], {})[r["status"]] = r["n"]
        return out

    # ── Retract ────────────────────────────────────────────────────────────────

    def retract_fact(self, fact_id: str, *, signed_by: str, reason: str = "") -> str:
        """Retract an active fact.  Returns 'ok' | 'not_found' | 'conflict'.

        Requires an author signature — retraction is a canon decision.
        """
        if not signed_by.strip():
            raise CanonFactError("Refused: retracting a fact requires an author signature.")
        db = self._db
        with db._lock:
            exists = db._conn.execute(
                "SELECT status FROM canon_fact WHERE id=?", (fact_id,)
            ).fetchone()
            if not exists:
                return "not_found"
        try:
            with db.governed_write(
                operation="canon.fact_retracted",
                event_type="canon.fact_retracted",
                object_id=fact_id,
                object_type="canon_fact",
                actor=signed_by.strip(),
                detail=reason[:160] if reason else None,
            ):
                cur = db._conn.execute(
                    "UPDATE canon_fact SET status='retracted', retracted_by=?, retracted_at=? "
                    "WHERE id=? AND status='active'",
                    (signed_by.strip(), _now(), fact_id),
                )
                if cur.rowcount == 0:
                    raise _RetractConflict
        except _RetractConflict:
            return "conflict"
        return "ok"

    # ── Proposal ratification (review gate) ────────────────────────────────────

    def ratify_proposal(
        self,
        proposal_id: str,
        *,
        decision: str,
        author: str,
        classification: str | None = None,
        statement: str | None = None,
        source_ref: str | None = None,
        work_id: str | None = None,
        parent_ids: list[str] | None = None,
    ) -> dict:
        """Ratify (approve/reject) one machine-proposed fact.

        Approve creates a canon_fact signed by the ratifying author; the
        author may reclassify or edit the statement/source in the same act.

        The claim (conditional UPDATE of the proposal row) and the fact
        insert run in ONE transaction: an approved proposal can never exist
        without its canon fact, a refused fact automatically releases the
        claim (rollback), and two concurrent ratifications can never both
        write canon.

        Returns {"result": "ok"|"not_found"|"conflict", "fact": dict|None}.
        """
        if decision not in ("approve", "reject"):
            raise CanonFactError("decision must be 'approve' or 'reject'")
        if not author.strip():
            raise CanonFactError("Refused: ratification requires the author's signature.")
        db = self._db
        fact_id: str | None = None
        outcome = "ok"
        with db.governed_write(
            operation="canon.proposal_ratified",
            event_type="canon.proposal_ratified",
            object_id=proposal_id,
            object_type="canon_proposal",
            actor=author.strip(),
            detail=f"decision={decision}"
            + (f" reclass={classification}" if classification else ""),
        ):
            prop = db._conn.execute(
                "SELECT * FROM wa_canon_proposals WHERE id=?", (proposal_id,)
            ).fetchone()
            if not prop:
                outcome = "not_found"
            else:
                new_status = "approved" if decision == "approve" else "rejected"
                cur = db._conn.execute(
                    "UPDATE wa_canon_proposals SET status=?, decided_at=? "
                    "WHERE id=? AND status='proposed'",
                    (new_status, _now(), proposal_id),
                )
                if cur.rowcount == 0:
                    outcome = "conflict"
                elif decision == "approve":
                    fact_id = self._ratify_insert(
                        dict(prop),
                        proposal_id,
                        author=author,
                        classification=classification,
                        statement=statement,
                        source_ref=source_ref,
                        work_id=work_id,
                        parent_ids=parent_ids,
                    )
        if outcome != "ok":
            return {"result": outcome, "fact": None}
        return {"result": "ok", "fact": self.get_fact(fact_id) if fact_id else None}

    def _ratify_insert(
        self,
        prop_d: dict,
        proposal_id: str,
        *,
        author: str,
        classification: str | None,
        statement: str | None,
        source_ref: str | None,
        work_id: str | None,
        parent_ids: list[str] | None,
    ) -> str:
        """Validate and insert the ratified fact (inside the open txn)."""
        db = self._db
        stmt = (statement or "").strip()
        if not stmt:
            title = (prop_d.get("fact_title") or "").strip()
            text = (prop_d.get("fact_text") or "").strip()
            stmt = f"{title}: {text}" if title and title not in text else (text or title)
        src = (
            source_ref
            if source_ref is not None
            else (f"{prop_d.get('source_path', '')}#{prop_d.get('source_location', '')}".strip("#"))
        )
        resolved_work = self._resolve_proposal_work(work_id, str(prop_d.get("scope") or ""))
        cls = (classification or prop_d.get("classification") or "").strip().upper()
        parents = list(parent_ids or [])
        self._validate(
            statement=stmt,
            classification=cls,
            source_ref=src,
            parent_ids=parents,
            signed_by=author,
            conn=db._conn,
        )
        fact_id = str(uuid.uuid4())
        self._insert_row(
            db._conn,
            fact_id,
            work_id=resolved_work,
            statement=stmt,
            classification=cls,
            source_ref=src,
            parent_ids=parents,
            signed_by=author,
            origin="wa_archive",
            proposal_id=proposal_id,
        )
        return fact_id

    @staticmethod
    def _resolve_proposal_work(work_id: str | None, scope: str) -> str | None:
        """Map a proposal's scope to a fact scope.

        An explicit work_id from the ratifying author always wins.  Proposal
        scopes of the form 'series:*' (the archive decomposer's trilogy
        scope) become series-wide facts (work_id NULL).  Any other non-series
        scope requires the author to pick a Work explicitly — never guess.
        """
        if work_id:
            return work_id
        if not scope or scope.lower().startswith("series"):
            return None
        raise CanonFactError(
            f"Refused: proposal scope {scope!r} is not series-wide — pick the "
            "Work this fact belongs to before ratifying."
        )


class _RetractConflict(Exception):
    """Internal sentinel: fact was not active when the retract UPDATE ran."""
