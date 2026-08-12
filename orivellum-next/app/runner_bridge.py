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

from .db import DB, now
from .nextaction import ActionError

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


def enqueue(db: DB, action_id: str, chain: Chain | None = None) -> dict:
    """Hand one action to the runner, or explain why it waits."""
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
    # Hand-off point. In the real system this calls the orivellum-runner harness:
    #   runner.enqueue(job=act["prompt"], unit_ref=act["anchor_ref"], budget=...)
    return {"queued": True, "auto": True, "why": act["auto_reason"],
            "unit": {"prompt": act["prompt"], "anchor_ref": act["anchor_ref"],
                     "cost_units": act["cost_units"],
                     "cost_minutes": act["cost_minutes"]}}


def finish(db: DB, action_id: str, ok: bool, detail: str = "") -> None:
    db.conn.execute("UPDATE next_action SET state=? WHERE id=?",
                    ("done" if ok else "failed", action_id))
    db.conn.commit()
    db.event("done" if ok else "failed", action_id=action_id, detail=detail)


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
