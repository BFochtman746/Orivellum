"""Whole-series continuity reviews with coverage truth (spec §4 + §8.3).

A series review must never pretend an LLM held all books in context.  This
module is the evidence-backed map/reduce pipeline:

  Stage B — per-book ledgers (:func:`build_book_ledger`): a deterministic
      evidence map built from the already-grounded ATLAS graph rows plus the
      book's canon facts.  Every item cites an exact source span (chapter +
      character offset + verbatim quote) and the content fingerprint of the
      manuscript it came from.  Items are ``candidate`` until the author
      approves them; rebuilds carry approved/rejected forward by item_key.

  Stage C — cross-book reconciliation (:func:`reconcile`): deterministic
      comparators over the ledgers.  No model runs at review time — the
      model's only contribution happened upstream (grounded graph
      extraction); everything here is reproducible code, and severity is
      computed in code, never chosen by a model.

  Stage D — findings: every finding carries evidence spans on ALL affected
      passages, linked canon facts with authority class, a plain-language
      explanation, and the spec's closed resolution-choice list.  Nothing
      auto-edits.  Dispositions (intentional/dismissed/…) are inherited
      across re-runs by stable dedupe key — never resurrected as 'open'.

  Stage E — coverage truth (:func:`build_manifest`): every run emits a
      CoverageManifest listing each book+fingerprint scanned, chapters
      parsed/skipped/failed/stale, tool versions and hashes.  ANY exclusion
      or staleness forces ``partial=1`` on the run — the API and UI must
      label it a partial review.

Runs execute as durable operations (one ledger step per book, then a
reconcile step) so they checkpoint, survive restarts, and resume without
redoing finished books.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

TOOL_VERSION = "series-review/1.0.0"

MODES = (
    "chapter_vs_book",
    "book_vs_series",
    "full_series",
    "terminology_audit",
    "canon_audit",
    "change_impact",
    "release_gate",
)

# Stage D — the closed resolution-choice list (spec §4 Stage D).  The system
# never auto-edits; these are the ONLY choices the author can record.
RESOLUTION_CHOICES = (
    "update_book_text",
    "approve_canon_correction",
    "add_bridge_scene",
    "clarify_time_jump",
    "retag_entity",
    "accept_intentional_ambiguity",
    "defer",
    "dismiss",
)

FINDING_STATUSES = ("open", "resolved", "intentional", "deferred", "dismissed")

# Severity is computed here, in code, per finding type.  Canon involvement
# raises the floor by authority class (same discipline as ConStory).
_SEVERITY_ORDER = ("low", "medium", "high", "critical")
_BASE_SEVERITY: dict[str, str] = {
    "timeline_date_conflict": "high",
    "age_regression": "high",
    "duration_impossible": "high",
    "state_drift": "medium",
    "injury_drift": "high",
    "possession_conflict": "medium",
    "relationship_drift": "medium",
    "spoiler_leakage": "high",
    "payoff_without_setup": "medium",
    "terminology_variant": "low",
    "entity_type_conflict": "medium",
    "rule_conflict": "high",
    "unresolved_thread": "low",
    "canon_conflict": "high",
}
_CANON_FLOOR = {"HISTORICAL": "critical", "INFERRED": "high", "INVENTED": "medium"}


def compute_severity(finding_type: str, canon_class: str | None = None) -> str:
    if finding_type not in _BASE_SEVERITY:
        raise ValueError(f"unknown finding type {finding_type!r}")
    base = _BASE_SEVERITY[finding_type]
    if canon_class is None:
        return base
    floor = _CANON_FLOOR.get(canon_class, "medium")
    return max(base, floor, key=_SEVERITY_ORDER.index)


class SeriesReviewError(ValueError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _key(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()


_NORM_RE = re.compile(r"[^a-z0-9]+")


def _norm(name: str) -> str:
    """Normalized entity identity: lowercase, alnum only."""
    return _NORM_RE.sub(" ", (name or "").lower()).strip()


# Year parsing for temporal comparators.  Uses ± (never a bare '-') for any
# uncertainty rendering; here we only READ years from prose statements.
_YEAR_RE = re.compile(r"\b(?:in|year|AD|A\.D\.|BC|B\.C\.)?\s*([1-9]\d{2,3})\s*(BC|B\.C\.)?\b")
_AGE_RE = re.compile(r"\b(?:is|was|turned|aged?)\s+(\d{1,3})\s+(?:years?\s+old|winters|summers)\b", re.I)


def _parse_year(text: str) -> int | None:
    m = _YEAR_RE.search(text or "")
    if not m:
        return None
    year = int(m.group(1))
    return -year if m.group(2) else year


def _parse_age(text: str) -> int | None:
    m = _AGE_RE.search(text or "")
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Stage B — per-book ledger
# ---------------------------------------------------------------------------

LEDGER_KINDS = (
    "fact",
    "event",
    "character_state",
    "thread",
    "location",
    "knowledge",
    "terminology",
)

_SOCIAL_EDGES = ("kinship_with", "affinity_with", "hostility_with", "affiliated_with")


def _chapters(db: OrivellumDB, work_id: str) -> list[dict]:
    rows = db.read_conn().execute(
        "SELECT id, seq, title, text FROM book_chapters WHERE work_id=? ORDER BY seq",
        (work_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _chapter_status(ch: dict, graph_chapter_ids: set[str]) -> str:
    """parsed | skipped (no text) | failed (text but never extracted)."""
    if not (ch.get("text") or "").strip():
        return "skipped"
    return "parsed" if ch["id"] in graph_chapter_ids else "failed"


def build_book_ledger(db: OrivellumDB, work_id: str) -> dict:
    """Build (or rebuild) the deterministic evidence ledger for one book.

    Reads chapters + grounded graph rows + work-scoped canon facts, writes a
    ``book_ledger`` row with per-chapter content hashes and ``ledger_item``
    rows with span provenance.  Approved/rejected statuses from a previous
    build are carried forward by item_key; everything new is ``candidate``.
    """
    work = db.get_work(work_id)
    if not work:
        raise SeriesReviewError(f"Work {work_id!r} not found")
    chapters = _chapters(db, work_id)
    nodes = db.list_graph_nodes(work_ids=[work_id], limit=10000)
    edges = db.list_graph_edges(work_ids=[work_id], limit=20000)
    from orivellum.database.canon_store import CanonStore  # noqa: PLC0415

    canon_facts = CanonStore(db).list_facts(work_id=work_id, status="active")

    seq_by_chapter = {c["id"]: int(c["seq"]) for c in chapters}
    text_by_chapter = {c["id"]: c.get("text") or "" for c in chapters}
    graph_chapter_ids = {n["chapter_id"] for n in nodes} | {e["chapter_id"] for e in edges}
    chapter_meta = [
        {
            "chapter_id": c["id"],
            "seq": int(c["seq"]),
            "title": c.get("title") or "",
            "sha256": _sha(c.get("text") or ""),
            "chars": len(c.get("text") or ""),
            "status": _chapter_status(c, graph_chapter_ids),
        }
        for c in chapters
    ]
    fingerprint = _sha("|".join(f"{m['chapter_id']}:{m['sha256']}" for m in chapter_meta))

    node_by_id = {n["id"]: n for n in nodes}
    items: list[dict] = []

    unverified_by_chapter: dict[str, int] = {}

    def _add(
        kind: str,
        subject: str,
        statement: str,
        *,
        chapter_id: str | None,
        quote: str = "",
        offset: int | None = None,
        meta: dict | None = None,
    ) -> None:
        meta = dict(meta or {})
        # Enforce the provenance guarantee at build time: a passage-cited
        # item whose quote can no longer be found in the chapter text is
        # UNVERIFIED evidence — it never feeds comparators, and the manifest
        # names it so the run is honestly partial.
        if chapter_id is not None:
            text = text_by_chapter.get(chapter_id, "")
            if not quote.strip() or quote not in text:
                meta["span_unverified"] = True
                unverified_by_chapter[chapter_id] = (
                    unverified_by_chapter.get(chapter_id, 0) + 1
                )
        items.append(
            {
                "kind": kind,
                "subject": subject,
                "statement": statement,
                "chapter_id": chapter_id,
                "chapter_seq": seq_by_chapter.get(chapter_id or ""),
                "quote": quote,
                "span_offset": offset,
                "meta": meta,
            }
        )

    seen_terms: set[str] = set()
    for n in nodes:
        name = n["name"]
        quote, offset, ch = n.get("evidence_quote") or "", n.get("evidence_offset"), n["chapter_id"]
        attrs = n.get("attributes") or {}
        # terminology — one entry per distinct surface form per book
        tkey = f"{_norm(name)}::{name}"
        if tkey not in seen_terms:
            seen_terms.add(tkey)
            _add(
                "terminology",
                _norm(name),
                name,
                chapter_id=ch,
                quote=quote,
                offset=offset,
                meta={"node_type": n["node_type"]},
            )
        if n["node_type"] == "Event":
            _add(
                "event",
                _norm(name),
                n.get("description") or name,
                chapter_id=ch,
                quote=quote,
                offset=offset,
                meta={"attributes": attrs},
            )
        elif n["node_type"] == "Location":
            _add("location", _norm(name), n.get("description") or name,
                 chapter_id=ch, quote=quote, offset=offset)
        elif n["node_type"] == "TimePoint":
            year = _parse_year(name) or _parse_year(n.get("description") or "")
            _add(
                "fact",
                _norm(name),
                n.get("description") or name,
                chapter_id=ch,
                quote=quote,
                offset=offset,
                meta={"temporal": True, "year": year},
            )
        elif n["node_type"] == "Concept":
            _add("thread", _norm(name), n.get("description") or name,
                 chapter_id=ch, quote=quote, offset=offset, meta={"origin": "concept"})
        elif n["node_type"] == "Character":
            for k, v in attrs.items():
                sv = str(v).strip()
                if not sv:
                    continue
                age = _parse_age(f"is {sv} years old") or _parse_age(sv)
                _add(
                    "character_state",
                    _norm(name),
                    f"{name} — {k}: {sv}",
                    chapter_id=ch,
                    quote=quote,
                    offset=offset,
                    meta={"attr": k, "value": sv, "age": age, "display": name},
                )

    for e in edges:
        src, dst = node_by_id.get(e["src"]), node_by_id.get(e["dst"])
        if not src or not dst:
            continue
        et, ch = e["edge_type"], e["chapter_id"]
        quote, offset = e.get("evidence_quote") or "", e.get("evidence_offset")
        if et == "possesses" and src["node_type"] == "Character":
            _add(
                "character_state",
                _norm(dst["name"]),
                f"{src['name']} possesses {dst['name']}",
                chapter_id=ch,
                quote=quote,
                offset=offset,
                meta={"relation": "possesses", "holder": _norm(src["name"]),
                      "holder_display": src["name"], "object_display": dst["name"]},
            )
        elif et in _SOCIAL_EDGES:
            pair = "::".join(sorted((_norm(src["name"]), _norm(dst["name"]))))
            _add(
                "character_state",
                pair,
                f"{src['name']} {et.replace('_', ' ')} {dst['name']}",
                chapter_id=ch,
                quote=quote,
                offset=offset,
                meta={"relation": et, "pair": pair,
                      "a": src["name"], "b": dst["name"]},
            )
        elif et in ("references", "experiences") and src["node_type"] == "Character":
            _add(
                "knowledge",
                _norm(src["name"]),
                f"{src['name']} {et} {dst['name']}",
                chapter_id=ch,
                quote=quote,
                offset=offset,
                meta={"entity": _norm(dst["name"]), "entity_display": dst["name"],
                      "relation": et},
            )
        elif et in ("causes", "precedes", "occurs_after"):
            _add(
                "thread",
                _norm(dst["name"]),
                f"{src['name']} {et.replace('_', ' ')} {dst['name']}",
                chapter_id=ch,
                quote=quote,
                offset=offset,
                meta={"origin": "edge", "relation": et, "src": _norm(src["name"])},
            )

    for f in canon_facts:
        stmt = str(f.get("statement") or "")
        # Subject = the statement with any year removed, so two canon facts
        # that differ ONLY in the year land on the same subject and the
        # timeline comparator can see the contradiction.
        _add(
            "fact",
            _norm(_YEAR_RE.sub(" ", stmt))[:80],
            stmt,
            chapter_id=None,
            meta={
                "canon_fact_id": f.get("id"),
                "classification": f.get("classification"),
                "source_ref": f.get("source_ref") or "",
                "year": _parse_year(stmt),
                "temporal": _parse_year(stmt) is not None,
            },
        )

    # Annotate per-chapter unverified-evidence counts so the manifest can
    # name them (and refuse a full-review claim while any exist).
    for cm in chapter_meta:
        cm["unverified"] = unverified_by_chapter.get(cm["chapter_id"], 0)

    # Persist — replace the previous ledger, carrying dispositions forward.
    now = _now()
    ledger_id = str(uuid.uuid4())
    with db.atomic():
        with db.governed_write(
            operation="book_ledger.built",
            event_type="book_ledger.built",
            object_id=work_id,
            object_type="work",
            actor="system",
            detail=f"{len(items)} items, fingerprint {fingerprint[:12]}",
        ):
            prior = {
                r["item_key"]: r["review_status"]
                for r in db._conn.execute(
                    "SELECT item_key, review_status FROM ledger_item "
                    "WHERE work_id=? AND review_status != 'candidate'",
                    (work_id,),
                ).fetchall()
            }
            db._conn.execute("DELETE FROM book_ledger WHERE work_id=?", (work_id,))
            db._conn.execute(
                """INSERT INTO book_ledger
                   (id, work_id, fingerprint, tool_version, chapters,
                    item_count, built_at) VALUES(?,?,?,?,?,?,?)""",
                (ledger_id, work_id, fingerprint, TOOL_VERSION,
                 json.dumps(chapter_meta), len(items), now),
            )
            for it in items:
                ikey = _key(it["kind"], it["subject"], it["statement"],
                            it["chapter_id"], it["span_offset"])
                db._conn.execute(
                    """INSERT INTO ledger_item
                       (id, ledger_id, work_id, kind, item_key, subject,
                        statement, chapter_id, chapter_seq, quote, span_offset,
                        review_status, meta, created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), ledger_id, work_id, it["kind"], ikey,
                     it["subject"], it["statement"], it["chapter_id"],
                     it["chapter_seq"], it["quote"], it["span_offset"],
                     prior.get(ikey, "candidate"), json.dumps(it["meta"]), now),
                )
    return {
        "ledger_id": ledger_id,
        "work_id": work_id,
        "fingerprint": fingerprint,
        "item_count": len(items),
        "chapters": chapter_meta,
    }


def get_ledger(db: OrivellumDB, work_id: str) -> dict | None:
    row = db.read_conn().execute(
        "SELECT * FROM book_ledger WHERE work_id=?", (work_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["chapters"] = json.loads(d["chapters"] or "[]")
    return d


def list_ledger_items(
    db: OrivellumDB, work_id: str, *, kind: str | None = None, limit: int = 2000
) -> list[dict]:
    q = "SELECT * FROM ledger_item WHERE work_id=?"
    args: list = [work_id]
    if kind:
        q += " AND kind=?"
        args.append(kind)
    q += " ORDER BY chapter_seq, span_offset LIMIT ?"
    args.append(max(1, min(limit, 10000)))
    out = []
    for r in db.read_conn().execute(q, args).fetchall():
        d = dict(r)
        d["meta"] = json.loads(d["meta"] or "{}")
        out.append(d)
    return out


def set_ledger_item_status(db: OrivellumDB, item_id: str, status: str) -> dict | None:
    if status not in ("candidate", "approved", "rejected"):
        raise SeriesReviewError("status must be candidate, approved, or rejected")
    with db.governed_write(
        operation="ledger_item.reviewed",
        event_type="ledger_item.reviewed",
        object_id=item_id,
        object_type="ledger_item",
        actor="author",
        detail=status,
    ):
        cur = db._conn.execute(
            "UPDATE ledger_item SET review_status=? WHERE id=?", (status, item_id)
        )
        if cur.rowcount == 0:
            return None
    row = db.read_conn().execute(
        "SELECT * FROM ledger_item WHERE id=?", (item_id,)
    ).fetchone()
    d = dict(row)
    d["meta"] = json.loads(d["meta"] or "{}")
    return d


# ---------------------------------------------------------------------------
# Scope resolution — which books, in which reading order
# ---------------------------------------------------------------------------


def resolve_scope(db: OrivellumDB, *, mode: str, work_id: str | None,
                  series_id: str | None) -> list[dict]:
    """Ordered [{work_id, title, order}] for a run.  Reading order = volume."""
    from orivellum.database.series_store import SeriesStore  # noqa: PLC0415

    store = SeriesStore(db)
    if mode == "chapter_vs_book":
        if not work_id:
            raise SeriesReviewError("chapter_vs_book requires work_id")
        w = db.get_work(work_id)
        if not w:
            raise SeriesReviewError(f"Work {work_id!r} not found")
        return [{"work_id": work_id, "title": w["title"], "order": 1}]
    sid = series_id
    if not sid and work_id:
        sfw = store.series_for_work(work_id)
        sid = sfw["series_id"] if sfw else None
    if sid:
        members = store.list_members(sid)
        scope = [
            {"work_id": m["work_id"], "title": m.get("work_title") or m["work_id"],
             "order": int(m["volume"])}
            for m in members
        ]
        if mode == "book_vs_series" and work_id:
            # target book + everything BEFORE it in reading order
            target = next((s for s in scope if s["work_id"] == work_id), None)
            if target is None:
                raise SeriesReviewError("work is not a member of the series")
            scope = [s for s in scope if s["order"] <= target["order"]]
        return sorted(scope, key=lambda s: s["order"])
    if work_id:
        w = db.get_work(work_id)
        if not w:
            raise SeriesReviewError(f"Work {work_id!r} not found")
        return [{"work_id": work_id, "title": w["title"], "order": 1}]
    raise SeriesReviewError("a work_id or series_id is required")


# ---------------------------------------------------------------------------
# Stage C — deterministic cross-book reconciliation
# ---------------------------------------------------------------------------


def _span(item: dict, book: dict) -> dict:
    span = {
        "work_id": book["work_id"],
        "work_title": book["title"],
        "order": book["order"],
        "chapter_id": item.get("chapter_id"),
        "chapter_seq": item.get("chapter_seq"),
        "quote": item.get("quote") or "",
        "offset": item.get("span_offset"),
        "statement": item.get("statement") or "",
    }
    meta = item.get("meta") or {}
    if item.get("chapter_id") is None and meta.get("canon_fact_id"):
        # Canon facts are not passage evidence — say so explicitly instead of
        # presenting an empty quote as if it were a manuscript span.
        span["source"] = "canon"
        span["source_ref"] = meta.get("source_ref") or ""
    return span


def _mk_finding(ftype: str, subject: str, explanation: str, spans: list[dict],
                *, canon_fact_id: str | None = None,
                canon_class: str | None = None) -> dict:
    sev = compute_severity(ftype, canon_class)
    dedupe = _key(ftype, subject,
                  *sorted(f"{s['work_id']}:{s.get('chapter_id')}:{s.get('offset')}"
                          for s in spans))
    return {
        "finding_type": ftype,
        "severity": sev,
        "subject": subject,
        "explanation": explanation,
        "evidence": spans,
        "canon_fact_id": canon_fact_id,
        "canon_class": canon_class,
        "dedupe_key": dedupe,
    }


def _by_kind(items: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {k: [] for k in LEDGER_KINDS}
    for it in items:
        # rejected ledger items are author-refuted evidence — never compare on them
        if it["review_status"] == "rejected":
            continue
        # unverifiable spans never feed comparators — a finding must be able
        # to cite a real passage; the manifest reports these as unreviewed
        if (it.get("meta") or {}).get("span_unverified"):
            continue
        out[it["kind"]].append(it)
    return out


def _cmp_timeline(books: list[dict]) -> list[dict]:
    """Same subject anchored to different years across books; age regression."""
    findings: list[dict] = []
    years: dict[str, list[tuple[int, dict, dict]]] = {}
    for b in books:
        for it in b["kinds"]["fact"]:
            y = (it["meta"] or {}).get("year")
            if y is not None and it["subject"]:
                years.setdefault(it["subject"], []).append((int(y), it, b))
    for subject, entries in years.items():
        distinct = {y for y, _, _ in entries}
        if len(distinct) > 1:
            spans = [_span(it, b) for _, it, b in entries]
            canon = next(((it["meta"].get("canon_fact_id"), it["meta"].get("classification"))
                          for _, it, _ in entries if it["meta"].get("canon_fact_id")),
                         (None, None))
            findings.append(_mk_finding(
                "timeline_date_conflict", subject,
                f"“{entries[0][1]['statement'][:80]}” is anchored to different years "
                f"across books: {sorted(distinct)}. Both claims are preserved — "
                "choose which is canonical.",
                spans, canon_fact_id=canon[0], canon_class=canon[1]))
    # age regression: same character older in an earlier book than a later one
    ages: dict[str, list[tuple[int, int, dict, dict]]] = {}
    for b in books:
        for it in b["kinds"]["character_state"]:
            age = (it["meta"] or {}).get("age")
            if age is not None and not it["meta"].get("relation"):
                ages.setdefault(it["subject"], []).append((b["order"], int(age), it, b))
    for subject, entries in ages.items():
        entries.sort()
        for i in range(len(entries) - 1):
            o1, a1, it1, b1 = entries[i]
            o2, a2, it2, b2 = entries[i + 1]
            if o2 > o1 and a2 < a1:
                findings.append(_mk_finding(
                    "age_regression", subject,
                    f"{it1['meta'].get('display', subject)} is {a1} in "
                    f"“{b1['title']}” but {a2} in the later book “{b2['title']}”.",
                    [_span(it1, b1), _span(it2, b2)]))
    return findings


def _cmp_character_state(books: list[dict]) -> list[dict]:
    """Attribute drift between books: same character, same attribute, new value."""
    findings: list[dict] = []
    _INJURY = re.compile(r"\b(injur|wound|scar|broken|maim|blind|lame|lost)\w*", re.I)
    latest: dict[tuple[str, str], tuple[dict, dict]] = {}
    for b in books:
        for it in b["kinds"]["character_state"]:
            meta = it["meta"] or {}
            if meta.get("relation") or meta.get("attr") is None:
                continue
            k = (it["subject"], str(meta["attr"]).lower())
            prior = latest.get(k)
            if prior is not None:
                p_it, p_b = prior
                if p_b["work_id"] != b["work_id"] and \
                        _norm(str(meta["value"])) != _norm(str(p_it["meta"]["value"])):
                    injury = bool(_INJURY.search(str(p_it["meta"]["value"]))
                                  or _INJURY.search(str(meta["value"])))
                    findings.append(_mk_finding(
                        "injury_drift" if injury else "state_drift",
                        f"{k[0]}::{k[1]}",
                        f"{meta.get('display', k[0])}'s {k[1]} changes from "
                        f"“{p_it['meta']['value']}” ({p_b['title']}) to "
                        f"“{meta['value']}” ({b['title']}) with no recorded "
                        "transition between books.",
                        [_span(p_it, p_b), _span(it, b)]))
            latest[k] = (it, b)
    return findings


def _cmp_possession(books: list[dict]) -> list[dict]:
    """Same object held by different characters in different books."""
    findings: list[dict] = []
    holders: dict[str, tuple[str, dict, dict]] = {}
    for b in books:
        for it in b["kinds"]["character_state"]:
            meta = it["meta"] or {}
            if meta.get("relation") != "possesses":
                continue
            obj, holder = it["subject"], meta.get("holder") or ""
            prior = holders.get(obj)
            if prior and prior[0] != holder and prior[2]["work_id"] != b["work_id"]:
                p_holder, p_it, p_b = prior
                findings.append(_mk_finding(
                    "possession_conflict", obj,
                    f"“{meta.get('object_display', obj)}” is held by "
                    f"{p_it['meta'].get('holder_display', p_holder)} in "
                    f"“{p_b['title']}” but by "
                    f"{meta.get('holder_display', holder)} in “{b['title']}” "
                    "with no transfer in evidence.",
                    [_span(p_it, p_b), _span(it, b)]))
            holders[obj] = (holder, it, b)
    return findings


def _cmp_relationship(books: list[dict]) -> list[dict]:
    """Affinity in one book, hostility in another, for the same pair."""
    findings: list[dict] = []
    seen: dict[str, dict[str, tuple[dict, dict]]] = {}
    for b in books:
        for it in b["kinds"]["character_state"]:
            meta = it["meta"] or {}
            rel = meta.get("relation")
            if rel not in _SOCIAL_EDGES:
                continue
            seen.setdefault(it["subject"], {})[rel] = (it, b)
    for pair, rels in seen.items():
        aff, hos = rels.get("affinity_with"), rels.get("hostility_with")
        if aff and hos and aff[1]["work_id"] != hos[1]["work_id"]:
            a_it, a_b = aff
            h_it, h_b = hos
            findings.append(_mk_finding(
                "relationship_drift", pair,
                f"{a_it['meta'].get('a')} and {a_it['meta'].get('b')} are allies in "
                f"“{a_b['title']}” but hostile in “{h_b['title']}” with no "
                "recorded turn between books.",
                [_span(a_it, a_b), _span(h_it, h_b)]))
    return findings


def _first_appearance(books: list[dict]) -> dict[str, tuple[int, int | None, dict, dict]]:
    """entity -> (book order, chapter_seq, item, book) of first terminology sighting."""
    first: dict[str, tuple[int, int | None, dict, dict]] = {}
    for b in books:
        for it in b["kinds"]["terminology"]:
            cur = first.get(it["subject"])
            cseq = it.get("chapter_seq")
            cand = (b["order"], cseq if cseq is not None else 10**9, it, b)
            if cur is None or (cand[0], cand[1]) < (cur[0], cur[1] if cur[1] is not None else 10**9):
                first[it["subject"]] = (b["order"], cseq, it, b)
    return first


def _cmp_knowledge(books: list[dict]) -> list[dict]:
    """Spoiler leakage + payoff-without-setup, in reading order."""
    findings: list[dict] = []
    first = _first_appearance(books)
    for b in books:
        for it in b["kinds"]["knowledge"]:
            entity = (it["meta"] or {}).get("entity")
            if not entity:
                continue
            intro = first.get(entity)
            if intro is None:
                continue
            i_order, i_seq, i_it, i_b = intro
            if i_order > b["order"]:
                findings.append(_mk_finding(
                    "spoiler_leakage", f"{it['subject']}::{entity}",
                    f"In “{b['title']}” (book {b['order']}), "
                    f"{it['statement'][:100]} — but "
                    f"“{i_it['statement']}” is not introduced until "
                    f"“{i_b['title']}” (book {i_order}). A reader in reading "
                    "order does not yet have this information.",
                    [_span(it, b), _span(i_it, i_b)]))
            elif (
                i_order == b["order"]
                and i_seq is not None
                and it.get("chapter_seq") is not None
                and it["chapter_seq"] < i_seq
                and it["subject"] != entity
            ):
                findings.append(_mk_finding(
                    "payoff_without_setup", f"{it['subject']}::{entity}",
                    f"In “{b['title']}”, {it['statement'][:100]} (chapter "
                    f"{it['chapter_seq']}) reacts to “{i_it['statement']}” "
                    f"which is first established later, in chapter {i_seq}.",
                    [_span(it, b), _span(i_it, i_b)]))
    return findings


def _cmp_terminology(books: list[dict]) -> list[dict]:
    """Same entity, different surface forms / node types across books."""
    findings: list[dict] = []
    forms: dict[str, dict[str, tuple[dict, dict]]] = {}
    types: dict[str, dict[str, tuple[dict, dict]]] = {}
    for b in books:
        for it in b["kinds"]["terminology"]:
            # Space-insensitive identity so "Black-water Keep" and
            # "Blackwater Keep" collide — that IS the rename to catch.
            squash = it["subject"].replace(" ", "")
            forms.setdefault(squash, {}).setdefault(it["statement"], (it, b))
            nt = (it["meta"] or {}).get("node_type") or ""
            types.setdefault(squash, {}).setdefault(nt, (it, b))
    for subject, variants in forms.items():
        if len(variants) > 1:
            books_involved = {b["work_id"] for _, b in variants.values()}
            if len(books_involved) > 1:
                spans = [_span(it, b) for it, b in variants.values()]
                findings.append(_mk_finding(
                    "terminology_variant", subject,
                    "The same name is spelled differently across books: "
                    + ", ".join(f"“{v}”" for v in sorted(variants)) + ".",
                    spans))
    for subject, tps in types.items():
        if len(tps) > 1:
            books_involved = {b["work_id"] for _, b in tps.values()}
            if len(books_involved) > 1:
                spans = [_span(it, b) for it, b in tps.values()]
                findings.append(_mk_finding(
                    "entity_type_conflict", subject,
                    f"“{subject}” is treated as different kinds of entity across "
                    f"books ({', '.join(sorted(tps))}) — possibly two entities "
                    "sharing a name, or one entity mis-typed.",
                    spans))
    return findings


def _cmp_threads(books: list[dict]) -> list[dict]:
    """Threads set up in one book that never recur in any later book."""
    findings: list[dict] = []
    if len(books) < 2:
        return findings
    last_order = max(b["order"] for b in books)
    appearances: dict[str, list[tuple[int, dict, dict]]] = {}
    for b in books:
        for it in b["kinds"]["thread"]:
            appearances.setdefault(it["subject"], []).append((b["order"], it, b))
        for it in b["kinds"]["terminology"]:
            appearances.setdefault(it["subject"], []).append((b["order"], it, b))
    threads = {it["subject"] for b in books for it in b["kinds"]["thread"]}
    for subject in threads:
        entries = appearances.get(subject, [])
        orders = {o for o, _, _ in entries}
        if orders and max(orders) < last_order:
            o, it, b = min(entries, key=lambda e: e[0])
            findings.append(_mk_finding(
                "unresolved_thread", subject,
                f"“{it['statement'][:100]}” is set up in “{b['title']}” "
                f"(book {o}) and never appears in any later book — a possible "
                "promise without payoff. Advisory: this may be intentional.",
                [_span(it, b)]))
    return findings


_COMPARATORS = {
    "timeline": _cmp_timeline,
    "character_state": _cmp_character_state,
    "possession": _cmp_possession,
    "relationship": _cmp_relationship,
    "knowledge": _cmp_knowledge,
    "terminology": _cmp_terminology,
    "threads": _cmp_threads,
}

_MODE_COMPARATORS: dict[str, tuple[str, ...]] = {
    "terminology_audit": ("terminology",),
    "canon_audit": ("timeline", "terminology"),
}


def reconcile(db: OrivellumDB, *, mode: str, scope: list[dict],
              chapter_id: str | None = None) -> list[dict]:
    """Run the deterministic comparators over the scope's ledgers.

    Books without a ledger are EXCLUDED here (the manifest reports them —
    that is what makes the run partial); reconciliation never guesses at a
    book it has no evidence map for.
    """
    books = []
    for s in scope:
        ledger = get_ledger(db, s["work_id"])
        if ledger is None:
            continue
        items = list_ledger_items(db, s["work_id"], limit=10000)
        books.append({**s, "ledger": ledger, "kinds": _by_kind(items)})
    books.sort(key=lambda b: b["order"])
    findings: list[dict] = []
    for name in _MODE_COMPARATORS.get(mode, tuple(_COMPARATORS)):
        findings.extend(_COMPARATORS[name](books))
    if chapter_id and mode in ("chapter_vs_book", "change_impact"):
        findings = [
            f for f in findings
            if any(s.get("chapter_id") == chapter_id for s in f["evidence"])
        ]
    return findings


# ---------------------------------------------------------------------------
# Stage E — coverage manifest
# ---------------------------------------------------------------------------


def build_manifest(db: OrivellumDB, *, mode: str, scope: list[dict]) -> dict:
    """The CoverageManifest — what was ACTUALLY reviewed, with staleness.

    A chapter is *stale* when its current text hash differs from the hash
    recorded when the book's ledger was built.  Any missing ledger, skipped,
    failed, or stale chapter makes the whole run a partial review.
    """
    books: list[dict] = []
    unreviewed: list[dict] = []
    error_count = 0
    for s in scope:
        ledger = get_ledger(db, s["work_id"])
        current = {c["id"]: _sha(c.get("text") or "") for c in _chapters(db, s["work_id"])}
        if ledger is None:
            books.append({**s, "ledger": None, "scanned": False,
                          "chapters": [], "reason": "no ledger built"})
            unreviewed.append({"work_id": s["work_id"], "work_title": s["title"],
                               "reason": "no ledger built"})
            error_count += 1
            continue
        chapters = []
        for cm in ledger["chapters"]:
            status = cm["status"]
            if cm["chapter_id"] not in current:
                status = "stale"  # chapter deleted since the ledger was built
            elif current[cm["chapter_id"]] != cm["sha256"]:
                status = "stale"
            chapters.append({**cm, "status": status})
            if status != "parsed":
                unreviewed.append({
                    "work_id": s["work_id"], "work_title": s["title"],
                    "chapter_id": cm["chapter_id"], "seq": cm["seq"],
                    "title": cm.get("title") or "", "reason": status,
                })
                if status == "failed":
                    error_count += 1
            elif cm.get("unverified"):
                # Parsed, but some extracted evidence could not be verified
                # against the chapter text — those items were excluded from
                # reconciliation, so the review of this chapter is incomplete.
                unreviewed.append({
                    "work_id": s["work_id"], "work_title": s["title"],
                    "chapter_id": cm["chapter_id"], "seq": cm["seq"],
                    "title": cm.get("title") or "",
                    "reason": f"{cm['unverified']} unverifiable evidence span(s)",
                })
        new_ids = set(current) - {cm["chapter_id"] for cm in ledger["chapters"]}
        for cid in new_ids:
            unreviewed.append({"work_id": s["work_id"], "work_title": s["title"],
                               "chapter_id": cid, "reason": "added after ledger build"})
        books.append({
            **s,
            "ledger": {"id": ledger["id"], "fingerprint": ledger["fingerprint"],
                       "built_at": ledger["built_at"],
                       "tool_version": ledger["tool_version"],
                       "item_count": ledger["item_count"]},
            "scanned": True,
            "chapters": chapters,
        })
    counts = {"parsed": 0, "skipped": 0, "failed": 0, "stale": 0}
    for b in books:
        for c in b.get("chapters", []):
            counts[c["status"]] = counts.get(c["status"], 0) + 1
    partial = bool(unreviewed) or any(not b["scanned"] for b in books)
    return {
        "mode": mode,
        "reviewed_at": _now(),
        "tool_version": TOOL_VERSION,
        "books": books,
        "chapter_counts": counts,
        "unreviewed_regions": unreviewed,
        "error_count": error_count,
        "partial": partial,
    }


# ---------------------------------------------------------------------------
# Review runs — persistence + finding disposition inheritance
# ---------------------------------------------------------------------------


def create_run(db: OrivellumDB, *, mode: str, work_id: str | None,
               series_id: str | None, chapter_id: str | None = None,
               params: dict | None = None) -> dict:
    if mode not in MODES:
        raise SeriesReviewError(f"mode must be one of {MODES}")
    if mode in ("chapter_vs_book", "change_impact") and not chapter_id:
        raise SeriesReviewError(f"{mode} requires a chapter_id")
    scope = resolve_scope(db, mode=mode, work_id=work_id, series_id=series_id)
    # Snapshot the scope on the run: a durable job must review exactly the
    # books it was started for, even if series membership changes while it
    # is paused or running.  Reconciliation and the manifest consume ONLY
    # this snapshot — never live membership.
    params = dict(params or {})
    params["scope"] = scope
    run_id = str(uuid.uuid4())
    now = _now()
    with db.governed_write(
        operation="review_run.created",
        event_type="review_run.created",
        object_id=run_id,
        object_type="review_run",
        actor="author",
        detail=f"{mode}, {len(scope)} book(s)",
    ):
        db._conn.execute(
            """INSERT INTO review_run
               (id, mode, work_id, series_id, chapter_id, status, params,
                tool_version, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (run_id, mode, work_id, series_id, chapter_id, "pending",
             json.dumps(params), TOOL_VERSION, now, now),
        )
    run = get_run(db, run_id)
    run["scope"] = scope
    return run


def get_run(db: OrivellumDB, run_id: str) -> dict | None:
    row = db.read_conn().execute(
        "SELECT * FROM review_run WHERE id=?", (run_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["coverage"] = json.loads(d["coverage"] or "{}")
    d["params"] = json.loads(d["params"] or "{}")
    d["gate"] = json.loads(d["gate"]) if d.get("gate") else None
    return d


def list_runs(db: OrivellumDB, *, work_id: str | None = None,
              series_id: str | None = None, limit: int = 50) -> list[dict]:
    q = "SELECT * FROM review_run WHERE 1=1"
    args: list = []
    if work_id:
        q += " AND work_id=?"
        args.append(work_id)
    if series_id:
        q += " AND series_id=?"
        args.append(series_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(limit, 200)))
    out = []
    for r in db.read_conn().execute(q, args).fetchall():
        d = dict(r)
        d["coverage"] = json.loads(d["coverage"] or "{}")
        d["params"] = json.loads(d["params"] or "{}")
        d["gate"] = json.loads(d["gate"]) if d.get("gate") else None
        out.append(d)
    return out


def _inherited_status(db: OrivellumDB, dedupe_key: str) -> tuple[str, str | None, str]:
    """Latest author disposition for this finding identity, if any.

    A finding an author already marked intentional/dismissed/resolved must
    never resurrect as a fresh 'open' row on the next run.
    """
    row = db.read_conn().execute(
        """SELECT status, resolution, resolution_note FROM review_finding
           WHERE dedupe_key=? AND status != 'open'
           ORDER BY created_at DESC LIMIT 1""",
        (dedupe_key,),
    ).fetchone()
    if row:
        return row["status"], row["resolution"], row["resolution_note"]
    return "open", None, ""


def finalize_run(db: OrivellumDB, run_id: str, *,
                 findings: list[dict], manifest: dict) -> dict:
    """Persist findings + manifest atomically and close the run."""
    run = get_run(db, run_id)
    if run is None:
        raise SeriesReviewError(f"review run {run_id!r} not found")
    now = _now()
    gate = None
    if run["mode"] == "release_gate":
        blocking = [f for f in findings
                    if f["severity"] in ("high", "critical")
                    and _inherited_status(db, f["dedupe_key"])[0] == "open"]
        gate = {
            "verdict": "blocked" if (blocking or manifest["partial"]) else "passable",
            "blocking_findings": len(blocking),
            "partial_coverage": manifest["partial"],
        }
    with db.atomic():
        with db.governed_write(
            operation="review_run.completed",
            event_type="review_run.completed",
            object_id=run_id,
            object_type="review_run",
            actor="system",
            detail=f"{len(findings)} findings, partial={manifest['partial']}",
        ):
            db._conn.execute(
                "DELETE FROM review_finding WHERE run_id=?", (run_id,)
            )
            for f in findings:
                status, resolution, note = _inherited_status(db, f["dedupe_key"])
                db._conn.execute(
                    """INSERT OR IGNORE INTO review_finding
                       (id, run_id, finding_type, severity, subject,
                        explanation, evidence, canon_fact_id, canon_class,
                        dedupe_key, status, resolution, resolution_note,
                        created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (str(uuid.uuid4()), run_id, f["finding_type"],
                     f["severity"], f["subject"], f["explanation"],
                     json.dumps(f["evidence"]), f["canon_fact_id"],
                     f["canon_class"], f["dedupe_key"], status, resolution,
                     note, now),
                )
            db._conn.execute(
                """UPDATE review_run SET status='done', partial=?, coverage=?,
                   gate=?, updated_at=? WHERE id=?""",
                (1 if manifest["partial"] else 0, json.dumps(manifest),
                 json.dumps(gate) if gate else None, now, run_id),
            )
    return get_run(db, run_id)  # type: ignore[return-value]


def list_findings(db: OrivellumDB, run_id: str, *,
                  status: str | None = None,
                  severity: str | None = None) -> list[dict]:
    q = "SELECT * FROM review_finding WHERE run_id=?"
    args: list = [run_id]
    if status:
        q += " AND status=?"
        args.append(status)
    if severity:
        q += " AND severity=?"
        args.append(severity)
    q += (" ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 "
          "WHEN 'medium' THEN 2 ELSE 3 END, created_at")
    out = []
    for r in db.read_conn().execute(q, args).fetchall():
        d = dict(r)
        d["evidence"] = json.loads(d["evidence"] or "[]")
        out.append(d)
    return out


def set_finding_disposition(db: OrivellumDB, finding_id: str, *,
                            status: str, resolution: str | None = None,
                            note: str = "") -> dict | None:
    if status not in FINDING_STATUSES:
        raise SeriesReviewError(f"status must be one of {FINDING_STATUSES}")
    if resolution is not None and resolution not in RESOLUTION_CHOICES:
        raise SeriesReviewError(f"resolution must be one of {RESOLUTION_CHOICES}")
    if status in ("resolved", "intentional", "dismissed") and resolution is None:
        raise SeriesReviewError(
            "a resolution choice is required when closing a finding"
        )
    now = _now()
    with db.governed_write(
        operation="review_finding.dispositioned",
        event_type="review_finding.dispositioned",
        object_id=finding_id,
        object_type="review_finding",
        actor="author",
        detail=f"{status}/{resolution or '-'}",
    ):
        cur = db._conn.execute(
            """UPDATE review_finding SET status=?, resolution=?,
               resolution_note=?, resolved_at=? WHERE id=?""",
            (status, resolution, note,
             now if status != "open" else None, finding_id),
        )
        if cur.rowcount == 0:
            return None
    row = db.read_conn().execute(
        "SELECT * FROM review_finding WHERE id=?", (finding_id,)
    ).fetchone()
    d = dict(row)
    d["evidence"] = json.loads(d["evidence"] or "[]")
    return d


# ---------------------------------------------------------------------------
# Durable operation steps — one ledger step per book, then reconcile
# ---------------------------------------------------------------------------


def register_op_actions() -> None:
    from orivellum.capabilities.operations.registry import (  # noqa: PLC0415
        OpAction,
        OperationInterrupted,
        register,
    )

    def _ledger_step(ctx: Any, params: dict) -> dict:
        if ctx.should_stop():
            raise OperationInterrupted()
        wid = params.get("work_id") or ctx.work_id
        if not wid:
            raise SeriesReviewError("series_review.ledger needs work_id")
        result = build_book_ledger(ctx.db, wid)
        return {"work_id": wid, "item_count": result["item_count"],
                "fingerprint": result["fingerprint"]}

    def _reconcile_step(ctx: Any, params: dict) -> dict:
        if ctx.should_stop():
            raise OperationInterrupted()
        run_id = params.get("run_id") or (ctx.params or {}).get("run_id")
        run = get_run(ctx.db, run_id or "")
        if run is None:
            raise SeriesReviewError(f"review run {run_id!r} not found")
        # Use the scope SNAPSHOT taken at run creation — never live series
        # membership.  A membership change mid-run must not let the manifest
        # claim coverage of books this operation never built ledgers for.
        scope = (run.get("params") or {}).get("scope")
        if not scope:
            raise SeriesReviewError(
                f"review run {run_id!r} has no scope snapshot; "
                "re-create the run instead of resolving live membership"
            )
        findings = reconcile(ctx.db, mode=run["mode"], scope=scope,
                             chapter_id=run["chapter_id"])
        manifest = build_manifest(ctx.db, mode=run["mode"], scope=scope)
        done = finalize_run(ctx.db, run_id, findings=findings, manifest=manifest)
        return {"run_id": run_id, "findings": len(findings),
                "partial": bool(done["partial"])}

    register(OpAction(
        id="series_review.ledger",
        label="Build book evidence ledger",
        description="Builds the per-book fact/event/state/thread/terminology "
                    "ledger with span-level provenance and content hashes.",
        params_schema={"type": "object", "properties": {
            "work_id": {"type": "string"}}, "required": ["work_id"]},
        execute=_ledger_step,
    ))
    register(OpAction(
        id="series_review.reconcile",
        label="Reconcile ledgers across books",
        description="Runs the deterministic cross-book comparators, writes "
                    "findings with evidence, and emits the coverage manifest.",
        params_schema={"type": "object", "properties": {
            "run_id": {"type": "string"}}, "required": ["run_id"]},
        execute=_reconcile_step,
    ))
