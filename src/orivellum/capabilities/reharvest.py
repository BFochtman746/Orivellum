"""Domain-ontology re-harvest (THE RE-PROJECTION Phases 5-6).

Re-projects a ratified Work's substrate (chunks — never re-extracted) into
knowledge items whose kinds come from the Work's closed domain ontology
(:mod:`orivellum.capabilities.ontology`).

Guarantees:

- **Refuses Works without a ratified domain** — no ontology, no re-harvest.
- **Permitted doc_types only** — each domain reads only the doc_types listed
  in ``PERMITTED_DOC_TYPES``; unclassified (NULL) documents never seed
  knowledge.
- **Off-schema output is discarded and counted, never coerced.**  The discard
  count is a first-class number in the report — it measures extractor
  quality.
- **Substrate untouched** — reads chunks, writes only knowledge rows
  (``review_status='ai_auto'``, so every item is reviewable).
- **Prior machine items deleted per doc** before writing, otherwise the
  text-hash dedup in ``create_knowledge_item`` keeps stale rows alive.
  Approved items and quarantined evidence are never deleted.
- **One run per Work at a time** — a status-setting claim taken under
  ``db.atomic()`` fences concurrent runs; stale claims (> 2 h) are
  reclaimable so a crashed run never wedges the Work.

The pilot gate (one Work first, author sign-off unlocks the rest) lives in
the API routes, not here — this module is the mechanism, not the policy.
"""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from orivellum.capabilities.ontology import (
    PERMITTED_DOC_TYPES,
    allowed_kinds_for_domain,
)

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# Per-doc chunk budget per LLM call and per-doc call cap — bounded work per
# document regardless of size.
_CHUNKS_PER_CALL = 3
_MAX_CALLS_PER_DOC = 8
_MAX_ITEMS_PER_CALL = 12
# A running claim older than this is stale (crashed run) and reclaimable.
_STALE_CLAIM = timedelta(hours=2)

_STATUS_KEY = "reharvest_status:{work_id}"
_REPORT_KEY = "reharvest_report:{work_id}"

_DOMAIN_PROMPT = (
    "You are re-indexing a document for a knowledge base with a STRICT closed schema.\n\n"
    "Document title: {title}\n"
    "Domain: {domain}\n"
    "ALLOWED kinds (closed set — nothing else exists): {kinds}\n\n"
    "Extract up to {max_items} knowledge items from the text below. Every item MUST use "
    "one of the allowed kinds. If nothing in the text fits an allowed kind, return fewer "
    "items or none — NEVER invent a kind and NEVER force content into a kind it does "
    "not fit.\n\n"
    "Respond with ONLY a JSON object:\n"
    '{{"items": [{{"kind": "<one of the allowed kinds>", "text": "<the knowledge '
    'statement>", "subject": "<what it is about>", "confidence": 0.0-1.0}}]}}\n\n'
    "Text:\n{chunk}\n"
)


class ReharvestBusy(RuntimeError):
    """Another re-harvest run currently owns this Work."""


class ReharvestRefused(ValueError):
    """The Work is not eligible (no ratified domain)."""


def _now() -> datetime:
    return datetime.now(UTC)


def claim_run(db: OrivellumDB, work_id: str) -> str:
    """Atomically claim the Work for one run (raises ReharvestBusy if owned).

    A claim is a ``reharvest_status:<work_id>`` setting holding
    ``{"state":"running","started_at":...,"token":...}``.  Claims older than
    ``_STALE_CLAIM`` belong to a crashed run and are silently reclaimed.

    Returns the run's fencing token.  Every finalization (report write +
    release) requires the token to still match, so a stale worker that was
    reclaimed can never clobber the new run's status or report.
    """
    import uuid  # noqa: PLC0415

    key = _STATUS_KEY.format(work_id=work_id)
    token = str(uuid.uuid4())
    with db.atomic():
        raw = db.get_setting(key, "")
        if raw:
            try:
                cur = json.loads(raw)
            except Exception:
                cur = {}
            if cur.get("state") == "running":
                started = cur.get("started_at") or ""
                try:
                    fresh = _now() - datetime.fromisoformat(started) < _STALE_CLAIM
                except Exception:
                    fresh = False
                if fresh:
                    raise ReharvestBusy(f"A re-harvest run for work {work_id} is already running")
        db.set_setting(
            key,
            json.dumps({"state": "running", "started_at": _now().isoformat(), "token": token}),
        )
    return token


def _token_current(db: OrivellumDB, work_id: str, token: str | None) -> bool:
    """True when *token* still owns the run claim (or no fencing requested)."""
    if token is None:
        return True
    status = get_run_status(db, work_id)
    return status.get("token") == token


def release_run(db: OrivellumDB, work_id: str, final_state: str, token: str | None = None) -> bool:
    """Release the claim.  With a *token*, only the current owner may release
    (a reclaimed stale worker's release is a silent no-op).  Returns whether
    the release was applied."""
    key = _STATUS_KEY.format(work_id=work_id)
    with db.atomic():
        if not _token_current(db, work_id, token):
            return False
        db.set_setting(key, json.dumps({"state": final_state, "finished_at": _now().isoformat()}))
        return True


def get_run_status(db: OrivellumDB, work_id: str) -> dict:
    raw = db.get_setting(_STATUS_KEY.format(work_id=work_id), "")
    if not raw:
        return {"state": "idle"}
    try:
        return json.loads(raw)
    except Exception:
        return {"state": "idle"}


def get_report(db: OrivellumDB, work_id: str) -> dict | None:
    raw = db.get_setting(_REPORT_KEY.format(work_id=work_id), "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _parse_items(raw: str) -> list[dict]:
    """Parse the model's JSON reply; malformed replies yield [] (never raise)."""
    raw = (raw or "").strip()
    if not raw:
        return []
    # Strip common markdown fencing.
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except Exception:
        return []
    items = data.get("items") if isinstance(data, dict) else None
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def _reharvest_doc(
    db: OrivellumDB,
    *,
    doc: dict,
    work_id: str,
    domain: str,
    allowed: frozenset[str],
    report: dict,
    llm_call,
    shield_wrap,
    base_url: str,
    model: str,
    timeout: int,
) -> None:
    """Re-harvest one permitted document into *report* (mutated in place)."""
    doc_id = doc["id"]
    with db._lock:
        chunk_rows = db._conn.execute(
            "SELECT text FROM chunks WHERE doc_id=? ORDER BY page, created_at",
            (doc_id,),
        ).fetchall()
    texts = [r["text"] for r in chunk_rows if (r["text"] or "").strip()]
    if not texts:
        report["docs_skipped_no_chunks"] += 1
        return

    # Stale machine items must go first — text-hash dedup would keep
    # them alive otherwise.  Approved + quarantined survive (default).
    report["prior_items_deleted"] += db.delete_document_knowledge(doc_id)

    title = doc.get("title") or doc_id
    groups = [texts[i : i + _CHUNKS_PER_CALL] for i in range(0, len(texts), _CHUNKS_PER_CALL)][
        :_MAX_CALLS_PER_DOC
    ]
    for group in groups:
        fenced = shield_wrap("\n\n".join(group), source=f"document \u201c{title}\u201d")
        prompt = _DOMAIN_PROMPT.format(
            title=title,
            domain=domain,
            kinds=", ".join(sorted(allowed)),
            max_items=_MAX_ITEMS_PER_CALL,
            chunk=fenced,
        )
        raw = llm_call(prompt, base_url, model, timeout, db=db)
        if not raw:
            report["llm_calls_failed"] += 1
            continue
        for item in _parse_items(raw)[:_MAX_ITEMS_PER_CALL]:
            kind = (item.get("kind") or "").strip().lower()
            text = (item.get("text") or "").strip()
            if not text:
                continue
            if kind not in allowed:
                # Off-schema: discarded and counted, never coerced.
                report["items_discarded_off_schema"] += 1
                continue
            try:
                conf = max(0.0, min(1.0, float(item.get("confidence", 0.7))))
            except Exception:
                conf = 0.7
            db.create_knowledge_item(
                work_id=work_id,
                kind=kind,
                text=text[:2000],
                subject=(item.get("subject") or "").strip()[:300] or None,
                predicate=None,
                obj=None,
                confidence=conf,
                source_doc_id=doc_id,
                review_status="ai_auto",
                meta={"source": "reharvest", "domain": domain},
            )
            report["items_created"] += 1
    report["docs_processed"] += 1


def reharvest_work(
    db: OrivellumDB, work_id: str, *, claimed: bool = False, token: str | None = None
) -> dict:
    """Re-harvest one ratified Work under its domain ontology.

    Returns the report dict (also persisted to the
    ``reharvest_report:<work_id>`` setting).  Raises :class:`ReharvestRefused`
    when the Work has no ratified domain and :class:`ReharvestBusy` when a
    fresh run already owns the Work (unless *claimed* — the caller already
    holds the claim, e.g. a route that pre-claims for a clean 409, and passes
    its fencing *token*).  Finalization (report write + release) is fenced on
    the token: a stale worker whose claim was reclaimed cannot clobber the
    newer run's report or status.
    """
    from orivellum.api._deps import get_config  # noqa: PLC0415 — avoid cycles
    from orivellum.capabilities.knowledge_harvest import (  # noqa: PLC0415
        _call_llm_sync,
    )
    from orivellum.capabilities.shield import wrap as _shield_wrap  # noqa: PLC0415

    work = db.get_work(work_id)
    if not work:
        raise ReharvestRefused(f"Work {work_id!r} not found")
    domain = work.get("domain")
    allowed = allowed_kinds_for_domain(domain)
    if not domain or allowed is None:
        raise ReharvestRefused(
            f"Work {work_id!r} has no ratified domain — re-harvest is refused. "
            "Ratify the Work (assigning a domain) first."
        )
    permitted_types = PERMITTED_DOC_TYPES.get(domain, frozenset())

    if not claimed:
        token = claim_run(db, work_id)

    cfg = get_config()
    base_url = cfg.serving.base_url
    model = getattr(cfg.serving, "workhorse_model", None) or cfg.serving.model
    timeout = getattr(cfg.serving, "extraction_timeout_sec", 90)

    report: dict = {
        "work_id": work_id,
        "work_title": work.get("title"),
        "domain": domain,
        "allowed_kinds": sorted(allowed),
        "started_at": _now().isoformat(),
        "docs_processed": 0,
        "docs_skipped_doc_type": 0,
        "docs_skipped_no_chunks": 0,
        "prior_items_deleted": 0,
        "items_created": 0,
        "items_discarded_off_schema": 0,
        "llm_calls_failed": 0,
        "state": "running",
    }

    try:
        docs = db.list_documents(work_id=work_id, limit=1000)
        for doc in docs:
            if doc.get("doc_type") not in permitted_types:
                report["docs_skipped_doc_type"] += 1
                continue
            _reharvest_doc(
                db,
                doc=doc,
                work_id=work_id,
                domain=domain,
                allowed=allowed,
                report=report,
                llm_call=_call_llm_sync,
                shield_wrap=_shield_wrap,
                base_url=base_url,
                model=model,
                timeout=timeout,
            )

        report["state"] = "done"
    except BaseException as exc:
        report["state"] = "error"
        report["error"] = str(exc)[:500]
        raise
    finally:
        report["finished_at"] = _now().isoformat()
        # Fenced finalization: write the report and release the claim in ONE
        # transaction, and only if this run still owns the claim.
        with db.atomic():
            if _token_current(db, work_id, token):
                db.set_setting(_REPORT_KEY.format(work_id=work_id), json.dumps(report))
                release_run(db, work_id, report["state"], token)
            else:
                logger.warning(
                    "reharvest_work %s: claim was reclaimed by a newer run — "
                    "discarding this run's report/status",
                    work_id,
                )
        with contextlib.suppress(Exception):
            db.invalidate_gap_cache(work_id)
        logger.info(
            "reharvest_work %s (domain=%s): state=%s created=%d discarded=%d docs=%d",
            work_id,
            domain,
            report["state"],
            report["items_created"],
            report["items_discarded_off_schema"],
            report["docs_processed"],
        )

    return report
