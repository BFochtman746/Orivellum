"""Book-to-book handoff contract engine (Masterpiece Pipeline §5).

Every book in a connected sequence gets a versioned **End-State Package**
(what the book leaves behind) and the successor gets an **Opening Contract**
(what its opening scenes must address).  An automated **Handoff Audit**
compares these against the actual final and opening text with sentence-level
citations.

Design rules (identical to the series-review discipline):
- LLM extracts; ``ground_quote_span`` verifies; ungroundable output is
  DISCARDED, never coerced.
- The audit emits an explicit ``insufficient_evidence`` flag instead of
  fabricating a finding.
- Author ratification gates the package before it binds the successor.
- Severity is code-computed; the model never picks it.
- All LLM calls go through ``llm_call`` at temperature 0.0.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from orivellum.capabilities.atlas import _fence, _parse_json, ground_quote_span

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOOL_VERSION = "handoff-1.0"

# How much of the book counts as the "ending window".
ENDING_FRACTION = 0.20          # last 20 % of chapter texts
OPENING_WINDOW_CHARS = 20_000   # first ~5k words of successor
OPENING_CHAPTERS_MAX = 3        # or up to 3 chapters, whichever is smaller

# LLM limits
_TIMEOUT_SEC = 90
_MAX_ITEMS_PER_CALL = 25

# Closed finding-type registry — every comparator must use one of these.
FINDING_TYPES: frozenset[str] = frozenset(
    {
        "hard_contradiction",
        "missing_bridge",
        "unexplained_time_jump",
        "unexplained_location_change",
        "unexplained_knowledge_change",
        "unexplained_injury_change",
        "unexplained_object_change",
        "dropped_promise",
        "dropped_thread",
        "excessive_recap",
        "insufficient_reorientation",
        "accidental_spoiler",
        "emotional_discontinuity",
        "no_fresh_promise",
    }
)

# Severity by finding type — code-computed, never model-chosen.
_FINDING_SEVERITY: dict[str, str] = {
    "hard_contradiction": "critical",
    "missing_bridge": "high",
    "unexplained_time_jump": "high",
    "unexplained_location_change": "medium",
    "unexplained_knowledge_change": "high",
    "unexplained_injury_change": "high",
    "unexplained_object_change": "medium",
    "dropped_promise": "high",
    "dropped_thread": "medium",
    "excessive_recap": "low",
    "insufficient_reorientation": "medium",
    "accidental_spoiler": "high",
    "emotional_discontinuity": "medium",
    "no_fresh_promise": "high",
}

VALID_STATUSES = ("open", "accepted", "intentional", "dismissed")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _jload(s: str | None, default: Any = None) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:
        return default


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _dedupe_key(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


class HandoffError(RuntimeError):
    """Extraction/audit step failed — the run must stop rather than silently lie."""


# ---------------------------------------------------------------------------
# LLM gateway
# ---------------------------------------------------------------------------


def _call(prompt: str, *, purpose: str, cfg: "OrivellumConfig", db: "OrivellumDB") -> Any:
    from orivellum.capabilities.llm import llm_call  # noqa: PLC0415

    result = llm_call(
        [{"role": "user", "content": prompt}],
        cfg=cfg,
        db=db,
        purpose=purpose,
        timeout=_TIMEOUT_SEC,
        temperature=0.0,
    )
    if not result.ok or not result.text:
        raise HandoffError(f"{purpose} LLM call failed: {result.error or 'no response'}")
    parsed = _parse_json(result.text)
    if parsed is None:
        raise HandoffError(f"{purpose} returned unparseable JSON")
    return parsed


# ---------------------------------------------------------------------------
# Chapter helpers
# ---------------------------------------------------------------------------


def _chapters(db: "OrivellumDB", work_id: str) -> list[dict]:
    rows = db.read_conn().execute(
        "SELECT id, seq, title, text FROM book_chapters WHERE work_id=? ORDER BY seq",
        (work_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_work(db: "OrivellumDB", work_id: str) -> dict:
    row = db.read_conn().execute(
        "SELECT id, title FROM works WHERE id=?", (work_id,)
    ).fetchone()
    if row is None:
        raise HandoffError(f"Work {work_id!r} not found")
    return dict(row)


def _ending_chapters(chapters: list[dict]) -> list[dict]:
    """The last 20 % of chapters (at least 1, at most 5)."""
    if not chapters:
        return []
    n = max(1, min(5, round(len(chapters) * ENDING_FRACTION) or 1))
    return [c for c in chapters[-n:] if (c.get("text") or "").strip()]


def _opening_text(chapters: list[dict]) -> str:
    """Combined text of first 1–3 chapters, capped at OPENING_WINDOW_CHARS."""
    out = ""
    for ch in chapters[:OPENING_CHAPTERS_MAX]:
        if not (ch.get("text") or "").strip():
            continue
        out += (ch.get("text") or "") + "\n\n"
        if len(out) >= OPENING_WINDOW_CHARS:
            break
    return out[:OPENING_WINDOW_CHARS]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_END_STATE_PROMPT = """\
You are an expert developmental editor analyzing the ENDING of a novel.
Extract every significant "end-state" item from the final chapters below.

Categories to cover:
- dramatic_question: is the book's central dramatic question resolved or unresolved?
- character_state: final physical/emotional/relationship/location/knowledge state of major characters
- injury: wounds, disabilities, or lasting physical changes at the book's end
- possession: significant objects a character holds at the end
- promise: explicit or implicit promises/debts/obligations still owed
- thread: open plot threads, foreshadowing, deferred questions
- emotional_tone: the final emotional register of the closing scene
- world_state: world/historical/biblical facts established that constrain the next book

Rules:
- Extract ONLY items explicitly grounded in the text below.
- For each item, quote a SHORT verbatim passage (under 120 chars) that directly establishes it.
- No speculation. If something is ambiguous, note ambiguity in the subject.
- Max {max_items} items total.

Return ONLY JSON:
{{"items": [
  {{"category": "<category>", "subject": "<who or what>",
    "claim": "<one-sentence factual claim>",
    "quote": "<VERBATIM passage from the text below>"}}
]}}

FINAL CHAPTERS:
{text}"""


_OPENING_PROMPT = """\
You are an expert developmental editor analyzing the OPENING of a novel.
Extract every significant element from the opening chapters below.

Categories to cover:
- orientation: time/place/POV established for the reader
- character_reentry: how recurring characters are reintroduced (or their absence acknowledged)
- inherited_state_acknowledged: states from a prior book explicitly acknowledged
- inherited_state_changed: prior-book states changed and explained
- inherited_state_withheld: prior-book states deliberately not mentioned (spoiler/pacing choice)
- dramatic_question: the new central dramatic question this book raises
- emotional_tone: the opening emotional register

Rules:
- Quote only SHORT verbatim passages (under 120 chars) that directly establish each item.
- No speculation. Max {max_items} items.

Return ONLY JSON:
{{"items": [
  {{"category": "<category>", "subject": "<who or what>",
    "claim": "<one-sentence factual claim>",
    "quote": "<VERBATIM passage from the text below>"}}
]}}

OPENING CHAPTERS:
{text}"""


_AUDIT_PROPOSE_PROMPT = """\
You are auditing the handoff from Book N to Book N+1 in a series.
Below are the END-STATE items from Book N and the OPENING TEXT of Book N+1.

For each end-state item, determine whether the opening correctly handles it.
Propose findings ONLY for genuine problems — not stylistic preferences.

Allowed finding types:
- hard_contradiction: the opening text directly contradicts an end-state fact
- missing_bridge: a major state change has no explanation in the opening window
- unexplained_time_jump: significant time gap not acknowledged
- unexplained_location_change: character location changed without acknowledgment
- unexplained_knowledge_change: character knows something they shouldn't yet, or forgot something
- unexplained_injury_change: injury present at book end missing without healing explanation
- unexplained_object_change: significant possessed object missing/gained without explanation
- dropped_promise: an explicit promise/obligation from Book N is never mentioned
- dropped_thread: an open thread from Book N is not referenced at all
- insufficient_reorientation: reader not grounded in time/place/character state early enough
- emotional_discontinuity: opening emotional register ignores the ending's emotional state
- no_fresh_promise: opening window establishes no new dramatic question
- accidental_spoiler: opening reveals end-state information to a reader who hasn't read Book N

Return ONLY JSON:
{{"proposals": [
  {{"finding_type": "<one of the types above>",
    "subject": "<who or what>",
    "explanation": "<one sentence: what the problem is>",
    "end_state_quote": "<VERBATIM quote from end-state text that establishes the prior state>",
    "opening_quote": "<VERBATIM quote from opening text that shows the problem, or empty string if missing>",
    "insufficient_evidence": false}}
]}}

Return {{"proposals": []}} if there are no genuine problems.
If you cannot determine whether there is a problem because the opening text is too brief, set insufficient_evidence to true and explain in the explanation field.

END-STATE ITEMS (from Book N):
{end_state}

OPENING TEXT (Book N+1):
{opening}"""


_VERIFY_PROMPT = """\
You are verifying a proposed book-handoff finding.

Finding type: {finding_type}
Subject: {subject}
Proposed explanation: {explanation}

End-state evidence: "{end_state_quote}"
Opening evidence: "{opening_quote}"
Insufficient evidence claimed: {insufficient_evidence}

Is this a genuine handoff problem? Answer "confirmed" only if:
- The finding type accurately describes the problem
- The evidence actually supports the claim
- A reader would notice this as a real continuity or orientation problem

Answer "rejected" if the passages are compatible, the change is plausible without an explanation,
or the opening text adequately handles the end-state item in a way the proposer missed.

Return ONLY JSON:
{{"verdict": "confirmed" or "rejected",
  "reasoning": "<one sentence explaining the decision>"}}"""


# ---------------------------------------------------------------------------
# End-State Package extraction
# ---------------------------------------------------------------------------


def _extract_end_state_items(
    db: "OrivellumDB", cfg: "OrivellumConfig", chapters: list[dict]
) -> list[dict]:
    """LLM-extract end-state items from ending chapters; ground every quote."""
    combined = "\n\n".join(
        f"[Chapter {c['seq']}: {c.get('title') or ''}]\n{c['text']}"
        for c in chapters
        if (c.get("text") or "").strip()
    )
    if not combined.strip():
        return []

    # Build a chapter-text lookup for grounding.
    texts = {c["id"]: (c.get("text") or "") for c in chapters}
    all_text = combined  # for grounding searches across the ending window

    parsed = _call(
        _END_STATE_PROMPT.format(max_items=_MAX_ITEMS_PER_CALL, text=combined[:24_000]),
        purpose="handoff.extract_end_state",
        cfg=cfg,
        db=db,
    )

    items: list[dict] = []
    for raw in (parsed.get("items") or [])[:_MAX_ITEMS_PER_CALL]:
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category") or "").strip()
        subject = str(raw.get("subject") or "").strip()
        claim = str(raw.get("claim") or "").strip()
        quote = str(raw.get("quote") or "").strip()
        if not (category and subject and claim and quote):
            continue

        # Ground quote across the combined ending text.
        found = ground_quote_span(quote, all_text)
        if found is None:
            logger.debug("handoff: discarding ungroundable end-state quote %r", quote[:80])
            continue
        offset, verbatim = found

        # Map the offset back to the specific chapter.
        chapter_id: str | None = None
        chapter_seq: int | None = None
        cumulative = 0
        for c in chapters:
            t = (c.get("text") or "")
            if cumulative <= offset < cumulative + len(t) + 3:  # +3 for separator
                chapter_id = c["id"]
                chapter_seq = int(c["seq"])
                break
            cumulative += len(t) + 2  # "\n\n"

        items.append(
            {
                "id": _uid(),
                "category": category,
                "subject": subject,
                "claim": claim,
                "quote": verbatim,
                "offset": offset,
                "chapter_id": chapter_id,
                "chapter_seq": chapter_seq,
            }
        )

    return items


def build_end_state_package(
    db: "OrivellumDB",
    cfg: "OrivellumConfig",
    work_id: str,
) -> dict:
    """Extract and store an End-State Package for a book.

    Returns the new package dict (status=``draft``; ratification is a
    separate author action).  If the book has no chapter text the package is
    stored empty but is still created so the author can fill it manually.
    """
    work = _get_work(db, work_id)
    chapters = _chapters(db, work_id)
    ending = _ending_chapters(chapters)

    items: list[dict] = []
    extraction_error: str | None = None
    if ending:
        try:
            items = _extract_end_state_items(db, cfg, ending)
        except HandoffError as exc:
            extraction_error = str(exc)
            logger.warning("handoff: end-state extraction failed for %s: %s", work_id, exc)

    # Compute next version number.
    with db._lock:
        existing = db._conn.execute(
            "SELECT COALESCE(MAX(version),0) AS v FROM handoff_package WHERE work_id=?",
            (work_id,),
        ).fetchone()
    version = (existing["v"] if existing else 0) + 1

    now = _now()
    pkg_id = _uid()
    payload = {
        "items": items,
        "ending_chapter_count": len(ending),
        "ending_chapter_seqs": [c["seq"] for c in ending],
    }
    extraction_meta: dict = {
        "tool_version": TOOL_VERSION,
        "chapters_scanned": len(ending),
        "items_extracted": len(items),
        "built_at": now,
    }
    if extraction_error:
        extraction_meta["error"] = extraction_error

    with db._lock:
        db._conn.execute(
            """INSERT INTO handoff_package
               (id, work_id, version, status, payload, extraction_meta,
                author_intent, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                pkg_id,
                work_id,
                version,
                "draft",
                json.dumps(payload, ensure_ascii=False),
                json.dumps(extraction_meta, ensure_ascii=False),
                "",
                now,
                now,
            ),
        )
        db._conn.commit()

    logger.info(
        "handoff: built end-state package v%d for %r (%d items)",
        version,
        work["title"],
        len(items),
    )
    return _get_package(db, pkg_id)


def _get_package(db: "OrivellumDB", package_id: str) -> dict | None:
    row = db.read_conn().execute(
        "SELECT * FROM handoff_package WHERE id=?", (package_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["payload"] = _jload(d.get("payload"), {})
    d["extraction_meta"] = _jload(d.get("extraction_meta"), {})
    return d


def get_package(db: "OrivellumDB", package_id: str) -> dict | None:
    return _get_package(db, package_id)


def latest_package(db: "OrivellumDB", work_id: str) -> dict | None:
    """Most recent package for a work, ratified or draft."""
    row = db.read_conn().execute(
        "SELECT id FROM handoff_package WHERE work_id=? ORDER BY version DESC LIMIT 1",
        (work_id,),
    ).fetchone()
    return _get_package(db, row["id"]) if row else None


def list_packages(db: "OrivellumDB", work_id: str) -> list[dict]:
    rows = db.read_conn().execute(
        "SELECT id FROM handoff_package WHERE work_id=? ORDER BY version DESC",
        (work_id,),
    ).fetchall()
    return [p for r in rows if (p := _get_package(db, r["id"])) is not None]


def ratify_package(db: "OrivellumDB", package_id: str, actor: str = "author") -> dict:
    """Author ratifies the package — it can now bind the successor."""
    now = _now()
    with db._lock:
        updated = db._conn.execute(
            """UPDATE handoff_package
               SET status='ratified', ratified_at=?, ratified_by=?, updated_at=?
               WHERE id=? AND status='draft'
               RETURNING id""",
            (now, actor, now, package_id),
        ).fetchone()
        if not updated:
            raise HandoffError(
                f"Package {package_id!r} not found or already ratified"
            )
        db._conn.commit()
    return _get_package(db, package_id)


def update_package_intent(
    db: "OrivellumDB", package_id: str, author_intent: str
) -> dict:
    """Author sets or edits the handoff-intent statement."""
    now = _now()
    with db._lock:
        db._conn.execute(
            "UPDATE handoff_package SET author_intent=?, updated_at=? WHERE id=?",
            (author_intent.strip(), now, package_id),
        )
        db._conn.commit()
    return _get_package(db, package_id)


# ---------------------------------------------------------------------------
# Opening Contract extraction
# ---------------------------------------------------------------------------


def _extract_opening_items(
    db: "OrivellumDB", cfg: "OrivellumConfig", opening_text: str
) -> list[dict]:
    """LLM-extract opening contract items; ground every quote."""
    if not opening_text.strip():
        return []

    parsed = _call(
        _OPENING_PROMPT.format(max_items=_MAX_ITEMS_PER_CALL, text=opening_text[:24_000]),
        purpose="handoff.extract_opening",
        cfg=cfg,
        db=db,
    )

    items: list[dict] = []
    for raw in (parsed.get("items") or [])[:_MAX_ITEMS_PER_CALL]:
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category") or "").strip()
        subject = str(raw.get("subject") or "").strip()
        claim = str(raw.get("claim") or "").strip()
        quote = str(raw.get("quote") or "").strip()
        if not (category and subject and claim):
            continue

        offset: int | None = None
        verbatim = quote
        if quote:
            found = ground_quote_span(quote, opening_text)
            if found is None:
                logger.debug("handoff: discarding ungroundable opening quote %r", quote[:80])
                continue
            offset, verbatim = found

        items.append(
            {
                "id": _uid(),
                "category": category,
                "subject": subject,
                "claim": claim,
                "quote": verbatim,
                "offset": offset,
            }
        )

    return items


def build_opening_contract(
    db: "OrivellumDB",
    cfg: "OrivellumConfig",
    work_id: str,
    prior_package_id: str | None = None,
) -> dict:
    """Build and store an Opening Contract for a book.

    ``prior_package_id`` links the contract to a specific End-State Package
    so the audit knows what the successor was written against.
    """
    work = _get_work(db, work_id)
    chapters = _chapters(db, work_id)
    opening_text = _opening_text(chapters)

    # Determine opening window chapter count.
    opening_chs = [c for c in chapters[:OPENING_CHAPTERS_MAX] if (c.get("text") or "").strip()]

    items: list[dict] = []
    extraction_error: str | None = None
    if opening_text.strip():
        try:
            items = _extract_opening_items(db, cfg, opening_text)
        except HandoffError as exc:
            extraction_error = str(exc)
            logger.warning("handoff: opening extraction failed for %s: %s", work_id, exc)

    with db._lock:
        existing = db._conn.execute(
            "SELECT COALESCE(MAX(version),0) AS v FROM opening_contract WHERE work_id=?",
            (work_id,),
        ).fetchone()
    version = (existing["v"] if existing else 0) + 1

    now = _now()
    cid = _uid()
    payload = {"items": items, "opening_chapter_count": len(opening_chs)}
    extraction_meta: dict = {
        "tool_version": TOOL_VERSION,
        "window_chars": len(opening_text),
        "items_extracted": len(items),
        "built_at": now,
    }
    if extraction_error:
        extraction_meta["error"] = extraction_error

    with db._lock:
        db._conn.execute(
            """INSERT INTO opening_contract
               (id, work_id, version, prior_package_id, window_chars,
                payload, extraction_meta, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                cid,
                work_id,
                version,
                prior_package_id,
                len(opening_text),
                json.dumps(payload, ensure_ascii=False),
                json.dumps(extraction_meta, ensure_ascii=False),
                now,
                now,
            ),
        )
        db._conn.commit()

    logger.info(
        "handoff: built opening contract v%d for %r (%d items)",
        version,
        work["title"],
        len(items),
    )
    return _get_contract(db, cid)


def _get_contract(db: "OrivellumDB", contract_id: str) -> dict | None:
    row = db.read_conn().execute(
        "SELECT * FROM opening_contract WHERE id=?", (contract_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["payload"] = _jload(d.get("payload"), {})
    d["extraction_meta"] = _jload(d.get("extraction_meta"), {})
    return d


def get_contract(db: "OrivellumDB", contract_id: str) -> dict | None:
    return _get_contract(db, contract_id)


def latest_contract(db: "OrivellumDB", work_id: str) -> dict | None:
    row = db.read_conn().execute(
        "SELECT id FROM opening_contract WHERE work_id=? ORDER BY version DESC LIMIT 1",
        (work_id,),
    ).fetchone()
    return _get_contract(db, row["id"]) if row else None


def list_contracts(db: "OrivellumDB", work_id: str) -> list[dict]:
    rows = db.read_conn().execute(
        "SELECT id FROM opening_contract WHERE work_id=? ORDER BY version DESC",
        (work_id,),
    ).fetchall()
    return [c for r in rows if (c := _get_contract(db, r["id"])) is not None]


# ---------------------------------------------------------------------------
# Handoff Audit
# ---------------------------------------------------------------------------


def _render_end_state_for_prompt(items: list[dict]) -> str:
    lines = []
    for i, it in enumerate(items):
        lines.append(
            f"E{i}: [{it['category']}] {it['subject']} — {it['claim']}"
            + (f' (quote: "{it["quote"][:120]}")' if it.get("quote") else "")
        )
    return "\n".join(lines) if lines else "(no end-state items extracted)"


def _verify_finding(
    db: "OrivellumDB",
    cfg: "OrivellumConfig",
    finding: dict,
) -> bool:
    """Evidence-chain verification: confirmed or rejected."""
    try:
        verdict = _call(
            _VERIFY_PROMPT.format(
                finding_type=finding["finding_type"],
                subject=finding["subject"][:200],
                explanation=finding["explanation"][:400],
                end_state_quote=(finding.get("end_state_quote") or "")[:300],
                opening_quote=(finding.get("opening_quote") or "")[:300],
                insufficient_evidence=finding.get("insufficient_evidence", False),
            ),
            purpose="handoff.verify",
            cfg=cfg,
            db=db,
        )
        return str(verdict.get("verdict") or "").strip().lower() == "confirmed"
    except HandoffError:
        return False


def _check_no_fresh_promise(
    db: "OrivellumDB",
    cfg: "OrivellumConfig",
    opening_items: list[dict],
    opening_text: str,
) -> dict | None:
    """Deterministic check: does the opening inventory include a dramatic_question?"""
    has_promise = any(
        it.get("category") == "dramatic_question" for it in opening_items
    )
    if has_promise:
        return None
    # LLM double-check before flagging — maybe it missed a subtle promise.
    # We return a low-confidence finding that the verifier will confirm/reject.
    return {
        "finding_type": "no_fresh_promise",
        "subject": "opening dramatic question",
        "explanation": (
            "The opening window does not establish a fresh central dramatic question "
            "for this book — the reader has no new promise to follow."
        ),
        "end_state_quote": "",
        "opening_quote": "",
        "insufficient_evidence": len(opening_text.strip()) < 500,
    }


def _check_insufficient_reorientation(opening_items: list[dict]) -> dict | None:
    """Check that the opening at least orients the reader in time/place."""
    has_orientation = any(it.get("category") == "orientation" for it in opening_items)
    if has_orientation:
        return None
    return {
        "finding_type": "insufficient_reorientation",
        "subject": "reader orientation",
        "explanation": (
            "The opening window contains no clear time/place/POV orientation — "
            "the reader is dropped in without grounding."
        ),
        "end_state_quote": "",
        "opening_quote": "",
        "insufficient_evidence": False,
    }


def run_handoff_audit(
    db: "OrivellumDB",
    cfg: "OrivellumConfig",
    prior_work_id: str,
    successor_work_id: str,
    prior_package_id: str | None = None,
    successor_contract_id: str | None = None,
) -> dict:
    """Run the full Handoff Audit between two books.

    Resolves the latest package and contract if not specified, then:
    1. Proposes findings via LLM over (end-state, opening-text) pair.
    2. Evidence-chains each proposal (grounding + verification).
    3. Runs deterministic checks (no_fresh_promise, insufficient_reorientation).
    4. Stores findings and the audit run row.

    Returns the audit dict.
    """
    prior_work = _get_work(db, prior_work_id)
    successor_work = _get_work(db, successor_work_id)

    # Resolve package / contract.
    if prior_package_id:
        pkg = _get_package(db, prior_package_id)
        if not pkg or pkg["work_id"] != prior_work_id:
            raise HandoffError(
                f"Package {prior_package_id!r} not found or wrong work"
            )
    else:
        pkg = latest_package(db, prior_work_id)

    if successor_contract_id:
        contract = _get_contract(db, successor_contract_id)
        if not contract or contract["work_id"] != successor_work_id:
            raise HandoffError(
                f"Contract {successor_contract_id!r} not found or wrong work"
            )
    else:
        contract = latest_contract(db, successor_work_id)

    # Get actual opening text for grounding.
    successor_chapters = _chapters(db, successor_work_id)
    opening_text = _opening_text(successor_chapters)

    # Get end-state items.
    end_state_items: list[dict] = (
        (pkg.get("payload") or {}).get("items") or [] if pkg else []
    )
    opening_items: list[dict] = (
        (contract.get("payload") or {}).get("items") or [] if contract else []
    )

    # Create the audit run row.
    now = _now()
    audit_id = _uid()
    coverage = {
        "prior_work_id": prior_work_id,
        "prior_work_title": prior_work["title"],
        "successor_work_id": successor_work_id,
        "successor_work_title": successor_work["title"],
        "package_id": pkg["id"] if pkg else None,
        "package_version": pkg["version"] if pkg else None,
        "package_ratified": pkg["status"] == "ratified" if pkg else False,
        "contract_id": contract["id"] if contract else None,
        "contract_version": contract["version"] if contract else None,
        "end_state_items": len(end_state_items),
        "opening_items": len(opening_items),
        "opening_window_chars": len(opening_text),
        "tool_version": TOOL_VERSION,
        "partial": not (pkg and pkg["status"] == "ratified"),
    }

    with db._lock:
        db._conn.execute(
            """INSERT INTO handoff_audit
               (id, prior_work_id, successor_work_id, package_id, contract_id,
                status, coverage, error, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                audit_id,
                prior_work_id,
                successor_work_id,
                pkg["id"] if pkg else None,
                contract["id"] if contract else None,
                "running",
                json.dumps(coverage, ensure_ascii=False),
                None,
                now,
                now,
            ),
        )
        db._conn.commit()

    findings: list[dict] = []
    error: str | None = None

    try:
        # LLM-proposed findings from (end-state, opening) pair.
        if end_state_items and opening_text.strip():
            proposed = _call(
                _AUDIT_PROPOSE_PROMPT.format(
                    end_state=_render_end_state_for_prompt(end_state_items),
                    opening=opening_text[:12_000],
                ),
                purpose="handoff.audit_propose",
                cfg=cfg,
                db=db,
            )
            for prop in (proposed.get("proposals") or [])[:30]:
                if not isinstance(prop, dict):
                    continue
                ftype = str(prop.get("finding_type") or "").strip()
                if ftype not in FINDING_TYPES:
                    logger.debug("handoff: discarding out-of-schema finding type %r", ftype)
                    continue
                subject = str(prop.get("subject") or "").strip()
                explanation = str(prop.get("explanation") or "").strip()
                if not (ftype and subject and explanation):
                    continue

                end_quote = str(prop.get("end_state_quote") or "").strip()
                open_quote = str(prop.get("opening_quote") or "").strip()
                insufficient = bool(prop.get("insufficient_evidence", False))

                # Ground end-state quote against the ending text (if any).
                end_grounded: dict | None = None
                if end_quote and end_state_items:
                    # Build combined ending text for grounding.
                    prior_chapters = _chapters(db, prior_work_id)
                    ending_chs = _ending_chapters(prior_chapters)
                    ending_combined = "\n\n".join(
                        c.get("text") or "" for c in ending_chs
                    )
                    found = ground_quote_span(end_quote, ending_combined)
                    if found:
                        end_grounded = {"quote": found[1], "offset": found[0]}

                # Ground opening quote.
                open_grounded: dict | None = None
                if open_quote and opening_text:
                    found = ground_quote_span(open_quote, opening_text)
                    if found:
                        open_grounded = {"quote": found[1], "offset": found[0]}

                # For non-missing-evidence findings that cite quotes, require grounding.
                if not insufficient and end_quote and end_grounded is None:
                    logger.debug(
                        "handoff: discarding finding %r (ungroundable end-state quote %r)",
                        ftype,
                        end_quote[:80],
                    )
                    continue

                candidate = {
                    "finding_type": ftype,
                    "subject": subject,
                    "explanation": explanation,
                    "end_state_quote": end_quote,
                    "opening_quote": open_quote,
                    "insufficient_evidence": insufficient,
                    "end_grounded": end_grounded,
                    "open_grounded": open_grounded,
                }

                if _verify_finding(db, cfg, candidate):
                    findings.append(candidate)

        # Deterministic structural checks.
        structural = [
            _check_no_fresh_promise(db, cfg, opening_items, opening_text),
            _check_insufficient_reorientation(opening_items),
        ]
        for s in structural:
            if s is not None:
                if _verify_finding(db, cfg, s):
                    findings.append(s)
                elif s.get("insufficient_evidence"):
                    findings.append(s)  # keep insufficient-evidence findings

    except HandoffError as exc:
        error = str(exc)
        logger.error("handoff: audit failed for %s→%s: %s", prior_work_id, successor_work_id, exc)

    # Store findings.
    now2 = _now()
    with db._lock:
        for f in findings:
            fid = _uid()
            dkey = _dedupe_key(
                audit_id,
                f["finding_type"],
                f["subject"],
                f.get("end_state_quote", "")[:60],
                f.get("opening_quote", "")[:60],
            )
            evidence = []
            if f.get("end_grounded"):
                evidence.append({
                    "role": "prior_book",
                    "work_title": prior_work["title"],
                    "quote": f["end_grounded"]["quote"],
                    "offset": f["end_grounded"]["offset"],
                })
            if f.get("open_grounded"):
                evidence.append({
                    "role": "successor_book",
                    "work_title": successor_work["title"],
                    "quote": f["open_grounded"]["quote"],
                    "offset": f["open_grounded"]["offset"],
                })
            try:
                db._conn.execute(
                    """INSERT OR IGNORE INTO handoff_finding
                       (id, audit_id, finding_type, severity, subject,
                        explanation, evidence, insufficient_evidence,
                        status, dedupe_key, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        fid,
                        audit_id,
                        f["finding_type"],
                        _FINDING_SEVERITY[f["finding_type"]],
                        f["subject"],
                        f["explanation"],
                        json.dumps(evidence, ensure_ascii=False),
                        1 if f.get("insufficient_evidence") else 0,
                        "open",
                        dkey,
                        now2,
                    ),
                )
            except Exception as exc2:
                logger.warning("handoff: could not store finding: %s", exc2)

        db._conn.execute(
            """UPDATE handoff_audit
               SET status=?, error=?, updated_at=?
               WHERE id=?""",
            ("failed" if error else "done", error, now2, audit_id),
        )
        db._conn.commit()

    logger.info(
        "handoff: audit %s done — %d findings (%s→%s)",
        audit_id,
        len(findings),
        prior_work["title"],
        successor_work["title"],
    )
    return _get_audit(db, audit_id)


# ---------------------------------------------------------------------------
# Audit accessors
# ---------------------------------------------------------------------------


def _get_audit(db: "OrivellumDB", audit_id: str) -> dict | None:
    row = db.read_conn().execute(
        "SELECT * FROM handoff_audit WHERE id=?", (audit_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["coverage"] = _jload(d.get("coverage"), {})
    return d


def get_audit(db: "OrivellumDB", audit_id: str) -> dict | None:
    return _get_audit(db, audit_id)


def list_audits(
    db: "OrivellumDB",
    *,
    prior_work_id: str | None = None,
    successor_work_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    q = "SELECT id FROM handoff_audit WHERE 1=1"
    args: list = []
    if prior_work_id:
        q += " AND prior_work_id=?"
        args.append(prior_work_id)
    if successor_work_id:
        q += " AND successor_work_id=?"
        args.append(successor_work_id)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    rows = db.read_conn().execute(q, args).fetchall()
    return [a for r in rows if (a := _get_audit(db, r["id"])) is not None]


def list_findings(db: "OrivellumDB", audit_id: str) -> list[dict]:
    rows = db.read_conn().execute(
        """SELECT * FROM handoff_finding WHERE audit_id=?
           ORDER BY
             CASE severity
               WHEN 'critical' THEN 0 WHEN 'high' THEN 1
               WHEN 'medium' THEN 2 ELSE 3 END,
             created_at""",
        (audit_id,),
    ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["evidence"] = _jload(d.get("evidence"), [])
        d["insufficient_evidence"] = bool(d.get("insufficient_evidence"))
        result.append(d)
    return result


def resolve_finding(
    db: "OrivellumDB",
    finding_id: str,
    status: str,
    resolution_note: str = "",
) -> dict | None:
    if status not in VALID_STATUSES:
        raise HandoffError(f"Invalid status {status!r}; must be one of {VALID_STATUSES}")
    now = _now()
    with db._lock:
        db._conn.execute(
            """UPDATE handoff_finding
               SET status=?, resolution_note=?, resolved_at=?
               WHERE id=?""",
            (status, resolution_note.strip(), now if status != "open" else None, finding_id),
        )
        db._conn.commit()
    row = db.read_conn().execute(
        "SELECT * FROM handoff_finding WHERE id=?", (finding_id,)
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["evidence"] = _jload(d.get("evidence"), [])
    d["insufficient_evidence"] = bool(d.get("insufficient_evidence"))
    return d


# ---------------------------------------------------------------------------
# Series handoff map (for series dashboard)
# ---------------------------------------------------------------------------


def series_handoff_map(db: "OrivellumDB", series_id: str) -> list[dict]:
    """Return handoff health for each consecutive book pair in the series.

    Each entry has prior/successor work info, latest audit summary, and an
    honest ``health`` label based on open findings.
    """
    from orivellum.database.series_store import SeriesStore  # noqa: PLC0415

    members = SeriesStore(db).list_members(series_id)
    if len(members) < 2:
        return []

    result = []
    for i in range(len(members) - 1):
        prior = members[i]
        successor = members[i + 1]
        audit = list_audits(
            db, prior_work_id=prior["work_id"], successor_work_id=successor["work_id"], limit=1
        )
        latest = audit[0] if audit else None

        findings_summary: dict = {}
        if latest and latest["status"] == "done":
            rows = db.read_conn().execute(
                """SELECT severity, COUNT(*) as n FROM handoff_finding
                   WHERE audit_id=? AND status='open'
                   GROUP BY severity""",
                (latest["id"],),
            ).fetchall()
            findings_summary = {r["severity"]: r["n"] for r in rows}

        has_package = latest_package(db, prior["work_id"]) is not None
        has_contract = latest_contract(db, successor["work_id"]) is not None

        critical = findings_summary.get("critical", 0)
        high = findings_summary.get("high", 0)
        if not has_package:
            health = "no_package"
        elif not latest:
            health = "not_audited"
        elif latest["status"] != "done":
            health = "pending"
        elif critical > 0:
            health = "critical"
        elif high > 0:
            health = "warnings"
        else:
            health = "healthy"

        result.append(
            {
                "prior_work_id": prior["work_id"],
                "prior_work_title": prior.get("work_title") or "",
                "prior_volume": prior["volume"],
                "successor_work_id": successor["work_id"],
                "successor_work_title": successor.get("work_title") or "",
                "successor_volume": successor["volume"],
                "has_package": has_package,
                "has_contract": has_contract,
                "latest_audit_id": latest["id"] if latest else None,
                "latest_audit_status": latest["status"] if latest else None,
                "findings_summary": findings_summary,
                "health": health,
            }
        )

    return result
