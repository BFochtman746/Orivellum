"""ConStory contradiction-checker tests (Masterpiece Pipeline 2.3 + B6).

Proves by assertion:
- the subtype registry is exactly the 19 ConStory-Bench subtypes across the
  five categories, and severity is COMPUTED from (subtype, canon_class) —
  factual vs HISTORICAL canon = critical, vs INVENTED = medium — never
  model-chosen (unknown inputs raise);
- on an injected-error fixture the evidence chain (grounding + closed-schema
  + verifier) discards fabricated quotes, out-of-schema subtypes, dangling
  fact refs, and verifier-rejected pairs, so stored precision > 0.85;
- every stored finding carries dual quotes whose offsets land on the REAL
  text of the chapters (chapter_text[offset:offset+len(quote)] == quote);
- canon-fact contradictions record canon_class from the DB row (not the
  model) and link canon_fact_id;
- dispositions: 'intentional' requires a note (DB ValueError + API 422),
  reopen works, and re-runs never resurrect a dispositioned finding as a
  new 'open' row (stable dedupe key);
- CED = error findings per 10,000 words, per chapter and book, excluding
  intentional/wontfix findings;
- an LLM failure raises (never a silent "0 findings") and leaves stored
  findings untouched.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orivellum.capabilities.constory import (
    SUBTYPE_CATEGORY,
    SUBTYPES,
    ConStoryLLMError,
    compute_ced,
    compute_severity,
    dedupe_key,
    release_run_claim,
    run_constory_check,
    try_claim_run,
)
from orivellum.database.canon_store import CanonStore
from orivellum.database.db import OrivellumDB, _now

# ── Fixture story ─────────────────────────────────────────────────────────────

CH1 = (
    "Mara's eyes were green as river glass. "
    "The siege of Kellhaven began in the spring of 1211. "
    "Only the king may carry iron inside the temple. "
    "Mara watched the siege of Kellhaven from the walls."
)
CH2 = (
    "Rain fell for a week. Mara's brown eyes narrowed at the messenger. "
    "He swore the siege of Kellhaven had begun in the autumn of 1212. "
    "A merchant carried a copper ring into the temple."
)
CH3 = (
    "Everyone knew the Battle of Redford was fought in 1220. "
    "Late that night Mara admitted she had never been to Kellhaven at all."
)

CANON_STATEMENT = "The Battle of Redford happened in 1214."

# Facts the extraction stub emits for chapter 1 (F0..F3 for later pairing).
CH1_FACTS = [
    {"statement": "Mara has green eyes.", "quote": "Mara's eyes were green as river glass"},
    {
        "statement": "The siege of Kellhaven began in spring 1211.",
        "quote": "The siege of Kellhaven began in the spring of 1211",
    },
    {
        "statement": "Only the king may carry iron inside the temple.",
        "quote": "Only the king may carry iron inside the temple",
    },
    {
        "statement": "Mara personally watched the siege of Kellhaven.",
        "quote": "Mara watched the siege of Kellhaven from the walls",
    },
]

# Pairing stub output for chapter 2 — 2 real contradictions + 4 that the
# evidence chain must discard (fabricated quote, out-of-schema subtype,
# dangling ref, verifier-rejected pair).
CH2_PROPOSALS = [
    {
        "fact_ref": "F0",
        "quote": "Mara's brown eyes narrowed",
        "subtype": "appearance_mismatch",
        "reasoning": "eye colour changed",
    },
    {
        "fact_ref": "F1",
        "quote": "the siege of Kellhaven had begun in the autumn of 1212",
        "subtype": "absolute_time",
        "reasoning": "season and year differ",
    },
    {
        "fact_ref": "F0",
        "quote": "The dragon burned the city",
        "subtype": "appearance_mismatch",
        "reasoning": "fabricated quote",
    },
    {
        "fact_ref": "F1",
        "quote": "Rain fell for a week",
        "subtype": "vibe_shift",
        "reasoning": "not a real subtype",
    },
    {
        "fact_ref": "F99",
        "quote": "Rain fell for a week",
        "subtype": "duration",
        "reasoning": "dangling fact ref",
    },
    {
        "fact_ref": "F2",
        "quote": "A merchant carried a copper ring into the temple",
        "subtype": "core_rules",
        "reasoning": "REJECTME copper is not iron",
    },
]

# Chapter 3 — one canon contradiction (HISTORICAL → critical) and one
# delayed-revelation contradiction the author will disposition 'intentional'.
CH3_PROPOSALS = [
    {
        "fact_ref": "C0",
        "quote": "the Battle of Redford was fought in 1220",
        "subtype": "absolute_time",
        "reasoning": "year contradicts canon",
    },
    {
        "fact_ref": "F3",
        "quote": "she had never been to Kellhaven at all",
        "subtype": "memory",
        "reasoning": "she watched the siege in chapter 1",
    },
]


class _StubLLM:
    """Dispatch llm_call by purpose against the fixture; record every call."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, messages, **kwargs):
        purpose = kwargs.get("purpose", "")
        prompt = messages[0]["content"]
        self.calls.append({"purpose": purpose, "prompt": prompt, **kwargs})
        if purpose == "constory.extract":
            payload = {"facts": CH1_FACTS} if "river glass" in prompt else {"facts": []}
        elif purpose == "constory.pair":
            if "brown eyes" in prompt:
                payload = {"contradictions": CH2_PROPOSALS}
            elif "Battle of Redford" in prompt:
                payload = {"contradictions": CH3_PROPOSALS}
            else:
                payload = {"contradictions": []}
        elif purpose == "constory.verify":
            verdict = "rejected" if "REJECTME" in prompt else "confirmed"
            payload = {"verdict": verdict, "reasoning": "checked"}
        else:
            return SimpleNamespace(ok=False, text=None, error=f"unknown purpose {purpose}")
        return SimpleNamespace(ok=True, text=json.dumps(payload), error=None)


def _cfg():
    return SimpleNamespace(
        serving=SimpleNamespace(base_url="http://test.invalid", workhorse_model="stub")
    )


def _seed_chapter(db: OrivellumDB, work_id: str, seq: int, title: str, text: str):
    oid = db._create_object("book_chapter")
    with db._lock:
        db._conn.execute(
            """INSERT INTO book_chapters(id, work_id, seq, level, title, text,
               source_doc_id, status, meta, created_at, updated_at)
               VALUES(?,?,?,1,?,?,NULL,'draft','{}',?,?)""",
            (oid, work_id, seq, title, text, _now(), _now()),
        )
        db._conn.commit()
    return oid


class ConStoryBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.work_id = self.db.create_work("The Siege Ledger", work_type="writing")["id"]
        self.ch = {
            1: _seed_chapter(self.db, self.work_id, 1, "One", CH1),
            2: _seed_chapter(self.db, self.work_id, 2, "Two", CH2),
            3: _seed_chapter(self.db, self.work_id, 3, "Three", CH3),
        }
        self.canon = CanonStore(self.db).create_fact(
            statement=CANON_STATEMENT,
            classification="HISTORICAL",
            work_id=self.work_id,
            source_ref="chronicle p.12",
            signed_by="author",
        )

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _run(self, stub=None):
        stub = stub or _StubLLM()
        with patch("orivellum.capabilities.llm.llm_call", stub):
            result = run_constory_check(self.db, _cfg(), work_id=self.work_id)
        return result, stub

    def _texts(self):
        return {1: CH1, 2: CH2, 3: CH3}


# ── Registry + severity ───────────────────────────────────────────────────────


class TestRegistryAndSeverity(unittest.TestCase):
    def test_registry_is_exactly_the_19(self):
        self.assertEqual(len(SUBTYPE_CATEGORY), 19)
        self.assertEqual(
            set(SUBTYPES),
            {
                "timeline_plot",
                "characterization",
                "worldbuilding",
                "factual_detail",
                "narrative_style",
            },
        )
        self.assertEqual(len(SUBTYPES["timeline_plot"]), 6)
        self.assertEqual(len(SUBTYPES["characterization"]), 4)
        self.assertEqual(len(SUBTYPES["worldbuilding"]), 3)
        self.assertEqual(len(SUBTYPES["factual_detail"]), 3)
        self.assertEqual(len(SUBTYPES["narrative_style"]), 3)

    def test_severity_matrix(self):
        # Spec examples: factual vs HISTORICAL = critical, vs INVENTED = medium.
        self.assertEqual(compute_severity("quantitative_mismatch", "HISTORICAL"), "critical")
        self.assertEqual(compute_severity("quantitative_mismatch", "INVENTED"), "medium")
        # ANY subtype vs HISTORICAL escalates to critical.
        self.assertEqual(compute_severity("tone_inconsistency", "HISTORICAL"), "critical")
        # INFERRED floors at high.
        self.assertEqual(compute_severity("appearance_mismatch", "INFERRED"), "high")
        # No canon involvement — base severity.
        self.assertEqual(compute_severity("causal_logic", None), "high")
        self.assertEqual(compute_severity("style_shift", None), "low")
        self.assertEqual(compute_severity("memory", None), "medium")

    def test_severity_refuses_unknowns(self):
        with self.assertRaises(ValueError):
            compute_severity("vibe_shift", None)
        with self.assertRaises(ValueError):
            compute_severity("memory", "LEGENDARY")


# ── Injected-error fixture: precision + grounding ─────────────────────────────


class TestInjectedErrors(ConStoryBase):
    def test_precision_above_085_and_all_offsets_real(self):
        result, stub = self._run()
        findings = self.db.list_narrative_findings(self.work_id)

        # 8 proposals went in; only the 4 injected contradictions survive.
        proposed = len(CH2_PROPOSALS) + len(CH3_PROPOSALS)
        self.assertEqual(proposed, 8)
        self.assertEqual(result["findings_created"], 4)
        self.assertEqual(len(findings), 4)

        injected = {
            ("appearance_mismatch", 2),
            ("absolute_time", 2),
            ("absolute_time", 3),
            ("memory", 3),
        }
        stored = {(f["subtype"], f["contradiction_chapter"]) for f in findings}
        true_positives = len(stored & injected)
        precision = true_positives / len(findings)
        self.assertGreater(precision, 0.85)
        self.assertEqual(precision, 1.0)

        # Dual evidence: every offset lands on the real text.
        texts = self._texts()
        for f in findings:
            cq, co = f["contradiction_quote"], f["contradiction_offset"]
            text = texts[f["contradiction_chapter"]]
            self.assertEqual(text[co : co + len(cq)], cq)
            if f["fact_chapter"] > 0:  # prose-vs-prose side
                fq, fo = f["fact_quote"], f["fact_offset"]
                ftext = texts[f["fact_chapter"]]
                self.assertEqual(ftext[fo : fo + len(fq)], fq)
            self.assertTrue(f["reasoning"])

    def test_canon_class_comes_from_db_not_model(self):
        self._run()
        canon_findings = [
            f for f in self.db.list_narrative_findings(self.work_id) if f["canon_class"]
        ]
        self.assertEqual(len(canon_findings), 1)
        f = canon_findings[0]
        self.assertEqual(f["canon_class"], "HISTORICAL")
        self.assertEqual(f["canon_fact_id"], self.canon["id"])
        self.assertEqual(f["severity"], "critical")  # computed, HISTORICAL
        self.assertEqual(f["fact_quote"], CANON_STATEMENT)
        self.assertEqual(f["fact_chapter"], 0)  # canon predates prose

    def test_all_calls_temperature_zero_via_gateway(self):
        _, stub = self._run()
        self.assertTrue(stub.calls)
        for call in stub.calls:
            self.assertEqual(call.get("temperature"), 0.0)
            self.assertTrue(call["purpose"].startswith("constory."))

    def test_llm_failure_raises_and_keeps_stored_findings(self):
        self._run()
        before = self.db.list_narrative_findings(self.work_id)
        self.assertEqual(len(before), 4)

        def down(messages, **kwargs):
            return SimpleNamespace(ok=False, text=None, error="gateway down")

        with (
            patch("orivellum.capabilities.llm.llm_call", down),
            self.assertRaises(ConStoryLLMError),
        ):
            run_constory_check(self.db, _cfg(), work_id=self.work_id)
        after = self.db.list_narrative_findings(self.work_id)
        self.assertEqual({f["id"] for f in after}, {f["id"] for f in before})


# ── Dispositions + re-run stability ───────────────────────────────────────────


class TestDispositions(ConStoryBase):
    def test_intentional_requires_note(self):
        self._run()
        fid = self.db.list_narrative_findings(self.work_id)[0]["id"]
        with self.assertRaises(ValueError):
            self.db.update_narrative_finding_disposition(fid, "intentional", note="  ")
        updated = self.db.update_narrative_finding_disposition(
            fid, "intentional", note="Delayed revelation — resolved in ch 9."
        )
        self.assertEqual(updated["disposition"], "intentional")
        self.assertEqual(updated["disposition_note"], "Delayed revelation — resolved in ch 9.")
        self.assertTrue(updated["disposition_at"])

    def test_invalid_disposition_refused(self):
        self._run()
        fid = self.db.list_narrative_findings(self.work_id)[0]["id"]
        with self.assertRaises(ValueError):
            self.db.update_narrative_finding_disposition(fid, "shelved")
        self.assertIsNone(self.db.update_narrative_finding_disposition("no-such-id", "fixed"))

    def test_rerun_never_resurrects_dispositioned_findings(self):
        self._run()
        memory_finding = next(
            f for f in self.db.list_narrative_findings(self.work_id) if f["subtype"] == "memory"
        )
        self.db.update_narrative_finding_disposition(
            memory_finding["id"], "intentional", note="She lied in chapter 1."
        )

        result, _ = self._run()
        findings = self.db.list_narrative_findings(self.work_id)
        # Still exactly 4 findings — the intentional one kept its row and
        # disposition; the other three were re-detected as open.
        self.assertEqual(len(findings), 4)
        kept = next(f for f in findings if f["subtype"] == "memory")
        self.assertEqual(kept["id"], memory_finding["id"])
        self.assertEqual(kept["disposition"], "intentional")
        self.assertEqual(sum(1 for f in findings if f["disposition"] == "open"), 3)
        # The re-detected duplicate was skipped, not double-inserted.
        self.assertEqual(result["findings_created"], 3)
        self.assertEqual(result["findings_kept"], 1)

    def test_reopen(self):
        self._run()
        fid = self.db.list_narrative_findings(self.work_id)[0]["id"]
        self.db.update_narrative_finding_disposition(fid, "fixed")
        reopened = self.db.update_narrative_finding_disposition(fid, "open")
        self.assertEqual(reopened["disposition"], "open")


# ── CED ───────────────────────────────────────────────────────────────────────


class TestCED(ConStoryBase):
    def test_ced_per_chapter_and_book(self):
        self._run()
        ced = compute_ced(self.db, self.work_id)
        words = {c["seq"]: c["words"] for c in ced["chapters"]}
        self.assertEqual(words[1], len(CH1.split()))
        by_seq = {c["seq"]: c for c in ced["chapters"]}
        self.assertEqual(by_seq[1]["findings"], 0)
        self.assertEqual(by_seq[2]["findings"], 2)
        self.assertEqual(by_seq[3]["findings"], 2)
        self.assertAlmostEqual(by_seq[2]["ced"], round(2 * 10_000 / words[2], 2))
        total_words = sum(words.values())
        self.assertEqual(ced["book"]["findings"], 4)
        self.assertAlmostEqual(ced["book"]["ced"], round(4 * 10_000 / total_words, 2))

    def test_ced_excludes_intentional_and_wontfix(self):
        self._run()
        findings = self.db.list_narrative_findings(self.work_id)
        memory = next(f for f in findings if f["subtype"] == "memory")
        style = next(f for f in findings if f["subtype"] == "appearance_mismatch")
        self.db.update_narrative_finding_disposition(
            memory["id"], "intentional", note="deliberate lie"
        )
        self.db.update_narrative_finding_disposition(style["id"], "wontfix")
        ced = compute_ced(self.db, self.work_id)
        self.assertEqual(ced["book"]["findings"], 2)
        # 'fixed' still counts — it WAS an error.
        remaining = next(
            f for f in self.db.list_narrative_findings(self.work_id) if f["disposition"] == "open"
        )
        self.db.update_narrative_finding_disposition(remaining["id"], "fixed")
        self.assertEqual(compute_ced(self.db, self.work_id)["book"]["findings"], 2)


# ── API routes ────────────────────────────────────────────────────────────────


class TestFindingRoutes(ConStoryBase):
    def _client(self):
        from fastapi.testclient import TestClient

        from orivellum.api import _deps
        from orivellum.api.app import app
        from orivellum.configuration.config import OrivellumConfig
        from tests.conftest import AUTH_HEADERS

        _deps.init(db=self.db, cfg=OrivellumConfig(data_dir=self._tmp.name))
        return TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)

    def test_list_filter_and_disposition_endpoint(self):
        self._run()
        client = self._client()

        resp = client.get(f"/api/works/{self.work_id}/findings")
        self.assertEqual(resp.status_code, 200)
        findings = resp.json()["findings"]
        self.assertEqual(len(findings), 4)
        # Sorted most-severe first; chapter join present.
        self.assertEqual(findings[0]["severity"], "critical")
        self.assertIn("chapter_seq", findings[0])

        resp = client.get(f"/api/works/{self.work_id}/findings?severity=critical")
        self.assertEqual(len(resp.json()["findings"]), 1)
        resp = client.get(f"/api/works/{self.work_id}/findings?category=characterization")
        self.assertEqual(len(resp.json()["findings"]), 1)

        fid = findings[0]["id"]
        # intentional without a note → 422
        resp = client.patch(
            f"/api/works/{self.work_id}/findings/{fid}",
            json={"disposition": "intentional"},
        )
        self.assertEqual(resp.status_code, 422)
        # with a note → 200
        resp = client.patch(
            f"/api/works/{self.work_id}/findings/{fid}",
            json={"disposition": "intentional", "note": "alt-history on purpose"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["finding"]["disposition"], "intentional")
        # unknown finding → 404; wrong work → 404
        resp = client.patch(
            f"/api/works/{self.work_id}/findings/nope",
            json={"disposition": "fixed"},
        )
        self.assertEqual(resp.status_code, 404)
        other = self.db.create_work("Other", work_type="writing")["id"]
        resp = client.patch(f"/api/works/{other}/findings/{fid}", json={"disposition": "fixed"})
        self.assertEqual(resp.status_code, 404)

    def test_metrics_endpoint(self):
        self._run()
        client = self._client()
        resp = client.get(f"/api/works/{self.work_id}/findings/metrics")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["counts"]["total"], 4)
        self.assertEqual(body["counts"]["by_severity"]["critical"], 1)
        self.assertEqual(body["book"]["findings"], 4)
        self.assertEqual(len(body["chapters"]), 3)


# ── Storage boundary (LAW 3 in the write path) ───────────────────────────────


class TestStorageBoundary(ConStoryBase):
    def _base_kwargs(self, **over):
        quote = "Mara's brown eyes narrowed"
        kwargs = dict(
            work_id=self.work_id,
            chapter_id=self.ch[2],
            category="factual_detail",
            subtype="appearance_mismatch",
            fact_quote="Mara's eyes were green as river glass",
            fact_chapter=1,
            fact_offset=CH1.index("Mara's eyes were green as river glass"),
            contradiction_quote=quote,
            contradiction_chapter=2,
            contradiction_offset=CH2.index(quote),
            reasoning="eye colour changed",
            dedupe_key=dedupe_key("appearance_mismatch", 1, 0, 2, CH2.index(quote)),
        )
        kwargs.update(over)
        return kwargs

    def test_grounded_insert_accepted_and_severity_computed(self):
        fid = self.db.create_narrative_finding(**self._base_kwargs())
        self.assertIsNotNone(fid)
        stored = self.db.get_narrative_finding(fid)
        # severity is computed by the write path, never supplied.
        self.assertEqual(stored["severity"], "medium")

    def test_refuses_ungrounded_contradiction_quote(self):
        with self.assertRaises(ValueError):
            self.db.create_narrative_finding(
                **self._base_kwargs(contradiction_quote="The dragon burned the city")
            )
        with self.assertRaises(ValueError):
            self.db.create_narrative_finding(
                **self._base_kwargs(contradiction_offset=3)  # wrong offset
            )

    def test_refuses_ungrounded_fact_quote(self):
        with self.assertRaises(ValueError):
            self.db.create_narrative_finding(**self._base_kwargs(fact_quote="Mara owned a falcon"))

    def test_refuses_out_of_schema_subtype_and_category_mismatch(self):
        with self.assertRaises(ValueError):
            self.db.create_narrative_finding(**self._base_kwargs(subtype="vibe_shift"))
        with self.assertRaises(ValueError):
            self.db.create_narrative_finding(**self._base_kwargs(category="worldbuilding"))

    def test_refuses_severity_kwarg(self):
        with self.assertRaises(TypeError):
            self.db.create_narrative_finding(
                **self._base_kwargs(),
                severity="low",  # noqa: B026
            )

    def test_chapter_zero_requires_canon(self):
        with self.assertRaises(ValueError):
            self.db.create_narrative_finding(**self._base_kwargs(fact_chapter=0, fact_offset=0))
        fid = self.db.create_narrative_finding(
            **self._base_kwargs(
                fact_chapter=0,
                fact_offset=0,
                fact_quote=CANON_STATEMENT,
                canon_class="HISTORICAL",
                canon_fact_id=self.canon["id"],
            )
        )
        self.assertEqual(self.db.get_narrative_finding(fid)["severity"], "critical")

    def test_wrong_work_or_seq_refused(self):
        other = self.db.create_work("Other", work_type="writing")["id"]
        with self.assertRaises(ValueError):
            self.db.create_narrative_finding(**self._base_kwargs(work_id=other))
        with self.assertRaises(ValueError):
            self.db.create_narrative_finding(**self._base_kwargs(contradiction_chapter=3))


# ── Long-chapter windowing ────────────────────────────────────────────────────


class TestWindowing(unittest.TestCase):
    """A contradiction past the 16k model-view cap is still detected."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.work_id = self.db.create_work("Long One", work_type="writing")["id"]
        filler = ("The caravan crossed the dunes without incident. " * 400)[:18_000]
        self.late_quote = "Mara's brown eyes narrowed at the horizon"
        self.long_ch2 = filler + " " + self.late_quote + "."
        _seed_chapter(self.db, self.work_id, 1, "One", CH1)
        _seed_chapter(self.db, self.work_id, 2, "Two", self.long_ch2)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def test_finding_past_16k_offset(self):
        late_quote = self.late_quote

        def stub(messages, **kwargs):
            purpose = kwargs.get("purpose", "")
            prompt = messages[0]["content"]
            if purpose == "constory.extract":
                payload = {"facts": CH1_FACTS} if "river glass" in prompt else {"facts": []}
            elif purpose == "constory.pair":
                if "brown eyes narrowed at the horizon" in prompt:
                    payload = {
                        "contradictions": [
                            {
                                "fact_ref": "F0",
                                "quote": late_quote,
                                "subtype": "appearance_mismatch",
                                "reasoning": "eye colour changed",
                            }
                        ]
                    }
                else:
                    payload = {"contradictions": []}
            elif purpose == "constory.verify":
                payload = {"verdict": "confirmed", "reasoning": "checked"}
            else:
                return SimpleNamespace(ok=False, text=None, error="?")
            return SimpleNamespace(ok=True, text=json.dumps(payload), error=None)

        with patch("orivellum.capabilities.llm.llm_call", stub):
            result = run_constory_check(self.db, _cfg(), work_id=self.work_id)
        self.assertEqual(result["findings_created"], 1)
        f = self.db.list_narrative_findings(self.work_id)[0]
        self.assertGreater(f["contradiction_offset"], 16_000)
        self.assertEqual(
            self.long_ch2[
                f["contradiction_offset"] : f["contradiction_offset"]
                + len(f["contradiction_quote"])
            ],
            f["contradiction_quote"],
        )


# ── Run claim ─────────────────────────────────────────────────────────────────


class TestRunClaim(unittest.TestCase):
    def test_claim_refuse_release(self):
        wid = "claim-test-work"
        self.assertTrue(try_claim_run(wid))
        self.assertFalse(try_claim_run(wid))  # second claim refused
        release_run_claim(wid, error="executor unavailable")
        self.assertTrue(try_claim_run(wid))  # released -> claimable
        release_run_claim(wid)


if __name__ == "__main__":
    unittest.main()
