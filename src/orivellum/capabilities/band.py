"""BAND — surgical chapter edits with full lineage (spec §2.4).

Doctrine (BAND extraction control layer):

- The band is a TEMPORARY controlled edit object, never a new authority
  object.  Only the extracted span may change; every byte outside the band
  is reassembled by CODE, byte-identically — scope discipline is structural,
  never trusted to the model.
- Checkpoint before extraction: the pre-edit chapter text is guaranteed to
  exist as a revision row before anything mutates.
- Valid lineage + fingerprint match: an edit declares the fingerprint of the
  text it was made against; a stale fingerprint refuses the edit (something
  drafted or edited the chapter in between).
- Post-merge validation: the candidate band is delta-verified against the
  work's canon facts and world state, and pairwise re-scored against the old
  band by the critic model (never the model that applied the edit).  Any
  regression — more delta findings, a new critical finding, a higher band
  error density, or a pairwise loss — REFUSES the commit; nothing persists.
  The author may resubmit with ``accept_regression=True`` plus a signature,
  which commits and records the acceptance in the revision meta.
- Approved chapters: approval is of the exact text.  Editing or restoring an
  approved chapter requires the author signature and demotes the chapter
  back to 'drafted'.
- Nothing is ever deleted: restore copies an old revision's text into a NEW
  head revision that records its source.

Delta findings live in the revision meta, NOT in ``narrative_finding`` —
they are a pre-commit gate over a candidate text that may never be
persisted, and the narrative_finding write path owns severity for stored
findings (constory).  A full ConStory re-run remains the whole-book check.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from typing import TYPE_CHECKING, Any

from orivellum.capabilities.atlas import ground_quote_span
from orivellum.capabilities.loom import (
    _gateway,
    _parse_json_obj,
    _require_separated_models,
)

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.band")


class BandError(Exception):
    """The edit cannot proceed; nothing has been persisted."""


class BandBusy(Exception):
    """Another band edit for this chapter is in flight."""


# Hard ceilings — a "surgical" edit that spans a whole chapter is a rewrite,
# which is LOOM's job, not BAND's.
BAND_MAX_CHARS = 20_000
INSTRUCTION_MAX_CHARS = 2_000
# Context shown to the editor around the band (display only — never editable).
CONTEXT_MARGIN_CHARS = 600
# Replacement band size guard: reject runaway generations.
_GROWTH_CAP = 4
_GROWTH_SLACK = 2_000
# Fact lines fed to the delta check.
_MAX_CANON_FACTS = 120
_MAX_WORLD_STATE = 60

# Severity of a grounded, confirmed delta contradiction, by fact source.
_REF_SEVERITY = {
    "HISTORICAL": "critical",
    "INFERRED": "high",
    "INVENTED": "medium",
    "WORLD": "medium",
}
_SEV_RANK = {"critical": 3, "high": 2, "medium": 1}


# ── Per-chapter claim (one band per chapter at a time) ───────────────────────

_edit_locks: dict[str, threading.Lock] = {}
_edit_locks_guard = threading.Lock()


def _chapter_lock(chapter_id: str) -> threading.Lock:
    with _edit_locks_guard:
        return _edit_locks.setdefault(chapter_id, threading.Lock())


# ── Fingerprints + chapter access ────────────────────────────────────────────


def fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def _load_chapter(db: OrivellumDB, chapter_id: str) -> dict:
    with db._lock:
        row = db._conn.execute("SELECT * FROM book_chapters WHERE id=?", (chapter_id,)).fetchone()
    if row is None:
        raise BandError(f"chapter {chapter_id!r} not found")
    return dict(row)


def get_chapter_overview(db: OrivellumDB, chapter_id: str) -> dict:
    """Head state + revision timeline for the history/edit UI."""
    ch = _load_chapter(db, chapter_id)
    text = ch.get("text") or ""
    return {
        "chapter_id": ch["id"],
        "work_id": ch["work_id"],
        "seq": ch.get("seq"),
        "title": ch.get("title"),
        "status": ch.get("status"),
        "text": text,
        "word_count": len(text.split()),
        "fingerprint": fingerprint(text),
        "revisions": db.list_chapter_revisions(chapter_id),
    }


# ── Checkpoint (doctrine: before extraction, always) ─────────────────────────


def _checkpoint_current(db: OrivellumDB, ch: dict, *, expected_fp: str) -> None:
    """Guarantee the CURRENT chapter text exists as a revision row — in ONE
    transaction that re-reads the live text and validates the fingerprint.

    A concurrent writer (e.g. a LOOM draft) that lands between loading the
    chapter and checkpointing would otherwise get a stale checkpoint appended
    ON TOP of its newer revision — corrupted lineage.  Here the live text
    must still match the fingerprint this edit declared, or we refuse BEFORE
    anything is written.
    """
    prov = db.get_provenance(ch["id"], "book_chapter") if hasattr(db, "get_provenance") else None
    origin = (prov or {}).get("origin") or "human"
    if origin not in ("human", "ai_assisted", "ai_generated"):
        origin = "human"
    from orivellum.database.db import _now  # noqa: PLC0415

    with db._lock:
        row = db._conn.execute("SELECT text FROM book_chapters WHERE id=?", (ch["id"],)).fetchone()
        if row is None:
            raise BandError("chapter vanished before checkpoint")
        text = row["text"] or ""
        if fingerprint(text) != expected_fp:
            db._conn.rollback()
            raise BandError(
                "chapter text changed before the checkpoint could be taken — "
                "stale fingerprint, edit refused; reload and retry"
            )
        head_row = db._conn.execute(
            """SELECT rev, text FROM loom_chapter_revision WHERE chapter_id=?
               ORDER BY rev DESC LIMIT 1""",
            (ch["id"],),
        ).fetchone()
        if head_row is not None and (head_row["text"] or "") == text:
            return  # current text is already captured
        head = int(head_row["rev"]) if head_row is not None else 0
        db._conn.execute(
            """INSERT INTO loom_chapter_revision(id, chapter_id, work_id, rev,
               text, word_count, meta, created_at, parent_rev, origin,
               created_by, edit_scope) VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL)""",
            (
                str(uuid.uuid4()),
                ch["id"],
                ch["work_id"],
                head + 1,
                text,
                len(text.split()),
                json.dumps(
                    {"checkpoint": True, "note": "pre-edit checkpoint of untracked chapter text"}
                ),
                _now(),
                head or None,
                origin,
                "checkpoint",
            ),
        )
        db._conn.commit()


# ── Delta verification (band vs canon facts + world state) ───────────────────


def _fact_lines(db: OrivellumDB, work_id: str) -> list[dict]:
    """Numbered fact list: sealed/active canon facts + committed world state.

    A load failure raises — a delta check that silently skipped the record
    would pass a verdict it never earned.
    """
    from orivellum.database.canon_store import CanonStore  # noqa: PLC0415

    facts: list[dict] = []
    for f in CanonStore(db).list_facts(work_id=work_id, status="active", limit=_MAX_CANON_FACTS):
        cls = f.get("classification")
        cls = cls if cls in _REF_SEVERITY else "INFERRED"
        facts.append({"source": cls, "statement": str(f.get("statement") or "")})
    state = db.get_world_state(work_id)
    for key, entry in list(state.items())[:_MAX_WORLD_STATE]:
        facts.append({"source": "WORLD", "statement": f"{key}: {entry['value']}"})
    return [f for f in facts if f["statement"].strip()]


_DELTA_PROMPT = """You are a continuity and fact checker. Below is THE RECORD \
(established facts) and a PASSAGE from a manuscript. List every place the \
passage CONTRADICTS the record. Only real contradictions — not omissions, \
not new information.

THE RECORD:
{facts}

PASSAGE:
\"\"\"{passage}\"\"\"

JSON only: {{"contradictions": [{{"ref": "F<number>", "quote": "exact short quote \
from the PASSAGE that contradicts", "reasoning": "why"}}]}}
Return {{"contradictions": []}} if there are none."""


def _delta_check(
    db, cfg, *, critic: str, facts: list[dict], passage: str, llm_ids: list
) -> list[dict]:
    """Grounded delta findings for one band text.  Strict: a malformed
    checker response raises (fail closed) — it never counts as a clean pass.
    Ungrounded proposals are discarded, never coerced."""
    if not facts or not passage.strip():
        return []
    fact_block = "\n".join(
        f"F{i} [{f['source']}]: {f['statement'][:300]}" for i, f in enumerate(facts)
    )
    r = _gateway(
        db,
        cfg,
        model=critic,
        purpose="band.delta",
        system="You are a meticulous continuity checker. JSON only.",
        user=_DELTA_PROMPT.format(facts=fact_block, passage=passage[:BAND_MAX_CHARS]),
        temperature=0.0,
    )
    llm_ids.append(r.call_id)
    if not r.ok:
        raise BandError(f"delta check gateway failure: {r.error}")
    parsed = _parse_json_obj(r.text)
    if parsed is None or not isinstance(parsed.get("contradictions"), list):
        raise BandError(
            "delta check returned malformed output — refusing to treat an unverified band as clean"
        )
    findings = []
    for prop in parsed["contradictions"][:20]:
        if not isinstance(prop, dict):
            continue
        ref = str(prop.get("ref") or "").strip()
        try:
            fact = facts[int(ref[1:])] if ref.startswith("F") else None
        except (ValueError, IndexError):
            fact = None
        if fact is None:
            continue  # dangling reference — discard
        quote = str(prop.get("quote") or "").strip()
        found = ground_quote_span(quote, passage) if quote else None
        if found is None:
            continue  # ungrounded — discard, never coerce
        offset, grounded_quote = found
        findings.append(
            {
                "severity": _REF_SEVERITY[fact["source"]],
                "fact_source": fact["source"],
                "fact_statement": fact["statement"][:400],
                "quote": grounded_quote[:400],
                "offset": offset,
                "reasoning": str(prop.get("reasoning") or "")[:400],
            }
        )
    return findings


def _delta_summary(findings: list[dict], band_text: str) -> dict:
    words = max(len(band_text.split()), 1)
    critical = sum(1 for f in findings if f["severity"] == "critical")
    return {
        "findings": findings,
        "count": len(findings),
        "critical_count": critical,
        "band_words": words,
        "ced": round(len(findings) * 10_000 / words, 2),
    }


# ── Pairwise re-score (critic judges, never the editor) ──────────────────────

_PAIRWISE_PROMPT = """An editor was instructed to change a passage. Judge which \
version better serves the manuscript: the instruction must be satisfied, prose \
quality and voice consistency must not degrade.

INSTRUCTION:
{instruction}

SURROUNDING CONTEXT (before the passage):
\"\"\"{before}\"\"\"

VERSION OLD:
\"\"\"{old}\"\"\"

VERSION NEW:
\"\"\"{new}\"\"\"

JSON only: {{"winner": "new" | "old" | "tie", "rationale": "one or two sentences"}}"""


def _pairwise_score(
    db,
    cfg,
    *,
    critic: str,
    instruction: str,
    before: str,
    old_band: str,
    new_band: str,
    llm_ids: list,
) -> dict:
    r = _gateway(
        db,
        cfg,
        model=critic,
        purpose="band.pairwise",
        system="You are a strict manuscript editor. JSON only.",
        user=_PAIRWISE_PROMPT.format(
            instruction=instruction,
            before=before[-CONTEXT_MARGIN_CHARS:],
            old=old_band[:BAND_MAX_CHARS],
            new=new_band[:BAND_MAX_CHARS],
        ),
        temperature=0.0,
    )
    llm_ids.append(r.call_id)
    if not r.ok:
        raise BandError(f"pairwise re-score gateway failure: {r.error}")
    parsed = _parse_json_obj(r.text)
    winner = str((parsed or {}).get("winner") or "").strip().lower()
    if winner not in ("new", "old", "tie"):
        raise BandError(
            "pairwise re-score returned malformed output — refusing to score the edit by default"
        )
    return {"winner": winner, "rationale": str((parsed or {}).get("rationale") or "")[:600]}


# ── The edit itself ──────────────────────────────────────────────────────────

_EDIT_PROMPT = """You are performing a SURGICAL edit. You may rewrite ONLY the \
BAND below. Text outside the band is shown for context and will be preserved \
verbatim by the system — do not repeat it, do not extend beyond the band.

CONTEXT BEFORE (read-only):
\"\"\"{before}\"\"\"

BAND (the ONLY text you may change):
\"\"\"{band}\"\"\"

CONTEXT AFTER (read-only):
\"\"\"{after}\"\"\"

INSTRUCTION:
{instruction}

Rewrite the band per the instruction, keeping the voice of the surrounding \
prose and making the result flow seamlessly into the read-only context.
JSON only: {{"band": "the full replacement text for the band"}}"""


def _apply_edit_llm(
    db, cfg, *, drafter: str, before: str, band: str, after: str, instruction: str, llm_ids: list
) -> str:
    r = _gateway(
        db,
        cfg,
        model=drafter,
        purpose="band.edit",
        system="You are a precise line editor. JSON only.",
        user=_EDIT_PROMPT.format(
            before=before[-CONTEXT_MARGIN_CHARS:],
            band=band,
            after=after[:CONTEXT_MARGIN_CHARS],
            instruction=instruction,
        ),
        temperature=0.3,
        timeout=300,
    )
    llm_ids.append(r.call_id)
    if not r.ok:
        raise BandError(f"edit gateway failure: {r.error}")
    parsed = _parse_json_obj(r.text)
    new_band = (parsed or {}).get("band")
    if not isinstance(new_band, str) or not new_band.strip():
        raise BandError("editor returned no replacement band")
    if len(new_band) > max(len(band) * _GROWTH_CAP, len(band) + _GROWTH_SLACK):
        raise BandError(
            f"replacement band is {len(new_band)} chars for a {len(band)}-char "
            "band — runaway generation refused"
        )
    return new_band


def _regression_reasons(baseline: dict, candidate: dict, pairwise: dict) -> list[str]:
    reasons = []
    if candidate["critical_count"] > baseline["critical_count"]:
        reasons.append(
            f"introduces new critical finding(s): {candidate['critical_count']} "
            f"vs {baseline['critical_count']} before"
        )
    if candidate["count"] > baseline["count"]:
        reasons.append(f"delta findings increased: {candidate['count']} vs {baseline['count']}")
    if candidate["ced"] > baseline["ced"]:
        reasons.append(
            f"band error density increased: {candidate['ced']} vs {baseline['ced']} per 10k words"
        )
    if pairwise["winner"] == "old":
        reasons.append(f"critic prefers the previous text: {pairwise['rationale']}")
    return reasons


def _commit_revision(
    db: OrivellumDB,
    ch: dict,
    new_text: str,
    *,
    expected_fp: str,
    origin: str,
    created_by: str,
    edit_scope: dict | None,
    meta: dict,
    demote_approved: bool,
) -> dict:
    """ONE transaction: fingerprint re-check, approval re-check, revision
    insert, chapter text update (+ demotion when editing an approved
    chapter).  Any failed check persists nothing."""
    from orivellum.database.db import _now  # noqa: PLC0415

    rid = str(uuid.uuid4())
    now = _now()
    wc = len(new_text.split())
    with db._lock:
        row = db._conn.execute(
            "SELECT text, status FROM book_chapters WHERE id=?", (ch["id"],)
        ).fetchone()
        if row is None:
            db._conn.rollback()
            raise BandError("chapter vanished mid-edit")
        if fingerprint(row["text"] or "") != expected_fp:
            db._conn.rollback()
            raise BandError(
                "chapter text changed while the edit was in flight — "
                "stale fingerprint, edit refused; reload and retry"
            )
        approved = str(row["status"]) == "approved"
        if approved and not demote_approved:
            db._conn.rollback()
            raise BandError(
                "chapter was approved mid-edit — an approved chapter needs "
                "the author signature; edit refused"
            )
        head = int(
            db._conn.execute(
                "SELECT COALESCE(MAX(rev), 0) AS m FROM loom_chapter_revision WHERE chapter_id=?",
                (ch["id"],),
            ).fetchone()["m"]
        )
        rev = head + 1
        db._conn.execute(
            """INSERT INTO loom_chapter_revision(id, chapter_id, work_id, rev,
               text, word_count, meta, created_at, parent_rev, origin,
               created_by, edit_scope) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rid,
                ch["id"],
                ch["work_id"],
                rev,
                new_text,
                wc,
                json.dumps(meta),
                now,
                head or None,
                origin,
                created_by,
                json.dumps(edit_scope) if edit_scope is not None else None,
            ),
        )
        if approved:
            db._conn.execute(
                "UPDATE book_chapters SET text=?, status='drafted', updated_at=? WHERE id=?",
                (new_text, now, ch["id"]),
            )
        else:
            db._conn.execute(
                "UPDATE book_chapters SET text=?, updated_at=? WHERE id=?",
                (new_text, now, ch["id"]),
            )
        db._conn.commit()
    return {
        "id": rid,
        "rev": rev,
        "word_count": wc,
        "parent_rev": head or None,
        "demoted_from_approved": approved,
    }


def surgical_edit(
    db: OrivellumDB,
    cfg: Any,
    *,
    chapter_id: str,
    start: int,
    end: int,
    instruction: str,
    base_fingerprint: str,
    author: str = "",
    accept_regression: bool = False,
    band_text: str | None = None,
) -> dict:
    """The full BAND flow.  Returns ``{"committed": True, ...}`` on success or
    ``{"committed": False, "reasons": [...]}`` when regression gates refuse.

    ``accept_regression=True`` requires an author signature and commits past
    the gates, recording the acceptance in the revision meta."""
    lock = _chapter_lock(chapter_id)
    if not lock.acquire(blocking=False):
        raise BandBusy("a band edit for this chapter is already in flight")
    try:
        return _surgical_edit_locked(
            db,
            cfg,
            chapter_id=chapter_id,
            start=start,
            end=end,
            instruction=instruction,
            base_fingerprint=base_fingerprint,
            author=author,
            accept_regression=accept_regression,
            band_text=band_text,
        )
    finally:
        lock.release()


def _validate_edit_request(
    ch: dict,
    text: str,
    *,
    start: int,
    end: int,
    instruction: str,
    base_fingerprint: str,
    author: str,
    accept_regression: bool,
) -> None:
    if not instruction:
        raise BandError("an edit needs an instruction")
    if len(instruction) > INSTRUCTION_MAX_CHARS:
        raise BandError("instruction too long")
    if accept_regression and not author:
        raise BandError("accepting a regression requires the author signature")
    if not text.strip():
        raise BandError("chapter has no text to edit")
    if fingerprint(text) != base_fingerprint:
        raise BandError(
            "stale fingerprint — the chapter text is not the text this edit "
            "was made against; reload and reselect the band"
        )
    if str(ch.get("status")) == "approved" and not author:
        raise BandError(
            "chapter is approved — editing it requires the author signature "
            "and will demote it back to 'drafted'"
        )
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text)):
        raise BandError(f"invalid band boundaries [{start}, {end}) for a {len(text)}-char chapter")
    if end - start > BAND_MAX_CHARS:
        raise BandError(
            f"band of {end - start} chars exceeds the surgical limit "
            f"({BAND_MAX_CHARS}) — a change that large is a redraft, not a band edit"
        )


def _surgical_edit_locked(
    db,
    cfg,
    *,
    chapter_id,
    start,
    end,
    instruction,
    base_fingerprint,
    author,
    accept_regression,
    band_text=None,
) -> dict:
    instruction = (instruction or "").strip()
    author = (author or "").strip()
    drafter, critic = _require_separated_models(db, cfg)
    ch = _load_chapter(db, chapter_id)
    text = ch.get("text") or ""
    _validate_edit_request(
        ch,
        text,
        start=start,
        end=end,
        instruction=instruction,
        base_fingerprint=base_fingerprint,
        author=author,
        accept_regression=accept_regression,
    )
    # Boundary echo check: the caller states the exact text it selected.  Any
    # indexing drift between client and server (e.g. UTF-16 code units vs
    # Unicode code points) makes text[start:end] differ from what the author
    # saw — refuse rather than edit an unselected span.
    if band_text is not None and text[start:end] != band_text:
        raise BandError(
            "band text mismatch — the declared boundaries do not select the "
            "text you highlighted (offset encoding drift?); reselect the band"
        )

    # Doctrine: checkpoint BEFORE extraction — atomically fingerprint-guarded.
    _checkpoint_current(db, ch, expected_fp=base_fingerprint)

    before, band, after = text[:start], text[start:end], text[end:]
    llm_ids: list = []
    new_band = _apply_edit_llm(
        db,
        cfg,
        drafter=drafter,
        before=before,
        band=band,
        after=after,
        instruction=instruction,
        llm_ids=llm_ids,
    )
    # Reassembly by code — outside-band bytes are preserved by construction.
    candidate_text = before + new_band + after

    # Post-merge validation on the DELTA only.
    facts = _fact_lines(db, ch["work_id"])
    baseline = _delta_summary(
        _delta_check(db, cfg, critic=critic, facts=facts, passage=band, llm_ids=llm_ids), band
    )
    candidate = _delta_summary(
        _delta_check(db, cfg, critic=critic, facts=facts, passage=new_band, llm_ids=llm_ids),
        new_band,
    )
    pairwise = _pairwise_score(
        db,
        cfg,
        critic=critic,
        instruction=instruction,
        before=before,
        old_band=band,
        new_band=new_band,
        llm_ids=llm_ids,
    )
    reasons = _regression_reasons(baseline, candidate, pairwise)
    call_ids = [i for i in llm_ids if i is not None]

    gates = {
        "pairwise": pairwise,
        "delta": {"baseline": baseline, "candidate": candidate},
        "regression_reasons": reasons,
    }
    if reasons and not accept_regression:
        logger.info("band: chapter=%s edit REFUSED (%d reasons)", chapter_id, len(reasons))
        return {
            "committed": False,
            "reasons": reasons,
            "gates": gates,
            "proposed_band": new_band,
            "note": "resubmit with accept_regression=true and an author signature to commit anyway",
        }

    new_fp = fingerprint(candidate_text)
    edit_scope = {
        "start": start,
        "end": end,
        "instruction": instruction,
        "fingerprint_before": base_fingerprint,
        "fingerprint_after": new_fp,
    }
    meta = {
        "band_edit": True,
        "gates": gates,
        "accepted_regression": bool(reasons and accept_regression),
        "llm_call_ids": call_ids,
    }
    stored = _commit_revision(
        db,
        ch,
        candidate_text,
        expected_fp=base_fingerprint,
        origin="ai_assisted",
        created_by=author or "user",
        edit_scope=edit_scope,
        meta=meta,
        demote_approved=bool(author),
    )
    try:
        db.record_provenance(
            stored["id"],
            "loom_chapter_revision",
            origin="ai_assisted",
            llm_call_ids=call_ids,
            declared_by=author or "user",
        )
    except Exception:
        logger.exception("band: provenance recording failed (edit committed)")
    logger.info(
        "band: chapter=%s committed rev=%s (%d→%d chars, %d reasons accepted)",
        chapter_id,
        stored["rev"],
        end - start,
        len(new_band),
        len(reasons),
    )
    return {
        "committed": True,
        "revision": stored,
        "gates": gates,
        "fingerprint": new_fp,
        "demoted_from_approved": stored["demoted_from_approved"],
    }


# ── Restore (append-only history — restore is a NEW revision) ────────────────


def restore_revision(db: OrivellumDB, *, chapter_id: str, rev: int, author: str = "") -> dict:
    lock = _chapter_lock(chapter_id)
    if not lock.acquire(blocking=False):
        raise BandBusy("a band edit for this chapter is already in flight")
    try:
        ch = _load_chapter(db, chapter_id)
        text = ch.get("text") or ""
        author = (author or "").strip()
        if str(ch.get("status")) == "approved" and not author:
            raise BandError(
                "chapter is approved — restoring requires the author signature "
                "and will demote it back to 'drafted'"
            )
        source = next(
            (r for r in db.list_chapter_revisions(chapter_id) if r["rev"] == rev),
            None,
        )
        if source is None:
            raise BandError(f"revision {rev} not found for this chapter")
        full = db.get_chapter_revision(source["id"])
        if full is None:
            raise BandError(f"revision {rev} not found for this chapter")
        if (full.get("text") or "") == text:
            raise BandError(f"revision {rev} is already the current text")
        # Current state is preserved as a checkpoint before the restore lands.
        _checkpoint_current(db, ch, expected_fp=fingerprint(text))
        stored = _commit_revision(
            db,
            ch,
            full["text"] or "",
            expected_fp=fingerprint(text),
            origin=str(full.get("origin") or "human"),
            created_by=author or "user",
            edit_scope=None,
            meta={"restored_from_rev": rev, "restored_from_id": full["id"]},
            demote_approved=bool(author),
        )
        return {
            "committed": True,
            "revision": stored,
            "restored_from_rev": rev,
            "fingerprint": fingerprint(full["text"] or ""),
            "demoted_from_approved": stored["demoted_from_approved"],
        }
    finally:
        lock.release()
