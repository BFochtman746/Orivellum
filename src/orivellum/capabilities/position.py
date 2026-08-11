"""POSITION — diagnose where an inherited manuscript truly stands (E5).

Never trust a status field, a filename, or a memory of how far a book got.
The audit derives the stage from evidence and writes a position_audit row:

  Step 1  ingest/decompose      — chapters, word counts, scene counts
  Step 2  ten deterministic tests (T1–T10) — no LLM, each true/false + evidence
  Step 3  reconstruction proposals — canon extraction, persona reconstruction,
          de-facto blueprint, de-facto voice spec.  Everything lands as a
          PROPOSAL in the review gate; nothing becomes authority without the
          author's signature.  Existing prose is evidence, not authority.
  Step 4  instrument battery    — ConStory continuity + fact check, ASSAY
          voice/drift, hierarchical judge; findings + CED per chapter
  Step 5  stage derivation      — the highest stage all of whose acceptance
          tests pass, with no gaps below (i.e. the FIRST failing rung)
  Step 6  completion plan       — Backfill / Repair / Complete, with the
          15–30% early-book band weighted heavily in Repair
  Step 7  discrepancy report    — derived_stage vs claimed_stage in the row

The audit row is the claim (created 'running' under the write lock); every
exit path finishes it as 'done' or 'error' — a leaked 'running' row is a bug.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

# Word-count band a chapter of a full-length novel is expected to sit in.
WORD_BAND = (1_000, 9_000)
# The 15–30% band: facts established early are the ones later chapters
# contradict, so unfixed errors there propagate through everything unwritten.
EARLY_BAND = (0.15, 0.30)
EARLY_BAND_WEIGHT = 3.0

_CANON_CHAPTER_CAP = 60          # LLM canon extraction: max chapters per audit
_PERSONA_CAP = 12                # max characters reconstructed per audit
_CHAPTER_TEXT_CAP = 8_000        # chars of chapter text sent to the model
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_VALID_CLASSIFICATIONS = {"HISTORICAL", "INFERRED", "INVENTED"}
# Signature gates (D15–D17) are author decisions, not battery instruments.
_SIGNATURE_GATES = {"gate.d15", "gate.d16", "gate.d17"}


class PositionError(RuntimeError):
    """Raised when the audit cannot produce an honest result."""


def _det_id(*parts: str) -> str:
    """Deterministic proposal id — re-runs never clobber resolved rows."""
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _now_words(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _load_chapters(db: Any, work_id: str) -> list[dict]:
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, seq, title, text, meta FROM book_chapters
               WHERE work_id=? ORDER BY seq""",
            (work_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            meta = json.loads(d.get("meta") or "{}")
        except Exception:
            meta = {}
        d["meta"] = meta
        d["word_count"] = _now_words(d.get("text") or "")
        d["scene_count"] = int(meta.get("scene_count") or 0)
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Step 2 — the ten deterministic tests
# ---------------------------------------------------------------------------

def _test(test_id: str, name: str, passed: bool, evidence: dict) -> dict:
    return {"id": test_id, "name": name, "passed": bool(passed), "evidence": evidence}


def _t1_contiguity(chapters: list[dict]) -> dict:
    seqs = {int(c["seq"]) for c in chapters if c.get("seq") is not None}
    top = max(seqs) if seqs else 0
    gaps = [n for n in range(1, top + 1) if n not in seqs]
    return _test("T1", "Chapters contiguous 1..N", bool(seqs) and not gaps,
                 {"chapter_count": len(chapters), "max_seq": top, "gaps": gaps})


def _t2_text(chapters: list[dict]) -> dict:
    empty = [int(c["seq"]) for c in chapters if not (c.get("text") or "").strip()]
    return _test("T2", "All chapters have text", bool(chapters) and not empty,
                 {"empty_chapters": empty})


def _t3_word_band(chapters: list[dict]) -> dict:
    lo, hi = WORD_BAND
    outliers = [
        {"seq": int(c["seq"]), "title": c.get("title") or "", "words": c["word_count"]}
        for c in chapters if not (lo <= c["word_count"] <= hi)
    ]
    return _test("T3", "Word counts within genre band", bool(chapters) and not outliers,
                 {"band": [lo, hi], "outliers": outliers})


def _t4_blueprint(db: Any, work_id: str) -> dict:
    with db._lock:
        g8 = db._conn.execute(
            """SELECT 1 FROM genesis_books b
               JOIN genesis_artifacts a ON a.book_id=b.id AND a.stage_code='G8'
               WHERE b.work_id=? AND length(trim(a.content)) > 0""",
            (work_id,),
        ).fetchone()
    ratified = any(
        p["kind"] == "blueprint"
        for p in db.list_position_proposals(work_id=work_id, status="approved")
    )
    return _test("T4", "Blueprint exists", bool(g8) or ratified,
                 {"g8_artifact": bool(g8), "ratified_defacto_blueprint": ratified})


def _t5_canon(db: Any, work_id: str) -> dict:
    with db._lock:
        rows = db._conn.execute(
            """SELECT classification, COUNT(*) AS c FROM canon_fact
               WHERE status='active' AND (work_id=? OR work_id IS NULL)
               GROUP BY classification""",
            (work_id,),
        ).fetchall()
    counts = {r["classification"]: r["c"] for r in rows}
    return _test("T5", "Canon table populated", sum(counts.values()) > 0,
                 {"count_by_classification": counts})


def _t6_voice(db: Any, work_id: str) -> dict:
    baseline = db.get_assay_baseline(work_id, "voice_envelope")
    return _test("T6", "Voice baseline exists", baseline is not None,
                 {"stored": baseline is not None,
                  "source": (baseline or {}).get("source")})


def _press_book(work_id: str) -> tuple[dict | None, str | None]:
    try:
        from orivellum.capabilities.finishing import press  # noqa: PLC0415
        books = press.list_books(work_id=work_id)
        return (books[0] if books else None), None
    except Exception as exc:  # press not configured / no press.db yet
        return None, str(exc)


def _t7_style(book: dict | None, err: str | None) -> dict:
    locked = bool(book and book.get("style_locked"))
    ev: dict = {"press_row": bool(book), "style_locked": locked}
    if err:
        ev["press_error"] = err
    return _test("T7", "Style locked", locked, ev)


def _t8_matter(book: dict | None, err: str | None) -> dict:
    front = bool(book and book.get("has_front"))
    back = bool(book and book.get("has_back"))
    ev: dict = {"has_front": front, "has_back": back}
    if err:
        ev["press_error"] = err
    return _test("T8", "Front/back matter present", front and back, ev)


def _t9_pages(db: Any, work_id: str) -> dict:
    work = db.get_work(work_id) or {}
    try:
        meta = json.loads(work.get("meta") or "{}") if isinstance(work.get("meta"), str) \
            else (work.get("meta") or {})
    except Exception:
        meta = {}
    pages = meta.get("actual_pages")
    return _test("T9", "actual_pages known", isinstance(pages, int) and pages > 0,
                 {"actual_pages": pages if isinstance(pages, int) else None,
                  "note": "layout run not recorded" if not pages else "recorded in work meta"})


def _t10_standard(db: Any) -> dict:
    from orivellum.capabilities import assay  # noqa: PLC0415
    registered = {
        i["key"] for i in db.list_assay_instruments()
        if i.get("certification") != "retired"
    }
    missing = [k for k in assay.INSTRUMENT_KEYS if k not in registered]
    return _test("T10", "Standard bound (D13–D17 registered)", not missing,
                 {"registered": sorted(registered & set(assay.INSTRUMENT_KEYS)),
                  "missing": missing})


def deterministic_tests(db: Any, work_id: str, chapters: list[dict]) -> list[dict]:
    """The ten evidence tests.  No LLM — these are facts."""
    book, press_err = _press_book(work_id)
    return [
        _t1_contiguity(chapters),
        _t2_text(chapters),
        _t3_word_band(chapters),
        _t4_blueprint(db, work_id),
        _t5_canon(db, work_id),
        _t6_voice(db, work_id),
        _t7_style(book, press_err),
        _t8_matter(book, press_err),
        _t9_pages(db, work_id),
        _t10_standard(db),
    ]


# ---------------------------------------------------------------------------
# Step 3 — reconstruction proposals (review-gated; never auto-authority)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "The", "A", "An", "And", "But", "Or", "It", "He", "She", "They", "We",
    "You", "I", "His", "Her", "Their", "Then", "When", "There", "This",
    "That", "What", "Why", "How", "Not", "No", "Yes", "In", "On", "At",
    "By", "For", "With", "From", "To", "Of", "As", "If", "So", "Now",
}


def _proper_nouns(text: str) -> Counter:
    """Capitalized tokens that are not sentence-initial — a cheap, fully
    deterministic cast signal that needs no entity table."""
    counts: Counter = Counter()
    for m in re.finditer(r"(?<![.!?\"\u201c]\s)(?<!^)\b([A-Z][a-z]{2,})\b", text or ""):
        w = m.group(1)
        if w not in _STOPWORDS:
            counts[w] += 1
    return counts


def _character_names(chapters: list[dict], cap: int = _PERSONA_CAP) -> list[str]:
    total: Counter = Counter()
    for c in chapters:
        total.update(_proper_nouns(c.get("text") or ""))
    return [name for name, n in total.most_common(cap) if n >= 3]


def _snippet(text: str, n: int = 160) -> str:
    t = (text or "").strip()
    return t[:n] + ("…" if len(t) > n else "")


def _defacto_blueprint_payload(chapters: list[dict]) -> dict:
    """What each existing chapter actually does — versus what a blueprint
    would have contracted.  Deterministic; no model."""
    rows = []
    for c in chapters:
        text = c.get("text") or ""
        cast = [n for n, k in _proper_nouns(text).most_common(6) if k >= 2]
        rows.append({
            "seq": int(c["seq"]),
            "title": c.get("title") or "",
            "word_count": c["word_count"],
            "scene_count": c["scene_count"],
            "cast": cast,
            "opening": _snippet(text.split("\n\n", 1)[0]),
            "exit_state": _snippet(text.rsplit("\n\n", 1)[-1]),
        })
    return {"chapters": rows, "chapter_count": len(rows)}


def _defacto_voice_payload(chapters: list[dict], names: list[str]) -> dict:
    """A4 metrics computed over the existing chapters — the manuscript is its
    own voice authority.  Computed only; STORED as baseline only on approval."""
    from orivellum.capabilities.assay import metrics  # noqa: PLC0415
    text = "\n\n".join(c.get("text") or "" for c in chapters)
    if not text.strip():
        raise PositionError("no chapter text to compute a de-facto voice spec from")
    return {
        "metrics": metrics.compute_voice_metrics(text, character_names=names),
        "character_names": names,
        "source": f"{len(chapters)} chapters (de-facto, POSITION audit)",
    }


def _insert_canon_proposal(
    db: Any, *, pid: str, title: str, text: str, classification: str,
    work_id: str, source_path: str, source_location: str,
) -> bool:
    from datetime import UTC, datetime  # noqa: PLC0415
    with db._lock:
        cur = db._conn.execute(
            """INSERT OR IGNORE INTO wa_canon_proposals
               (id, fact_title, fact_text, classification, scope, source_path,
                source_location, status, created_at)
               VALUES(?,?,?,?,?,?,?,'proposed',?)""",
            (pid, title, text, classification, f"work:{work_id}", source_path,
             source_location, datetime.now(UTC).isoformat()),
        )
        db._conn.commit()
    return cur.rowcount > 0


def _parse_json_list(raw: str) -> list:
    """Strict: the model must return a JSON list (optionally fenced)."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?|```$", "", s).strip()
    data = json.loads(s)
    if not isinstance(data, list):
        raise ValueError("expected a JSON list")
    return data


def _canon_items_for_chapter(db: Any, cfg: Any, chapter: dict) -> list[dict]:
    """One LLM call per chapter; strict validation, no fabrication."""
    from orivellum.capabilities.llm import llm_call  # noqa: PLC0415
    text = (chapter.get("text") or "")[:_CHAPTER_TEXT_CAP]
    r = llm_call(
        [
            {"role": "system", "content": (
                "Extract every factual claim this chapter establishes about the "
                "story world (names, dates, places, relationships, physical facts). "
                "Return ONLY a JSON list of objects: "
                '{"statement": str, "classification": "HISTORICAL"|"INFERRED"|"INVENTED", '
                '"quote": str} where quote is the exact supporting text from the chapter. '
                "HISTORICAL means verifiable real-world fact; INFERRED means implied by "
                "the text; INVENTED means fictional. No prose, no markdown."
            )},
            {"role": "user",
             "content": f"Chapter {chapter['seq']}: {chapter.get('title') or ''}\n\n{text}"},
        ],
        cfg=cfg, db=db, purpose="position.canon_extract", timeout=120, temperature=0.0,
    )
    if not r.ok:
        raise PositionError(f"canon extraction LLM failure: {r.error}")
    items = _parse_json_list(r.text)
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        stmt = str(it.get("statement") or "").strip()
        cls = str(it.get("classification") or "").strip().upper()
        quote = str(it.get("quote") or "").strip()
        if not stmt or cls not in _VALID_CLASSIFICATIONS:
            continue
        out.append({"statement": stmt, "classification": cls, "quote": quote})
    return out


def _reconstruct_canon(db: Any, cfg: Any, work_id: str, chapters: list[dict]) -> dict:
    """Canon extraction → wa_canon_proposals (ratified via CanonStore only).

    HISTORICAL claims with no locatable source quote are proposed as INFERRED
    with the gap flagged — the classification is never auto-accepted anyway."""
    proposed = skipped = 0
    errors: list[str] = []
    for chapter in chapters[:_CANON_CHAPTER_CAP]:
        try:
            items = _canon_items_for_chapter(db, cfg, chapter)
        except Exception as exc:
            errors.append(f"chapter {chapter['seq']}: {exc}")
            break  # gateway down / malformed model — stop hammering, report
        for it in items:
            text = chapter.get("text") or ""
            offset = text.find(it["quote"]) if it["quote"] else -1
            classification, location = it["classification"], f"offset:{offset}"
            if offset < 0:
                location = "unlocated"
                if classification == "HISTORICAL":
                    # No locatable source → propose as INFERRED, gap flagged.
                    classification = "INFERRED"
                    it["statement"] += " [source gap: HISTORICAL claim without locatable source]"
            pid = _det_id("canon", work_id, it["statement"])
            created = _insert_canon_proposal(
                db, pid=pid, title=_snippet(it["statement"], 60),
                text=it["statement"], classification=classification,
                work_id=work_id,
                source_path=f"work:{work_id}/chapter:{chapter['seq']}",
                source_location=location,
            )
            proposed += int(created)
            skipped += int(not created)
    return {"proposed": proposed, "already_present": skipped, "errors": errors}


def _persona_payload(db: Any, cfg: Any, name: str, chapters: list[dict]) -> dict:
    """One character's attributes/relationships as the prose establishes them,
    with the chapter where each was set.  Strictly validated model output."""
    from orivellum.capabilities.llm import llm_call  # noqa: PLC0415
    excerpts = []
    budget = 6_000
    for c in chapters:
        for line in (c.get("text") or "").splitlines():
            if name in line and line.strip():
                snippet = f"[ch {c['seq']}] {line.strip()[:300]}"
                excerpts.append(snippet)
                budget -= len(snippet)
                if budget <= 0:
                    break
        if budget <= 0:
            break
    r = llm_call(
        [
            {"role": "system", "content": (
                "Reconstruct this character exactly as the excerpts establish them. "
                "Return ONLY a JSON list of objects: "
                '{"kind": "attribute"|"relationship", "statement": str, "chapter": int, '
                '"quote": str}. Only include what the text supports. No prose.'
            )},
            {"role": "user", "content": f"Character: {name}\n\n" + "\n".join(excerpts)},
        ],
        cfg=cfg, db=db, purpose="position.persona", timeout=120, temperature=0.0,
    )
    if not r.ok:
        raise PositionError(f"persona LLM failure: {r.error}")
    items = []
    for it in _parse_json_list(r.text):
        if not isinstance(it, dict) or not str(it.get("statement") or "").strip():
            continue
        items.append({
            "kind": "relationship" if it.get("kind") == "relationship" else "attribute",
            "statement": str(it["statement"]).strip(),
            "chapter": int(it.get("chapter") or 0),
            "quote": str(it.get("quote") or "").strip(),
        })
    return {"name": name, "established": items, "mention_excerpts": len(excerpts)}


def reconstruct(db: Any, cfg: Any, *, audit_id: str, work_id: str,
                chapters: list[dict]) -> dict:
    """Step 3 — derive the missing origination artifacts from the prose and
    stage every one as a review-gated proposal.  Nothing writes authority."""
    result: dict = {"proposals": {}, "errors": []}
    names = _character_names(chapters)

    # De-facto blueprint (deterministic).
    bp = _defacto_blueprint_payload(chapters)
    created = db.upsert_position_proposal(
        proposal_id=_det_id("blueprint", work_id),
        work_id=work_id, audit_id=audit_id, kind="blueprint",
        title=f"De-facto blueprint ({len(chapters)} chapters)",
        payload=bp, evidence={"derived_from": "existing chapter text", "deterministic": True},
    )
    result["proposals"]["blueprint"] = {"created": created}

    # De-facto voice spec (deterministic A4 metrics).
    try:
        vp = _defacto_voice_payload(chapters, names)
        created = db.upsert_position_proposal(
            proposal_id=_det_id("voice_spec", work_id),
            work_id=work_id, audit_id=audit_id, kind="voice_spec",
            title="De-facto voice spec (manuscript as its own voice authority)",
            payload=vp, evidence={"derived_from": vp["source"], "deterministic": True},
        )
        result["proposals"]["voice_spec"] = {"created": created}
    except PositionError as exc:
        result["errors"].append(f"voice_spec: {exc}")

    # Persona reconstruction (LLM; stops on first gateway failure).
    personas = {"created": 0, "already_present": 0}
    for name in names:
        try:
            payload = _persona_payload(db, cfg, name, chapters)
        except Exception as exc:
            result["errors"].append(f"persona {name}: {exc}")
            break
        created = db.upsert_position_proposal(
            proposal_id=_det_id("persona", work_id, name),
            work_id=work_id, audit_id=audit_id, kind="persona",
            title=f"Persona reconstruction: {name}",
            payload=payload,
            evidence={"derived_from": "prose excerpts", "character": name},
        )
        personas["created" if created else "already_present"] += 1
    result["proposals"]["persona"] = personas

    # Canon extraction (LLM → wa_canon_proposals, CanonStore-ratified).
    canon = _reconstruct_canon(db, cfg, work_id, chapters)
    result["proposals"]["canon_fact"] = canon
    result["errors"].extend(canon.pop("errors"))
    return result


# ---------------------------------------------------------------------------
# Step 4 — instrument battery
# ---------------------------------------------------------------------------

def run_battery(db: Any, cfg: Any, work_id: str) -> dict:
    """B6 continuity + B7 fact check (ConStory checks prose against canon),
    B8 voice/drift + D13/D14, B9 hierarchical judge.  Every failure is
    recorded — never swallowed, never fabricated as a clean result."""
    from orivellum.capabilities import assay  # noqa: PLC0415

    instruments = []
    for key in assay.INSTRUMENT_KEYS:
        if key in _SIGNATURE_GATES:
            continue  # D15–D17 open on the author's signature, not an audit
        try:
            run = assay.run_instrument(db, cfg, key=key, work_id=work_id)
            instruments.append({
                "key": key, "status": "done",
                "verdict": run.get("verdict"),
                "findings_count": run.get("findings_count", 0),
            })
        except Exception as exc:
            instruments.append({"key": key, "status": "error", "error": str(exc)})

    from orivellum.capabilities.constory import compute_ced, run_constory_check  # noqa: PLC0415
    try:
        cres = run_constory_check(db, cfg, work_id=work_id)
        constory = {"status": "done", **cres}
    except Exception as exc:
        constory = {"status": "error", "error": str(exc)}
    try:
        ced = compute_ced(db, work_id)
    except Exception as exc:
        ced = {"error": str(exc)}
    return {"instruments": instruments, "constory": constory, "ced": ced}


def _open_narrative_findings(db: Any, work_id: str) -> list[dict]:
    with db._lock:
        rows = db._conn.execute(
            """SELECT nf.id, nf.severity, nf.category, nf.subtype, nf.reasoning,
                      nf.contradiction_chapter, c.seq AS chapter_seq
               FROM narrative_finding nf
               LEFT JOIN book_chapters c ON c.id = nf.chapter_id
               WHERE nf.work_id=? AND nf.disposition='open'""",
            (work_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Steps 5–6 — stage derivation + completion plan
# ---------------------------------------------------------------------------

def _battery_clean(battery: dict, *, severities: tuple[str, ...]) -> bool:
    """True only when ConStory actually ran AND left no open finding at the
    given severities.  An errored run can never count as clean."""
    return battery.get("constory", {}).get("status") == "done" and not any(
        f["severity"] in severities for f in battery.get("_open_findings", [])
    )


_FAILING_VERDICTS = ("confirmed_drift", "out_of_envelope", "structural_violations")


def _instruments_ok(battery: dict, *, keys: set[str]) -> bool:
    """Fail closed: every required instrument must be PRESENT, status 'done',
    and free of a failing verdict.  A missing or errored run can never count
    as clean — that would be a fabricated clean result."""
    by_key = {i["key"]: i for i in battery.get("instruments", [])}
    for key in keys:
        inst = by_key.get(key)
        if inst is None or inst.get("status") != "done":
            return False
        if inst.get("verdict") in _FAILING_VERDICTS:
            return False
    return True


def _drift_clean(battery: dict) -> bool:
    """Voice/drift acceptance: envelope + all four drift detectors + the two
    Tier-1 gates (D13 pacing, D14 drift confirmation) ran and came back clean."""
    keys = {
        i["key"] for i in battery.get("instruments", [])
        if i["key"].startswith(("drift.", "voice."))
    }
    keys |= {"voice.envelope", "gate.d13", "gate.d14"}
    return _instruments_ok(battery, keys=keys)


def _judge_recorded(battery: dict) -> bool:
    """B9 acceptance: the hierarchical judge actually ran (its content is
    advisory and never blocks, but an errored/missing run is not a pass)."""
    return _instruments_ok(battery, keys={"judge.hierarchical"})


def _stage_ladder(tests: dict, battery: dict, chapters: list[dict],
                  approved: dict) -> list[tuple[str, str, bool]]:
    """Ordered acceptance rungs.  Derived stage = the first failing rung —
    'highest stage all of whose acceptance tests pass, with no gaps below'."""
    return [
        ("A1", "Canon ratified", tests["T5"]["passed"]),
        ("A2", "Personas ratified", bool(approved.get("persona"))),
        ("A3", "Blueprint sealed or ratified", tests["T4"]["passed"]),
        ("A4", "Voice baseline stored", tests["T6"]["passed"]),
        ("A5", "Standard bound", tests["T10"]["passed"]),
        ("B4", "Chapter scaffold sound",
         bool(chapters) and tests["T1"]["passed"] and tests["T2"]["passed"]),
        ("B5", "Drafting within band", tests["T3"]["passed"]),
        ("B6", "Continuity clean", _battery_clean(battery, severities=("critical", "high"))),
        ("B8", "Voice within envelope", _drift_clean(battery)),
        ("B9", "Judge pass recorded", _judge_recorded(battery)),
        ("B13", "Style locked", tests["T7"]["passed"]),
        ("B14", "Front/back matter present", tests["T8"]["passed"]),
        ("B15", "Page count known", tests["T9"]["passed"]),
    ]


def derive_stage(tests: dict, battery: dict, chapters: list[dict],
                 approved: dict) -> dict:
    ladder = _stage_ladder(tests, battery, chapters, approved)
    derived, failed_rung = "B16", None
    for stage, label, passed in ladder:
        if not passed:
            derived, failed_rung = stage, label
            break
    qualifier = "with-prose" if chapters and derived.startswith("A") else ""
    return {
        "derived_stage": derived,
        "qualifier": qualifier,
        "first_failing_rung": failed_rung,
        "ladder": [{"stage": s, "label": lbl, "passed": p} for s, lbl, p in ladder],
    }


def claimed_stage(db: Any, work_id: str) -> str | None:
    pipeline = db.get_book_pipeline_for_work(work_id)
    if pipeline:
        return pipeline.get("status") or None
    with db._lock:
        row = db._conn.execute(
            "SELECT state FROM genesis_books WHERE work_id=?", (work_id,)
        ).fetchone()
    return f"genesis:{row['state']}" if row else None


def _repair_weight(seq: int, total: int) -> float:
    if total <= 0 or seq <= 0:
        return 1.0
    frac = seq / total
    lo, hi = EARLY_BAND
    return EARLY_BAND_WEIGHT if lo <= frac <= hi else 1.0


def _repair_list(findings: list[dict], total_chapters: int) -> list[dict]:
    items = []
    for f in findings:
        seq = int(f.get("chapter_seq") or f.get("contradiction_chapter") or 0)
        items.append({
            "finding_id": f["id"],
            "chapter_seq": seq,
            "severity": f["severity"],
            "category": f.get("category") or "",
            "summary": _snippet(f.get("reasoning") or f.get("subtype") or "", 200),
            "weight": _repair_weight(seq, total_chapters),
        })
    items.sort(key=lambda i: (
        -i["weight"], _SEVERITY_RANK.get(i["severity"], 9), i["chapter_seq"]))
    return items


def completion_plan(db: Any, work_id: str, *, stage: dict, tests: dict,
                    chapters: list[dict], findings: list[dict]) -> dict:
    """Three ordered lists: Backfill, Repair (early band weighted), Complete."""
    backfill = [
        {"stage": s["stage"], "artifact": s["label"],
         "action": "ratify through the review gate"}
        for s in stage["ladder"] if s["stage"].startswith("A") and not s["passed"]
    ]
    repair = _repair_list(findings, len(chapters))

    complete: list[dict] = []
    target = None
    for p in db.list_position_proposals(work_id=work_id, status="approved"):
        if p["kind"] == "blueprint":
            target = p["payload"].get("chapter_count")
    if target and target > len(chapters):
        for seq in range(len(chapters) + 1, int(target) + 1):
            complete.append({
                "seq": seq,
                "contract": "derive from ratified de-facto blueprint + sealed structure",
            })
    elif not tests["T4"]["passed"]:
        complete.append({"note": "remaining-chapter contracts require a ratified "
                                 "blueprint (see Backfill)"})
    return {"backfill": backfill, "repair": repair, "complete": complete}


# ---------------------------------------------------------------------------
# The audit — orchestration
# ---------------------------------------------------------------------------

def _run(db: Any, cfg: Any, *, audit_id: str, work_id: str) -> dict:
    chapters = _load_chapters(db, work_id)
    tests_list = deterministic_tests(db, work_id, chapters)
    tests = {t["id"]: t for t in tests_list}

    recon = reconstruct(db, cfg, audit_id=audit_id, work_id=work_id, chapters=chapters)
    battery = run_battery(db, cfg, work_id)
    findings = _open_narrative_findings(db, work_id)
    battery["_open_findings"] = findings

    approved: dict = {}
    for p in db.list_position_proposals(work_id=work_id, status="approved"):
        approved.setdefault(p["kind"], []).append(p["id"])

    stage = derive_stage(tests, battery, chapters, approved)
    claimed = claimed_stage(db, work_id)
    plan = completion_plan(db, work_id, stage=stage, tests=tests,
                           chapters=chapters, findings=findings)
    battery.pop("_open_findings", None)

    evidence = {
        "chapters": {
            "count": len(chapters),
            "total_words": sum(c["word_count"] for c in chapters),
            "per_chapter": [{"seq": int(c["seq"]), "words": c["word_count"],
                             "scenes": c["scene_count"]} for c in chapters],
        },
        "tests": tests_list,
        "reconstruction": recon,
        "battery": battery,
        "stage_derivation": stage,
        "discrepancy": {
            "derived_stage": stage["derived_stage"],
            "claimed_stage": claimed,
            "match": claimed is not None and claimed == stage["derived_stage"],
        },
    }
    return {
        "derived_stage": stage["derived_stage"],
        "claimed_stage": claimed,
        "evidence": evidence,
        "blocking": plan,
    }


def run_position_audit(db: Any, cfg: Any, *, audit_id: str, work_id: str) -> dict:
    """Execute the full seven-step audit against a pre-claimed audit row.

    The row is the claim: every exit path finishes it — success as 'done',
    ANY failure as 'error'.  A leaked 'running' row is a bug."""
    try:
        result = _run(db, cfg, audit_id=audit_id, work_id=work_id)
    except Exception as exc:
        logger.exception("position audit %s failed", audit_id)
        db.finish_position_audit(audit_id, status="error", error=str(exc))
        raise
    db.finish_position_audit(
        audit_id, status="done",
        derived_stage=result["derived_stage"],
        claimed_stage=result["claimed_stage"],
        evidence=result["evidence"],
        blocking=result["blocking"],
    )
    return result
