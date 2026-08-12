# MILESTONES

Nine sessions. One per sitting. Paste the prompt verbatim, then run
`python selftest.py`. Green and `PROGRESS.json` updated before the next one starts.

N0–N4 ship in this package already built and passing — run them anyway to verify
the environment, then start real work at **N5**.

---

## N0 — Environment and invariants

> Verify the orivellum-next package runs in this Replit. Run `python selftest.py`
> and report the count. Run `python -m app.api` and confirm `/` serves the pattern
> study and `GET /api/policy` returns the dials from `policy/next_policy.yaml`.
> Do not change any file. Report anything that fails.

**Accept when:** 45 tests pass, `/` renders, `/api/policy` shows `max_facets: 3`
and `auto_run_enabled: 0`.

---

## N1 — Clarify gate (already built — verify and extend)

> Read `app/clarify.py` and `NON_NEGOTIABLES.md` rules 1–5. Add three tests of
> your own that try to break those rules in ways the existing suite does not
> cover, and confirm they fail as expected. Do not weaken any rule to make a test
> pass. Report what you tried.

**Accept when:** the new tests pass, no existing rule was relaxed, and the diff
touches only `tests/`.

---

## N2 — Next-action set (already built — verify and extend)

> Read `app/nextaction.py` and rules 6–9 and 11. Add a test proving that a set
> of exactly 3 with one recommendation is accepted, and one proving that a set of
> 5 is refused with a message naming the ceiling. Then run `nextaction.stats()`
> against a synthetic sequence of 20 offers where the recommendation is taken 60%
> of the time and confirm `recommendation_lift` is positive.

**Accept when:** tests pass and the lift figure is reported in your summary.

---

## N3 — Real probes against real tables

> Replace `generate.EXAMPLE_PROBES` with probes against the actual Orivellum
> schema. Each probe is a SQL `COUNT(*)` plus an `anchor_template` and a
> `ref_template`. Write at least six, covering: documents still on the default
> tier; Works whose name matches a migration batch; open critical findings;
> knowledge items awaiting review; chapters with no text; and gates left open.
> Every probe must return a real count or drop out — no probe may supply a
> hardcoded number. Add a test per probe using a synthetic fixture.

**Accept when:** every probe's anchor number comes from its own query, a probe
against a missing table drops silently, and each has a test.

---

## N4 — Front end wired to the API

> Wire `web/index.html` to the live API: `POST /api/next/build` renders the set,
> the recommended action gets the `is-rec` treatment with its rationale, tapping a
> chip calls `/api/next/select` and puts the (editable) prompt in the composer,
> dismissing calls `/api/next/dismiss`. The gate renders from `/api/gate/read`
> with the live progress count; "Run on the assumptions above" calls
> `/api/gate/close` with `skip=true` and shows which defaults were applied.
> Keep the existing CSS. No new dependencies. No browser storage.

**Accept when:** a full round trip works in the browser with no console errors,
and the progress counter and the applied-defaults list both come from the API
rather than from JS state.

---

## N5 — The runner bridge (the milestone that removes "continue")

> Wire `runner_bridge.enqueue()` to the real `orivellum-runner` queue at the
> hand-off point. An `auto_runnable` action becomes a runner unit with its
> `prompt`, `anchor_ref` and cost as the budget. Everything else moves to
> `state='queued'` with `auto_reason` attached and appears in
> `pending_for_you()`. Add an integration test: a chain of three cheap reversible
> actions runs unattended and stops at the chain budget with a report; a fourth
> irreversible action refuses to auto-run no matter what policy says.
> Leave `auto_run_enabled: 0`.

**Accept when:** the chain test passes, the irreversible test proves refusal, and
`pending_for_you()` shows every queued item with the reason it waits.

---

## N6 — One recommender, not two

> The Book tab already renders a hand-built "NEXT RECOMMENDED ACTION" panel.
> Change it to read from `next_action` where `recommended=1` for that Work, so
> there is one source of recommendations. Migrate the existing conditions
> (multiple manuscript versions, no chapter structure, unlocked style) into
> probes. Delete the hand-built logic once the panel renders identically.

**Accept when:** the panel shows the same guidance as before but sourced from
`next_action`, and a grep finds no second recommendation code path.

---

## N7 — Telemetry surface

> Add a small page at `/web/stats.html` reading `/api/stats`: take rate overall
> and per kind, edit rate, dismissal rate, expired-unused count, and
> `recommendation_lift` shown prominently with a plain-language note on what a
> non-positive value means. Match the existing type and palette.

**Accept when:** the page renders real figures and states honestly when there is
not enough data yet rather than showing a zero.

---

## N8 — Turn autonomy on, carefully

> Set `auto_run_enabled: 1` with `auto_run_max_units: 50` and
> `auto_run_max_minutes: 3` — deliberately below the real ceiling. Add a
> `--dry-run` mode to the bridge that logs what *would* have auto-run without
> running it. Run in dry-run for a week of real use, then report: how many steps
> would have run, what they were, and whether any of them would have been wrong.

**Accept when:** the dry-run log exists and you have read it before raising the
budgets. This milestone is not "done" the day it ships — it is done when the log
says the judgement was right.

---

## Order note

N5 before N6 before N7 is not arbitrary. N5 is the only milestone that changes
your daily experience; N6 removes a duplicate recommender before you start
trusting it; N7 is what tells you whether any of it is working. **N8 last, and
never before N7** — turning on autonomy without the telemetry to judge it is how
you end up with a system that continues confidently in the wrong direction.
