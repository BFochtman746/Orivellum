"""The clarify gate.

Rules enforced here, not documented elsewhere:

  1. Three facets maximum. A trained clarifier matches the best general models
     with 41% fewer questions by ranking on relevance and answerability, so the
     ceiling is a feature.
  2. Every facet MUST disclose its default and where that default comes from.
     A facet without a disclosed default is refused. This is the whole reason
     the gate is trustworthy: skipping becomes an informed choice.
  3. Every facet MUST carry options (a bare question demands expertise the
     person may not have) AND allow freeform (options are a shortcut, not a cage).
  4. Cost is mandatory. A gate in front of cheap reversible work is friction.
  5. Skipping resolves every unanswered facet to its disclosed default and
     records that it happened. Nothing is silently assumed.
"""

from __future__ import annotations

from .db import DB, nid, now


class GateError(Exception):
    pass


def open_gate(
    db: DB,
    thread_id: str,
    target: str,
    facets: list[dict],
    cost_units: int | None = None,
    cost_minutes: int | None = None,
    cost_replaces: str = "",
    reversible: bool = True,
    policy: dict | None = None,
) -> str:
    pol = policy or {}
    ceiling = int(pol.get("max_facets", 3))

    if not facets:
        raise GateError("a gate with no facets is not a gate — just run the work")
    if len(facets) > ceiling:
        raise GateError(
            f"{len(facets)} facets exceeds the ceiling of {ceiling}. "
            "Rank by what would change the output and drop the rest."
        )
    if cost_units is None and cost_minutes is None:
        raise GateError(
            "a gate must state its cost; without one it is friction with no payoff"
        )

    for f in facets:
        for req in ("name", "question", "why", "default_value", "default_source"):
            if not str(f.get(req, "")).strip():
                raise GateError(f"facet {f.get('name', '?')!r} is missing {req!r}")
        if not f.get("options"):
            raise GateError(
                f"facet {f['name']!r} has no options — a bare question needs "
                "expertise the person may not have yet"
            )

    rid = nid()
    db.write(
        f"gate:{thread_id}",
        "gate.opened",
        "INSERT INTO clarify_request (id,thread_id,target,cost_units,cost_minutes,"
        "cost_replaces,reversible,state,created_at) VALUES (?,?,?,?,?,?,?, 'open', ?)",
        (rid, thread_id, target, cost_units, cost_minutes, cost_replaces,
         1 if reversible else 0, now()),
        {"request": rid, "target": target, "facets": len(facets),
         "reversible": bool(reversible)},
    )

    for i, f in enumerate(facets, start=1):
        fid = nid()
        db.conn.execute(
            "INSERT INTO clarify_facet (id,request_id,seq,name,question,why,"
            "default_value,default_source,default_risk,allow_freeform) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (fid, rid, i, f["name"], f["question"], f["why"], f["default_value"],
             f["default_source"], f.get("default_risk", ""),
             0 if f.get("allow_freeform") is False else 1),
        )
        for j, o in enumerate(f["options"], start=1):
            db.conn.execute(
                "INSERT INTO clarify_option (id,facet_id,seq,label,value,hint) "
                "VALUES (?,?,?,?,?,?)",
                (nid(), fid, j, o["label"], o.get("value", o["label"]), o.get("hint", "")),
            )
    db.conn.commit()
    return rid


def read_gate(db: DB, rid: str) -> dict:
    r = db.q1("SELECT * FROM clarify_request WHERE id=?", (rid,))
    if not r:
        raise GateError(f"gate {rid} not found")
    facets = []
    for f in db.q("SELECT * FROM clarify_facet WHERE request_id=? ORDER BY seq", (rid,)):
        opts = [dict(o) for o in db.q(
            "SELECT label,value,hint FROM clarify_option WHERE facet_id=? ORDER BY seq",
            (f["id"],))]
        facets.append({**dict(f), "options": opts})
    answered = sum(1 for f in facets if f["resolved_value"] is not None)
    return {
        **dict(r),
        "facets": facets,
        "answered": answered,
        "total": len(facets),
        "progress": f"{answered} of {len(facets)} answered",
    }


def resolve(db: DB, facet_id: str, value: str, kind: str = "option") -> None:
    if kind not in ("option", "freeform", "default"):
        raise GateError(f"bad resolution kind {kind!r}")
    f = db.q1("SELECT * FROM clarify_facet WHERE id=?", (facet_id,))
    if not f:
        raise GateError(f"facet {facet_id} not found")
    if kind == "freeform" and not f["allow_freeform"]:
        raise GateError(f"facet {f['name']!r} does not accept freeform")
    if kind == "option":
        allowed = {o["value"] for o in db.q(
            "SELECT value FROM clarify_option WHERE facet_id=?", (facet_id,))}
        if value not in allowed:
            raise GateError(f"{value!r} is not an offered option for {f['name']!r}")
    if not str(value).strip():
        raise GateError("an empty answer is not an answer — skip the gate instead")
    req = db.q1("SELECT thread_id, state FROM clarify_request WHERE id=?", (f["request_id"],))
    if req["state"] != "open":
        raise GateError(f"gate is {req['state']}; cannot resolve facets")
    db.write(
        f"gate:{req['thread_id']}",
        "facet.resolved",
        "UPDATE clarify_facet SET resolved_value=?, resolved_kind=?, resolved_at=? WHERE id=?",
        (value, kind, now(), facet_id),
        {"facet": f["name"], "kind": kind, "value": value[:200]},
    )


def close_gate(db: DB, rid: str, skip: bool = False) -> dict:
    """Answer the gate. Unanswered facets fall to their DISCLOSED defaults and
    the fact that they did is recorded — never silently applied."""
    g = read_gate(db, rid)
    if g["state"] != "open":
        raise GateError(f"gate is already {g['state']}")
    applied_defaults = []
    for f in g["facets"]:
        if f["resolved_value"] is None:
            if not skip:
                raise GateError(
                    f"facet {f['name']!r} is unanswered. Answer it, or close the gate "
                    "with skip=True to accept the disclosed default."
                )
            resolve(db, f["id"], f["default_value"], kind="default")
            applied_defaults.append(
                {"facet": f["name"], "value": f["default_value"],
                 "source": f["default_source"], "risk": f["default_risk"]}
            )
    state = "skipped" if applied_defaults and len(applied_defaults) == g["total"] else "answered"
    db.write(
        f"gate:{g['thread_id']}",
        f"gate.{state}",
        "UPDATE clarify_request SET state=?, closed_at=? WHERE id=?",
        (state, now(), rid),
        {"request": rid, "state": state, "defaults_applied": applied_defaults},
    )
    final = {f["name"]: (f["resolved_value"] or f["default_value"])
             for f in read_gate(db, rid)["facets"]}
    return {"request": rid, "state": state, "answers": final,
            "defaults_applied": applied_defaults}


def cancel_gate(db: DB, rid: str) -> None:
    g = db.q1("SELECT thread_id FROM clarify_request WHERE id=?", (rid,))
    if not g:
        raise GateError(f"gate {rid} not found")
    db.write(
        f"gate:{g['thread_id']}", "gate.cancelled",
        "UPDATE clarify_request SET state='cancelled', closed_at=? WHERE id=?",
        (now(), rid), {"request": rid},
    )


def should_gate(cost_units: int | None, cost_minutes: int | None, reversible: bool,
                ambiguous_facets: int, policy: dict | None = None) -> tuple[bool, str]:
    """The decision to show a gate at all. Deterministic on purpose.

    A gate on an unambiguous request is friction wearing a governance costume;
    a gate on cheap reversible work is worse than just doing it and offering undo.
    """
    if ambiguous_facets <= 0:
        return False, "nothing genuinely ambiguous — act"
    if not reversible:
        return True, "irreversible work always confirms"
    units = cost_units or 0
    mins = cost_minutes or 0
    pol = policy or {}
    if units <= int(pol.get("auto_run_max_units", 200)) and \
       mins <= int(pol.get("auto_run_max_minutes", 10)):
        return False, "cheap and reversible — do it and offer undo"
    return True, "underspecified and expensive"
