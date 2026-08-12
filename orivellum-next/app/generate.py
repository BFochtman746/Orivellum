"""Generating the set — and the one rule that makes it trustworthy.

DETERMINISTIC CODE FINDS, THE MODEL PHRASES. Carried over from the runner: the
anchors (counts, ids, table facts) come from queries you can rerun. The model
writes the sentence around them. It never invents the number.

So the flow is:
    1. code gathers candidate anchors from real state  -> facts
    2. model proposes labels/prompts for those anchors -> words
    3. code validates every anchor_ref against the facts it gathered
    4. code picks the recommendation by a scored rule, not by asking
    5. code computes auto_runnable

Step 3 is the guard: an action whose anchor_ref is not in the gathered fact set
is DISCARDED, not corrected. Same discipline as off-schema graph nodes.
"""

from __future__ import annotations

from .db import DB
from .nextaction import ActionError, offer

# ── the gateway boundary ──────────────────────────────────────────────────
# MockGateway keeps this runnable on Replit with no keys and no model. Swap to
# the local endpoint at handoff; preserve every abstain branch.


class MockGateway:
    name = "mock"

    def phrase(self, answer: str, facts: list[dict]) -> list[dict]:
        """Return one proposal per fact, in the mock's deterministic voice."""
        verbs = {"narrow": "Show me", "widen": "What else", "act": "Draft"}
        out = []
        for f in facts:
            k = f["kind"]
            out.append({
                "kind": k,
                "label": f.get("label") or f"{verbs.get(k, 'Do')} {f['subject']}",
                "prompt": f.get("prompt") or f"{verbs.get(k, 'Do')} {f['subject']}",
                "anchor": f["anchor"],
                "anchor_ref": f["anchor_ref"],
            })
        return out


class AbstainingGateway(MockGateway):
    """Used in tests: refuses to phrase anything, to prove the caller degrades."""

    name = "abstain"

    def phrase(self, answer, facts):
        return []


# ── candidate gathering (deterministic) ───────────────────────────────────

def gather_facts(db: DB, probes: list[dict],
                 orivellum_db: "DB | None" = None) -> list[dict]:
    """Run each probe's SQL and keep only the ones that actually return something.

    A probe is: {kind, subject, sql, params, anchor_template, ref_template,
                 cost_units_from_count, reversible, db, ...}

    Probes tagged `"db": "orivellum"` run against `orivellum_db` if provided;
    otherwise they fall back to `db`.  All other probes always use `db`.
    The count from the query becomes both the anchor text and the cost estimate,
    so the number a person reads and the number the budget checks are the same one.
    """
    facts = []
    for p in probes:
        query_db = (orivellum_db
                    if p.get("db") == "orivellum" and orivellum_db is not None
                    else db)
        try:
            row = query_db.q1(p["sql"], tuple(p.get("params", ())))
        except Exception:
            continue                        # a broken probe drops out; it never guesses
        n = (row[0] if row else 0) or 0
        if n <= 0:
            continue
        facts.append({
            "kind": p["kind"],
            "subject": p["subject"],
            "count": n,
            "anchor": p["anchor_template"].format(n=n),
            "anchor_ref": p["ref_template"].format(n=n),
            "cost_units": n if p.get("cost_units_from_count") else p.get("cost_units"),
            "cost_minutes": p.get("cost_minutes"),
            "reversible": p.get("reversible", True),
            "needs_clarify": p.get("needs_clarify", False),
            "blocked_by": p.get("blocked_by", ""),
            "weight": p.get("weight", 1.0),
            "label": p.get("label"),
            "prompt": p.get("prompt"),
            "rationale": p.get("rationale", ""),
        })
    return facts


# ── the recommendation rule ───────────────────────────────────────────────

def score(fact: dict) -> float:
    """Higher is more recommendable. Deliberately simple and inspectable.

    Cheap, reversible, unblocked, and unblocking-for-others wins. Expensive or
    blocked work is still offered — it just is not the one recommended.
    """
    s = float(fact.get("weight", 1.0))
    if fact.get("blocked_by"):
        s -= 1.5
    if fact.get("needs_clarify"):
        s -= 0.7
    if not fact.get("reversible", True):
        s -= 1.0
    mins = fact.get("cost_minutes") or 0
    s -= min(mins / 60.0, 1.0)
    if fact["kind"] == "act":
        s += 0.35            # a forward step beats another question, slightly
    return round(s, 3)


def pick_kinds(facts: list[dict], hi: int) -> list[dict]:
    """One narrow, one widen, one act where possible — directional control
    without a menu. Falls back gracefully when a kind has no candidate."""
    chosen, seen = [], set()
    for k in ("narrow", "widen", "act"):
        best = max((f for f in facts if f["kind"] == k), key=score, default=None)
        if best is not None:
            chosen.append(best)
            seen.add(id(best))
        if len(chosen) >= hi:
            return chosen
    for f in sorted(facts, key=score, reverse=True):
        if id(f) not in seen and len(chosen) < hi:
            chosen.append(f)
            seen.add(id(f))
    return chosen


def build_set(db: DB, thread_id: str, from_message: str, answer: str,
              probes: list[dict], policy: dict, gateway=None,
              orivellum_db: "DB | None" = None) -> dict:
    """The whole pipeline. Returns a dict; raises only on programmer error."""
    gw = gateway or MockGateway()
    facts = gather_facts(db, probes, orivellum_db=orivellum_db)
    if not facts:
        return {"set_id": None, "reason":
                "nothing in current state suggests a next step — the composer is enough"}

    hi = int(policy.get("max_actions", 4))
    lo = int(policy.get("min_actions", 2))
    picked = pick_kinds(facts, hi)
    if len(picked) < lo:
        return {"set_id": None, "reason":
                f"only {len(picked)} grounded candidate(s); below the floor of {lo}"}

    proposals = gw.phrase(answer, picked)
    if not proposals:
        return {"set_id": None, "reason": f"{gw.name} gateway declined to phrase; "
                                          "showing no suggestions rather than guessing"}

    # Step 3: validate every anchor_ref against the gathered facts. Discard, never fix.
    allowed = {f["anchor_ref"]: f for f in picked}
    actions, discarded = [], 0
    for p in proposals:
        f = allowed.get(p.get("anchor_ref"))
        if f is None:
            discarded += 1
            continue
        actions.append({**p, "cost_units": f["cost_units"],
                        "cost_minutes": f["cost_minutes"],
                        "reversible": f["reversible"],
                        "needs_clarify": f["needs_clarify"],
                        "blocked_by": f["blocked_by"],
                        "confidence": round(min(0.95, 0.5 + 0.1 * score(f)), 2),
                        "_score": score(f), "_rationale": f.get("rationale", "")})
    if len(actions) < lo:
        return {"set_id": None, "discarded": discarded, "reason":
                f"{discarded} proposal(s) failed anchor validation; too few left"}

    # Step 4: the recommendation, chosen by rule.
    best = max(actions, key=lambda a: a["_score"])
    threshold = float(policy.get("recommend_min_confidence", 0.55))
    no_reason = ""
    if best["confidence"] >= threshold and not best["blocked_by"]:
        best["recommended"] = True
        best["rationale"] = best["_rationale"] or (
            f"Cheapest unblocked step that unlocks the rest — {best['anchor']}.")
    else:
        no_reason = ("Nothing here is clearly next: "
                     + ("the strongest candidate is blocked by "
                        f"{best['blocked_by']}." if best["blocked_by"]
                        else f"best confidence {best['confidence']:.2f} is below "
                             f"{threshold:.2f}."))
    for a in actions:
        a.pop("_score", None)
        a.pop("_rationale", None)

    sid = offer(db, thread_id, from_message, actions, policy,
                no_recommendation_reason=no_reason)
    return {"set_id": sid, "discarded": discarded, "count": len(actions),
            "no_recommendation_reason": no_reason}


# ── Orivellum schema probes (N3) ──────────────────────────────────────────
# Six real queries against actual Orivellum tables. Each returns a COUNT(*)
# that becomes both the anchor number and the cost estimate — so the number the
# person reads and the number the budget checks are the same one.
#
# A probe whose table is missing or returns 0 drops silently (gather_facts
# catches all exceptions). No probe may supply a hardcoded number.

EXAMPLE_PROBES = [
    # ── A: documents still on the default tier ─────────────────────────────
    {
        "kind": "narrow",
        "subject": "documents still classified as source tier",
        "label": "Show me every document still on the source tier",
        "prompt": (
            "List every document whose tier is still 'source', grouped by kind, "
            "so I can see what classification work remains."
        ),
        "sql": "SELECT COUNT(*) FROM documents WHERE tier='source'",
        "anchor_template": "{n} documents still on the source tier",
        "ref_template": "documents.tier=source:{n}",
        "cost_units_from_count": True,
        "cost_minutes": 2,
        "reversible": True,
        "weight": 1.5,
        "rationale": "Every later pipeline phase depends on correct tier classification.",
        "db": "orivellum",
    },
    # ── B: Works whose title matches a migration batch ─────────────────────
    {
        "kind": "act",
        "subject": "Works still shaped as migration batches",
        "label": "Collapse Works still shaped as migration batches",
        "prompt": (
            "List Works whose titles match the BATCH pattern and propose a migration "
            "to reassign their documents to the correct collection."
        ),
        "sql": "SELECT COUNT(*) FROM works WHERE title LIKE '%BATCH%'",
        "anchor_template": "{n} batch-titled Works that should be collections",
        "ref_template": "works.title~BATCH:{n}",
        "cost_units": 1,
        "cost_minutes": 6,
        "reversible": True,
        "weight": 1.2,
        "rationale": "Batch-titled Works pollute the Work list with structural noise.",
        "db": "orivellum",
    },
    # ── C: open critical findings ──────────────────────────────────────────
    {
        "kind": "narrow",
        "subject": "open critical findings",
        "label": "Review the open critical findings",
        "prompt": (
            "Show me every finding with severity='critical' and status='open', "
            "with its detector and explanation."
        ),
        "sql": (
            "SELECT COUNT(*) FROM pacing_findings "
            "WHERE severity='critical' AND status='open'"
        ),
        "anchor_template": "{n} critical findings still open",
        "ref_template": "pacing_findings.severity=critical,status=open:{n}",
        "cost_units": 1,
        "cost_minutes": 5,
        "reversible": True,
        "weight": 1.8,
        "rationale": "Critical findings block downstream quality gates.",
        "db": "orivellum",
    },
    # ── D: AI-extracted knowledge awaiting author review ───────────────────
    # Table: `knowledge` (not knowledge_items).  LLM-extracted items use
    # review_status='ai_auto'; rule-based items use 'auto'.
    {
        "kind": "act",
        "subject": "AI-extracted knowledge awaiting author review",
        "label": "Review AI-extracted knowledge items",
        "prompt": (
            "Show me every knowledge item with review_status='ai_auto', grouped by Work, "
            "so I can approve or reject them."
        ),
        "sql": "SELECT COUNT(*) FROM knowledge WHERE review_status='ai_auto'",
        "anchor_template": "{n} AI-extracted items awaiting review",
        "ref_template": "knowledge.review_status=ai_auto:{n}",
        "cost_units_from_count": True,
        "cost_minutes": 10,
        "reversible": True,
        "weight": 1.3,
        "rationale": "Unreviewed AI extraction degrades chat context quality.",
        "db": "orivellum",
    },
    # ── E: chapters with no extracted text ────────────────────────────────
    {
        "kind": "narrow",
        "subject": "chapters with no extracted text",
        "label": "Find chapters with no extracted text",
        "prompt": (
            "List every chapter that has no extracted text, with its Work title and "
            "source document, so I can decide whether to reprocess or mark as empty."
        ),
        "sql": (
            "SELECT COUNT(*) FROM book_chapters "
            "WHERE (text IS NULL OR TRIM(text)='') AND work_id IS NOT NULL"
        ),
        "anchor_template": "{n} chapters with no extracted text",
        "ref_template": "book_chapters.text=empty:{n}",
        "cost_units": 1,
        "cost_minutes": 3,
        "reversible": True,
        "weight": 1.1,
        "rationale": "Empty chapters silently degrade pacing analysis and chat context.",
        "db": "orivellum",
    },
    # ── F: clarify gates still open ───────────────────────────────────────
    {
        "kind": "clarify",
        "subject": "clarify gates still open",
        "label": "Close orphaned clarify gates",
        "prompt": (
            "List every clarify gate still in state='open' with its thread and target, "
            "so I can decide whether to skip or close each one."
        ),
        "sql": "SELECT COUNT(*) FROM clarify_request WHERE state='open'",
        "anchor_template": "{n} clarify gates still open",
        "ref_template": "clarify_request.state=open:{n}",
        "cost_units": 1,
        "cost_minutes": 2,
        "reversible": True,
        "weight": 0.9,
    },
]
