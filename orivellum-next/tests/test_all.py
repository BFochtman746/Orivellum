"""Every test here asserts an INVARIANT, not an implementation detail.
If one fails, a rule is broken and the milestone is not done.

Run:  python selftest.py
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import clarify, generate, nextaction, runner_bridge  # noqa: E402
from app.db import DB, load_policy  # noqa: E402

POLICY = load_policy(Path(__file__).resolve().parent.parent / "policy" / "next_policy.yaml")


def facet(name="ontology", **kw):
    base = {
        "name": name,
        "question": f"Which {name}?",
        "why": "Because the last run got it wrong.",
        "default_value": "narrative",
        "default_source": "work.domain is unset",
        "default_risk": "the setting that produced the bad harvest",
        "options": [{"label": "Technical", "value": "technical", "hint": "function · platform"},
                    {"label": "Domain reference", "value": "domain"}],
    }
    base.update(kw)
    return base


def action(kind="narrow", label="Show me the retypes", rec=False, **kw):
    base = {
        "kind": kind, "label": label, "prompt": label,
        "anchor": "from the 9,142 still labelled source",
        "anchor_ref": "documents.tier=source:9142",
        "recommended": rec, "rationale": "It unblocks the rest." if rec else "",
        "confidence": 0.8, "cost_units": 10, "cost_minutes": 2, "reversible": True,
    }
    base.update(kw)
    return base


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()


# ── clarify gate ──────────────────────────────────────────────────────────

class TestGate(Base):
    def test_facet_ceiling_enforced(self):
        with self.assertRaises(clarify.GateError):
            clarify.open_gate(self.db, "t1", "x",
                              [facet("a"), facet("b"), facet("c"), facet("d")],
                              cost_units=100, policy=POLICY)

    def test_default_disclosure_is_mandatory(self):
        f = facet()
        f["default_value"] = ""
        with self.assertRaises(clarify.GateError):
            clarify.open_gate(self.db, "t1", "x", [f], cost_units=100, policy=POLICY)

    def test_default_source_is_mandatory(self):
        f = facet()
        f["default_source"] = "  "
        with self.assertRaises(clarify.GateError):
            clarify.open_gate(self.db, "t1", "x", [f], cost_units=100, policy=POLICY)

    def test_options_are_mandatory(self):
        f = facet()
        f["options"] = []
        with self.assertRaises(clarify.GateError):
            clarify.open_gate(self.db, "t1", "x", [f], cost_units=100, policy=POLICY)

    def test_cost_is_mandatory(self):
        with self.assertRaises(clarify.GateError):
            clarify.open_gate(self.db, "t1", "x", [facet()], policy=POLICY)

    def test_progress_counter(self):
        rid = clarify.open_gate(self.db, "t1", "x", [facet("a"), facet("b")],
                                cost_units=500, cost_minutes=30, policy=POLICY)
        g = clarify.read_gate(self.db, rid)
        self.assertEqual(g["progress"], "0 of 2 answered")
        clarify.resolve(self.db, g["facets"][0]["id"], "technical")
        self.assertEqual(clarify.read_gate(self.db, rid)["progress"], "1 of 2 answered")

    def test_unoffered_option_refused(self):
        rid = clarify.open_gate(self.db, "t1", "x", [facet()], cost_units=500, policy=POLICY)
        fid = clarify.read_gate(self.db, rid)["facets"][0]["id"]
        with self.assertRaises(clarify.GateError):
            clarify.resolve(self.db, fid, "something-nobody-offered")

    def test_freeform_allowed_and_blockable(self):
        rid = clarify.open_gate(self.db, "t1", "x", [facet(), facet("scope", allow_freeform=False)],
                               cost_units=500, policy=POLICY)
        fs = clarify.read_gate(self.db, rid)["facets"]
        clarify.resolve(self.db, fs[0]["id"], "anything I like", kind="freeform")
        with self.assertRaises(clarify.GateError):
            clarify.resolve(self.db, fs[1]["id"], "nope", kind="freeform")

    def test_cannot_close_with_unanswered_facets_unless_skipping(self):
        rid = clarify.open_gate(self.db, "t1", "x", [facet(), facet("scope")],
                               cost_units=500, policy=POLICY)
        with self.assertRaises(clarify.GateError):
            clarify.close_gate(self.db, rid)

    def test_skip_applies_disclosed_defaults_and_records_them(self):
        rid = clarify.open_gate(self.db, "t1", "x", [facet(), facet("scope")],
                               cost_units=500, policy=POLICY)
        res = clarify.close_gate(self.db, rid, skip=True)
        self.assertEqual(res["state"], "skipped")
        self.assertEqual(len(res["defaults_applied"]), 2)
        self.assertTrue(all(d["source"] for d in res["defaults_applied"]))
        # the risk text travels with the default, so the record shows what was risked
        self.assertIn("bad harvest", res["defaults_applied"][0]["risk"])

    def test_answers_win_over_defaults(self):
        rid = clarify.open_gate(self.db, "t1", "x", [facet(), facet("scope")],
                               cost_units=500, policy=POLICY)
        fs = clarify.read_gate(self.db, rid)["facets"]
        clarify.resolve(self.db, fs[0]["id"], "technical")
        res = clarify.close_gate(self.db, rid, skip=True)
        self.assertEqual(res["answers"]["ontology"], "technical")
        self.assertEqual(res["state"], "answered")
        self.assertEqual(len(res["defaults_applied"]), 1)

    def test_ledger_verifies(self):
        rid = clarify.open_gate(self.db, "t1", "x", [facet()], cost_units=500, policy=POLICY)
        clarify.close_gate(self.db, rid, skip=True)
        self.assertTrue(self.db.verify("gate:t1")["ok"])

    def test_should_gate_decision(self):
        # cheap + reversible + ambiguous -> just do it
        self.assertFalse(clarify.should_gate(10, 1, True, 2, POLICY)[0])
        # expensive + ambiguous -> gate
        self.assertTrue(clarify.should_gate(9000, 40, True, 2, POLICY)[0])
        # irreversible always gates
        self.assertTrue(clarify.should_gate(1, 1, False, 1, POLICY)[0])
        # nothing ambiguous -> never gate, however expensive
        self.assertFalse(clarify.should_gate(9000, 90, True, 0, POLICY)[0])

    # ── N1 adversarial tests ──────────────────────────────────────────────

    def test_zero_facet_gate_refused(self):
        """N1 adversarial (Rule 1): a gate with zero facets is refused.
        The code path `if not facets: raise GateError(...)` exists but had no test."""
        with self.assertRaises(clarify.GateError) as cm:
            clarify.open_gate(self.db, "t_adv1", "x", [], cost_units=100, policy=POLICY)
        self.assertIn("facet", str(cm.exception).lower())

    def test_whitespace_only_answer_refused(self):
        """N1 adversarial (Rule 5): a resolve with spaces only is rejected.

        The "empty answer is not an answer" guard strips the value before checking.
        We submit via kind='freeform' so the code reaches the emptiness check
        before the offered-option check — the rule applies to all resolution paths.
        """
        rid = clarify.open_gate(self.db, "t_adv2", "x", [facet()],
                                cost_units=100, policy=POLICY)
        fid = clarify.read_gate(self.db, rid)["facets"][0]["id"]
        with self.assertRaises(clarify.GateError) as cm:
            clarify.resolve(self.db, fid, "   ", kind="freeform")  # whitespace only
        self.assertIn("empty", str(cm.exception).lower())

    def test_resolve_on_closed_gate_refused(self):
        """N1 adversarial (Rule 5): once a gate is skipped, its facets are locked.
        A second resolve must raise — defaults cannot be overwritten after close."""
        rid = clarify.open_gate(self.db, "t_adv3", "x", [facet()],
                                cost_units=100, policy=POLICY)
        clarify.close_gate(self.db, rid, skip=True)
        fid = clarify.read_gate(self.db, rid)["facets"][0]["id"]
        with self.assertRaises(clarify.GateError):
            clarify.resolve(self.db, fid, "technical")


# ── next actions ──────────────────────────────────────────────────────────

class TestActions(Base):
    def test_set_size_floor_and_ceiling(self):
        with self.assertRaises(nextaction.ActionError):
            nextaction.offer(self.db, "t", "m", [action(rec=True)], POLICY)
        with self.assertRaises(nextaction.ActionError):
            nextaction.offer(self.db, "t", "m",
                             [action(label=f"a{i}") for i in range(6)] , POLICY,
                             no_recommendation_reason="none")

    def test_exactly_one_recommendation(self):
        with self.assertRaises(nextaction.ActionError) as cm:
            nextaction.offer(self.db, "t", "m",
                             [action(rec=True), action(kind="act", label="b", rec=True)],
                             POLICY)
        self.assertIn("two recommendations is no recommendation", str(cm.exception).lower())

    def test_no_recommendation_requires_a_reason(self):
        with self.assertRaises(nextaction.ActionError):
            nextaction.offer(self.db, "t", "m",
                             [action(), action(kind="act", label="b")], POLICY)

    def test_no_recommendation_with_reason_is_allowed(self):
        sid = nextaction.offer(self.db, "t", "m",
                               [action(), action(kind="act", label="b")], POLICY,
                               no_recommendation_reason="everything is blocked")
        s = nextaction.read_set(self.db, sid)
        self.assertIsNone(s["recommended"])
        self.assertIn("blocked", s["no_recommendation_reason"])

    def test_recommendation_requires_rationale(self):
        a = action(rec=True)
        a["rationale"] = ""
        with self.assertRaises(nextaction.ActionError):
            nextaction.offer(self.db, "t", "m", [a, action(kind="act", label="b")], POLICY)

    def test_anchor_ref_required(self):
        a = action(rec=True)
        a["anchor_ref"] = ""
        with self.assertRaises(nextaction.ActionError):
            nextaction.offer(self.db, "t", "m", [a, action(kind="act", label="b")], POLICY)

    def test_bad_kind_refused(self):
        with self.assertRaises(nextaction.ActionError):
            nextaction.offer(self.db, "t", "m",
                             [action(kind="vibes", rec=True), action(kind="act", label="b")],
                             POLICY)

    def test_new_set_expires_the_old_one(self):
        s1 = nextaction.offer(self.db, "t", "m1",
                              [action(rec=True), action(kind="act", label="b")], POLICY)
        nextaction.offer(self.db, "t", "m2",
                         [action(rec=True), action(kind="act", label="c")], POLICY)
        old = nextaction.read_set(self.db, s1)
        self.assertEqual(old["state"], "expired")
        self.assertTrue(all(a["state"] == "expired" for a in old["actions"]))

    def test_expired_action_cannot_be_selected(self):
        s1 = nextaction.offer(self.db, "t", "m1",
                              [action(rec=True), action(kind="act", label="b")], POLICY)
        aid = nextaction.read_set(self.db, s1)["actions"][0]["id"]
        nextaction.offer(self.db, "t", "m2",
                         [action(rec=True), action(kind="act", label="c")], POLICY)
        with self.assertRaises(nextaction.ActionError):
            nextaction.select(self.db, aid)

    def test_edit_before_send_is_recorded_distinctly(self):
        sid = nextaction.offer(self.db, "t", "m",
                               [action(rec=True), action(kind="act", label="b")], POLICY)
        acts = nextaction.read_set(self.db, sid)["actions"]
        r1 = nextaction.select(self.db, acts[0]["id"])
        self.assertEqual(r1["state"], "selected")
        sid2 = nextaction.offer(self.db, "t", "m2",
                                [action(rec=True), action(kind="act", label="b")], POLICY)
        a2 = nextaction.read_set(self.db, sid2)["actions"][0]
        r2 = nextaction.select(self.db, a2["id"], edited_prompt="my own words")
        self.assertEqual(r2["state"], "edited")
        self.assertEqual(r2["prompt"], "my own words")


# ── auto_runnable is computed, never asserted ─────────────────────────────

class TestAutoRunnable(Base):
    def setUp(self):
        super().setUp()
        self.on = {**POLICY, "auto_run_enabled": 1}

    def test_off_by_policy(self):
        ok, why = nextaction.compute_auto_runnable(action(), POLICY)
        self.assertEqual(ok, 0)
        self.assertIn("policy", why)

    def test_irreversible_never_runs(self):
        ok, why = nextaction.compute_auto_runnable(action(reversible=False), self.on)
        self.assertEqual(ok, 0)
        self.assertIn("reversible", why)

    def test_blocked_never_runs(self):
        ok, why = nextaction.compute_auto_runnable(action(blocked_by="F-12"), self.on)
        self.assertEqual(ok, 0)
        self.assertIn("F-12", why)

    def test_needs_clarify_never_runs(self):
        ok, _ = nextaction.compute_auto_runnable(action(needs_clarify=True), self.on)
        self.assertEqual(ok, 0)

    def test_over_budget_never_runs(self):
        ok, why = nextaction.compute_auto_runnable(action(cost_units=99999), self.on)
        self.assertEqual(ok, 0)
        self.assertIn("budget", why)
        ok2, why2 = nextaction.compute_auto_runnable(action(cost_minutes=600), self.on)
        self.assertEqual(ok2, 0)
        self.assertIn("budget", why2)

    def test_clarify_kind_never_runs(self):
        ok, _ = nextaction.compute_auto_runnable(action(kind="clarify"), self.on)
        self.assertEqual(ok, 0)

    def test_model_cannot_assert_it(self):
        """A supplied auto_runnable field is ignored — it is always recomputed."""
        a = action(rec=True)
        a["auto_runnable"] = 1
        a["reversible"] = False
        sid = nextaction.offer(self.db, "t", "m",
                               [a, action(kind="act", label="b")], self.on)
        row = nextaction.read_set(self.db, sid)["actions"][0]
        self.assertEqual(row["auto_runnable"], 0)

    def test_cheap_reversible_unblocked_runs(self):
        ok, why = nextaction.compute_auto_runnable(action(), self.on)
        self.assertEqual(ok, 1)
        self.assertIn("inside budget", why)


# ── the runner bridge ─────────────────────────────────────────────────────

class TestBridge(Base):
    def test_non_autorunnable_queues_with_a_reason(self):
        sid = nextaction.offer(self.db, "t", "m",
                               [action(rec=True), action(kind="act", label="b")], POLICY)
        aid = nextaction.read_set(self.db, sid)["actions"][0]["id"]
        res = runner_bridge.enqueue(self.db, aid)
        self.assertTrue(res["queued"])
        self.assertFalse(res["auto"])
        self.assertTrue(res["why"])
        q = runner_bridge.pending_for_you(self.db, "t")
        self.assertEqual(len(q), 1)
        self.assertTrue(q[0]["waits_because"])

    def test_autorunnable_hands_off_a_unit(self):
        on = {**POLICY, "auto_run_enabled": 1}
        sid = nextaction.offer(self.db, "t", "m",
                               [action(rec=True), action(kind="act", label="b")], on)
        aid = nextaction.read_set(self.db, sid)["actions"][0]["id"]
        res = runner_bridge.enqueue(self.db, aid)
        self.assertTrue(res["auto"])
        self.assertIn("prompt", res["unit"])
        self.assertIn("anchor_ref", res["unit"])

    def test_chain_budget_stops_the_loop(self):
        on = {**POLICY, "auto_run_enabled": 1}
        chain = runner_bridge.Chain("t", {"max_steps": 1, "max_minutes": 99,
                                          "max_units": 9999})
        s1 = nextaction.offer(self.db, "t", "m1",
                              [action(rec=True), action(kind="act", label="b")], on)
        a1 = nextaction.read_set(self.db, s1)["actions"][0]["id"]
        runner_bridge.enqueue(self.db, a1, chain)
        s2 = nextaction.offer(self.db, "t", "m2",
                              [action(rec=True), action(kind="act", label="c")], on)
        a2 = nextaction.read_set(self.db, s2)["actions"][0]["id"]
        with self.assertRaises(runner_bridge.ChainExhausted):
            runner_bridge.enqueue(self.db, a2, chain)
        self.assertEqual(chain.report()["steps_run"], 1)

    def test_chain_report_leads_with_spend(self):
        chain = runner_bridge.Chain("t")
        rep = chain.report()
        for k in ("steps_run", "minutes_spent", "units_spent", "budget", "ran"):
            self.assertIn(k, rep)


# ── the generator ─────────────────────────────────────────────────────────

class TestGenerate(Base):
    def setUp(self):
        super().setUp()
        # stand in for real corpus state
        # Mirrors the real Orivellum schema: works uses 'title', not 'name'.
        self.db.conn.executescript(
            "CREATE TABLE documents (id TEXT, tier TEXT);"
            "CREATE TABLE works (id TEXT, title TEXT);"
        )
        for i in range(40):
            self.db.conn.execute("INSERT INTO documents VALUES (?, 'source')", (f"d{i}",))
        for n in ("A01_MIGRATION_BATCH_011", "A01_MIGRATION_BATCH_013"):
            self.db.conn.execute("INSERT INTO works VALUES (?,?)", (n, n))
        self.db.conn.commit()

    def test_builds_a_grounded_set(self):
        res = generate.build_set(self.db, "t", "m", "an answer",
                                 generate.EXAMPLE_PROBES, POLICY)
        self.assertIsNotNone(res["set_id"])
        s = nextaction.read_set(self.db, res["set_id"])
        self.assertGreaterEqual(len(s["actions"]), 2)
        # every anchor carries the real number from the query
        self.assertTrue(any("40" in a["anchor"] for a in s["actions"]))
        self.assertIsNotNone(s["recommended"])
        self.assertTrue(s["recommended_because"])

    def test_probe_with_no_rows_is_dropped(self):
        facts = generate.gather_facts(self.db, [{
            "kind": "narrow", "subject": "nothing",
            "sql": "SELECT COUNT(*) FROM documents WHERE tier='does-not-exist'",
            "anchor_template": "{n}", "ref_template": "x:{n}",
        }])
        self.assertEqual(facts, [])

    def test_broken_probe_never_guesses(self):
        facts = generate.gather_facts(self.db, [{
            "kind": "narrow", "subject": "boom",
            "sql": "SELECT COUNT(*) FROM table_that_is_not_there",
            "anchor_template": "{n}", "ref_template": "x:{n}",
        }])
        self.assertEqual(facts, [])

    def test_unvalidated_anchor_is_discarded_not_corrected(self):
        class Liar(generate.MockGateway):
            def phrase(self, answer, facts):
                out = super().phrase(answer, facts)
                out.append({"kind": "act", "label": "invented step",
                            "prompt": "do a thing",
                            "anchor": "from the 5,000 files",
                            "anchor_ref": "documents.tier=source:5000"})
                return out

        res = generate.build_set(self.db, "t", "m", "a", generate.EXAMPLE_PROBES,
                                 POLICY, gateway=Liar())
        self.assertEqual(res["discarded"], 1)
        s = nextaction.read_set(self.db, res["set_id"])
        self.assertFalse(any("invented" in a["label"] for a in s["actions"]))

    def test_abstaining_gateway_yields_no_chips(self):
        res = generate.build_set(self.db, "t", "m", "a", generate.EXAMPLE_PROBES,
                                 POLICY, gateway=generate.AbstainingGateway())
        self.assertIsNone(res["set_id"])
        self.assertIn("declined", res["reason"])

    def test_no_probes_means_no_chips(self):
        res = generate.build_set(self.db, "t", "m", "a", [], POLICY)
        self.assertIsNone(res["set_id"])

    def test_kind_mix_prefers_one_of_each(self):
        res = generate.build_set(self.db, "t", "m", "a", generate.EXAMPLE_PROBES, POLICY)
        kinds = {a["kind"] for a in nextaction.read_set(self.db, res["set_id"])["actions"]}
        self.assertIn("narrow", kinds)
        self.assertIn("act", kinds)

    def test_blocked_candidate_is_not_recommended(self):
        probes = [
            {**generate.EXAMPLE_PROBES[0], "blocked_by": "gate G-02", "weight": 9.0},
            generate.EXAMPLE_PROBES[1],
        ]
        res = generate.build_set(self.db, "t", "m", "a", probes, POLICY)
        s = nextaction.read_set(self.db, res["set_id"])
        rec = next((a for a in s["actions"] if a["recommended"]), None)
        if rec:
            self.assertEqual(rec["blocked_by"], "")


# ── telemetry ─────────────────────────────────────────────────────────────

class TestStats(Base):
    def test_lift_is_measurable(self):
        for i in range(4):
            sid = nextaction.offer(self.db, "t", f"m{i}",
                                   [action(rec=True, label=f"rec{i}"),
                                    action(kind="act", label=f"other{i}")], POLICY)
            acts = nextaction.read_set(self.db, sid)["actions"]
            nextaction.select(self.db, acts[0]["id"])   # always take the recommendation
        st = nextaction.stats(self.db)
        self.assertEqual(st["overall"]["recommendation_take_rate"], 1.0)
        self.assertGreater(st["overall"]["recommendation_lift"], 0)
        self.assertIsNotNone(st["by_kind"]["narrow"]["take_rate"])

    def test_dismissal_is_recorded(self):
        sid = nextaction.offer(self.db, "t", "m",
                               [action(rec=True), action(kind="act", label="b")], POLICY)
        aid = nextaction.read_set(self.db, sid)["actions"][1]["id"]
        nextaction.dismiss(self.db, aid, "not now")
        self.assertEqual(
            self.db.q1("SELECT COUNT(*) c FROM next_event WHERE event='dismissed'")["c"], 1)


# ── N2 — next-action set rules ────────────────────────────────────────────

class TestN2(Base):
    """N2 — set size, recommendation, and lift tests per MILESTONES N2."""

    def test_three_action_set_with_one_rec_accepted(self):
        """Rule 6+7: exactly 3 actions with exactly 1 recommendation is valid."""
        sid = nextaction.offer(
            self.db, "t", "m",
            [
                action(rec=True,   label="Recommended step"),
                action(kind="act", label="Alternative A"),
                action(kind="widen", label="Broader approach"),
            ],
            POLICY,
        )
        s = nextaction.read_set(self.db, sid)
        self.assertEqual(len(s["actions"]), 3)
        self.assertEqual(s["recommended"], "Recommended step")
        self.assertTrue(s["recommended_because"],
                        "recommendation must carry its rationale")

    def test_five_action_set_refused_naming_ceiling(self):
        """Rule 6: 5 actions exceeds max_actions=4; error must name the ceiling."""
        with self.assertRaises(nextaction.ActionError) as cm:
            nextaction.offer(
                self.db, "t", "m",
                [action(label=f"step-{i}", kind="narrow") for i in range(5)],
                POLICY,
                no_recommendation_reason="too many to rank",
            )
        self.assertIn("4", str(cm.exception),
                      "ceiling (4) must appear in the error so the caller knows the limit")

    def test_recommendation_lift_positive_at_60pct_take_rate(self):
        """Stats: recommendation_lift > 0 when the rec is taken 60 % of the time.

        20 independent threads each get one 2-action set (1 rec + 1 non-rec).
        Taking the rec 12/20 times gives rec_take_rate = 12/20 = 0.6,
        overall_take_rate = 12/40 = 0.3, lift = 0.3 — the recommender earns its badge.
        """
        for i in range(20):
            sid = nextaction.offer(
                self.db, f"th{i}", f"m{i}",
                [action(rec=True, label="rec"), action(kind="act", label="alt")],
                POLICY,
            )
            if i < 12:                      # take the recommendation 60 % of the time
                acts = nextaction.read_set(self.db, sid)["actions"]
                rec_act = next(a for a in acts if a["recommended"])
                nextaction.select(self.db, rec_act["id"])
            # For the remaining 8 sessions we leave the set un-taken.

        st = nextaction.stats(self.db)
        lift = st["overall"].get("recommendation_lift")
        self.assertIsNotNone(lift, "lift must be computable after 20 sessions")
        self.assertGreater(
            lift, 0,
            f"lift={lift!r}  rec_take={st['overall']['recommendation_take_rate']!r}  "
            f"overall_take={st['overall']['take_rate']!r}",
        )


# ── N3 — real probes against Orivellum schema ─────────────────────────────

class TestOrivellumProbes(Base):
    """N3 — one test per real Orivellum probe; each creates a minimal fixture."""

    def _probe(self, idx):
        return generate.EXAMPLE_PROBES[idx]

    def test_probe_A_source_tier_count(self):
        """Probe 0: documents still on source tier — returns the real count.
        Uses orivellum_db=self.db to simulate routing to the Orivellum database."""
        self.db.conn.executescript(
            "CREATE TABLE IF NOT EXISTS documents (id TEXT, tier TEXT);"
        )
        self.db.conn.executemany("INSERT INTO documents VALUES (?,?)", [
            (f"d{i}", "source" if i < 5 else "artifact") for i in range(8)
        ])
        self.db.conn.commit()
        facts = generate.gather_facts(self.db, [self._probe(0)], orivellum_db=self.db)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["count"], 5,
                         "must return the real count, not a hardcoded number")
        self.assertIn("5", facts[0]["anchor"])
        self.assertIn("5", facts[0]["anchor_ref"])

    def test_probe_B_batch_works_count(self):
        """Probe 1: Works titled like migration batches — uses 'title', not 'name'."""
        self.db.conn.executescript(
            "CREATE TABLE IF NOT EXISTS works (id TEXT, title TEXT);"
        )
        self.db.conn.executemany("INSERT INTO works VALUES (?,?)", [
            ("w1", "MIGRATION_BATCH_001"),
            ("w2", "MIGRATION_BATCH_002"),
            ("w3", "Normal Work Title"),
        ])
        self.db.conn.commit()
        facts = generate.gather_facts(self.db, [self._probe(1)], orivellum_db=self.db)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["count"], 2)
        self.assertIn("2", facts[0]["anchor"])

    def test_probe_C_critical_findings_count(self):
        """Probe 2: open critical findings — counts only the right severity+status."""
        self.db.conn.executescript(
            "CREATE TABLE pacing_findings (id TEXT, severity TEXT, status TEXT);"
        )
        self.db.conn.executemany("INSERT INTO pacing_findings VALUES (?,?,?)", [
            ("f1", "critical", "open"),
            ("f2", "critical", "open"),
            ("f3", "high",     "open"),     # wrong severity — excluded
            ("f4", "critical", "resolved"), # wrong status — excluded
        ])
        self.db.conn.commit()
        facts = generate.gather_facts(self.db, [self._probe(2)], orivellum_db=self.db)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["count"], 2)

    def test_probe_D_knowledge_items_awaiting_review(self):
        """Probe 3: AI-extracted knowledge waiting for review.
        Table is 'knowledge' (not knowledge_items); AI status is 'ai_auto' (not 'auto')."""
        self.db.conn.executescript(
            "CREATE TABLE knowledge (id TEXT, review_status TEXT);"
        )
        self.db.conn.executemany("INSERT INTO knowledge VALUES (?,?)", [
            ("k1", "ai_auto"),
            ("k2", "ai_auto"),
            ("k3", "auto"),     # rule-based — excluded (not the LLM-extracted status)
            ("k4", "approved"),
        ])
        self.db.conn.commit()
        facts = generate.gather_facts(self.db, [self._probe(3)], orivellum_db=self.db)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["count"], 2)

    def test_probe_E_chapters_with_no_text(self):
        """Probe 4: chapters with NULL or blank text that belong to a Work."""
        self.db.conn.executescript(
            "CREATE TABLE book_chapters (id TEXT, work_id TEXT, text TEXT);"
        )
        self.db.conn.executemany("INSERT INTO book_chapters VALUES (?,?,?)", [
            ("c1", "w1", None),           # NULL — counts
            ("c2", "w1", "   "),          # blank — counts
            ("c3", "w1", "Real text."),   # has content — excluded
            ("c4", None,  None),          # no work_id — excluded
        ])
        self.db.conn.commit()
        facts = generate.gather_facts(self.db, [self._probe(4)], orivellum_db=self.db)
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["count"], 2)

    def test_probe_F_open_gates_count(self):
        """Probe 5: clarify gates still open — runs against next.db (no orivellum_db)."""
        clarify.open_gate(self.db, "ta", "target-a", [facet()],
                          cost_units=50, policy=POLICY)
        clarify.open_gate(self.db, "tb", "target-b", [facet()],
                          cost_units=50, policy=POLICY)
        # No orivellum_db — probe F is tagged for next.db (no "db": "orivellum")
        facts = generate.gather_facts(self.db, [self._probe(5)])
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["count"], 2)

    def test_probe_drops_silently_when_table_missing(self):
        """N3 rule: a probe whose table doesn't exist in orivellum_db drops silently."""
        # pacing_findings does not exist in a fresh DB passed as orivellum_db
        facts = generate.gather_facts(self.db, [self._probe(2)], orivellum_db=self.db)
        self.assertEqual(facts, [])


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromModule(sys.modules[__name__])
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
