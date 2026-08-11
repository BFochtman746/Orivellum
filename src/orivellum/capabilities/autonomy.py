"""AUTONOMY — unattended draft → check → revise runs (Masterpiece M12).

The long-horizon runner: draft the next chapter with LOOM, run the check
battery, attempt a BOUNDED revision of blocking continuity findings on the
chapter it just drafted, and either continue or halt.

Guarantees, in order of importance:

- **Signatures stay human, forever.**  The runner never writes an
  ``assay_signature`` row and never calls a resolver with an author.  An
  unsigned signature gate (D15–D17) halts the run and queues a review item.
- **Fail closed.**  Any open critical/high narrative finding, any failing or
  errored BLOCKING instrument, an errored ConStory check, or a LOOM
  escalation halts the chapter.  A check that could not run never counts as
  clean.
- **Halts leave a clean queue.**  Every halt inserts ONE ``suggestions`` row
  (kind ``autonomy_halt``) carrying the full context — run id, chapter,
  reasons, finding ids — which the unified review queue surfaces as
  ``suggestion:<id>``.
- **The run row is the claim.**  ``db.create_autonomy_run`` refuses a second
  concurrent run per work; every exit path finishes the row.
- **Budgets are enforced between steps**: max chapters, wall-clock minutes,
  and LLM tokens (measured against ``llm_calls`` rows created after the run
  started).  The kill switch (``autonomy_enabled`` setting) is re-checked
  before every chapter so flipping it off stops a run cleanly mid-flight.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from orivellum.capabilities.constory import run_constory_check
from orivellum.capabilities.loom import run_loom_draft
from orivellum.capabilities.position import run_battery
from orivellum.database.db import OrivellumDB, _now

logger = logging.getLogger(__name__)

SIGNATURE_GATES = ("gate.d15", "gate.d16", "gate.d17")
_FAILING_VERDICTS = ("confirmed_drift", "out_of_envelope", "structural_violations")
_BLOCKING_SEVERITIES = ("critical", "high")

# Bounded revision: at most this many findings get ONE surgical-edit attempt
# each, per chapter.  Anything left open after that halts the chapter.
MAX_REVISE_FINDINGS = 2
# Context added around a finding's quoted contradiction for the edit band.
_BAND_PAD = 300

DEFAULT_BUDGET = {"max_chapters": 1, "max_minutes": 30, "max_tokens": 0, "halt_policy": "stop"}

SETTING_KEYS = {
    "autonomy_enabled": "false",
    "autonomy_nightshift_enabled": "false",
    "autonomy_max_chapters": "1",
    "autonomy_max_minutes": "30",
    "autonomy_max_tokens": "0",
    "autonomy_halt_policy": "stop",
}


def budget_from_settings(db: OrivellumDB, overrides: dict | None = None) -> dict:
    """Resolve the run budget: defaults ← settings ← explicit overrides."""
    budget = dict(DEFAULT_BUDGET)
    try:
        budget["max_chapters"] = int(db.get_setting("autonomy_max_chapters", "1"))
        budget["max_minutes"] = int(db.get_setting("autonomy_max_minutes", "30"))
        budget["max_tokens"] = int(db.get_setting("autonomy_max_tokens", "0"))
    except (TypeError, ValueError):
        pass
    policy = db.get_setting("autonomy_halt_policy", "stop")
    budget["halt_policy"] = policy if policy in ("stop", "continue") else "stop"
    for key, value in (overrides or {}).items():
        if value is None or key not in budget:
            continue
        if key == "halt_policy":
            if value in ("stop", "continue"):
                budget[key] = value
        else:
            budget[key] = max(0, int(value))
    budget["max_chapters"] = max(1, int(budget["max_chapters"]))
    return budget


def enabled(db: OrivellumDB) -> bool:
    return db.get_setting("autonomy_enabled", "false").lower() == "true"


# ── internals ────────────────────────────────────────────────────────────────


def _next_chapter(db: OrivellumDB, work_id: str, exclude: set[str] | None = None) -> dict | None:
    """Lowest-seq chapter that still needs prose and is not approved.
    ``exclude`` holds chapters already halted THIS run (halt_policy
    'continue' must move on, never spin on the same chapter)."""
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, seq, title, text, status, meta FROM book_chapters
               WHERE work_id=? AND status != 'approved'
                 AND (text IS NULL OR trim(text) = '')
               ORDER BY seq ASC""",
            (work_id,),
        ).fetchall()
    for row in rows:
        if not exclude or row["id"] not in exclude:
            return dict(row)
    return None


def _llm_call_baseline(db: OrivellumDB) -> int:
    with db._lock:
        row = db._conn.execute("SELECT COALESCE(MAX(id),0) AS m FROM llm_calls").fetchone()
    return int(row["m"])


def _tokens_since(db: OrivellumDB, baseline: int) -> int:
    with db._lock:
        row = db._conn.execute(
            """SELECT COALESCE(SUM(COALESCE(prompt_tokens,0)
                                 + COALESCE(completion_tokens,0)),0) AS t
               FROM llm_calls WHERE id > ?""",
            (baseline,),
        ).fetchone()
    return int(row["t"])


def _open_blocking_findings(db: OrivellumDB, work_id: str) -> list[dict]:
    with db._lock:
        rows = db._conn.execute(
            """SELECT * FROM narrative_finding
               WHERE work_id=? AND disposition='open'
                 AND severity IN ('critical','high')""",
            (work_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def _instrument_blockers(db: OrivellumDB, battery: dict) -> list[str]:
    """Failing/errored BLOCKING instruments (Tier 1/2, certified).  Advisory
    and shadow instruments never halt a run; their results stay in the
    report."""
    from orivellum.capabilities import assay  # noqa: PLC0415

    reasons: list[str] = []
    for inst in battery.get("instruments", []):
        key = inst.get("key")
        failed = inst.get("status") != "done" or inst.get("verdict") in _FAILING_VERDICTS
        if not failed:
            continue
        contract = db.get_assay_instrument(key)
        if contract is not None and assay.is_blocking(contract):
            detail = inst.get("verdict") or inst.get("error") or inst.get("status")
            reasons.append(f"blocking instrument {key}: {detail}")
    return reasons


def _unsigned_gates(db: OrivellumDB, work_id: str) -> list[str]:
    """Signature gates without a live open/go decision.  The runner reports
    them — it NEVER signs them."""
    pending = []
    for key in SIGNATURE_GATES:
        sig = db.latest_assay_signature(work_id, key)
        if sig is None or sig["decision"] not in ("open", "go"):
            pending.append(key)
    return pending


def _queue_halt(
    db: OrivellumDB,
    *,
    run_id: str,
    work_id: str,
    title: str,
    chapter: dict | None,
    reasons: list[str],
    finding_ids: list[str] | None = None,
) -> str:
    """ONE review-queue item per halt, with full context in meta."""
    sid = str(uuid.uuid4())
    seq = chapter.get("seq") if chapter else None
    text = title if seq is None else f"Chapter {seq}: {title}"
    meta = {
        "run_id": run_id,
        "chapter_id": chapter.get("id") if chapter else None,
        "chapter_seq": seq,
        "reasons": reasons[:20],
        "finding_ids": (finding_ids or [])[:20],
    }
    with db._lock:
        db._conn.execute(
            """INSERT INTO suggestions(id, work_id, kind, text, meta, created_at)
               VALUES(?,?,?,?,?,?)""",
            (sid, work_id, "autonomy_halt", text, json.dumps(meta), _now()),
        )
        db._conn.commit()
    return sid


def _chapter_findings(findings: list[dict], seq: int) -> list[dict]:
    return [f for f in findings if int(f.get("contradiction_chapter", -1)) == seq]


def _revise_finding(db: OrivellumDB, cfg: Any, chapter: dict, finding: dict) -> dict:
    """ONE bounded surgical-edit attempt for one finding.  Never accepts a
    regression (that needs an author signature — humans only)."""
    from orivellum.capabilities import band  # noqa: PLC0415

    with db._lock:
        row = db._conn.execute(
            "SELECT text FROM book_chapters WHERE id=?", (chapter["id"],)
        ).fetchone()
    text = (row["text"] if row else "") or ""
    quote = finding.get("contradiction_quote") or ""
    offset = int(finding.get("contradiction_offset") or 0)
    start = max(0, offset - _BAND_PAD)
    end = min(len(text), offset + max(len(quote), 1) + _BAND_PAD)
    if end <= start or not text:
        return {"committed": False, "reasons": ["finding offsets outside chapter text"]}
    instruction = (
        f"Fix this {finding.get('category', 'continuity')} contradiction without "
        f"changing anything else. Established fact: {finding.get('fact_quote', '')!r}. "
        f"The contradicting passage: {quote!r}. "
        f"Reasoning: {finding.get('reasoning', '')}"
    )[:2000]
    try:
        return band.surgical_edit(
            db,
            cfg,
            chapter_id=chapter["id"],
            start=start,
            end=end,
            instruction=instruction,
            base_fingerprint=band.fingerprint(text),
            author="",
            accept_regression=False,
        )
    except Exception as exc:
        return {"committed": False, "reasons": [f"band edit failed: {exc}"]}


def _check_chapter(
    db: OrivellumDB, cfg: Any, work_id: str, seq: int
) -> tuple[list[str], list[dict], dict]:
    """Full battery + blocker evaluation.  Returns (reasons, chapter-scoped
    blocking findings, battery summary)."""
    battery = run_battery(db, cfg, work_id)
    reasons: list[str] = []
    constory = battery.get("constory", {})
    if constory.get("status") != "done":
        reasons.append(f"continuity check failed to run: {constory.get('error')}")
    reasons.extend(_instrument_blockers(db, battery))
    findings = _open_blocking_findings(db, work_id)
    if findings:
        reasons.append(
            f"{len(findings)} open critical/high finding(s): "
            + ", ".join(f"{f['severity']}/{f['category']}" for f in findings[:5])
        )
    summary = {
        "instruments": battery.get("instruments", []),
        "constory_status": constory.get("status"),
        "open_blocking_findings": len(findings),
    }
    return reasons, _chapter_findings(findings, seq), summary


def _draft_chapter(db: OrivellumDB, cfg: Any, work_id: str, chapter: dict) -> tuple[bool, str]:
    """Draft via LOOM under its own run claim.  (ok, detail)."""
    try:
        loom_run_id = db.create_loom_run(work_id, chapter["id"])
    except RuntimeError as exc:
        return False, f"draft claim refused: {exc}"
    try:
        result = run_loom_draft(
            db, cfg, run_id=loom_run_id, work_id=work_id, chapter_id=chapter["id"]
        )
    except Exception as exc:  # loom already finished its row as 'error'
        return False, f"draft error: {exc}"
    if result.get("status") != "done":
        return False, f"draft {result.get('status')}: {result.get('reason')}"
    return True, "drafted"


def _budget_stop(
    budget: dict, *, started: float, tokens_used: int, chapters_done: int
) -> str | None:
    if chapters_done >= budget["max_chapters"]:
        return f"budget: chapter cap {budget['max_chapters']} reached"
    if budget["max_minutes"] and (time.monotonic() - started) / 60 >= budget["max_minutes"]:
        return f"budget: {budget['max_minutes']} minute cap reached"
    if budget["max_tokens"] and tokens_used >= budget["max_tokens"]:
        return f"budget: {budget['max_tokens']} token cap reached"
    return None


def _try_revise(
    db: OrivellumDB,
    cfg: Any,
    work_id: str,
    chapter: dict,
    chapter_findings: list[dict],
    entry: dict,
) -> tuple[list[str], list[dict]]:
    """Bounded revise loop for the just-drafted chapter, then a re-check.
    Returns the post-revise (reasons, still-open chapter findings)."""
    for finding in chapter_findings[:MAX_REVISE_FINDINGS]:
        result = _revise_finding(db, cfg, chapter, finding)
        entry["revisions"].append(
            {
                "finding_id": finding["id"],
                "committed": bool(result.get("committed")),
                "reasons": result.get("reasons", []),
            }
        )
    try:
        # Raises on LLM failure and leaves stored findings untouched — a
        # re-check that could not run can never clear a blocker.
        run_constory_check(db, cfg, work_id=work_id)
    except Exception as exc:
        return [f"post-revise continuity re-check failed: {exc}"], []
    findings = _open_blocking_findings(db, work_id)
    reasons = []
    if findings:
        reasons.append(f"{len(findings)} open critical/high finding(s) after bounded revision")
    return reasons, _chapter_findings(findings, int(chapter["seq"]))


def run_autonomy(db: OrivellumDB, cfg: Any, *, run_id: str, work_id: str) -> dict:
    """Background entry point.  The run row is the claim; every exit path
    finishes it — done, halted, stopped, or error."""
    try:
        result = _run(db, cfg, run_id=run_id, work_id=work_id)
    except Exception as exc:
        logger.exception("Autonomy run failed (work=%s)", work_id)
        db.finish_autonomy_run(run_id, status="error", stop_reason=str(exc))
        raise
    db.finish_autonomy_run(
        run_id,
        status=result["status"],
        consumed=result["consumed"],
        report=result["report"],
        stop_reason=result["stop_reason"],
    )
    return result


def _run(db: OrivellumDB, cfg: Any, *, run_id: str, work_id: str) -> dict:  # noqa: C901
    run = db.get_autonomy_run(run_id)
    budget = {**DEFAULT_BUDGET, **(run.get("budget") if run else {})}
    started = time.monotonic()
    baseline = _llm_call_baseline(db)
    chapters: list[dict] = []
    queued: list[str] = []
    status, stop_reason = "done", "complete"

    def finishup() -> dict:
        consumed = {
            "chapters": len([c for c in chapters if c.get("drafted")]),
            "minutes": round((time.monotonic() - started) / 60, 2),
            "tokens": _tokens_since(db, baseline),
        }
        report = {
            "work_id": work_id,
            "budget": budget,
            "chapters": chapters,
            "queued_review_items": queued,
            "generated_at": _now(),
        }
        return {
            "status": status,
            "stop_reason": stop_reason,
            "consumed": consumed,
            "report": report,
        }

    chapters_done = 0
    halted_ids: set[str] = set()
    while True:
        if not enabled(db):
            status, stop_reason = "stopped", "autonomy disabled (kill switch)"
            break
        over = _budget_stop(
            budget,
            started=started,
            tokens_used=_tokens_since(db, baseline),
            chapters_done=chapters_done,
        )
        if over:
            status, stop_reason = "done", over
            break

        chapter = _next_chapter(db, work_id, exclude=halted_ids)
        if chapter is None:
            pending = _unsigned_gates(db, work_id)
            if pending:
                # NEVER auto-sign: queue the signature requirement and halt.
                sid = _queue_halt(
                    db,
                    run_id=run_id,
                    work_id=work_id,
                    chapter=None,
                    title="author signature required",
                    reasons=[f"unsigned gate {k} — signatures stay human" for k in pending],
                )
                queued.append(sid)
                status, stop_reason = "halted", "signature_required: " + ", ".join(pending)
            else:
                status, stop_reason = "done", "no chapters need drafting"
            break

        entry: dict = {
            "chapter_id": chapter["id"],
            "seq": chapter["seq"],
            "drafted": False,
            "revisions": [],
            "halted": False,
            "reasons": [],
        }
        chapters.append(entry)

        ok, detail = _draft_chapter(db, cfg, work_id, chapter)
        if not ok:
            entry["halted"], entry["reasons"] = True, [detail]
            halted_ids.add(chapter["id"])
            sid = _queue_halt(
                db,
                run_id=run_id,
                work_id=work_id,
                chapter=chapter,
                title="drafting halted",
                reasons=[detail],
            )
            queued.append(sid)
            if budget["halt_policy"] == "stop":
                status, stop_reason = "halted", detail
                break
            chapters_done += 1
            continue
        entry["drafted"] = True

        reasons, chapter_findings, entry["check"] = _check_chapter(
            db, cfg, work_id, int(chapter["seq"])
        )
        if reasons and chapter_findings:
            reasons, chapter_findings = _try_revise(
                db, cfg, work_id, chapter, chapter_findings, entry
            )
        if reasons:
            entry["halted"], entry["reasons"] = True, reasons
            halted_ids.add(chapter["id"])
            finding_ids = [f["id"] for f in chapter_findings]
            sid = _queue_halt(
                db,
                run_id=run_id,
                work_id=work_id,
                chapter=chapter,
                title="blocking problem after draft",
                reasons=reasons,
                finding_ids=finding_ids,
            )
            queued.append(sid)
            if budget["halt_policy"] == "stop":
                status, stop_reason = "halted", "; ".join(reasons)[:500]
                break
        chapters_done += 1

    return finishup()


# ── Nightshift pass (opt-in) ─────────────────────────────────────────────────

MAX_NIGHTSHIFT_WORKS = 3


def run_nightshift_pass(db: OrivellumDB, cfg: Any, report: list[str]) -> None:
    """Opt-in nightly pass: runs the autonomy loop for every Work whose meta
    carries ``autonomy_optin: true``.  Gated by BOTH global settings so it is
    off unless deliberately enabled twice."""
    if not enabled(db):
        report.append("Autonomy: disabled (autonomy_enabled=false)")
        return
    if db.get_setting("autonomy_nightshift_enabled", "false").lower() != "true":
        report.append("Autonomy: nightly runs off (autonomy_nightshift_enabled=false)")
        return
    with db._lock:
        rows = db._conn.execute(
            """SELECT w.id, w.title, w.meta FROM works w
               JOIN objects o ON o.id = w.id
               WHERE o.lifecycle != 'deleted'"""
        ).fetchall()
    optins = []
    for r in rows:
        try:
            meta = json.loads(r["meta"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        if meta.get("autonomy_optin") is True:
            optins.append({"id": r["id"], "title": r["title"]})
    if not optins:
        report.append("Autonomy: no Works opted in")
        return
    for work in optins[:MAX_NIGHTSHIFT_WORKS]:
        try:
            rid = db.create_autonomy_run(work["id"], budget_from_settings(db))
        except RuntimeError:
            report.append(f"Autonomy: {work['title']} — skipped (run already in flight)")
            continue
        try:
            result = run_autonomy(db, cfg, run_id=rid, work_id=work["id"])
            report.append(
                f"Autonomy: {work['title']} — {result['status']} "
                f"({result['stop_reason']}; {result['consumed']['chapters']} chapter(s), "
                f"{len(result['report']['queued_review_items'])} queued)"
            )
        except Exception as exc:
            report.append(f"Autonomy: {work['title']} — error: {exc}")
