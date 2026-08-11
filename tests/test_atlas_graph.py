"""ATLAS-O world graph tests (LAW 2 + LAW 3).

Proves by assertion:
- closed schema: out-of-schema node/edge types are discarded, not coerced,
  and the DB write path refuses them outright;
- LAW 3 grounding: every stored node/edge/inconsistency carries a verbatim
  quote whose stored offset is where it actually appears; ungroundable
  extractor output is discarded;
- three extraction passes + attribute pass all run at temperature 0.0
  through the llm_call gateway;
- cross-chapter verification is propose-then-verify: fabricated quotes are
  discarded by deterministic grounding, grounded-but-unconfirmed proposals
  are discarded by the verifier, and on a fixture with 10 injected
  contradictions exactly those 10 survive with correct offsets;
- canon linkage: a node instantiating a sealed canon fact links to it;
- multi-work scoping: one work's graph never leaks into another's;
- the chapter-harvest flow feeds the graph (and respects the atlas_enabled
  gate) rather than growing a parallel entity store.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orivellum.capabilities.atlas import (
    build_work_graph,
    extract_chapter_graph,
    ground_quote,
    verify_chapter,
)
from orivellum.database.db import OrivellumDB, _now


def _uid() -> str:
    return str(uuid.uuid4())


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        serving=SimpleNamespace(base_url="http://test.invalid", workhorse_model="stub")
    )


def _seed_chapter(db: OrivellumDB, work_id: str, seq: int, title: str, text: str, doc_id=None):
    oid = db._create_object("book_chapter")
    with db._lock:
        db._conn.execute(
            """INSERT INTO book_chapters(id, work_id, seq, level, title, text,
               source_doc_id, status, meta, created_at, updated_at)
               VALUES(?,?,?,1,?,?,?,'draft','{}',?,?)""",
            (oid, work_id, seq, title, text, doc_id, _now(), _now()),
        )
        db._conn.commit()
    return oid


class _StubLLM:
    """Dispatches llm_call by purpose; records every call for assertions."""

    def __init__(self, responses: dict):
        # purpose -> payload (object to be JSON-encoded) OR callable(prompt)->object
        self.responses = responses
        self.calls: list[dict] = []

    def __call__(self, messages, **kwargs):
        purpose = kwargs.get("purpose", "")
        self.calls.append({"purpose": purpose, **kwargs, "prompt": messages[0]["content"]})
        handler = self.responses.get(purpose)
        payload = handler(messages[0]["content"]) if callable(handler) else handler
        if payload is None:
            return SimpleNamespace(ok=False, text=None, error="no stub")
        return SimpleNamespace(ok=True, text=json.dumps(payload), error=None)


class AtlasBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.work_id = self.db.create_work("Ash and Silence", work_type="book")["id"]

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


class GroundingTests(unittest.TestCase):
    TEXT = "The storm came out of the east at dawn.\nJob   tore his robe\tand wept."

    def test_exact_match_offset(self):
        q = "storm came out of the east"
        self.assertEqual(ground_quote(q, self.TEXT), self.TEXT.find(q))

    def test_normalised_whitespace_and_case(self):
        off = ground_quote("job tore his robe and wept", self.TEXT)
        self.assertEqual(off, self.TEXT.find("Job"))

    def test_fabricated_quote_is_none(self):
        self.assertIsNone(ground_quote("Behemoth drinks the Jordan", self.TEXT))
        self.assertIsNone(ground_quote("", self.TEXT))


# ---------------------------------------------------------------------------
# DB write-path guards (closed schema even if atlas.py is bypassed)
# ---------------------------------------------------------------------------


class DbGuardTests(AtlasBase):
    def test_bad_node_type_refused(self):
        with self.assertRaises(ValueError):
            self.db.create_graph_node(
                work_id=self.work_id, chapter_id=None, node_type="Deity",
                name="X", evidence_quote="q", evidence_offset=0,
            )

    def test_missing_evidence_refused(self):
        with self.assertRaises(ValueError):
            self.db.create_graph_node(
                work_id=self.work_id, chapter_id=None, node_type="Character",
                name="Job", evidence_quote="  ", evidence_offset=0,
            )
        with self.assertRaises(ValueError):
            self.db.create_graph_node(
                work_id=self.work_id, chapter_id=None, node_type="Character",
                name="Job", evidence_quote="q", evidence_offset=-1,
            )

    def test_bad_edge_type_refused(self):
        a = self.db.create_graph_node(
            work_id=self.work_id, chapter_id=None, node_type="Character",
            name="Job", evidence_quote="q", evidence_offset=0,
        )
        b = self.db.create_graph_node(
            work_id=self.work_id, chapter_id=None, node_type="Event",
            name="The storm", evidence_quote="q", evidence_offset=0,
        )
        with self.assertRaises(ValueError):
            self.db.create_graph_edge(
                work_id=self.work_id, chapter_id=None, src=a, dst=b,
                edge_type="loves", evidence_quote="q", evidence_offset=0,
            )

    def test_edge_group_derived_from_type(self):
        a = self.db.create_graph_node(
            work_id=self.work_id, chapter_id=None, node_type="Character",
            name="Job", evidence_quote="q", evidence_offset=0,
        )
        b = self.db.create_graph_node(
            work_id=self.work_id, chapter_id=None, node_type="Event",
            name="The storm", evidence_quote="q", evidence_offset=0,
        )
        self.db.create_graph_edge(
            work_id=self.work_id, chapter_id=None, src=a, dst=b,
            edge_type="undergoes", evidence_quote="q", evidence_offset=3,
        )
        edges = self.db.list_graph_edges(work_ids=[self.work_id])
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["edge_group"], "event_role")

    def test_inconsistency_requires_both_sides(self):
        ch = _seed_chapter(self.db, self.work_id, 0, "One", "text")
        with self.assertRaises(ValueError):
            self.db.create_graph_inconsistency(
                work_id=self.work_id, chapter_id=ch, description="d",
                current_quote="a", current_offset=0,
                prior_chapter_id=ch, prior_quote="", prior_offset=0,
            )


# ---------------------------------------------------------------------------
# Extraction: three passes + attribute pass, discard-not-coerce
# ---------------------------------------------------------------------------

_CH_TEXT = (
    "Job of Uz rose before dawn. The storm came out of the east and struck "
    "the eldest son's house. Job tore his robe and fell to the ground. "
    "Eliphaz watched from the ridge above the river."
)


class ExtractionTests(AtlasBase):
    def _run(self, stub: _StubLLM) -> tuple[str, dict]:
        ch = _seed_chapter(self.db, self.work_id, 0, "The Storm", _CH_TEXT)
        with patch("orivellum.capabilities.llm.llm_call", stub):
            counts = extract_chapter_graph(
                self.db, _cfg(), work_id=self.work_id,
                chapter={"id": ch, "seq": 0, "title": "The Storm", "text": _CH_TEXT},
            )
        return ch, counts

    def _stub(self) -> _StubLLM:
        return _StubLLM(
            {
                "atlas.events": [
                    {
                        "name": "The storm strikes",
                        "description": "A storm destroys the house.",
                        "evidence_quote": "The storm came out of the east and struck",
                    },
                    {   # fabricated quote — must be discarded
                        "name": "Angels sing",
                        "description": "…",
                        "evidence_quote": "The angels sang above the whirlwind",
                    },
                ],
                "atlas.entities": [
                    {
                        "name": "Job of Uz",
                        "node_type": "Character",
                        "description": "The protagonist.",
                        "evidence_quote": "Job of Uz rose before dawn",
                    },
                    {   # out-of-schema type — discarded, not coerced
                        "name": "The Accuser",
                        "node_type": "Deity",
                        "description": "…",
                        "evidence_quote": "Job tore his robe",
                    },
                    {   # duplicate of pass-1/2 name — discarded
                        "name": "Job of Uz",
                        "node_type": "Character",
                        "description": "dup",
                        "evidence_quote": "Job tore his robe",
                    },
                    {   # ungroundable — discarded
                        "name": "Bildad",
                        "node_type": "Character",
                        "description": "…",
                        "evidence_quote": "Bildad the Shuhite answered",
                    },
                ],
                "atlas.relations": [
                    {
                        "src": "Job of Uz",
                        "dst": "The storm strikes",
                        "edge_type": "experiences",
                        "evidence_quote": "Job tore his robe and fell to the ground",
                    },
                    {   # out-of-schema edge type — discarded
                        "src": "Job of Uz",
                        "dst": "The storm strikes",
                        "edge_type": "loves",
                        "evidence_quote": "Job tore his robe",
                    },
                    {   # unknown endpoint — discarded
                        "src": "Zophar",
                        "dst": "The storm strikes",
                        "edge_type": "performs",
                        "evidence_quote": "Job tore his robe",
                    },
                ],
                "atlas.attributes": {
                    "Job of Uz": {"role": "protagonist", "state": "grieving"},
                    "Nobody": {"role": "ghost"},
                },
            }
        )

    def test_passes_store_grounded_and_discard_rest(self):
        stub = self._stub()
        ch, counts = self._run(stub)

        nodes = self.db.list_graph_nodes(work_ids=[self.work_id])
        self.assertEqual({n["name"] for n in nodes}, {"The storm strikes", "Job of Uz"})
        self.assertEqual(counts["nodes"], 2)
        self.assertEqual(counts["edges"], 1)
        # 1 fabricated event + Deity + ungroundable + 2 bad relations.
        # (The duplicate name is skipped silently — not a schema rejection.)
        self.assertEqual(counts["discarded"], 5)

        # LAW 3: offsets are where the quotes actually are.
        for n in nodes:
            self.assertEqual(n["evidence_offset"], _CH_TEXT.find(n["evidence_quote"]))
        edge = self.db.list_graph_edges(work_ids=[self.work_id])[0]
        self.assertEqual(edge["evidence_offset"], _CH_TEXT.find(edge["evidence_quote"]))
        self.assertEqual(edge["edge_type"], "experiences")

        # Attribute pass applied to the real node only.
        job = next(n for n in nodes if n["name"] == "Job of Uz")
        self.assertEqual(job["attributes"], {"role": "protagonist", "state": "grieving"})

    def test_all_calls_temperature_zero_via_gateway(self):
        stub = self._stub()
        self._run(stub)
        purposes = [c["purpose"] for c in stub.calls]
        self.assertEqual(
            purposes,
            ["atlas.events", "atlas.entities", "atlas.relations", "atlas.attributes"],
        )
        for c in stub.calls:
            self.assertEqual(c.get("temperature"), 0.0)

    def test_reextraction_is_idempotent(self):
        stub = self._stub()
        ch, _ = self._run(stub)
        with patch("orivellum.capabilities.llm.llm_call", self._stub()):
            extract_chapter_graph(
                self.db, _cfg(), work_id=self.work_id,
                chapter={"id": ch, "seq": 0, "title": "The Storm", "text": _CH_TEXT},
            )
        self.assertEqual(len(self.db.list_graph_nodes(work_ids=[self.work_id])), 2)
        self.assertEqual(len(self.db.list_graph_edges(work_ids=[self.work_id])), 1)


class OffsetIntegrityTests(AtlasBase):
    def test_leading_whitespace_never_shifts_offsets(self):
        """Offsets index into the text exactly as stored (no strip)."""
        raw_text = "\n\n   " + _CH_TEXT
        stub = _StubLLM(
            {
                "atlas.events": [],
                "atlas.entities": [
                    {
                        "name": "Job of Uz",
                        "node_type": "Character",
                        "description": "…",
                        "evidence_quote": "Job of Uz rose before dawn",
                    }
                ],
                "atlas.relations": [],
                "atlas.attributes": {},
            }
        )
        ch = _seed_chapter(self.db, self.work_id, 0, "One", raw_text)
        with patch("orivellum.capabilities.llm.llm_call", stub):
            extract_chapter_graph(
                self.db, _cfg(), work_id=self.work_id,
                chapter={"id": ch, "seq": 0, "title": "One", "text": raw_text},
            )
        node = self.db.list_graph_nodes(work_ids=[self.work_id])[0]
        self.assertEqual(node["evidence_offset"], raw_text.find(node["evidence_quote"]))
        self.assertEqual(
            raw_text[node["evidence_offset"] :][: len(node["evidence_quote"])],
            node["evidence_quote"],
        )

    def test_sql_layer_refuses_blank_evidence(self):
        """CHECK constraints hold even if the Python validators are bypassed."""
        import sqlite3

        from orivellum.database.db import _now as now

        with self.db._lock:
            with self.assertRaises(sqlite3.IntegrityError):
                self.db._conn.execute(
                    """INSERT INTO graph_node(id, work_id, chapter_id, node_type, name,
                           description, evidence_quote, evidence_offset, attributes,
                           canon_fact_id, created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (_uid(), self.work_id, None, "Character", "Job", "", "   ", 0,
                     "{}", None, now()),
                )
            with self.assertRaises(sqlite3.IntegrityError):
                self.db._conn.execute(
                    """INSERT INTO graph_node(id, work_id, chapter_id, node_type, name,
                           description, evidence_quote, evidence_offset, attributes,
                           canon_fact_id, created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (_uid(), self.work_id, None, "Character", "Job", "", "quote", -3,
                     "{}", None, now()),
                )
            self.db._conn.rollback()


class VerbatimSpanTests(AtlasBase):
    def test_normalized_match_stores_exact_original_span(self):
        """A case/whitespace-normalized match must store the ORIGINAL text
        span at the recorded offset — never the model's version of it."""
        raw_text = "Job   Wept over the ruined fields of Uz. " + _FILLER
        stub = _StubLLM(
            {
                "atlas.events": [],
                "atlas.entities": [
                    {
                        "name": "Job of Uz",
                        "node_type": "Character",
                        "description": "…",
                        # lowercase + collapsed whitespace — not verbatim
                        "evidence_quote": "job wept",
                    }
                ],
                "atlas.relations": [],
                "atlas.attributes": {},
            }
        )
        ch = _seed_chapter(self.db, self.work_id, 0, "One", raw_text)
        with patch("orivellum.capabilities.llm.llm_call", stub):
            extract_chapter_graph(
                self.db, _cfg(), work_id=self.work_id,
                chapter={"id": ch, "seq": 0, "title": "One", "text": raw_text},
            )
        node = self.db.list_graph_nodes(work_ids=[self.work_id])[0]
        self.assertEqual(node["evidence_quote"], "Job   Wept")
        self.assertEqual(
            raw_text[node["evidence_offset"] :][: len(node["evidence_quote"])],
            node["evidence_quote"],
        )


class GatewayFailureTests(AtlasBase):
    """A gateway outage must never erase previously good graph data."""

    _GOOD = {
        "atlas.events": [],
        "atlas.entities": [
            {
                "name": "Job of Uz",
                "node_type": "Character",
                "description": "…",
                "evidence_quote": "Job of Uz rose before dawn",
            }
        ],
        "atlas.relations": [],
        "atlas.attributes": {},
        "atlas.propose": [],
    }

    def test_failed_rebuild_preserves_prior_graph(self):
        from orivellum.capabilities.atlas import AtlasLLMError

        _seed_chapter(self.db, self.work_id, 0, "One", _CH_TEXT)
        with patch("orivellum.capabilities.llm.llm_call", _StubLLM(dict(self._GOOD))):
            build_work_graph(self.db, _cfg(), work_id=self.work_id)
        self.assertEqual(len(self.db.list_graph_nodes(work_ids=[self.work_id])), 1)

        # Gateway goes down: the first extraction call fails.  The rebuild
        # must raise — and the previously stored graph must survive intact.
        down = _StubLLM({})  # every purpose -> ok=False
        with (
            patch("orivellum.capabilities.llm.llm_call", down),
            self.assertRaises(AtlasLLMError),
        ):
            build_work_graph(self.db, _cfg(), work_id=self.work_id)
        nodes = self.db.list_graph_nodes(work_ids=[self.work_id])
        self.assertEqual([n["name"] for n in nodes], ["Job of Uz"])

    def test_failed_verify_preserves_prior_inconsistencies(self):
        from orivellum.capabilities.atlas import AtlasLLMError

        t0 = _FILLER + " The gate of Uz was made of cedar."
        t1 = _FILLER + " The gate of Uz was made of iron."
        _seed_chapter(self.db, self.work_id, 0, "One", t0)
        ch1 = _seed_chapter(self.db, self.work_id, 1, "Two", t1)

        def propose(prompt):
            if "seq 1)" in prompt:
                return [
                    {
                        "description": "Gate material",
                        "current_quote": "The gate of Uz was made of iron.",
                        "prior_chapter_seq": 0,
                        "prior_quote": "The gate of Uz was made of cedar.",
                        "reasoning": "conflict",
                    }
                ]
            return []

        good = {
            **{k: v for k, v in self._GOOD.items() if k != "atlas.propose"},
            "atlas.propose": propose,
            "atlas.verify": {"verdict": "confirmed"},
        }
        with patch("orivellum.capabilities.llm.llm_call", _StubLLM(good)):
            build_work_graph(self.db, _cfg(), work_id=self.work_id)
        self.assertEqual(
            [r["chapter_id"] for r in self.db.list_graph_inconsistencies(work_id=self.work_id)],
            [ch1],
        )

        # Verifier call fails mid-rebuild: extraction succeeds but verify
        # can't complete — nothing may be committed for chapter 2, so the
        # stored inconsistency survives.
        half_down = _StubLLM(
            {**{k: v for k, v in self._GOOD.items() if k != "atlas.propose"},
             "atlas.propose": propose}  # no atlas.verify handler -> ok=False
        )
        with (
            patch("orivellum.capabilities.llm.llm_call", half_down),
            self.assertRaises(AtlasLLMError),
        ):
            build_work_graph(self.db, _cfg(), work_id=self.work_id)
        self.assertEqual(
            [r["chapter_id"] for r in self.db.list_graph_inconsistencies(work_id=self.work_id)],
            [ch1],
        )


class EmptiedChapterPurgeTests(AtlasBase):
    def test_blanked_chapter_graph_rows_are_purged_on_rebuild(self):
        raw_text = _CH_TEXT
        stub = _StubLLM(
            {
                "atlas.events": [],
                "atlas.entities": [
                    {
                        "name": "Job of Uz",
                        "node_type": "Character",
                        "description": "…",
                        "evidence_quote": "Job of Uz rose before dawn",
                    }
                ],
                "atlas.relations": [],
                "atlas.attributes": {},
                "atlas.propose": [],
            }
        )
        ch = _seed_chapter(self.db, self.work_id, 0, "One", raw_text)
        with patch("orivellum.capabilities.llm.llm_call", stub):
            build_work_graph(self.db, _cfg(), work_id=self.work_id)
        self.assertEqual(len(self.db.list_graph_nodes(work_ids=[self.work_id])), 1)

        # The chapter is subsequently cleared (whitespace-only text).
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE book_chapters SET text='   ' WHERE id=?", (ch,)
            )
            self.db._conn.commit()
        with patch("orivellum.capabilities.llm.llm_call", stub):
            build_work_graph(self.db, _cfg(), work_id=self.work_id)
        self.assertEqual(self.db.list_graph_nodes(work_ids=[self.work_id]), [])
        self.assertEqual(self.db.list_graph_inconsistencies(work_id=self.work_id), [])


class LongChapterWindowTests(AtlasBase):
    def test_windows_cover_the_tail_of_long_chapters(self):
        from orivellum.capabilities.atlas import _MAX_PASS_CHARS

        tail = "Elihu the Buzite finally spoke from the shadows."
        text = (_FILLER * ((_MAX_PASS_CHARS + 4000) // len(_FILLER) + 1)) + tail
        self.assertGreater(len(text), _MAX_PASS_CHARS)

        def entities(prompt: str):
            if tail in prompt:
                return [
                    {
                        "name": "Elihu the Buzite",
                        "node_type": "Character",
                        "description": "…",
                        "evidence_quote": tail,
                    }
                ]
            return []

        stub = _StubLLM(
            {
                "atlas.events": [],
                "atlas.entities": entities,
                "atlas.relations": [],
                "atlas.attributes": {},
            }
        )
        ch = _seed_chapter(self.db, self.work_id, 0, "Long", text)
        with patch("orivellum.capabilities.llm.llm_call", stub):
            extract_chapter_graph(
                self.db, _cfg(), work_id=self.work_id,
                chapter={"id": ch, "seq": 0, "title": "Long", "text": text},
            )
        # More than one extraction window ran…
        event_calls = [c for c in stub.calls if c["purpose"] == "atlas.events"]
        self.assertGreater(len(event_calls), 1)
        # …and the tail entity (beyond the first 16k chars) was captured
        # with a correct full-text offset.
        nodes = self.db.list_graph_nodes(work_ids=[self.work_id])
        self.assertEqual([n["name"] for n in nodes], ["Elihu the Buzite"])
        self.assertEqual(nodes[0]["evidence_offset"], text.find(tail))
        self.assertGreater(nodes[0]["evidence_offset"], _MAX_PASS_CHARS)


class WorkGraphMergeTests(AtlasBase):
    def test_get_work_graph_includes_atlas_nodes_and_edges(self):
        doc = self.db.create_document(
            title="Manuscript", source="ms.txt", kind="book", work_id=self.work_id
        )
        self.assertIsNotNone(doc)
        a = self.db.create_graph_node(
            work_id=self.work_id, chapter_id=None, node_type="Character",
            name="Job", evidence_quote="q", evidence_offset=0,
        )
        b = self.db.create_graph_node(
            work_id=self.work_id, chapter_id=None, node_type="Event",
            name="The storm", evidence_quote="q", evidence_offset=0,
        )
        self.db.create_graph_edge(
            work_id=self.work_id, chapter_id=None, src=a, dst=b,
            edge_type="experiences", evidence_quote="q", evidence_offset=0,
        )
        graph = self.db.get_work_graph(self.work_id)
        labels = {n["label"] for n in graph["nodes"]}
        self.assertIn("Job", labels)
        self.assertIn("The storm", labels)
        self.assertIn(
            ("Job".lower(), "experiences"),
            {
                (next(n["label"] for n in graph["nodes"] if n["id"] == e["source"]).lower(),
                 e["type"])
                for e in graph["edges"]
                if e["type"] == "experiences"
            },
        )


class WorkGraphBudgetTests(AtlasBase):
    def _atlas_pair(self):
        a = self.db.create_graph_node(
            work_id=self.work_id, chapter_id=None, node_type="Character",
            name="Job", evidence_quote="q", evidence_offset=0,
        )
        b = self.db.create_graph_node(
            work_id=self.work_id, chapter_id=None, node_type="Event",
            name="The storm", evidence_quote="q", evidence_offset=0,
        )
        self.db.create_graph_edge(
            work_id=self.work_id, chapter_id=None, src=a, dst=b,
            edge_type="experiences", evidence_quote="q", evidence_offset=0,
        )

    def test_saturated_legacy_graph_still_shows_atlas_and_no_dangling_edges(self):
        # Enough documents to saturate a small node budget on their own.
        for i in range(12):
            self.db.create_document(
                title=f"Doc {i}", source=f"d{i}.txt", kind="note", work_id=self.work_id
            )
        self._atlas_pair()
        graph = self.db.get_work_graph(self.work_id, limit=8)
        labels = {n["label"] for n in graph["nodes"]}
        self.assertIn("Job", labels)
        self.assertIn("The storm", labels)
        self.assertLessEqual(len(graph["nodes"]), 8)
        ids = {n["id"] for n in graph["nodes"]}
        for e in graph["edges"]:
            self.assertIn(e["source"], ids)
            self.assertIn(e["target"], ids)

    def test_atlas_only_work_without_documents_returns_graph(self):
        self._atlas_pair()
        graph = self.db.get_work_graph(self.work_id)
        self.assertEqual({n["label"] for n in graph["nodes"]}, {"Job", "The storm"})
        self.assertEqual(len(graph["edges"]), 1)
        ids = {n["id"] for n in graph["nodes"]}
        self.assertIn(graph["edges"][0]["source"], ids)
        self.assertIn(graph["edges"][0]["target"], ids)


class CanonLinkTests(AtlasBase):
    def test_node_links_to_sealed_canon(self):
        from orivellum.database.canon_store import CanonStore

        fact = CanonStore(self.db).create_fact(
            statement="Job of Uz is blameless and upright.",
            classification="INVENTED",
            work_id=self.work_id,
            signed_by="Author",
        )
        stub = _StubLLM(
            {
                "atlas.events": [],
                "atlas.entities": [
                    {
                        "name": "Job of Uz",
                        "node_type": "Character",
                        "description": "…",
                        "evidence_quote": "Job of Uz rose before dawn",
                    }
                ],
                "atlas.relations": [],
                "atlas.attributes": {},
            }
        )
        ch = _seed_chapter(self.db, self.work_id, 0, "One", _CH_TEXT)
        with patch("orivellum.capabilities.llm.llm_call", stub):
            extract_chapter_graph(
                self.db, _cfg(), work_id=self.work_id,
                chapter={"id": ch, "seq": 0, "title": "One", "text": _CH_TEXT},
            )
        node = self.db.list_graph_nodes(work_ids=[self.work_id])[0]
        self.assertEqual(node["canon_fact_id"], fact["id"])


class MultiWorkScopingTests(AtlasBase):
    def test_graphs_do_not_leak_between_works(self):
        other = self.db.create_work("Book Two", work_type="book")["id"]
        self.db.create_graph_node(
            work_id=self.work_id, chapter_id=None, node_type="Character",
            name="Job", evidence_quote="q", evidence_offset=0,
        )
        self.db.create_graph_node(
            work_id=other, chapter_id=None, node_type="Character",
            name="Elihu", evidence_quote="q", evidence_offset=0,
        )
        self.assertEqual(
            [n["name"] for n in self.db.list_graph_nodes(work_ids=[self.work_id])], ["Job"]
        )
        self.assertEqual(
            [n["name"] for n in self.db.list_graph_nodes(work_ids=[other])], ["Elihu"]
        )
        # Trilogy-wide query spans both.
        self.assertEqual(len(self.db.list_graph_nodes(work_ids=[self.work_id, other])), 2)


# ---------------------------------------------------------------------------
# The fixture proof: 10 injected contradictions
# ---------------------------------------------------------------------------

_FILLER = (
    "The wind moved over the plain and the flocks were quiet. "
    "Men spoke in the gate and the elders listened. "
)

# (cur_seq, current_quote, prior_seq, prior_quote, description)
_CONTRADICTIONS = [
    (1, "Job owned five hundred sheep in all.", 0,
     "Job owned seven thousand sheep and three thousand camels.", "Sheep count"),
    (1, "Eliphaz arrived from Shuah on the seventh day.", 0,
     "Eliphaz arrived from Teman on the third day.", "Eliphaz origin/day"),
    (2, "The storm came out of the west at dusk.", 0,
     "The storm came out of the east at dawn.", "Storm direction/time"),
    (2, "The eldest son lived in a tent of goat hair.", 0,
     "The eldest son lived in a stone house by the river.", "Eldest son dwelling"),
    (3, "Dinah wore a crimson veil at the market.", 0,
     "Dinah wore a blue veil at the market.", "Veil colour"),
    (3, "Bildad was the youngest of the three friends.", 1,
     "Bildad was the eldest of the three friends.", "Bildad age order"),
    (4, "The feast lasted three days and ended in silence.", 1,
     "The feast lasted seven days and ended in song.", "Feast length"),
    (4, "Job cursed the day aloud before his friends.", 2,
     "Job kept silence for seven days and seven nights.", "Silence broken"),
    (5, "The messenger came on foot across the dry ravine.", 2,
     "The messenger rode a grey mare across the flooded ford.", "Messenger travel"),
    (5, "Zophar quoted the proverb of the papyrus reed.", 3,
     "Zophar refused every proverb and spoke plainly.", "Zophar proverb"),
]


def _build_fixture_chapters() -> list[str]:
    """Six chapters; prior facts and contradictions embedded verbatim."""
    texts = [_FILLER * 3 for _ in range(6)]
    for cur_seq, cur_q, prior_seq, prior_q, _d in _CONTRADICTIONS:
        if prior_q not in texts[prior_seq]:
            texts[prior_seq] += " " + prior_q
        texts[cur_seq] += " " + cur_q
    return texts


class VerificationFixtureTests(AtlasBase):
    def setUp(self):
        super().setUp()
        self.texts = _build_fixture_chapters()
        self.chapter_ids = [
            _seed_chapter(self.db, self.work_id, i, f"Chapter {i + 1}", t)
            for i, t in enumerate(self.texts)
        ]

    def _proposals_for(self, seq: int) -> list[dict]:
        props = [
            {
                "description": d,
                "current_quote": cq,
                "prior_chapter_seq": ps,
                "prior_quote": pq,
                "reasoning": "state conflict",
            }
            for cs, cq, ps, pq, d in _CONTRADICTIONS
            if cs == seq
        ]
        # One fabricated proposal per chapter — quotes not in any chapter.
        props.append(
            {
                "description": f"Fabricated {seq}",
                "current_quote": "Leviathan surfaced in the village well.",
                "prior_chapter_seq": 0,
                "prior_quote": "Leviathan was sealed beneath the sea.",
                "reasoning": "invented",
            }
        )
        if seq == 2:
            # Grounded on both sides but the verifier rejects it.
            props.append(
                {
                    "description": "REJECTME",
                    "current_quote": "The storm came out of the west at dusk.",
                    "prior_chapter_seq": 0,
                    "prior_quote": "Men spoke in the gate and the elders listened.",
                    "reasoning": "not actually contradictory",
                }
            )
        return props

    def _stub(self) -> _StubLLM:
        def propose(prompt: str):
            m = [int(s) for s in prompt.split("CURRENT chapter (seq ")[1].split(")")[0:1]]
            return self._proposals_for(m[0])

        def verify(prompt: str):
            if "REJECTME" in prompt:
                return {"verdict": "rejected", "reason": "compatible"}
            return {"verdict": "confirmed"}

        return _StubLLM(
            {
                "atlas.events": [],
                "atlas.entities": [],
                "atlas.relations": [],
                "atlas.attributes": {},
                "atlas.propose": propose,
                "atlas.verify": verify,
            }
        )

    def test_finds_all_ten_with_correct_offsets_and_discards_rest(self):
        stub = self._stub()
        with patch("orivellum.capabilities.llm.llm_call", stub):
            totals = build_work_graph(self.db, _cfg(), work_id=self.work_id)

        self.assertEqual(totals["inconsistencies"], 10)
        rows = self.db.list_graph_inconsistencies(work_id=self.work_id)
        self.assertEqual(len(rows), 10)
        self.assertEqual({r["description"] for r in rows}, {d for *_x, d in _CONTRADICTIONS})

        by_desc = {r["description"]: r for r in rows}
        for cur_seq, cur_q, prior_seq, prior_q, d in _CONTRADICTIONS:
            row = by_desc[d]
            self.assertEqual(row["chapter_id"], self.chapter_ids[cur_seq])
            self.assertEqual(row["prior_chapter_id"], self.chapter_ids[prior_seq])
            self.assertEqual(row["current_offset"], self.texts[cur_seq].find(cur_q))
            self.assertEqual(row["prior_offset"], self.texts[prior_seq].find(prior_q))
            self.assertGreaterEqual(row["current_offset"], 0)
            self.assertGreaterEqual(row["prior_offset"], 0)

        # 5 fabricated (chapters 1-5) + 1 verifier-rejected were discarded.
        self.assertEqual(totals["discarded"], 6)
        # Verifier calls also ran at temperature 0.0.
        for c in stub.calls:
            self.assertEqual(c.get("temperature"), 0.0)

    def test_first_chapter_never_verified(self):
        stub = self._stub()
        with patch("orivellum.capabilities.llm.llm_call", stub):
            build_work_graph(self.db, _cfg(), work_id=self.work_id)
        proposed_on = [
            c["prompt"].split("CURRENT chapter (seq ")[1].split(")")[0]
            for c in stub.calls
            if c["purpose"] == "atlas.propose"
        ]
        self.assertNotIn("0", proposed_on)

    def test_reextracting_later_chapter_keeps_rows_where_it_is_prior(self):
        stub = self._stub()
        with patch("orivellum.capabilities.llm.llm_call", stub):
            build_work_graph(self.db, _cfg(), work_id=self.work_id)
        # Chapter seq 2 raised rows AND is the prior side of a seq-4 row.
        self.db.delete_graph_for_chapter(self.chapter_ids[2])
        rows = self.db.list_graph_inconsistencies(work_id=self.work_id)
        descs = {r["description"] for r in rows}
        self.assertNotIn("Storm direction/time", descs)  # raised BY seq 2 — dropped
        self.assertIn("Silence broken", descs)  # seq 2 is only the prior side — kept


class UnverifiableProposalTests(AtlasBase):
    def test_llm_failure_raises_and_writes_nothing(self):
        """A gateway failure must RAISE — never be mistaken for 'no findings'."""
        from orivellum.capabilities.atlas import AtlasLLMError

        ch0 = _seed_chapter(self.db, self.work_id, 0, "One", _FILLER)
        ch1 = _seed_chapter(self.db, self.work_id, 1, "Two", _FILLER)
        stub = _StubLLM({})  # every call fails
        with (
            patch("orivellum.capabilities.llm.llm_call", stub),
            self.assertRaises(AtlasLLMError),
        ):
            verify_chapter(
                self.db, _cfg(), work_id=self.work_id,
                chapter={"id": ch1, "seq": 1, "title": "Two", "text": _FILLER},
                prior_chapters=[{"id": ch0, "seq": 0, "title": "One", "text": _FILLER}],
            )
        self.assertEqual(self.db.list_graph_inconsistencies(work_id=self.work_id), [])


# ---------------------------------------------------------------------------
# Harvest feeds the graph (no parallel store; gate respected)
# ---------------------------------------------------------------------------


class PartialRebuildReverifyTests(AtlasBase):
    """Rebuilding one doc's chapters must re-verify downstream chapters."""

    def test_downstream_inconsistencies_reverified_after_partial_rebuild(self):
        doc_a = self.db.create_document(
            title="Part One", source="a.txt", kind="book", work_id=self.work_id
        )["id"]
        doc_b = self.db.create_document(
            title="Part Two", source="b.txt", kind="book", work_id=self.work_id
        )["id"]
        t0 = _FILLER + " The gate of Uz was made of cedar."
        t1 = _FILLER + " The gate of Uz was made of iron."
        _seed_chapter(self.db, self.work_id, 0, "One", t0, doc_id=doc_a)
        ch1 = _seed_chapter(self.db, self.work_id, 1, "Two", t1, doc_id=doc_b)

        def propose(prompt):
            if "seq 1)" in prompt:
                return [
                    {
                        "description": "Gate material",
                        "current_quote": "The gate of Uz was made of iron.",
                        "prior_chapter_seq": 0,
                        "prior_quote": "The gate of Uz was made of cedar.",
                        "reasoning": "conflict",
                    }
                ]
            return []

        base = {
            "atlas.events": [],
            "atlas.entities": [],
            "atlas.relations": [],
            "atlas.attributes": {},
            "atlas.verify": {"verdict": "confirmed"},
        }
        with patch(
            "orivellum.capabilities.llm.llm_call", _StubLLM({**base, "atlas.propose": propose})
        ):
            build_work_graph(self.db, _cfg(), work_id=self.work_id)
        rows = self.db.list_graph_inconsistencies(work_id=self.work_id)
        self.assertEqual([r["chapter_id"] for r in rows], [ch1])

        # Rebuild ONLY doc A (the prior side).  The proposer now finds no
        # conflict — the stale chapter-2 finding must be dropped, not kept.
        with patch(
            "orivellum.capabilities.llm.llm_call",
            _StubLLM({**base, "atlas.propose": lambda _p: []}),
        ):
            build_work_graph(self.db, _cfg(), work_id=self.work_id, doc_id=doc_a)
        self.assertEqual(self.db.list_graph_inconsistencies(work_id=self.work_id), [])


class PartialRebuildAtomicityTests(AtlasBase):
    """A failed partial rebuild must leave EVERY prior row untouched —
    including downstream chapters whose re-verification fails mid-run."""

    def _snapshot(self):
        return (
            sorted(
                (n["chapter_id"], n["name"], n["evidence_offset"])
                for n in self.db.list_graph_nodes(work_ids=[self.work_id])
            ),
            sorted(
                (e["chapter_id"], e["edge_type"])
                for e in self.db.list_graph_edges(work_ids=[self.work_id])
            ),
            sorted(
                (r["chapter_id"], r["description"])
                for r in self.db.list_graph_inconsistencies(work_id=self.work_id)
            ),
        )

    def test_downstream_verify_failure_preserves_everything(self):
        from orivellum.capabilities.atlas import AtlasLLMError

        doc_a = self.db.create_document(
            title="Part One", source="a.txt", kind="book", work_id=self.work_id
        )["id"]
        doc_b = self.db.create_document(
            title="Part Two", source="b.txt", kind="book", work_id=self.work_id
        )["id"]
        t0 = _FILLER + " The gate of Uz was made of cedar."
        t1 = _FILLER + " The gate of Uz was made of iron."
        t2 = _FILLER + " The gate of Uz was made of bronze."
        # Doc A owns chapter 0; doc B owns TWO downstream chapters (1, 2).
        _seed_chapter(self.db, self.work_id, 0, "One", t0, doc_id=doc_a)
        _seed_chapter(self.db, self.work_id, 1, "Two", t1, doc_id=doc_b)
        _seed_chapter(self.db, self.work_id, 2, "Three", t2, doc_id=doc_b)

        def propose(prompt):
            if "seq 1)" in prompt:
                return [
                    {
                        "description": "Gate material vs ch1",
                        "current_quote": "The gate of Uz was made of iron.",
                        "prior_chapter_seq": 0,
                        "prior_quote": "The gate of Uz was made of cedar.",
                        "reasoning": "conflict",
                    }
                ]
            if "seq 2)" in prompt:
                return [
                    {
                        "description": "Gate material vs ch2",
                        "current_quote": "The gate of Uz was made of bronze.",
                        "prior_chapter_seq": 0,
                        "prior_quote": "The gate of Uz was made of cedar.",
                        "reasoning": "conflict",
                    }
                ]
            return []

        base = {
            "atlas.events": [],
            "atlas.entities": [
                {
                    "name": "Job of Uz",
                    "node_type": "Character",
                    "description": "…",
                    "evidence_quote": "Job of Uz rose before dawn",
                }
            ],
            "atlas.relations": [],
            "atlas.attributes": {},
        }
        good = {**base, "atlas.propose": propose, "atlas.verify": {"verdict": "confirmed"}}
        with patch("orivellum.capabilities.llm.llm_call", _StubLLM(good)):
            build_work_graph(self.db, _cfg(), work_id=self.work_id)
        before = self._snapshot()
        # Sanity: both downstream chapters raised a stored inconsistency.
        self.assertEqual(len(before[2]), 2)

        # Partial rebuild of doc A: extraction and chapter-1 re-verification
        # would succeed, but the SECOND downstream re-verification (chapter 2)
        # hits a gateway failure — its verify calls error out.
        calls = {"verify": 0}

        def flaky_verify(prompt):
            calls["verify"] += 1
            if "vs ch2" in prompt:
                return None  # -> ok=False -> AtlasLLMError
            return {"verdict": "confirmed"}

        flaky = _StubLLM({**base, "atlas.propose": propose, "atlas.verify": flaky_verify})
        with (
            patch("orivellum.capabilities.llm.llm_call", flaky),
            self.assertRaises(AtlasLLMError),
        ):
            build_work_graph(self.db, _cfg(), work_id=self.work_id, doc_id=doc_a)
        # Chapter 2's verify was actually reached (the failure happened
        # mid-plan, after chapter 1 staged successfully) …
        self.assertGreaterEqual(calls["verify"], 2)
        # … and NOTHING changed: nodes, edges, and both downstream
        # inconsistencies are exactly as before the failed rebuild.
        self.assertEqual(self._snapshot(), before)


class FirstImportOrderingTests(AtlasBase):
    """Real production ordering: chapters written via upsert_book_chapters
    (as the pipeline does BEFORE invoking harvest), then harvest runs and
    the ATLAS graph is actually built — no pre-seeded rows, no mocked hook."""

    def test_graph_built_on_first_import(self):
        doc_id = self.db.create_document(
            title="Manuscript", source="ms.txt", kind="book", work_id=self.work_id
        )["id"]
        self.db.upsert_book_chapters(
            doc_id,
            self.work_id,
            [
                {"seq": 0, "level": 1, "title": "One", "text": _CH_TEXT},
                {"seq": 1, "level": 1, "title": "Two", "text": _FILLER},
            ],
        )
        stub = _StubLLM(
            {
                # Harvest's own extraction calls fail (unknown purposes → ok=False);
                # only the ATLAS passes answer.
                "atlas.events": [],
                "atlas.entities": [
                    {
                        "name": "Job of Uz",
                        "node_type": "Character",
                        "description": "…",
                        "evidence_quote": "Job of Uz rose before dawn",
                    }
                ],
                "atlas.relations": [],
                "atlas.attributes": {},
                "atlas.propose": [],
            }
        )
        from orivellum.capabilities import knowledge_harvest as kh

        with (
            patch("orivellum.capabilities.llm.llm_call", stub),
            patch("orivellum.api._deps.get_config", return_value=_cfg()),
        ):
            kh.llm_harvest_by_chapters(doc_id, self.work_id, "Manuscript", self.db)

        nodes = self.db.list_graph_nodes(work_ids=[self.work_id])
        self.assertEqual([n["name"] for n in nodes], ["Job of Uz"])
        self.assertEqual(nodes[0]["evidence_offset"], _CH_TEXT.find("Job of Uz rose before dawn"))

        # Legacy entity double-writes were removed for fiction, so the
        # GLOBAL graph must surface ATLAS rows too — otherwise harvested
        # characters vanish from the cross-work graph view.
        g = self.db.get_global_graph()
        labels = {n["label"] for n in g["nodes"]}
        self.assertIn("Job of Uz", labels)
        node_ids = {n["id"] for n in g["nodes"]}
        for e in g["edges"]:
            self.assertIn(e["source"], node_ids)
            self.assertIn(e["target"], node_ids)
        # Kind filter applies to ATLAS kinds (lowercase node types).
        g2 = self.db.get_global_graph(entity_kinds=["character"])
        self.assertIn("Job of Uz", {n["label"] for n in g2["nodes"]})
        g3 = self.db.get_global_graph(entity_kinds=["location"])
        self.assertNotIn("Job of Uz", {n["label"] for n in g3["nodes"]})


class HarvestFailureBoundaryTests(AtlasBase):
    """A gateway failure during the graph build must reach the harvest
    caller and leave an observable per-work marker — never a silent
    'successful' harvest."""

    def _harvest(self, stub):
        from orivellum.capabilities import knowledge_harvest as kh

        doc_id = self.db.create_document(
            title="Manuscript", source="ms.txt", kind="book", work_id=self.work_id
        )["id"]
        _seed_chapter(self.db, self.work_id, 0, "One", _CH_TEXT, doc_id=doc_id)
        with (
            patch("orivellum.capabilities.llm.llm_call", stub),
            patch("orivellum.api._deps.get_config", return_value=_cfg()),
        ):
            kh.llm_harvest_by_chapters(doc_id, self.work_id, "Manuscript", self.db)

    def test_atlas_failure_propagates_and_sets_marker(self):
        from orivellum.capabilities.atlas import AtlasLLMError

        with self.assertRaises(AtlasLLMError):
            self._harvest(_StubLLM({}))  # every call fails, incl. atlas.*
        marker = self.db.get_setting(f"atlas_build_error:{self.work_id}")
        self.assertTrue(marker)

    def test_marker_cleared_on_successful_rebuild(self):
        self.db.set_setting(f"atlas_build_error:{self.work_id}", "stale failure")
        self._harvest(
            _StubLLM(
                {
                    "atlas.events": [],
                    "atlas.entities": [],
                    "atlas.relations": [],
                    "atlas.attributes": {},
                    "atlas.propose": [],
                }
            )
        )
        self.assertEqual(self.db.get_setting(f"atlas_build_error:{self.work_id}"), "")


class HarvestHookTests(AtlasBase):
    def _run_harvest(self):
        from orivellum.capabilities import knowledge_harvest as kh

        doc = self.db.create_document(
            title="Manuscript", source="ms.txt", kind="book", work_id=self.work_id
        )
        doc_id = doc["id"]
        _seed_chapter(self.db, self.work_id, 0, "One", _CH_TEXT, doc_id=doc_id)
        stub = _StubLLM({})  # harvest's own LLM calls fail — hook must still run
        with (
            patch("orivellum.capabilities.llm.llm_call", stub),
            patch("orivellum.api._deps.get_config", return_value=_cfg()),
            patch("orivellum.capabilities.atlas.build_work_graph") as bwg,
        ):
            kh.llm_harvest_by_chapters(doc_id, self.work_id, "Manuscript", self.db)
        return doc_id, bwg

    def test_chapter_harvest_builds_graph(self):
        doc_id, bwg = self._run_harvest()
        bwg.assert_called_once()
        _, kwargs = bwg.call_args
        self.assertEqual(kwargs["work_id"], self.work_id)
        self.assertEqual(kwargs["doc_id"], doc_id)

    def test_atlas_enabled_gate(self):
        self.db.set_setting("atlas_enabled", "false")
        _, bwg = self._run_harvest()
        bwg.assert_not_called()


if __name__ == "__main__":
    unittest.main()
