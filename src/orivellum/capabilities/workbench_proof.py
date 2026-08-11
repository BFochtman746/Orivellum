"""Workbench ↔ Orivellum Runner bridge — six-gate workbook proving.

The Project Workbench used to accept an xlsx version when the workbook merely
*loaded* in openpyxl. The repo already owns a far stronger standard: the
Orivellum Runner's proof gates (recalculate every formula with the pure-Python
``formulas`` engine, values must match, no saved error cells, canonical OOXML
child order, surgery containment, clean double-load). This module runs that
exact gate suite in-process against a workbench output file.

Semantics mirror ``runner/jobs/xlsx.py::prove_unit``:

- Repairs first (OOXML reorder + refreshing stale/missing cached values from
  the recalculation), applied to a hidden candidate copy — the original is
  untouched until every gate passes, then atomically promoted. LLM-built
  workbooks almost never carry cached values, so the repair step is what makes
  an archived workbook open with real numbers instead of blanks.
- HONESTY RULE: an unavailable engine or an uncomputable function yields
  verdict ``unverified`` — reported as such, never as clean.

The runner stays a standalone harness (own requirements, own CLI); we import
only its two pure modules (engine + surgery: stdlib/openpyxl/formulas, no
runner config or store) via a lazy path insert.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import sys
import zipfile
from collections import defaultdict
from typing import Any

logger = logging.getLogger("orivellum.workbench.proof")

GATE_NAMES = (
    "G1_recalc_covers_all",
    "G2_values_match",
    "G3_no_error_cells",
    "G4_ooxml_order",
    "G5_surgery_contained",
    "G6_loads_clean",
)

_SHEET_XML = re.compile(r"xl/worksheets/sheet\d+\.xml$")


def _runner_dir() -> pathlib.Path:
    override = os.environ.get("ORIVELLUM_RUNNER_DIR")
    if override:
        return pathlib.Path(override)
    # src/orivellum/capabilities/workbench_proof.py → repo root / orivellum-runner
    return pathlib.Path(__file__).resolve().parents[3] / "orivellum-runner"


_runner_cache: tuple | None = None


def _load_runner_modules():
    """Return (engine, surgery) or (None, None) when the harness is missing.

    Loads the two pure modules by absolute file path under private module
    names — never touches ``sys.path``, so an unrelated ``runner`` package
    elsewhere in the process can neither be shadowed nor picked up by
    mistake. Never raises — an absent harness must degrade to
    ``unverified``, not break a build.
    """
    global _runner_cache
    if _runner_cache is not None:
        return _runner_cache
    try:
        import importlib.util

        jobs = _runner_dir() / "runner" / "jobs"

        def _load(alias: str, path: pathlib.Path):
            spec = importlib.util.spec_from_file_location(alias, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"cannot load {path}")
            mod = importlib.util.module_from_spec(spec)
            sys.modules[alias] = mod
            spec.loader.exec_module(mod)
            return mod

        # surgery first — engine's structural helpers reference it lazily,
        # but the functions we call (recalculate/compare/ERRVALS) do not.
        surgery = _load("_orivellum_runner_xlsx_surgery", jobs / "xlsx_surgery.py")
        engine = _load("_orivellum_runner_xlsx_engine", jobs / "xlsx_engine.py")
        _runner_cache = (engine, surgery)
    except Exception:  # noqa: BLE001
        logger.warning("Orivellum Runner proof modules unavailable", exc_info=True)
        _runner_cache = (None, None)
    return _runner_cache


def _unverified(error: str) -> dict:
    return {"verdict": "unverified", "error": error[:400], "gates": {}, "problems": [error[:400]]}


def prove_workbook(path: pathlib.Path, promote: bool = True) -> dict[str, Any]:
    """Repair → recalculate → six gates → atomic promotion, on ONE workbook.

    Returns a JSON-safe dict:
      {verdict: proven|failed|unverified, gates: {G1..G6: bool},
       failed_gates: [...], problems: [...], repairs: {reordered_parts,
       refreshed_cells}, recalc: {formulas_checked, agreed}}

    On ``proven`` with ``promote=True`` the file at *path* is atomically
    replaced by the repaired, fully-gated candidate. With ``promote=False``
    (imported versions stay verbatim) the original is never touched, and the
    verdict is honest about WHICH bytes passed: ``proven`` only when no
    repairs were needed (candidate == original); ``provable`` when the gates
    pass only after repairs the file itself never received.
    """
    engine, surgery = _load_runner_modules()
    if engine is None or surgery is None:
        return _unverified("proving harness unavailable (orivellum-runner not found)")

    recalc = engine.recalculate(path)
    if not recalc["available"]:
        return _unverified(recalc["error"] or "recalculation unavailable")

    edits, reordered, refreshed = _plan_repairs(engine, surgery, path, recalc)
    return _run_gates(engine, surgery, path, edits, reordered, refreshed, promote)


def _plan_repairs(engine, surgery, path, recalc):
    """R1 (OOXML reorder) + R2 (refresh stale/missing caches) — the same
    repair plan as the runner's prove_unit. Returns (edits, reordered,
    refreshed_count)."""
    cmp0 = engine.compare(path, recalc)
    part_of = surgery.sheet_part_names(path)
    edits: dict[str, str] = {}
    reordered: list[str] = []
    refreshed = 0
    with zipfile.ZipFile(path) as z:
        raw = {
            n: z.read(n).decode("utf-8", errors="replace")
            for n in z.namelist()
            if _SHEET_XML.match(n)
        }
    for part, xml in raw.items():
        if surgery.sheet_order_violations(xml):
            fixed = surgery.reorder_sheet_xml(xml)
            if fixed != xml:  # reorder REFUSES on unknown content — G4 then fails
                raw[part] = fixed
                edits[part] = fixed
                reordered.append(part)
    by_sheet: dict[str, dict] = defaultdict(dict)
    for m in cmp0["mismatches"]:
        computed = m["computed_raw"]
        if isinstance(computed, str) and computed.startswith("#"):
            continue  # a formula that genuinely errors is not repairable
        by_sheet[m["sheet"]][m["cell"]] = computed
    for sheet, updates in by_sheet.items():
        part = part_of.get(sheet)
        if not part or part not in raw:
            continue
        new_xml, changed = surgery.refresh_cached(raw[part], updates)
        if changed:
            raw[part] = new_xml
            edits[part] = new_xml
            refreshed += len(changed)
    return edits, reordered, refreshed


def _scan_error_cells(engine, candidate) -> list[str]:
    """G3: every saved cell value that is an Excel error literal."""
    from openpyxl import load_workbook

    err_cells = []
    wv = load_workbook(candidate, data_only=True, read_only=True)
    try:
        for name in wv.sheetnames:
            for row in wv[name].iter_rows():
                for c in row:
                    if isinstance(c.value, str) and c.value.strip() in engine.ERRVALS:
                        err_cells.append(f"{name}!{c.coordinate}")
    finally:
        wv.close()
    return err_cells


def _scan_sheet_order(surgery, candidate) -> list[str]:
    """G4: worksheet parts whose OOXML child order is non-canonical."""
    order_bad = []
    with zipfile.ZipFile(candidate) as z:
        for n in [x for x in z.namelist() if _SHEET_XML.match(x)]:
            if surgery.sheet_order_violations(z.read(n).decode("utf-8", errors="replace")):
                order_bad.append(n)
    return order_bad


def _passing_verdict(path, candidate, promote: bool, repaired: bool, problems: list) -> str:
    """All six gates passed — decide what that honestly certifies."""
    if promote:
        candidate.replace(path)  # atomic promotion — gates first, always
        return "proven"
    if not repaired:
        # candidate is byte-equivalent to the original — the file itself
        # passed every gate
        return "proven"
    # gates pass only after repairs the file never received; 'proven' would
    # certify bytes that were never archived
    problems.append(
        "passes all gates only after cache/order repairs; the file itself "
        "was kept verbatim and would show stale or blank values in Excel"
    )
    return "provable"


def _run_gates(engine, surgery, path, edits, reordered, refreshed, promote):
    """Apply the repair plan to a hidden candidate, run all six gates against
    it, and promote atomically only when every gate passes (and promotion is
    allowed). The original file is never touched on any other outcome."""
    import uuid

    from openpyxl import load_workbook

    candidate = path.with_name(f".candidate_{uuid.uuid4().hex[:8]}_{path.name}")
    gates: dict[str, bool] = {}
    problems: list[str] = []
    result: dict[str, Any] = {
        "verdict": "failed",
        "gates": gates,
        "problems": problems,
        "repairs": {"reordered_parts": len(reordered), "refreshed_cells": refreshed},
    }
    try:
        touched = surgery.apply(path, candidate, edits)

        recalc2 = engine.recalculate(candidate)
        if not recalc2["available"]:
            return _unverified(recalc2["error"] or "engine failed on repaired candidate")
        cmp2 = engine.compare(candidate, recalc2)
        gates["G1_recalc_covers_all"] = not cmp2["uncovered"]
        gates["G2_values_match"] = not cmp2["mismatches"]
        if cmp2["uncovered"]:
            problems.append(f"engine could not cover: {', '.join(cmp2['uncovered'][:5])}")
        for m in cmp2["mismatches"][:5]:
            problems.append(f"{m['ref']}: cached={m['cached']} computed={m['computed']}")

        err_cells = _scan_error_cells(engine, candidate)
        gates["G3_no_error_cells"] = not err_cells
        if err_cells:
            problems.append(f"error cells: {', '.join(err_cells[:5])}")

        order_bad = _scan_sheet_order(surgery, candidate)
        gates["G4_ooxml_order"] = not order_bad
        if order_bad:
            problems.append(f"OOXML order violated: {', '.join(order_bad[:3])}")

        diff = surgery.parts_diff(path, candidate)
        gates["G5_surgery_contained"] = (
            not diff["added"] and not diff["removed"] and set(diff["changed"]) <= set(touched)
        )
        try:
            load_workbook(candidate, data_only=False).close()
            load_workbook(candidate, data_only=True).close()
            gates["G6_loads_clean"] = True
        except Exception as exc:  # noqa: BLE001
            gates["G6_loads_clean"] = False
            problems.append(f"candidate fails to load: {str(exc)[:200]}")

        result["recalc"] = {
            "formulas_checked": len(cmp2.get("formula_cells", [])),
            "agreed": cmp2.get("agreed", 0),
        }
        if all(gates.values()):
            result["verdict"] = _passing_verdict(path, candidate, promote, bool(edits), problems)
        else:
            result["failed_gates"] = sorted(k for k, v in gates.items() if not v)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Proof run crashed for %s", path)
        return _unverified(f"proof run crashed: {type(exc).__name__}: {exc}")
    finally:
        candidate.unlink(missing_ok=True)  # no gate suite, no candidate left
    return result


def prove_outputs(
    out_dir: pathlib.Path, workbooks: list[pathlib.Path], promote: bool = True
) -> dict[str, Any]:
    """Prove every workbook of a version; aggregate to one verdict.

    proven     — every workbook's ACTUAL bytes passed all six gates
    provable   — gates pass, but only after repairs a verbatim file never got
    failed     — at least one workbook failed a gate
    unverified — none failed, but at least one could not be recalculated
    """
    per_file: dict[str, dict] = {}
    for p in workbooks:
        per_file[str(p.relative_to(out_dir))] = prove_workbook(p, promote=promote)
    verdicts = {r["verdict"] for r in per_file.values()}
    for worst in ("failed", "unverified", "provable"):
        if worst in verdicts:
            return {"verdict": worst, "workbooks": per_file}
    return {"verdict": "proven", "workbooks": per_file}
