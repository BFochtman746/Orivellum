"""Compliance — AI disclosure sheet (B15) and the B16 final assembly gate.

Disclosure maps ``artifact_provenance`` onto KDP's definitions:

  · ai_generated — the tool created it; DISCLOSED, even after heavy editing;
  · ai_assisted — the tool helped a human-created artifact; NOT disclosed;
  · human — human-created; NOT disclosed;
  · a chapter with NO provenance row is UNDECLARED and reported loudly —
    absence of a record is never treated as "human".

The assembly gate is ONE deterministic function. Every check is computed
from stored state; nothing accepts an override parameter, and a missing
record fails the check it belongs to. If the gate says no, the answer is no.
"""

from __future__ import annotations

from typing import Any

from . import atelier, press

__all__ = ["disclosure_sheet", "assembly_gate"]

# KDP asks the question per content type; chapters and epigraphs are text.
_KIND_TO_KDP = {
    "book_chapter": "text",
    "chapter_revision": "text",
    "loom_chapter_revision": "text",
    "epigraph": "text",
    "cover": "images",
    "cover_image": "images",
}

_FAILING_VERDICTS = ("confirmed_drift", "out_of_envelope", "structural_violations")
_GATE_KEYS = ("gate.d13", "gate.d14", "gate.d15", "gate.d16", "gate.d17")


def disclosure_sheet(db: Any, work_id: str) -> dict:
    """Build the KDP disclosure sheet for a Work's manuscript."""
    work = db.get_work(work_id)
    if not work:
        raise KeyError(f"Work '{work_id}' not found.")
    chapters = db._conn.execute(
        "SELECT id, seq, title FROM book_chapters WHERE work_id=? ORDER BY seq",
        (work_id,),
    ).fetchall()
    items: list[dict] = []
    undeclared: list[int] = []
    disclosed = 0
    for ch in chapters:
        prov = db.get_provenance(ch["id"], "book_chapter")
        if prov is None:
            undeclared.append(ch["seq"])
            items.append(
                {
                    "artifact_kind": "book_chapter",
                    "chapter_seq": ch["seq"],
                    "title": ch["title"] or "",
                    "origin": "undeclared",
                    "kdp_category": "text",
                    "disclose": None,
                }
            )
            continue
        disclose = prov["origin"] == "ai_generated"
        disclosed += 1 if disclose else 0
        items.append(
            {
                "artifact_kind": "book_chapter",
                "chapter_seq": ch["seq"],
                "title": ch["title"] or "",
                "origin": prov["origin"],
                "kdp_category": _KIND_TO_KDP["book_chapter"],
                "disclose": disclose,
                "declared_by": prov.get("declared_by", ""),
                "llm_call_count": len(prov.get("llm_call_ids", [])),
            }
        )
    kdp_answer = {
        "used_ai_tools": disclosed > 0,
        "text": "ai_generated"
        if any(i.get("disclose") and i["kdp_category"] == "text" for i in items)
        else "none_disclosable",
        "images": "none_disclosable",
        "translations": "none_disclosable",
    }
    return {
        "work_id": work_id,
        "work_title": work["title"],
        "complete": not undeclared,
        "undeclared_chapters": undeclared,
        "items": items,
        "kdp_answer": kdp_answer,
        "note": (
            "KDP definition applied: AI-GENERATED content is disclosed even "
            "after substantial editing; AI-ASSISTED content is not."
        ),
    }


def _check_genesis(db: Any) -> Any:
    from ..genesis import seal as genesis_seal  # noqa: PLC0415

    return genesis_seal.verify_ledger


class _Gate:
    """Accumulator for gate checks — every check records a bool, every
    failure records a human-readable reason."""

    def __init__(self) -> None:
        self.checks: dict[str, bool] = {}
        self.reasons: list[str] = []

    def record(self, key: str, ok: bool, reason: str) -> None:
        self.checks[key] = bool(ok)
        if not ok:
            self.reasons.append(reason)


def _gate_genesis(g: _Gate, db: Any, work_id: str) -> None:
    """1 · GENESIS origination ledger verifies clean."""
    grow = db._conn.execute("SELECT id FROM genesis_books WHERE work_id=?", (work_id,)).fetchone()
    if grow is None:
        g.record("genesis_ledger", False, "No GENESIS origination package for this Work.")
        return
    ok, msg = _check_genesis(db)(db._conn, grow["id"])
    g.record("genesis_ledger", ok, f"GENESIS ledger: {msg}")


def _gate_findings(g: _Gate, db: Any, work_id: str) -> None:
    """2+3 · Zero open critical/high findings; zero HISTORICAL contradictions
    (a subset by rule, but checked independently so a rule regression in the
    severity computation cannot hide one)."""
    open_findings = db.list_narrative_findings(work_id, disposition="open")
    open_bad = [f for f in open_findings if f["severity"] in ("critical", "high")]
    g.record(
        "no_open_critical_high_findings",
        not open_bad,
        f"{len(open_bad)} open critical/high narrative finding(s).",
    )
    open_hist = [f for f in open_findings if f.get("canon_class") == "HISTORICAL"]
    g.record(
        "no_open_historical_contradictions",
        not open_hist,
        f"{len(open_hist)} unresolved HISTORICAL contradiction(s).",
    )


def _gate_assay(g: _Gate, db: Any, work_id: str) -> None:
    """4 · D13–D17 all ran clean AND carry an author 'go' signature."""
    for key in _GATE_KEYS:
        inst = db.get_assay_instrument(key)
        if inst is None:
            g.record(f"{key}_clean_and_signed", False, f"{key}: instrument not registered.")
            continue
        # assay_run.instrument_id stores the instrument's UUID, not its key.
        runs = db.list_assay_runs(work_id, instrument_id=inst["id"], limit=1)
        run = runs[0] if runs else None
        run_ok = (
            run is not None
            and run.get("status") == "done"
            and run.get("verdict") not in _FAILING_VERDICTS
        )
        sig = db.latest_assay_signature(work_id, key)
        sig_ok = bool(sig and sig["decision"] == "go")
        reason = (
            f"{key}: no clean completed run." if not run_ok else f"{key}: no author 'go' signature."
        )
        g.record(f"{key}_clean_and_signed", run_ok and sig_ok, reason)


def _gate_press(g: _Gate, press_slug: str) -> None:
    """5 · PRESS: style locked, rendered outputs, EPUBCheck+Ace clean."""
    if not press_slug:
        g.record("press_render", False, "No PRESS book supplied — nothing rendered.")
        g.record("epub_validation_clean", False, "EPUB validation impossible without a PRESS book.")
        return
    try:
        pv = press.verify(press_slug)
    except KeyError as exc:
        g.record("press_render", False, str(exc))
        g.record("epub_validation_clean", False, "EPUB validation impossible: book missing.")
        g.record("press_ledger", False, "PRESS ledger unverifiable: book missing.")
        return
    pb_pages = pv.get("actual_pages") or 0
    reason = (
        "PRESS style is not locked."
        if not pv["checks"]["style_locked"]
        else "PRESS book has no rendered outputs (actual_pages=0)."
    )
    g.record("press_render", pv["checks"]["style_locked"] and pb_pages > 0, reason)
    vs = press.validation_status(press_slug)
    g.record(
        "epub_validation_clean",
        vs["clean"],
        f"EPUB validation not clean (epubcheck={vs['epubcheck']}, ace={vs['ace']}).",
    )
    lok, lmsg = press.verify_ledger(press_slug)
    g.record("press_ledger", lok, f"PRESS ledger: {lmsg}")


def _gate_atelier(g: _Gate, press_slug: str, atelier_slug: str) -> None:
    """6 · ATELIER: design verified against the RENDERED page count."""
    if not atelier_slug:
        g.record("cover_geometry_verified", False, "No ATELIER book supplied — cover unverified.")
        return
    try:
        av = atelier.verify_design(atelier_slug)
        ab = atelier.get_book(atelier_slug) or {}
    except KeyError as exc:
        g.record("cover_geometry_verified", False, str(exc))
        g.record("atelier_ledger", False, "ATELIER ledger unverifiable: book missing.")
        return
    press_pages = -1
    if press_slug:
        try:
            press_pages = press.verify(press_slug).get("actual_pages") or -1
        except KeyError:
            press_pages = -1
    pages_match = bool(ab.get("actual_pages")) and ab["actual_pages"] == press_pages
    if not av["passed"]:
        bad = [k for k, v in av["checks"].items() if not v]
        reason = f"ATELIER design verify failed: {', '.join(bad)}."
    else:
        reason = "ATELIER geometry is not based on the rendered page count."
    g.record("cover_geometry_verified", av["passed"] and pages_match, reason)
    lok, lmsg = atelier.verify_ledger(atelier_slug)
    g.record("atelier_ledger", lok, f"ATELIER ledger: {lmsg}")


def assembly_gate(db: Any, work_id: str, press_slug: str = "", atelier_slug: str = "") -> dict:
    """B16 — the single deterministic release decision.

    Returns ``{"passed": bool, "checks": {...}, "reasons": [...]}``.
    There is no override path by design: fixing the input is the only way
    to change the answer.
    """
    work = db.get_work(work_id)
    if not work:
        raise KeyError(f"Work '{work_id}' not found.")

    g = _Gate()
    _gate_genesis(g, db, work_id)
    _gate_findings(g, db, work_id)
    _gate_assay(g, db, work_id)
    _gate_press(g, press_slug)
    _gate_atelier(g, press_slug, atelier_slug)

    # 7 · Disclosure sheet complete — every chapter's provenance declared.
    sheet = disclosure_sheet(db, work_id)
    g.record(
        "provenance_complete",
        sheet["complete"],
        f"Chapters with undeclared provenance: {sheet['undeclared_chapters']}.",
    )

    return {
        "passed": all(g.checks.values()),
        "checks": g.checks,
        "reasons": g.reasons,
        "disclosure": sheet["kdp_answer"],
    }
