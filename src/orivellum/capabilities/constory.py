"""ConStory — story-contradiction checker (Masterpiece Pipeline Part 2.3 + B6).

Catches all 19 kinds of story contradiction with quoted evidence.  Design:

- **Closed schema.**  Five categories, 19 subtypes (verbatim from
  ConStory-Bench).  Proposals outside the registry are DISCARDED, never
  coerced.
- **Whole-book pairing.**  Each chapter is checked against facts from ALL
  prior chapters (plus sealed canon facts), not a sliding window.
- **Evidence chain (LAW 3).**  Extraction produces facts with verbatim
  quotes grounded at real character offsets; pairing proposes contradictions
  quoting the current chapter; verification confirms each proposal.  A
  finding is stored only when BOTH quotes ground at real offsets AND the
  verifier confirms — everything else is discarded.  This deterministic
  rejection of ungroundable output is what produces precision.
- **Computed severity.**  ``severity = f(subtype, canon_class)`` — the model
  never picks it.  A factual contradiction against a HISTORICAL canon fact
  is critical; the same subtype against an INVENTED fact is medium.
- **Stable identity.**  Each finding carries a dedupe key over
  (subtype, both chapters, both offsets) so re-runs never resurrect a
  dispositioned finding as a new 'open' row.
- All LLM calls go through the ``llm_call`` gateway at temperature 0.0 with
  a ``constory.*`` purpose tag.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from orivellum.capabilities.atlas import (
    _fence,
    _parse_json,
    ground_quote_span,
)

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Closed subtype registry — the 19, verbatim from ConStory-Bench
# ---------------------------------------------------------------------------

SUBTYPES: dict[str, tuple[str, ...]] = {
    "timeline_plot": (
        "absolute_time",
        "duration",
        "simultaneity",
        "causeless_effect",
        "causal_logic",
        "abandoned_plot_element",
    ),
    "characterization": (
        "memory",
        "knowledge",
        "skill_fluctuation",
        "forgotten_ability",
    ),
    "worldbuilding": (
        "core_rules",
        "social_norms",
        "geographical",
    ),
    "factual_detail": (
        "appearance_mismatch",
        "nomenclature_confusion",
        "quantitative_mismatch",
    ),
    "narrative_style": (
        "perspective_confusion",
        "tone_inconsistency",
        "style_shift",
    ),
}

# subtype -> category reverse map (19 entries)
SUBTYPE_CATEGORY: dict[str, str] = {
    sub: cat for cat, subs in SUBTYPES.items() for sub in subs
}
assert len(SUBTYPE_CATEGORY) == 19, "ConStory subtype registry must have exactly 19 entries"

CANON_CLASSES = ("HISTORICAL", "INFERRED", "INVENTED")
DISPOSITIONS = ("open", "fixed", "intentional", "wontfix")

# ---------------------------------------------------------------------------
# Severity — computed, never model-chosen
# ---------------------------------------------------------------------------

_SEVERITY_ORDER = ("low", "medium", "high", "critical")

# Base severity per subtype (no canon involvement).
_BASE_SEVERITY: dict[str, str] = {
    # timeline_plot — broken causality is worse than a shifted date
    "absolute_time": "medium",
    "duration": "medium",
    "simultaneity": "medium",
    "causeless_effect": "high",
    "causal_logic": "high",
    "abandoned_plot_element": "medium",
    # characterization
    "memory": "medium",
    "knowledge": "high",
    "skill_fluctuation": "medium",
    "forgotten_ability": "medium",
    # worldbuilding — breaking the world's core rules is severe
    "core_rules": "high",
    "social_norms": "medium",
    "geographical": "medium",
    # factual_detail
    "appearance_mismatch": "medium",
    "nomenclature_confusion": "medium",
    "quantitative_mismatch": "medium",
    # narrative_style — craft issues, not fact errors
    "perspective_confusion": "low",
    "tone_inconsistency": "low",
    "style_shift": "low",
}
assert set(_BASE_SEVERITY) == set(SUBTYPE_CATEGORY)


def compute_severity(subtype: str, canon_class: str | None) -> str:
    """severity = f(subtype, canon_class).  Deterministic; raises on unknowns.

    - contradiction of a HISTORICAL canon fact  -> critical, always
    - contradiction of an INFERRED canon fact   -> at least high
    - contradiction of an INVENTED canon fact   -> at least medium
    - prose-vs-prose (no canon fact involved)   -> subtype base severity
    """
    if subtype not in _BASE_SEVERITY:
        raise ValueError(f"unknown ConStory subtype {subtype!r}")
    if canon_class is not None and canon_class not in CANON_CLASSES:
        raise ValueError(f"unknown canon class {canon_class!r}")
    base = _BASE_SEVERITY[subtype]
    if canon_class == "HISTORICAL":
        return "critical"
    floor = {"INFERRED": "high", "INVENTED": "medium", None: "low"}[canon_class]
    return max(base, floor, key=_SEVERITY_ORDER.index)


def dedupe_key(
    subtype: str,
    fact_chapter: int,
    fact_offset: int,
    contradiction_chapter: int,
    contradiction_offset: int,
) -> str:
    """Stable identity of a finding across re-runs."""
    raw = f"{subtype}|{fact_chapter}|{fact_offset}|{contradiction_chapter}|{contradiction_offset}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# LLM plumbing (gateway, temp 0.0, fail loudly)
# ---------------------------------------------------------------------------

class ConStoryLLMError(RuntimeError):
    """An extraction/pairing/verification call failed — the run must stop.

    Silent degradation would report "0 findings" for chapters that were
    never actually checked, which is worse than an honest failure.
    """


_TIMEOUT_SEC = 60
_MAX_FACTS_PER_CHAPTER = 30
_MAX_PROPOSALS = 20
_PAIR_FACT_BATCH = 80          # prior facts per pairing call
_MAX_FACT_STATEMENT = 240      # chars of a fact statement rendered in prompts


def _call(prompt: str, *, purpose: str, cfg: Any, db: OrivellumDB) -> Any:
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
        raise ConStoryLLMError(f"{purpose} call failed: {result.error or 'no response'}")
    parsed = _parse_json(result.text)
    if parsed is None:
        raise ConStoryLLMError(f"{purpose} returned unparseable output")
    return parsed


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """You are a continuity analyst building a fact ledger for a novel.
Extract up to {max_facts} concrete, checkable story facts from the chapter below:
times/dates and durations, cause-effect claims, what characters know/remember/can do,
world rules and social norms, geography, appearances, names/titles, and quantities.

Return ONLY JSON: {{"facts": [{{"statement": "<one-sentence factual claim>",
"quote": "<short VERBATIM quote from the chapter that states it>"}}]}}
Rules: every quote must be copied exactly from the chapter text. No commentary.

{chapter}"""

_PAIR_PROMPT = """You are ConStory, a story-contradiction checker.  Below is a numbered
list of ESTABLISHED FACTS (from earlier chapters and from sealed canon), then the text
of the CURRENT chapter.  Propose contradictions: places where the current chapter
contradicts an established fact.

The ONLY allowed subtypes (grouped by category) are:
- timeline_plot: absolute_time, duration, simultaneity, causeless_effect,
  causal_logic, abandoned_plot_element
- characterization: memory, knowledge, skill_fluctuation, forgotten_ability
- worldbuilding: core_rules, social_norms, geographical
- factual_detail: appearance_mismatch, nomenclature_confusion, quantitative_mismatch
- narrative_style: perspective_confusion, tone_inconsistency, style_shift

Return ONLY JSON: {{"contradictions": [{{"fact_ref": "<id of the established fact, e.g. F3 or C1>",
"quote": "<short VERBATIM quote from the CURRENT chapter that contradicts it>",
"subtype": "<one of the 19 subtypes above>",
"reasoning": "<one sentence: why these two passages contradict>"}}]}}
Rules: at most {max_proposals} proposals; quotes copied exactly from the current chapter;
only real contradictions — do not invent tension where the passages are compatible.
Return {{"contradictions": []}} if there are none.

ESTABLISHED FACTS:
{facts}

{chapter}"""

_VERIFY_PROMPT = """You are verifying ONE proposed story contradiction.

Established fact (chapter {fact_chapter}): "{fact_quote}"
Fact statement: {fact_statement}
Current chapter passage (chapter {curr_chapter}): "{curr_quote}"
Proposed subtype: {subtype}
Proposed reasoning: {reasoning}

Do these two passages GENUINELY contradict each other?  Answer "rejected" if they are
compatible, if the later passage is a plausible development rather than a contradiction,
or if the reasoning misreads either passage.

Return ONLY JSON: {{"verdict": "confirmed" or "rejected",
"reasoning": "<one sentence, final explanation of the contradiction (or why rejected)>"}}"""


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def _extract_chapter_facts(
    db: OrivellumDB, cfg: Any, chapter: dict
) -> list[dict]:
    """Stage 1 — extract grounded facts from one chapter.

    Returns [{"statement", "quote", "offset", "chapter_id", "chapter_seq"}].
    Ungroundable quotes are discarded (never coerced).
    """
    text = chapter["text"] or ""
    if not text.strip():
        return []
    parsed = _call(
        _EXTRACT_PROMPT.format(
            max_facts=_MAX_FACTS_PER_CHAPTER,
            chapter=_fence(text, chapter.get("title") or f"chapter {chapter['seq']}"),
        ),
        purpose="constory.extract",
        cfg=cfg,
        db=db,
    )
    facts: list[dict] = []
    for item in (parsed.get("facts") or [])[:_MAX_FACTS_PER_CHAPTER]:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement") or "").strip()
        quote = str(item.get("quote") or "").strip()
        if not statement or not quote:
            continue
        found = ground_quote_span(quote, text)
        if found is None:
            logger.debug("constory: discarding ungroundable fact quote %r", quote[:80])
            continue
        offset, verbatim = found
        facts.append(
            {
                "statement": statement,
                "quote": verbatim,
                "offset": offset,
                "chapter_id": chapter["id"],
                "chapter_seq": int(chapter["seq"]),
            }
        )
    return facts


def _render_fact_lines(prior_facts: list[dict], canon_facts: list[dict]) -> list[list[str]]:
    """Render established facts as prompt lines, batched for whole-book scale.

    Prior-chapter facts get ids F<i>; canon facts get ids C<i>.  Canon lines
    ride along with EVERY batch (they are few and severity-relevant).
    """
    canon_lines = [
        f"C{i}: (canon, {c.get('classification') or 'INFERRED'}) "
        f"{str(c.get('statement') or '')[:_MAX_FACT_STATEMENT]}"
        for i, c in enumerate(canon_facts)
    ]
    fact_lines = [
        f"F{i}: (chapter {f['chapter_seq']}) {f['statement'][:_MAX_FACT_STATEMENT]}"
        for i, f in enumerate(prior_facts)
    ]
    if not fact_lines and not canon_lines:
        return []
    batches: list[list[str]] = []
    for start in range(0, max(len(fact_lines), 1), _PAIR_FACT_BATCH):
        batches.append(canon_lines + fact_lines[start : start + _PAIR_FACT_BATCH])
    return batches


def _resolve_ref(
    ref: str, prior_facts: list[dict], canon_facts: list[dict]
) -> tuple[dict | None, dict | None]:
    """fact_ref -> (prior_fact, canon_fact); exactly one side non-None, or both None."""
    ref = str(ref).strip()
    try:
        if ref.startswith("F"):
            return prior_facts[int(ref[1:])], None
        if ref.startswith("C"):
            return None, canon_facts[int(ref[1:])]
    except (ValueError, IndexError):
        pass
    return None, None


def _check_chapter(
    db: OrivellumDB,
    cfg: Any,
    *,
    work_id: str,
    chapter: dict,
    prior_facts: list[dict],
    canon_facts: list[dict],
) -> list[dict]:
    """Stages 2+3 — pair one chapter against ALL prior facts, verify each proposal.

    Returns verified finding dicts ready for storage (severity computed here).
    """
    text = chapter["text"] or ""
    if not text.strip():
        return []
    batches = _render_fact_lines(prior_facts, canon_facts)
    if not batches:
        return []

    fenced = _fence(text, chapter.get("title") or f"chapter {chapter['seq']}")
    curr_seq = int(chapter["seq"])
    findings: list[dict] = []
    seen_keys: set[str] = set()

    for batch in batches:
        parsed = _call(
            _PAIR_PROMPT.format(
                max_proposals=_MAX_PROPOSALS,
                facts="\n".join(batch),
                chapter=fenced,
            ),
            purpose="constory.pair",
            cfg=cfg,
            db=db,
        )
        for prop in (parsed.get("contradictions") or [])[:_MAX_PROPOSALS]:
            finding = _stage_proposal(
                db, cfg,
                prop=prop,
                text=text,
                work_id=work_id,
                chapter=chapter,
                curr_seq=curr_seq,
                prior_facts=prior_facts,
                canon_facts=canon_facts,
                seen_keys=seen_keys,
            )
            if finding is not None:
                findings.append(finding)
    return findings


def _stage_proposal(
    db: OrivellumDB,
    cfg: Any,
    *,
    prop: Any,
    text: str,
    work_id: str,
    chapter: dict,
    curr_seq: int,
    prior_facts: list[dict],
    canon_facts: list[dict],
    seen_keys: set[str],
) -> dict | None:
    """Evidence-chain one proposal: closed schema → grounding → verification.

    Returns a storable finding dict, or None when any link fails (the
    proposal is discarded, never coerced)."""
    if not isinstance(prop, dict):
        return None
    subtype = str(prop.get("subtype") or "").strip()
    category = SUBTYPE_CATEGORY.get(subtype)
    if category is None:
        logger.debug("constory: discarding out-of-schema subtype %r", subtype)
        return None  # closed set — discard, never coerce

    # Ground the contradicting quote in the CURRENT chapter.
    quote = str(prop.get("quote") or "").strip()
    found = ground_quote_span(quote, text) if quote else None
    if found is None:
        logger.debug("constory: discarding ungroundable proposal quote %r", quote[:80])
        return None
    curr_offset, curr_quote = found

    prior_fact, canon_fact = _resolve_ref(
        prop.get("fact_ref") or "", prior_facts, canon_facts
    )
    if prior_fact is not None:
        fact_quote = prior_fact["quote"]
        fact_statement = prior_fact["statement"]
        fact_chapter = prior_fact["chapter_seq"]
        fact_offset = prior_fact["offset"]
        canon_class = None
        canon_fact_id = None
    elif canon_fact is not None:
        fact_quote = str(canon_fact.get("statement") or "").strip()
        fact_statement = fact_quote
        # Canon facts predate the manuscript — no prose position.
        fact_chapter = 0
        fact_offset = 0
        cls = canon_fact.get("classification")
        canon_class = cls if cls in CANON_CLASSES else "INFERRED"
        canon_fact_id = canon_fact.get("id")
    else:
        return None  # dangling reference — discard
    if not fact_quote:
        return None

    key = dedupe_key(subtype, fact_chapter, fact_offset, curr_seq, curr_offset)
    if key in seen_keys:
        return None

    # Stage 3 — evidence-chain verification of THIS pair.
    verdict = _call(
        _VERIFY_PROMPT.format(
            fact_chapter=fact_chapter,
            fact_quote=fact_quote.replace('"', "'")[:400],
            fact_statement=fact_statement[:_MAX_FACT_STATEMENT],
            curr_chapter=curr_seq,
            curr_quote=curr_quote.replace('"', "'")[:400],
            subtype=subtype,
            reasoning=str(prop.get("reasoning") or "")[:400],
        ),
        purpose="constory.verify",
        cfg=cfg,
        db=db,
    )
    if str(verdict.get("verdict") or "").strip().lower() != "confirmed":
        return None

    seen_keys.add(key)
    return {
        "work_id": work_id,
        "chapter_id": chapter["id"],
        "category": category,
        "subtype": subtype,
        "fact_quote": fact_quote,
        "fact_chapter": fact_chapter,
        "fact_offset": fact_offset,
        "contradiction_quote": curr_quote,
        "contradiction_chapter": curr_seq,
        "contradiction_offset": curr_offset,
        "reasoning": str(
            verdict.get("reasoning") or prop.get("reasoning") or ""
        ).strip(),
        "severity": compute_severity(subtype, canon_class),
        "canon_class": canon_class,
        "canon_fact_id": canon_fact_id,
        "dedupe_key": key,
    }


# ---------------------------------------------------------------------------
# Run orchestration (per-work lock + status registry)
# ---------------------------------------------------------------------------

_run_locks: dict[str, threading.Lock] = {}
_run_locks_guard = threading.Lock()
# work_id -> {"state": running|done|error, "started_at", "finished_at",
#             "chapters_done", "chapters_total", "findings_created", "error"}
_RUNS: dict[str, dict] = {}
_RUNS_GUARD = threading.Lock()


def _work_run_lock(work_id: str) -> threading.Lock:
    with _run_locks_guard:
        return _run_locks.setdefault(work_id, threading.Lock())


def get_run_status(work_id: str) -> dict | None:
    with _RUNS_GUARD:
        status = _RUNS.get(work_id)
        return dict(status) if status else None


def is_running(work_id: str) -> bool:
    status = get_run_status(work_id)
    return bool(status and status.get("state") == "running")


def _set_run(work_id: str, **fields: Any) -> None:
    with _RUNS_GUARD:
        _RUNS.setdefault(work_id, {}).update(fields)


def run_constory_check(db: OrivellumDB, cfg: Any, *, work_id: str) -> dict:
    """Run the full ConStory check for a Work (all chapters, whole-book pairing).

    Serialized per work.  On success: replaces the work's still-open constory
    findings with the fresh detection set (dispositioned findings are kept,
    and their dedupe keys prevent re-creation).  On LLM failure: raises
    ConStoryLLMError and leaves stored findings untouched.
    """
    with _work_run_lock(work_id):
        _set_run(
            work_id,
            state="running",
            started_at=time.time(),
            finished_at=None,
            chapters_done=0,
            chapters_total=0,
            findings_created=0,
            error=None,
        )
        try:
            result = _run_locked(db, cfg, work_id=work_id)
            _set_run(work_id, state="done", finished_at=time.time(), **{
                "chapters_done": result["chapters"],
                "chapters_total": result["chapters"],
                "findings_created": result["findings_created"],
            })
            return result
        except Exception as exc:
            _set_run(work_id, state="error", finished_at=time.time(), error=str(exc))
            raise


def _run_locked(db: OrivellumDB, cfg: Any, *, work_id: str) -> dict:
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, seq, title, text FROM book_chapters
               WHERE work_id=? ORDER BY seq""",
            (work_id,),
        ).fetchall()
    chapters = [dict(r) for r in rows]
    _set_run(work_id, chapters_total=len(chapters))
    if len(chapters) == 0:
        return {"chapters": 0, "findings_created": 0, "findings_kept": 0}

    # Sealed/active canon facts for this work (+ series-wide).
    try:
        from orivellum.database.canon_store import CanonStore  # noqa: PLC0415

        canon_facts = CanonStore(db).list_facts(work_id=work_id, status="active", limit=200)
    except Exception:
        canon_facts = []

    # Stage everything first — no deletes until every LLM call succeeded.
    all_facts: list[dict] = []
    staged: list[dict] = []
    for i, chapter in enumerate(chapters):
        if i > 0:  # chapter 1 has nothing prior to contradict
            staged.extend(
                _check_chapter(
                    db, cfg,
                    work_id=work_id,
                    chapter=chapter,
                    prior_facts=all_facts,
                    canon_facts=canon_facts,
                )
            )
        all_facts.extend(_extract_chapter_facts(db, cfg, chapter))
        _set_run(work_id, chapters_done=i + 1)

    # Commit: clear still-open constory findings, insert the fresh set.
    # Dispositioned rows survive; the UNIQUE dedupe key silently skips any
    # re-detection of a finding the author already dispositioned.
    removed = db.delete_open_narrative_findings(work_id, detector="constory")
    created = 0
    for finding in staged:
        if db.create_narrative_finding(**finding, detector="constory") is not None:
            created += 1
    logger.info(
        "constory: work=%s chapters=%d staged=%d created=%d cleared_open=%d",
        work_id, len(chapters), len(staged), created, removed,
    )
    return {
        "chapters": len(chapters),
        "findings_created": created,
        "findings_kept": len(staged) - created,
    }


# ---------------------------------------------------------------------------
# CED — contradiction error density (findings per 10,000 words)
# ---------------------------------------------------------------------------

def compute_ced(db: OrivellumDB, work_id: str) -> dict:
    """CED per chapter and for the whole book.

    Counts findings that represent real (current or fixed) errors — findings
    dispositioned 'intentional' or 'wontfix' are author-declared non-errors
    and excluded from the density.
    """
    with db._lock:
        chapters = db._conn.execute(
            "SELECT id, seq, title, text FROM book_chapters WHERE work_id=? ORDER BY seq",
            (work_id,),
        ).fetchall()
        counts = db._conn.execute(
            """SELECT chapter_id, COUNT(*) AS n FROM narrative_finding
               WHERE work_id=? AND disposition IN ('open','fixed')
               GROUP BY chapter_id""",
            (work_id,),
        ).fetchall()
    by_chapter_count = {r["chapter_id"]: r["n"] for r in counts}

    per_chapter = []
    total_words = 0
    total_findings = 0
    for ch in chapters:
        words = len((ch["text"] or "").split())
        n = by_chapter_count.get(ch["id"], 0)
        total_words += words
        total_findings += n
        per_chapter.append(
            {
                "chapter_id": ch["id"],
                "seq": ch["seq"],
                "title": ch["title"],
                "words": words,
                "findings": n,
                "ced": round(n * 10_000 / words, 2) if words else 0.0,
            }
        )
    return {
        "work_id": work_id,
        "book": {
            "words": total_words,
            "findings": total_findings,
            "ced": round(total_findings * 10_000 / total_words, 2) if total_words else 0.0,
        },
        "chapters": per_chapter,
    }
