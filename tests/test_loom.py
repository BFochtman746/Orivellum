"""LOOM drafting engine tests (Masterpiece Pipeline B5, E2).

Proves by assertion:
- the acceptance fixture: three consecutive chapters drafted on a synthetic
  work, each within its contracted word range, each conditioned on the TRUE
  accumulated world state (chapter 2's prompts contain chapter 1's committed
  updates and its closing passage verbatim), provenance recorded with every
  llm_call id as ai_generated;
- the critic is NEVER skipped: zero critic-accepted actions → escalated run,
  a governance finding, and NO draft stored;
- the drafting model never judges its own output (same model → refusal);
- approved chapters are never overwritten;
- personas are review-gated: drafting refuses on missing/unapproved personas,
  resolution needs an author signature and is an atomic claim;
- knowledge horizons restrict what each character agent is told it knows;
- beat compliance controller: word-band and beat drift escalate as findings,
  never rewrite the story;
- entropy gate: high -logprob spans get targeted verification (failures →
  findings); absent logprobs are reported as available:false, never fabricated;
- only the narrator-SELECTED actions' world updates are committed (overwrite
  semantics);
- resuming mid-book replays the world graph forward so chapter N+1 sees the
  true state of 1..N;
- the run row is the claim: double-dispatch refused, gateway failure finishes
  the row as 'error' — never a leaked 'running' row.
"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orivellum.capabilities import loom
from orivellum.database.db import OrivellumDB, _now


def _cfg(same_model=False):
    return SimpleNamespace(
        serving=SimpleNamespace(
            base_url="http://test.invalid",
            workhorse_model="drafter-model",
            reasoner_model="drafter-model" if same_model else "judge-model",
        )
    )


class StubLLM:
    """Dispatch llm_call by purpose; records every prompt for conditioning
    assertions.  call_id/logprobs mirror the extended LLMResult contract."""

    def __init__(self, *, reject_all=False, narrator_logprobs=None, verify_ok=True,
                 beat_ok=True, prose_words=200, narrator_selected=None,
                 down_purposes=()):
        self.calls: list[dict] = []
        self.reject_all = reject_all
        self.narrator_logprobs = narrator_logprobs
        self.verify_ok = verify_ok
        self.beat_ok = beat_ok
        self.prose_words = prose_words
        self.narrator_selected = narrator_selected
        self.down_purposes = set(down_purposes)
        self.narr = 0

    def prompts(self, purpose):
        return [c["user"] for c in self.calls if c["purpose"] == purpose]

    def __call__(self, messages, **kwargs):
        purpose = kwargs.get("purpose", "")
        user = messages[-1]["content"]
        self.calls.append({"purpose": purpose, "user": user,
                           "model": kwargs.get("model")})

        def ok(payload, logprobs=None):
            return SimpleNamespace(ok=True, text=json.dumps(payload), error=None,
                                   call_id=len(self.calls), logprobs=logprobs)

        if purpose in self.down_purposes:
            return SimpleNamespace(ok=False, text=None, error="gateway down",
                                   call_id=None, logprobs=None)
        if purpose == "loom.agent.action":
            return ok({"action": f"advances the beat (call {len(self.calls)})",
                       "motivation": "duty", "dialogue": ""})
        if purpose == "loom.critic.action":
            if self.reject_all:
                return ok({"accept": False, "feedback": "too vague — be concrete"})
            m = re.search(r"CHARACTER: (\w+)", user)
            name = m.group(1) if m else "X"
            return ok({"accept": True, "feedback": "",
                       "world_updates": {f"Character:{name}":
                                         f"acted in call {len(self.calls)}"}})
        if purpose == "loom.narrator":
            self.narr += 1
            prose = (f"PROSE-{self.narr} "
                     + "the caravan rolled east through the wet grey fields "
                     * max(1, self.prose_words // 8)).strip()
            return ok({"selected": self.narrator_selected, "prose": prose},
                      logprobs=self.narrator_logprobs)
        if purpose == "loom.critic.beat":
            return ok({"accomplishes_beat": self.beat_ok, "premature_reveal": False,
                       "feedback": "" if self.beat_ok else "prose drifted off the beat"})
        if purpose == "loom.entropy.verify":
            return ok({"ok": self.verify_ok,
                       "issue": "" if self.verify_ok else "contradicts canon"})
        return SimpleNamespace(ok=False, text=None, error=f"unknown purpose {purpose}",
                               call_id=None, logprobs=None)


def _hot_logprobs(n=60, nll=5.0):
    return [{"token": f" tok{i}", "logprob": -nll} for i in range(n)]


class LoomBase(unittest.TestCase):
    CAST = ["Mara", "Tobin"]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.work_id = self.db.create_work("Drafted Book", work_type="writing")["id"]
        for name in self.CAST:
            self._persona(name)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _persona(self, name, horizon=None, approve=True):
        pid = self.db.create_loom_persona(self.work_id, name, {
            "role": "traveler", "diction_profile": {"register": "plain"},
            "knowledge_horizon": horizon or {},
        })
        if approve:
            self.assertEqual(
                self.db.resolve_loom_persona(pid, decision="approved", author="Brian"),
                "ok")
        return pid

    def _contract(self, seq, **over):
        c = {"beat": f"Beat for chapter {seq}", "act": 1, "cast": list(self.CAST),
             "location": "the yard", "word_range": [50, 400],
             "must_not_reveal": ["the twist"]}
        c.update(over)
        return c

    def _seed_chapter(self, seq, contract="default", status="draft", text=None):
        oid = self.db._create_object("book_chapter")
        meta = {}
        if contract == "default":
            meta["contract"] = self._contract(seq)
        elif contract is not None:
            meta["contract"] = contract
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO book_chapters(id, work_id, seq, level, title, text,
                   source_doc_id, status, meta, created_at, updated_at)
                   VALUES(?,?,?,1,?,?,NULL,?,?,?,?)""",
                (oid, self.work_id, seq, f"Chapter {seq}", text, status,
                 json.dumps(meta), _now(), _now()),
            )
            self.db._conn.commit()
        return oid

    def _seed_fact(self, statement, cls="INVENTED"):
        fid = str(uuid.uuid4())
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO canon_fact(id, work_id, statement, classification,
                   created_at) VALUES(?,?,?,?,?)""",
                (fid, self.work_id, statement, cls, _now()),
            )
            self.db._conn.commit()
        return fid

    def _seed_node(self, chapter_id, node_type, name, description, attrs=None):
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO graph_node(id, work_id, chapter_id, node_type, name,
                   description, evidence_quote, evidence_offset, attributes,
                   created_at) VALUES(?,?,?,?,?,?,'quoted evidence',0,?,?)""",
                (str(uuid.uuid4()), self.work_id, chapter_id, node_type, name,
                 description, json.dumps(attrs or {}), _now()),
            )
            self.db._conn.commit()

    def _draft(self, chapter_id, stub=None, cfg=None):
        stub = stub or StubLLM()
        run_id = self.db.create_loom_run(self.work_id, chapter_id)
        with patch("orivellum.capabilities.llm.llm_call", stub):
            try:
                result = loom.run_loom_draft(
                    self.db, cfg or _cfg(),
                    run_id=run_id, work_id=self.work_id, chapter_id=chapter_id)
            except Exception as exc:
                return run_id, exc, stub
        return run_id, result, stub

    def _findings(self):
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT * FROM findings WHERE kind='loom_escalation'").fetchall()
        return [dict(r) for r in rows]


# ── Acceptance fixture: three consecutive chapters ───────────────────────────


class TestThreeChapterAcceptance(LoomBase):
    def test_three_chapters_conditioned_on_true_state(self):
        chs = [self._seed_chapter(seq) for seq in (1, 2, 3)]
        stubs = []
        for cid in chs:
            run_id, result, stub = self._draft(cid)
            stubs.append(stub)
            self.assertNotIsInstance(result, Exception)
            self.assertEqual(result["status"], "done", result.get("reason"))
            run = self.db.get_loom_run(run_id)
            self.assertEqual(run["status"], "done")

        # Each chapter within its contracted word range, as a new revision.
        for i, cid in enumerate(chs, start=1):
            revs = self.db.list_chapter_revisions(cid)
            self.assertEqual(len(revs), 1)
            self.assertTrue(50 <= revs[i - 1 if False else 0]["word_count"] <= 400)
            # Provenance recorded with the llm_call audit trail, ai_generated.
            prov = self.db.get_provenance(revs[0]["id"], "chapter_revision")
            self.assertIsNotNone(prov)
            self.assertEqual(prov["origin"], "ai_generated")
            self.assertGreater(len(prov["llm_call_ids"]), 0)
            self.assertIsNotNone(self.db.get_provenance(cid, "book_chapter"))

        # Chapter 2 was conditioned on chapter 1's TRUE committed state …
        ch2_agent_prompt = stubs[1].prompts("loom.agent.action")[0]
        self.assertIn("Character:Mara", ch2_agent_prompt)
        self.assertIn("(as of ch 1)", ch2_agent_prompt)
        # … and on chapter 1's closing passage verbatim.
        ch1_text = dict(self.db._conn.execute(
            "SELECT text FROM book_chapters WHERE id=?", (chs[0],)).fetchone())["text"]
        ch2_narr_prompt = stubs[1].prompts("loom.narrator")[0]
        self.assertIn(ch1_text[-200:], ch2_narr_prompt)
        self.assertIn("PROSE-1", ch2_narr_prompt)
        # Chapter 3 sees state accumulated through chapter 2 (overwrite: the
        # newest chapter's update wins for the same key).
        state = self.db.get_world_state(self.work_id)
        self.assertEqual(state["Character:Mara"]["source_chapter_seq"], 3)
        ch3_prompt = stubs[2].prompts("loom.agent.action")[0]
        self.assertIn("(as of ch 2)", ch3_prompt)

    def test_word_band_violation_escalates_but_never_rewrites(self):
        cid = self._seed_chapter(1)
        stub = StubLLM(prose_words=8)  # far below the 50-word floor
        _run_id, result, _ = self._draft(cid, stub)
        self.assertEqual(result["status"], "escalated")
        self.assertIn("word count", result["reason"])
        # The draft is still stored (a finding escalates; nothing rewrites).
        self.assertEqual(len(self.db.list_chapter_revisions(cid)), 1)
        self.assertTrue(any("word count" in f["description"] for f in self._findings()))

    def test_beat_drift_escalates(self):
        cid = self._seed_chapter(1)
        _run_id, result, _ = self._draft(cid, StubLLM(beat_ok=False))
        self.assertEqual(result["status"], "escalated")
        self.assertTrue(any("beat drift" in f["description"] for f in self._findings()))


# ── Critic gate ───────────────────────────────────────────────────────────────


class TestCriticGate(LoomBase):
    def test_zero_accepted_actions_escalates_without_a_draft(self):
        cid = self._seed_chapter(1)
        run_id, result, stub = self._draft(cid, StubLLM(reject_all=True))
        self.assertEqual(result["status"], "escalated")
        self.assertEqual(self.db.get_loom_run(run_id)["status"], "escalated")
        self.assertEqual(self.db.list_chapter_revisions(cid), [])
        self.assertEqual(stub.prompts("loom.narrator"), [])  # narrator never ran
        # Bounded retries: each of 2 cast members got exactly 3 attempts.
        self.assertEqual(len(stub.prompts("loom.agent.action")), 6)
        self.assertEqual(len(stub.prompts("loom.critic.action")), 6)
        self.assertTrue(any("beat stall" in f["description"] for f in self._findings()))

    def test_every_accepted_action_passed_the_critic(self):
        cid = self._seed_chapter(1)
        _run_id, result, stub = self._draft(cid)
        self.assertEqual(len(stub.prompts("loom.critic.action")),
                         len(stub.prompts("loom.agent.action")))
        self.assertEqual(len(result["evidence"]["accepted_actions"]), 2)

    def test_drafter_never_judges_its_own_output(self):
        cid = self._seed_chapter(1)
        run_id, result, _ = self._draft(cid, cfg=_cfg(same_model=True))
        self.assertIsInstance(result, loom.LoomError)
        self.assertIn("never judge its own output", str(result))
        self.assertEqual(self.db.get_loom_run(run_id)["status"], "error")

    def test_critic_and_drafter_use_distinct_models(self):
        cid = self._seed_chapter(1)
        _run_id, _result, stub = self._draft(cid)
        models = {c["purpose"]: c["model"] for c in stub.calls}
        self.assertEqual(models["loom.agent.action"], "drafter-model")
        self.assertEqual(models["loom.critic.action"], "judge-model")
        self.assertEqual(models["loom.critic.beat"], "judge-model")


# ── Refusals ──────────────────────────────────────────────────────────────────


class TestRefusals(LoomBase):
    def test_approved_chapter_is_never_overwritten(self):
        cid = self._seed_chapter(1, status="approved", text="the sacred text")
        run_id, result, _ = self._draft(cid)
        self.assertIsInstance(result, loom.LoomError)
        self.assertIn("never overwritten", str(result))
        self.assertEqual(self.db.get_loom_run(run_id)["status"], "error")
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT text FROM book_chapters WHERE id=?", (cid,)).fetchone()
        self.assertEqual(row["text"], "the sacred text")

    def test_no_contract_refused(self):
        cid = self._seed_chapter(1, contract=None)
        _run_id, result, _ = self._draft(cid)
        self.assertIsInstance(result, loom.LoomError)
        self.assertIn("no contract", str(result))

    def test_missing_word_range_refused(self):
        cid = self._seed_chapter(1, contract=self._contract(1, word_range=None))
        _run_id, result, _ = self._draft(cid)
        self.assertIsInstance(result, loom.LoomError)
        self.assertIn("word_range", str(result))

    def test_unapproved_persona_refuses_drafting(self):
        contract = self._contract(1, cast=["Mara", "Ghost"])
        self._persona("Ghost", approve=False)  # proposed, never approved
        cid = self._seed_chapter(1, contract=contract)
        _run_id, result, _ = self._draft(cid)
        self.assertIsInstance(result, loom.LoomError)
        self.assertIn("Ghost", str(result))

    def test_gateway_failure_finishes_run_as_error(self):
        cid = self._seed_chapter(1)
        run_id, result, _ = self._draft(
            cid, StubLLM(down_purposes={"loom.agent.action"}))
        self.assertIsInstance(result, loom.LoomError)
        run = self.db.get_loom_run(run_id)
        self.assertEqual(run["status"], "error")
        self.assertIn("gateway", run["error"])


# ── Knowledge horizons ────────────────────────────────────────────────────────


class TestKnowledgeHorizon(LoomBase):
    def test_agent_sees_only_facts_inside_its_horizon(self):
        f1 = self._seed_fact("The well water is poisoned at the source.")
        f2 = self._seed_fact("The duke secretly funds the bandits himself.")
        # Rebuild Mara: knows f1 from act 1; f2 only unlocks at act 2.
        with self.db._lock:
            self.db._conn.execute(
                "DELETE FROM loom_persona WHERE work_id=? AND name='Mara'",
                (self.work_id,))
            self.db._conn.commit()
        self._persona("Mara", horizon={"1": [f1], "2": [f2]})
        cid = self._seed_chapter(1, contract=self._contract(1, act=1))
        _run_id, result, stub = self._draft(cid)
        self.assertNotIsInstance(result, Exception)
        mara_prompt = next(p for p in stub.prompts("loom.agent.action")
                           if "YOUR CHARACTER:\nMara" in p
                           or "YOUR CHARACTER: Mara" in p)
        self.assertIn("well water is poisoned", mara_prompt)
        self.assertNotIn("duke secretly funds", mara_prompt)
        self.assertEqual(result["evidence"]["horizon_map"]["Mara"], [f1])


# ── Entropy gate ──────────────────────────────────────────────────────────────


class TestEntropyGate(LoomBase):
    def test_hot_spans_get_targeted_verification_before_storing(self):
        cid = self._seed_chapter(1)
        stub = StubLLM(narrator_logprobs=_hot_logprobs(), verify_ok=False)
        _run_id, result, stub = self._draft(cid, stub)
        entropy = result["evidence"]["entropy"]
        self.assertTrue(entropy["available"])
        self.assertGreater(len(entropy["spans"]), 0)
        self.assertGreater(len(stub.prompts("loom.entropy.verify")), 0)
        self.assertFalse(entropy["verification"][0]["verified"])
        self.assertTrue(any("entropy gate" in f["description"]
                            for f in self._findings()))

    def test_absent_logprobs_reported_never_fabricated(self):
        cid = self._seed_chapter(1)
        _run_id, result, stub = self._draft(cid)  # stub returns logprobs=None
        entropy = result["evidence"]["entropy"]
        self.assertEqual(entropy, {"available": False, "spans": []})
        self.assertEqual(stub.prompts("loom.entropy.verify"), [])

    def test_low_entropy_prose_passes_without_verification(self):
        cid = self._seed_chapter(1)
        calm = [{"token": " t", "logprob": -0.1} for _ in range(60)]
        _run_id, result, stub = self._draft(cid, StubLLM(narrator_logprobs=calm))
        self.assertTrue(result["evidence"]["entropy"]["available"])
        self.assertEqual(result["evidence"]["entropy"]["spans"], [])
        self.assertEqual(stub.prompts("loom.entropy.verify"), [])


# ── World state ───────────────────────────────────────────────────────────────


class TestWorldState(LoomBase):
    def test_only_selected_actions_updates_are_committed(self):
        cid = self._seed_chapter(1)
        _run_id, result, _ = self._draft(cid, StubLLM(narrator_selected=[0]))
        self.assertEqual(len(result["evidence"]["accepted_actions"]), 2)
        self.assertEqual(len(result["evidence"]["selected_actions"]), 1)
        state = self.db.get_world_state(self.work_id)
        self.assertEqual(len(state), 1)  # only the selected character's update

    def test_overwrite_semantics(self):
        self.db.commit_world_state(self.work_id, {"Character:Mara": "at the yard"},
                                   source_chapter_seq=1)
        self.db.commit_world_state(
            self.work_id,
            {"Character:Mara": "on the road", "Object:Gate": "open"},
            source_chapter_seq=2)
        state = self.db.get_world_state(self.work_id)
        self.assertEqual(state["Character:Mara"],
                         {"value": "on the road", "source_chapter_seq": 2})
        self.assertEqual(len(state), 2)

    def test_replay_mid_book_folds_graph_forward_in_order(self):
        ch1 = self._seed_chapter(1, text="chapter one prose")
        ch2 = self._seed_chapter(2, text="chapter two prose")
        self._seed_node(ch1, "Character", "Mara", "leaves the yard")
        self._seed_node(ch2, "Character", "Mara", "reaches the ford",
                        attrs={"mood": "grim"})
        self._seed_node(ch2, "Location", "Ford", "swollen by rain")
        # Stale state that MUST be discarded by the replay.
        self.db.commit_world_state(self.work_id, {"Character:Mara": "STALE"},
                                   source_chapter_seq=9)
        report = loom.replay_world_state(self.db, self.work_id, upto_seq=3)
        self.assertEqual(report["folded_nodes"], 3)
        state = self.db.get_world_state(self.work_id)
        self.assertEqual(state["Character:Mara"]["value"],
                         "reaches the ford; mood=grim")
        self.assertEqual(state["Character:Mara"]["source_chapter_seq"], 2)
        self.assertIn("Location:Ford", state)
        # upto_seq=2 sees only chapter 1's state.
        loom.replay_world_state(self.db, self.work_id, upto_seq=2)
        self.assertEqual(self.db.get_world_state(self.work_id)
                         ["Character:Mara"]["value"], "leaves the yard")

    def test_resume_auto_replays_when_state_is_empty(self):
        ch1 = self._seed_chapter(1, text="chapter one prose ends at the gate")
        self._seed_node(ch1, "Character", "Tobin", "wounded at the gate")
        cid2 = self._seed_chapter(2)
        _run_id, result, stub = self._draft(cid2)
        self.assertNotIsInstance(result, Exception)
        self.assertEqual(result["evidence"]["replay"]["folded_nodes"], 1)
        self.assertIn("wounded at the gate", stub.prompts("loom.agent.action")[0])


# ── Run claim + persona gate ──────────────────────────────────────────────────


class TestRunClaimAndPersonaGate(LoomBase):
    def test_run_row_is_the_claim(self):
        cid = self._seed_chapter(1)
        run_id = self.db.create_loom_run(self.work_id, cid)
        with self.assertRaises(RuntimeError):
            self.db.create_loom_run(self.work_id, cid)
        self.db.finish_loom_run(run_id, status="error", error="test")
        self.db.create_loom_run(self.work_id, cid)  # claim released

    def test_persona_resolution_is_an_atomic_claim_with_signature(self):
        pid = self.db.create_loom_persona(self.work_id, "Vex", {})
        with self.assertRaises(ValueError):
            self.db.resolve_loom_persona(pid, decision="approved", author="  ")
        self.assertEqual(
            self.db.resolve_loom_persona(pid, decision="approved", author="Brian"),
            "ok")
        self.assertEqual(
            self.db.resolve_loom_persona(pid, decision="rejected", author="Brian"),
            "conflict")
        self.assertEqual(
            self.db.resolve_loom_persona("nope", decision="approved", author="B"),
            "not_found")

    def test_duplicate_persona_name_refused(self):
        self.db.create_loom_persona(self.work_id, "Vex", {})
        with self.assertRaises(ValueError):
            self.db.create_loom_persona(self.work_id, "Vex", {})

    def test_provenance_merges_call_ids(self):
        self.db.record_provenance("a1", "chapter_revision", origin="ai_generated",
                                  llm_call_ids=[1, 2], declared_by="loom")
        self.db.record_provenance("a1", "chapter_revision", origin="ai_generated",
                                  llm_call_ids=[2, 3], declared_by="loom")
        prov = self.db.get_provenance("a1", "chapter_revision")
        self.assertEqual(prov["llm_call_ids"], [1, 2, 3])
        with self.assertRaises(ValueError):
            self.db.record_provenance("a1", "chapter_revision", origin="magic")


if __name__ == "__main__":
    unittest.main()
