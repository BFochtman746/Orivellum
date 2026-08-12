"""The runner bridge — the part that actually removes "continue".

The chips are a convenience: they turn "what do I ask next" into one tap. They
do not remove the tap. What removes the tap is this: an action the system judged
CHEAP, REVERSIBLE, UNBLOCKED and INSIDE BUDGET is handed to the existing runner
as a unit, and the loop continues in code.

Everything else queues and waits for you. The distinction is not a preference,
it is the safety property:

    auto-run  = reversible, cheap, unblocked, inside budget, policy enabled
    queue     = everything else, with the reason it did not auto-run attached

Nothing irreversible auto-runs. No setting unlocks that — `force` cannot either.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

from .db import DB, now
from .nextaction import ActionError

# ── optional orivellum-runner hand-off ───────────────────────────────────
# The runner lives in a sibling package. We try to import its store module;
# if it is absent (standalone dev, tests without the runner installed) we
# degrade gracefully to a stub that still records everything in next.db.

_RUNNER_ROOT = _Path(__file__).resolve().parent.parent.parent / "orivellum-runner"
if str(_RUNNER_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_RUNNER_ROOT))

try:
    from runner import store as _runner_store          # type: ignore[import-not-found]
    from runner import harness as _runner_harness      # type: ignore[import-not-found]
    from runner.jobs import next_action as _next_action_job  # type: ignore[import-not-found]
    _HAS_RUNNER = True
except Exception:
    _runner_store = None       # type: ignore[assignment]
    _runner_harness = None     # type: ignore[assignment]
    _next_action_job = None    # type: ignore[assignment]
    _HAS_RUNNER = False


def _runner_dispatch(action: dict, *, executor=None) -> dict:
    """Dispatch one action to the runner and synchronously wait for the outcome.

    Returns a descriptor whose ``ok`` field is the ONLY authority on whether
    the action actually executed successfully.  Callers must never mark an
    action 'done' until they see ``ok=True`` here.

    Resolution order
    ────────────────
    1. ``executor`` callback — trusted callable used by tests and dry-run callers.
       The caller controls the result; ``ok`` defaults to ``True`` if not set,
       so a test that returns ``{"status": "done"}`` is treated as success.
    2. orivellum-runner harness — the real path.  Creates a run in the runner
       store, queues the unit, runs the harness *synchronously*, and returns
       ``ok=True`` only when the harness reports ``status='done'``.
    3. Stub — runner package is not installed.  Always returns ``ok=False``
       because we cannot verify execution happened.  The action is recorded in
       next.db but must NOT be marked done.
    """
    if executor is not None:
        result = executor(action)
        d = result if isinstance(result, dict) else {}
        # Require an EXACT boolean True as the success signal.  bool() is
        # intentionally avoided because it would coerce truthy strings like
        # "false" or integers like 1 into True — defeating the contract.
        # If the executor does not set 'ok', fall back to status="done".
        if d.get("ok") is True:
            ok = True
        elif "status" in d:
            ok = d["status"] == "done"
        else:
            ok = False
        # Construct the descriptor so canonical 'ok' and 'source' always WIN
        # over whatever the executor returned — even if its dict contained
        # its own 'ok' key with a raw/string value.
        return {**d, "source": "executor", "ok": ok}

    if _HAS_RUNNER:
        try:
            run_id = _runner_store.start_run(
                job="next_action",
                target=action.get("anchor_ref", ""),
                label=action.get("label", ""),
                plan={
                    "prompt": action["prompt"],
                    "anchor_ref": action.get("anchor_ref", ""),
                    "cost_units": action.get("cost_units"),
                    "cost_minutes": action.get("cost_minutes"),
                },
            )
            _runner_store.add_units(run_id, [{
                "kind": "next",
                "ref": action.get("anchor_ref", action.get("id", "")),
                "payload": {
                    "action_id": action["id"],
                    "prompt": action["prompt"],
                    "anchor_ref": action.get("anchor_ref", ""),
                },
            }])
            # Execute synchronously.  The harness retries transient unit
            # failures internally; we read unit counts after completion.
            res = _runner_harness.execute(
                run_id,
                _next_action_job,
                _next_action_job.unit_worker,
                _next_action_job.final_pass,
            )
            # A run can reach status='done' (all units dequeued) even when
            # units FAILED — the harness marks exhausted-retry units as
            # 'failed', not 'queued'.  Derive ok from unit outcomes, not the
            # run-level status alone.
            counts = _runner_store.unit_counts(run_id)
            failed_units = counts.get("failed", 0)
            ok = res.get("status") == "done" and failed_units == 0
            return {
                "source": "runner",
                "ok": ok,
                "run_id": run_id,
                "status": res.get("status"),
                "stop_reason": res.get("stop_reason"),
                "failed_units": failed_units,
                "totals": res.get("totals"),
            }
        except Exception as exc:
            # Runner present but threw — surface the error, never swallow it.
            return {
                "source": "runner",
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}"[:400],
            }

    # Stub: runner package absent.  Explicitly failed so callers never mark done.
    return {
        "source": "stub",
        "ok": False,
        "error": "runner package not installed; action recorded in next.db only",
    }

# A session budget, so an autonomous chain cannot run away. The runner has its
# own per-run budgets; this one caps the CHAIN of self-continued steps.
DEFAULT_CHAIN_BUDGET = {"max_steps": 8, "max_minutes": 45, "max_units": 2000}


class ChainExhausted(Exception):
    pass


class Chain:
    """Tracks one unattended continuation chain and stops it honestly."""

    def __init__(self, thread_id: str, budget: dict | None = None):
        self.thread_id = thread_id
        self.budget = {**DEFAULT_CHAIN_BUDGET, **(budget or {})}
        self.steps = 0
        self.minutes = 0
        self.units = 0
        self.log: list[dict] = []

    def admit(self, action: dict) -> tuple[bool, str]:
        if self.steps + 1 > self.budget["max_steps"]:
            return False, f"chain budget: {self.budget['max_steps']} steps reached"
        m = self.minutes + (action.get("cost_minutes") or 0)
        u = self.units + (action.get("cost_units") or 0)
        if m > self.budget["max_minutes"]:
            return False, f"chain budget: {self.budget['max_minutes']} minutes reached"
        if u > self.budget["max_units"]:
            return False, f"chain budget: {self.budget['max_units']} units reached"
        return True, "within chain budget"

    def charge(self, action: dict) -> None:
        self.steps += 1
        self.minutes += action.get("cost_minutes") or 0
        self.units += action.get("cost_units") or 0
        self.log.append({"action": action["id"], "label": action["label"],
                         "at": now()})

    def report(self) -> dict:
        """A chain report leads with what it could NOT do — same as every other
        report in this platform."""
        return {
            "thread": self.thread_id,
            "steps_run": self.steps,
            "minutes_spent": self.minutes,
            "units_spent": self.units,
            "budget": self.budget,
            "ran": self.log,
        }


def enqueue(db: DB, action_id: str, chain: Chain | None = None,
            executor=None) -> dict:
    """Hand one action to the runner, or explain why it waits.

    ``executor`` is an optional callable ``(action_dict) -> dict`` used by
    tests and dry-run callers in place of the real runner.  Pass ``None``
    (the default) to use the orivellum-runner store when available.
    """
    a = db.q1("SELECT * FROM next_action WHERE id=?", (action_id,))
    if not a:
        raise ActionError(f"action {action_id} not found")
    act = dict(a)

    if act["state"] == "expired":
        return {"queued": False, "auto": False,
                "why": "expired when a newer answer arrived"}

    if not act["auto_runnable"]:
        db.conn.execute("UPDATE next_action SET state='queued' WHERE id=?", (action_id,))
        db.conn.commit()
        db.event("queued", action_id=action_id, set_id=act["set_id"], kind=act["kind"],
                 recommended=act["recommended"],
                 detail=f"waits for you: {act['auto_reason']}")
        return {"queued": True, "auto": False, "why": act["auto_reason"]}

    if chain is not None:
        ok, why = chain.admit(act)
        if not ok:
            db.conn.execute("UPDATE next_action SET state='queued' WHERE id=?", (action_id,))
            db.conn.commit()
            db.event("queued", action_id=action_id, set_id=act["set_id"],
                     kind=act["kind"], detail=why)
            raise ChainExhausted(why)
        chain.charge(act)

    s = db.q1("SELECT thread_id FROM next_action_set WHERE id=?", (act["set_id"],))
    db.write(
        f"next:{s['thread_id']}", "action.autoqueued",
        "UPDATE next_action SET state='running' WHERE id=?",
        (action_id,),
        {"action": action_id, "label": act["label"], "reason": act["auto_reason"]},
    )
    db.event("queued", action_id=action_id, set_id=act["set_id"], kind=act["kind"],
             recommended=act["recommended"], detail="auto: " + act["auto_reason"])
    # Hand off to the orivellum-runner queue (or executor / stub fallback).
    #
    # Cost reconciliation: each action's cost_units / cost_minutes are forwarded
    # to the runner as plan metadata.  The runner harness enforces its own CFG
    # limits (max_units, max_minutes) as a per-run hard safety net.  The Chain
    # budget (bridge level) caps the total session across all steps.  This
    # two-tier model is intentional — the runner stops runaway units; the Chain
    # stops runaway sessions.
    unit = _runner_dispatch(act, executor=executor)

    # Gate state transition on what the runner actually reported.
    # This is the SINGLE place finish() is called for auto-runnable actions —
    # both the API (h_next_enqueue) and run_chain() reach here, so neither
    # path can leave an action stranded in 'running'.
    ok = unit.get("ok", False)
    detail = unit.get("error") or ("executed successfully" if ok else "dispatch failed")
    finish(db, action_id, ok=ok, detail=detail)

    return {"queued": True, "auto": True, "why": act["auto_reason"],
            "final_state": "done" if ok else "failed", "unit": unit}


def finish(db: DB, action_id: str, ok: bool, detail: str = "") -> None:
    db.conn.execute("UPDATE next_action SET state=? WHERE id=?",
                    ("done" if ok else "failed", action_id))
    db.conn.commit()
    db.event("done" if ok else "failed", action_id=action_id, detail=detail)


def run_chain(db: DB, thread_id: str, action_ids: list[str],
              budget: dict | None = None, executor=None) -> dict:
    """Run a chain of actions unattended until the budget is hit or all are done.

    Each ``auto_runnable`` action is dispatched to the runner (or executor)
    and immediately finished as 'done'.  Non-auto-runnable actions are queued
    with their reason and do NOT stop the chain — they simply skip the runner
    step and appear in ``pending_for_you()`` afterward.

    Returns the chain report; if budget was exhausted the report includes
    ``"stopped_at": "<reason>"`` so the caller knows why the loop ended.

    ``executor`` is the same escape hatch as in ``enqueue()``: pass a callable
    for tests or dry-run mode; ``None`` uses the real runner store.
    """
    chain = Chain(thread_id, budget)
    stopped_reason: str | None = None

    for aid in action_ids:
        try:
            # enqueue() calls finish() internally for auto-runnable actions.
            # Non-auto actions are queued with their reason and do not stop
            # the chain; they appear in pending_for_you() afterward.
            enqueue(db, aid, chain=chain, executor=executor)
        except ChainExhausted as exc:
            stopped_reason = str(exc)
            break

    report = chain.report()
    if stopped_reason:
        report["stopped_at"] = stopped_reason
    return report


def pending_for_you(db: DB, thread_id: str | None = None) -> list[dict]:
    """The queue the person actually has to look at, newest first, with the
    reason each item could not proceed on its own."""
    sql = ("SELECT a.*, s.thread_id FROM next_action a "
           "JOIN next_action_set s ON s.id=a.set_id "
           "WHERE a.state='queued'")
    params: tuple = ()
    if thread_id:
        sql += " AND s.thread_id=?"
        params = (thread_id,)
    sql += " ORDER BY a.recommended DESC, a.rowid DESC"
    return [{"id": r["id"], "label": r["label"], "kind": r["kind"],
             "recommended": bool(r["recommended"]), "waits_because": r["auto_reason"],
             "cost_minutes": r["cost_minutes"], "anchor": r["anchor"]}
            for r in db.q(sql, params)]
