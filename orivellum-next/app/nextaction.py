"""Next actions — the thing that decides what comes next.

One producer, two consumers. The same row renders as a tappable chip AND can be
picked up by the runner without a human turn. That is what actually removes
"continue" from the workflow: the loop becomes code, not conversation.

Invariants enforced here:

  1. TWO TO FOUR actions per set. Six or more rebuilds the decision paralysis
     the pattern exists to remove.
  2. EXACTLY ZERO OR ONE recommendation per set. Two recommendations is no
     recommendation.
  3. A recommendation REQUIRES a rationale and a structured anchor_ref.
     Evidence or it did not happen.
  4. When nothing earns a recommendation, the set says so in
     no_recommendation_reason rather than promoting a weak option.
  5. auto_runnable is COMPUTED from reversibility, cost, budget, blockers and
     unresolved clarification. A model may never assert it.
  6. Nothing irreversible ever auto-runs. No setting unlocks that.
  7. A new answer in the thread EXPIRES the previous set, so a stale chip
     cannot be tapped.
"""

from __future__ import annotations

from .db import DB, nid, now

KINDS = ("narrow", "widen", "act", "clarify")


class ActionError(Exception):
    pass


# ── the computed permission ───────────────────────────────────────────────

def compute_auto_runnable(action: dict, policy: dict) -> tuple[int, str]:
    """Deterministic. Never ask a model whether something is safe to run."""
    if not policy.get("auto_run_enabled", 0):
        return 0, "auto-run is off in policy"
    if action.get("kind") == "clarify":
        return 0, "a clarification needs the person"
    if not action.get("reversible"):
        return 0, "not reversible"
    if action.get("needs_clarify"):
        return 0, "unresolved clarification"
    if str(action.get("blocked_by", "")).strip():
        return 0, f"blocked by {action['blocked_by']}"
    units = action.get("cost_units") or 0
    mins = action.get("cost_minutes") or 0
    if units > int(policy.get("auto_run_max_units", 200)):
        return 0, f"{units} units over the budget of {policy['auto_run_max_units']}"
    if mins > int(policy.get("auto_run_max_minutes", 10)):
        return 0, f"{mins} min over the budget of {policy['auto_run_max_minutes']}"
    conf = action.get("confidence")
    if conf is not None and conf < float(policy.get("recommend_min_confidence", 0.55)):
        return 0, f"confidence {conf:.2f} below threshold"
    return 1, "reversible, inside budget, nothing blocking"


# ── writing a set ─────────────────────────────────────────────────────────

def offer(db: DB, thread_id: str, from_message: str, actions: list[dict],
          policy: dict, no_recommendation_reason: str = "") -> str:
    lo = int(policy.get("min_actions", 2))
    hi = int(policy.get("max_actions", 4))
    if not (lo <= len(actions) <= hi):
        raise ActionError(
            f"{len(actions)} actions; the set must hold {lo}-{hi}. "
            "More than that recreates the paralysis this removes."
        )

    recs = [a for a in actions if a.get("recommended")]
    if len(recs) > 1:
        raise ActionError(
            f"{len(recs)} recommendations in one set. Exactly one, or none with a "
            "stated reason — two recommendations is no recommendation."
        )
    if not recs and not no_recommendation_reason.strip():
        raise ActionError(
            "no recommendation and no reason given. If nothing earns it, say why."
        )
    for a in actions:
        if a.get("kind") not in KINDS:
            raise ActionError(f"kind {a.get('kind')!r} is not one of {KINDS}")
        for req in ("label", "prompt", "anchor"):
            if not str(a.get(req, "")).strip():
                raise ActionError(f"action {a.get('label', '?')!r} is missing {req!r}")
        if policy.get("require_anchor_ref", 1) and not str(a.get("anchor_ref", "")).strip():
            raise ActionError(
                f"action {a['label']!r} has no anchor_ref — a suggestion with no "
                "evidence pointer is a guess dressed as a next step"
            )
        if a.get("recommended") and not str(a.get("rationale", "")).strip():
            raise ActionError(
                f"{a['label']!r} is recommended with no rationale. Say why it is next."
            )

    # A new answer retires the old set.
    if policy.get("expire_on_new_answer", 1):
        expire_thread(db, thread_id)

    sid = nid()
    db.write(
        f"next:{thread_id}", "set.offered",
        "INSERT INTO next_action_set (id,thread_id,from_message,"
        "no_recommendation_reason,state,created_at) VALUES (?,?,?,?, 'offered', ?)",
        (sid, thread_id, from_message, no_recommendation_reason, now()),
        {"set": sid, "count": len(actions),
         "recommended": recs[0]["label"] if recs else None},
    )
    for i, a in enumerate(actions, start=1):
        auto, why = compute_auto_runnable(a, policy)
        aid = nid()
        db.conn.execute(
            "INSERT INTO next_action (id,set_id,seq,kind,label,prompt,anchor,anchor_ref,"
            "recommended,rationale,confidence,cost_units,cost_minutes,reversible,"
            "needs_clarify,blocked_by,auto_runnable,auto_reason,state) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'offered')",
            (aid, sid, i, a["kind"], a["label"], a["prompt"], a["anchor"],
             a.get("anchor_ref", ""), 1 if a.get("recommended") else 0,
             a.get("rationale", ""), a.get("confidence"), a.get("cost_units"),
             a.get("cost_minutes"), 1 if a.get("reversible") else 0,
             1 if a.get("needs_clarify") else 0, a.get("blocked_by", ""), auto, why),
        )
        db.event("offered", action_id=aid, set_id=sid, kind=a["kind"],
                 recommended=1 if a.get("recommended") else 0)
    db.conn.commit()
    return sid


def read_set(db: DB, sid: str) -> dict:
    s = db.q1("SELECT * FROM next_action_set WHERE id=?", (sid,))
    if not s:
        raise ActionError(f"set {sid} not found")
    acts = [dict(a) for a in db.q(
        "SELECT * FROM next_action WHERE set_id=? ORDER BY seq", (sid,))]
    rec = next((a for a in acts if a["recommended"]), None)
    return {**dict(s), "actions": acts,
            "recommended": rec["label"] if rec else None,
            "recommended_because": rec["rationale"] if rec else None}


def latest_set(db: DB, thread_id: str) -> dict | None:
    s = db.q1(
        "SELECT id FROM next_action_set WHERE thread_id=? AND state='offered' "
        "ORDER BY created_at DESC LIMIT 1", (thread_id,))
    return read_set(db, s["id"]) if s else None


def expire_thread(db: DB, thread_id: str) -> int:
    rows = db.q(
        "SELECT id FROM next_action_set WHERE thread_id=? AND state='offered'",
        (thread_id,))
    for r in rows:
        db.conn.execute("UPDATE next_action_set SET state='expired' WHERE id=?", (r["id"],))
        db.conn.execute(
            "UPDATE next_action SET state='expired' WHERE set_id=? AND state='offered'",
            (r["id"],))
        db.event("expired", set_id=r["id"], detail="superseded by a newer answer")
    db.conn.commit()
    return len(rows)


# ── state transitions ─────────────────────────────────────────────────────

def select(db: DB, action_id: str, edited_prompt: str | None = None) -> dict:
    a = db.q1("SELECT * FROM next_action WHERE id=?", (action_id,))
    if not a:
        raise ActionError(f"action {action_id} not found")
    if a["state"] == "expired":
        raise ActionError("that suggestion expired when a newer answer arrived")
    if a["state"] not in ("offered",):
        raise ActionError(f"action is {a['state']}")
    prompt = edited_prompt.strip() if edited_prompt and edited_prompt.strip() else a["prompt"]
    state = "edited" if prompt != a["prompt"] else "selected"
    s = db.q1("SELECT thread_id FROM next_action_set WHERE id=?", (a["set_id"],))
    db.write(
        f"next:{s['thread_id']}", f"action.{state}",
        "UPDATE next_action SET state=?, prompt=? WHERE id=?",
        (state, prompt, action_id),
        {"action": action_id, "state": state, "recommended": bool(a["recommended"])},
    )
    db.conn.execute("UPDATE next_action_set SET state='spent' WHERE id=?", (a["set_id"],))
    db.conn.commit()
    db.event(state, action_id=action_id, set_id=a["set_id"], kind=a["kind"],
             recommended=a["recommended"])
    return {"action_id": action_id, "state": state, "prompt": prompt,
            "auto_runnable": bool(a["auto_runnable"])}


def dismiss(db: DB, action_id: str, reason: str = "") -> None:
    a = db.q1("SELECT * FROM next_action WHERE id=?", (action_id,))
    if not a:
        raise ActionError(f"action {action_id} not found")
    db.conn.execute("UPDATE next_action SET state='dismissed' WHERE id=?", (action_id,))
    db.conn.commit()
    db.event("dismissed", action_id=action_id, set_id=a["set_id"], kind=a["kind"],
             recommended=a["recommended"], detail=reason)


# ── telemetry — the only way to know whether any of this is working ────────

def stats(db: DB) -> dict:
    out: dict = {"by_kind": {}, "overall": {}}
    for k in KINDS:
        offered = db.q1(
            "SELECT COUNT(*) c FROM next_event WHERE event='offered' AND kind=?", (k,))["c"]
        taken = db.q1(
            "SELECT COUNT(*) c FROM next_event WHERE event IN ('selected','edited') "
            "AND kind=?", (k,))["c"]
        out["by_kind"][k] = {
            "offered": offered, "taken": taken,
            "take_rate": round(taken / offered, 3) if offered else None,
        }
    off = db.q1("SELECT COUNT(*) c FROM next_event WHERE event='offered'")["c"]
    took = db.q1(
        "SELECT COUNT(*) c FROM next_event WHERE event IN ('selected','edited')")["c"]
    edited = db.q1("SELECT COUNT(*) c FROM next_event WHERE event='edited'")["c"]
    rec_off = db.q1(
        "SELECT COUNT(*) c FROM next_event WHERE event='offered' AND recommended=1")["c"]
    rec_took = db.q1(
        "SELECT COUNT(*) c FROM next_event WHERE event IN ('selected','edited') "
        "AND recommended=1")["c"]
    out["overall"] = {
        "offered": off,
        "taken": took,
        "take_rate": round(took / off, 3) if off else None,
        "edit_rate": round(edited / took, 3) if took else None,
        "recommendation_take_rate": round(rec_took / rec_off, 3) if rec_off else None,
        "expired_unused": db.q1(
            "SELECT COUNT(*) c FROM next_event WHERE event='expired'")["c"],
    }
    # The number that matters: if the recommendation is taken no more often than
    # a random chip, the recommender is decoration.
    r = out["overall"]["recommendation_take_rate"]
    t = out["overall"]["take_rate"]
    if r is not None and t is not None:
        out["overall"]["recommendation_lift"] = round(r - t, 3)
    return out
