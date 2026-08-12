# ORIVELLUM NEXT — Replit build package

**What this builds:** the two components that decide what happens next, plus the
bridge that lets the system continue on its own instead of waiting for you to
type "continue."

**Your workflow, unchanged:** Replit builds and tests → commit to GitHub → pull
onto A-01. Nothing here needs a model, a key, or your data. `MOCK` is the default
and the whole test suite runs offline.

---

## The distinction that matters — read this before anything else

You said the goal is to stop saying "continue." There are **two different
mechanisms** here and conflating them is the trap:

| | What it does | Does it remove "continue"? |
|---|---|---|
| **Next-step chips** | Turns *"what do I ask next"* into one tap | **No.** It removes the thinking, not the tap. |
| **The runner bridge** | Hands a qualified step to the harness as a unit; the loop runs in code | **Yes.** This is the one. |

So this package builds **one producer and two consumers**. A `next_action` row
renders as a chip *and* can be picked up by the runner without a human turn.
Which one happens is decided by `compute_auto_runnable()` — deterministic, in
code, never by a model:

```
auto-run  = reversible AND cheap AND unblocked AND no open clarification AND policy enabled
queue     = everything else, with the reason it could not proceed attached
```

**Nothing irreversible ever auto-runs.** No setting unlocks that. `auto_run_enabled`
ships at `0` — turn it on after a week of watching what it *would* have done.

---

## Run it

```bash
python selftest.py        # 45 invariant tests, offline, must be green
python -m app.api         # serves web/ + the API on :8000
```

FastAPI is used when present; otherwise a stdlib `http.server` serves the same
routes, so a bare Replit box works with nothing installed.

Open `/` for the pattern study — the clarify gate, the next-step set with the
recommendation marked, the autonomy strip, and the rules panel.

---

## What is in here

```
app/schema.sql        clarify_request/facet/option, next_action_set/action,
                      next_event (telemetry), next_ledger (hash-chained)
app/db.py             storage, ledger, policy loader
app/clarify.py        the gate: facet ceiling, mandatory default disclosure,
                      skip semantics, should_gate()
app/nextaction.py     the set: size bounds, one-recommendation invariant,
                      computed auto_runnable, expiry, telemetry + lift
app/generate.py       probes -> facts -> model phrasing -> anchor validation ->
                      scored recommendation.  MockGateway default.
app/runner_bridge.py  enqueue(), Chain budget, pending_for_you()
app/api.py            14 routes, FastAPI or stdlib
web/index.html        the working front end / pattern study
policy/next_policy.yaml   every threshold. No dial lives in code.
tests/test_all.py     45 tests, one per invariant
docs/PATTERN_RESEARCH.md  what the evidence says and where each rule came from
```

---

## How to drive the agent

One milestone per session, in order, from `MILESTONES.md`. Paste the prompt
verbatim. Do not start the next one until `python selftest.py` is green and you
have updated `PROGRESS.json`.

If the agent proposes skipping a test, widening a limit, or "temporarily"
letting something irreversible auto-run — that is the milestone failing, not the
test being inconvenient. `NON_NEGOTIABLES.md` is the list it may not touch.

---

## A-01 handoff

Three changes, no code rewrite:

1. **Real probes.** `generate.EXAMPLE_PROBES` is the *shape*, not the list. Write
   probes against your real tables — `documents.tier`, `works.name`,
   `finding.disposition`, `clarify_request.state`. Every probe is a SQL count you
   can rerun by hand; that is what keeps the anchors honest.
2. **Real gateway.** Replace `MockGateway` with the Lemonade endpoint. **Preserve
   every abstain branch** — `AbstainingGateway` exists in the tests specifically
   to prove the caller shows nothing rather than guessing.
3. **Real runner.** `runner_bridge.enqueue()` returns a unit dict at the hand-off
   point. Wire it to `orivellum-runner`'s queue there. The `Chain` budget caps the
   self-continuation chain; the runner's own budgets still cap each run.

Then set `auto_run_enabled: 1` — and not before.

---

## Where this plugs into what you already have

- Your Book tab already has a **NEXT RECOMMENDED ACTION** panel ("Two or more
  manuscript versions found — confirm which is canonical"). That is this pattern,
  hand-built for one screen. Milestone N6 makes that panel read from
  `next_action` so there is one recommender, not two.
- `orivellum-runner` already has the harness (queue, worker, checkpoint DB,
  budgets, mid-run resume, reports that lead with completeness). This package
  supplies what to put in the queue. The hard part was already done.
- The clarify gate is the UI form of the abstain contract you already enforce for
  epigraphs and hardware inventory: when the request is underspecified, ask
  rather than guess — and **disclose what would be assumed if you skip.**
