"""BAND surgical edit + LINEAGE tests (spec §2.4).

Proves by assertion:
- outside-band bytes are reassembled BYTE-IDENTICALLY by code — the model's
  replacement can never leak past the declared boundaries;
- checkpoint before extraction: untracked chapter text lands as a revision
  row BEFORE the edit revision, so the pre-edit state is always restorable;
- fingerprint discipline: a stale base fingerprint refuses the edit;
- lineage: every revision records parent_rev, origin, created_by, and (for
  band edits) the exact edit scope; LOOM drafts record ai_generated/loom;
- delta re-verification gates: more findings, a new critical, higher band
  error density, or a pairwise loss each REFUSE the commit; nothing persists;
- explicit acceptance: accept_regression requires an author signature and
  records the acceptance in the revision meta;
- fail-closed verification: a malformed delta-check or pairwise response is
  a BandError, never a clean pass; ungrounded delta quotes are discarded;
- approved chapters: editing/restoring requires the author signature and
  demotes the chapter to 'drafted';
- restore is append-only: a NEW head revision copies the old text; history
  survives; restoring the current text is refused;
- the per-chapter claim refuses concurrent edits;
- the editor model never judges its own output (same model → refusal).
"""

from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from orivellum.capabilities import band
from orivellum.capabilities.band import BandBusy, BandError
from orivellum.database.db import OrivellumDB, _now


def _cfg(same_model=False):
    return SimpleNamespace(
        serving=SimpleNamespace(
            base_url="http://test.invalid",
            workhorse_model="editor-model",
            reasoner_model="editor-model" if same_model else "judge-model",
        )
    )


CHAPTER = (
    "The caravan rolled east through the wet grey fields. "
    "Mara counted the wagons twice before dawn. "
    "Tobin slept beneath the third cart, boots still laced."
)


class StubLLM:
    """Dispatch llm_call by purpose.  band.delta findings are driven by a
    marker: any passage containing 'GOAT' contradicts fact F0."""

    def __init__(
        self,
        *,
        new_band="Mara counted the wagons three times before dawn. ",
        winner="new",
        edit_raw=None,
        delta_raw=None,
        pairwise_raw=None,
        delta_quote=None,
        down_purposes=(),
    ):
        self.calls: list[dict] = []
        self.new_band = new_band
        self.winner = winner
        self.edit_raw = edit_raw
        self.delta_raw = delta_raw
        self.pairwise_raw = pairwise_raw
        self.delta_quote = delta_quote  # override the quote (grounding tests)
        self.down_purposes = set(down_purposes)

    def prompts(self, purpose):
        return [c["user"] for c in self.calls if c["purpose"] == purpose]

    def __call__(self, messages, **kwargs):
        purpose = kwargs.get("purpose", "")
        user = messages[-1]["content"]
        self.calls.append({"purpose": purpose, "user": user, "model": kwargs.get("model")})

        def ok(text):
            return SimpleNamespace(
                ok=True, text=text, error=None, call_id=len(self.calls), logprobs=None
            )

        if purpose in self.down_purposes:
            return SimpleNamespace(
                ok=False, text=None, error="gateway down", call_id=None, logprobs=None
            )
        if purpose == "band.edit":
            if self.edit_raw is not None:
                return ok(self.edit_raw)
            return ok(json.dumps({"band": self.new_band}))
        if purpose == "band.delta":
            if self.delta_raw is not None:
                return ok(self.delta_raw)
            passage = user.split('PASSAGE:\n"""')[1].split('"""')[0]
            if "GOAT" in passage:
                quote = (
                    self.delta_quote
                    if self.delta_quote is not None
                    else next(w for w in passage.split(". ") if "GOAT" in w)
                )
                return ok(
                    json.dumps(
                        {
                            "contradictions": [
                                {"ref": "F0", "quote": quote, "reasoning": "contradicts"}
                            ]
                        }
                    )
                )
            return ok(json.dumps({"contradictions": []}))
        if purpose == "band.pairwise":
            if self.pairwise_raw is not None:
                return ok(self.pairwise_raw)
            return ok(json.dumps({"winner": self.winner, "rationale": "cleaner"}))
        return SimpleNamespace(
            ok=False, text=None, error=f"unknown {purpose}", call_id=None, logprobs=None
        )


class BandBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.work_id = self.db.create_work("Edited Book", work_type="writing")["id"]
        self.chapter_id = self._seed_chapter(1, text=CHAPTER)
        # F0 in the fact list (canon facts come first).
        self._seed_fact("Mara never counted wagons more than twice.", "HISTORICAL")

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _seed_chapter(self, seq, text, status="draft"):
        oid = self.db._create_object("book_chapter")
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO book_chapters(id, work_id, seq, level, title, text,
                   source_doc_id, status, meta, created_at, updated_at)
                   VALUES(?,?,?,1,?,?,NULL,?,'{}',?,?)""",
                (oid, self.work_id, seq, f"Chapter {seq}", text, status, _now(), _now()),
            )
            self.db._conn.commit()
        return oid

    def _seed_fact(self, statement, cls="INVENTED"):
        with self.db._lock:
            self.db._conn.execute(
                """INSERT INTO canon_fact(id, work_id, statement, classification,
                   created_at) VALUES(?,?,?,?,?)""",
                (str(uuid.uuid4()), self.work_id, statement, cls, _now()),
            )
            self.db._conn.commit()

    def _set_status(self, status):
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE book_chapters SET status=? WHERE id=?", (status, self.chapter_id)
            )
            self.db._conn.commit()

    def _text(self):
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT text, status FROM book_chapters WHERE id=?", (self.chapter_id,)
            ).fetchone()
        return row["text"], row["status"]

    def _band_span(self):
        start = CHAPTER.index("Mara counted")
        end = CHAPTER.index("Tobin slept")
        return start, end

    def _edit(self, stub=None, cfg=None, **over):
        stub = stub or StubLLM()
        start, end = self._band_span()
        kwargs = dict(
            chapter_id=self.chapter_id,
            start=start,
            end=end,
            instruction="make the counting three times",
            base_fingerprint=band.fingerprint(CHAPTER),
        )
        kwargs.update(over)
        with patch("orivellum.capabilities.llm.llm_call", stub):
            return band.surgical_edit(self.db, cfg or _cfg(), **kwargs), stub


class TestSurgicalEdit(BandBase):
    def test_happy_path_outside_band_byte_identical(self):
        result, stub = self._edit()
        self.assertTrue(result["committed"])
        start, end = self._band_span()
        text, _ = self._text()
        self.assertEqual(text[:start], CHAPTER[:start])
        self.assertEqual(text[len(text) - (len(CHAPTER) - end) :], CHAPTER[end:])
        self.assertIn("three times", text)
        self.assertEqual(result["fingerprint"], band.fingerprint(text))

    def test_checkpoint_then_edit_revision_lineage(self):
        result, _ = self._edit()
        revs = self.db.list_chapter_revisions(self.chapter_id)
        self.assertEqual(len(revs), 2)
        cp, edit = revs
        self.assertTrue(cp["meta"].get("checkpoint"))
        self.assertIsNone(cp["parent_rev"])
        self.assertEqual(cp["created_by"], "checkpoint")
        self.assertEqual(edit["parent_rev"], cp["rev"])
        self.assertEqual(edit["origin"], "ai_assisted")
        self.assertEqual(edit["created_by"], "user")
        scope = edit["edit_scope"]
        start, end = self._band_span()
        self.assertEqual((scope["start"], scope["end"]), (start, end))
        self.assertEqual(scope["fingerprint_before"], band.fingerprint(CHAPTER))
        self.assertEqual(scope["fingerprint_after"], result["fingerprint"])
        # Checkpoint preserves the pre-edit text verbatim.
        full_cp = self.db.get_chapter_revision(cp["id"])
        self.assertEqual(full_cp["text"], CHAPTER)

    def test_provenance_recorded_with_all_call_ids(self):
        result, stub = self._edit()
        prov = self.db.get_provenance(result["revision"]["id"], "loom_chapter_revision")
        self.assertIsNotNone(prov)
        self.assertEqual(prov["origin"], "ai_assisted")
        self.assertEqual(len(prov["llm_call_ids"]), len(stub.calls))

    def test_stale_fingerprint_refused(self):
        with self.assertRaisesRegex(BandError, "stale fingerprint"):
            self._edit(base_fingerprint="0" * 16)
        self.assertEqual(self.db.list_chapter_revisions(self.chapter_id), [])

    def test_invalid_boundaries_refused(self):
        for start, end in ((-1, 5), (5, 5), (10, 4), (0, len(CHAPTER) + 1)):
            with self.assertRaisesRegex(BandError, "invalid band boundaries"):
                self._edit(start=start, end=end)

    def test_band_size_cap(self):
        big = "x" * (band.BAND_MAX_CHARS + 10)
        cid = self._seed_chapter(2, text=big)
        with (
            patch("orivellum.capabilities.llm.llm_call", StubLLM()),
            self.assertRaisesRegex(BandError, "redraft"),
        ):
            band.surgical_edit(
                self.db,
                _cfg(),
                chapter_id=cid,
                start=0,
                end=len(big),
                instruction="shrink",
                base_fingerprint=band.fingerprint(big),
            )

    def test_runaway_generation_refused(self):
        stub = StubLLM(new_band="y" * 50_000)
        with self.assertRaisesRegex(BandError, "runaway"):
            self._edit(stub=stub)
        self.assertEqual(self._text()[0], CHAPTER)

    def test_malformed_edit_output_refused(self):
        with self.assertRaisesRegex(BandError, "no replacement band"):
            self._edit(stub=StubLLM(edit_raw="sure, here you go!"))
        self.assertEqual(self._text()[0], CHAPTER)

    def test_same_editor_and_judge_model_refused(self):
        from orivellum.capabilities.loom import LoomError

        with self.assertRaisesRegex(LoomError, "never judge its own output"):
            self._edit(cfg=_cfg(same_model=True))

    def test_gateway_failure_persists_nothing(self):
        for purpose in ("band.edit", "band.delta", "band.pairwise"):
            with self.assertRaises(BandError):
                self._edit(stub=StubLLM(down_purposes=(purpose,)))
            self.assertEqual(self._text()[0], CHAPTER)
            # Only the pre-edit checkpoint may exist — never an edit revision.
            revs = self.db.list_chapter_revisions(self.chapter_id)
            self.assertTrue(all(r["meta"].get("checkpoint") for r in revs))


class TestRegressionGates(BandBase):
    def test_new_critical_finding_refuses(self):
        result, _ = self._edit(
            stub=StubLLM(new_band="Mara led the GOAT past the wagons before dawn. ")
        )
        self.assertFalse(result["committed"])
        self.assertTrue(any("critical" in r for r in result["reasons"]))
        self.assertEqual(self._text()[0], CHAPTER)
        # Nothing but the checkpoint may exist — the edit itself never landed.
        revs = self.db.list_chapter_revisions(self.chapter_id)
        self.assertTrue(all(r["meta"].get("checkpoint") for r in revs))

    def test_pairwise_loss_refuses(self):
        result, _ = self._edit(stub=StubLLM(winner="old"))
        self.assertFalse(result["committed"])
        self.assertTrue(any("prefers the previous" in r for r in result["reasons"]))

    def test_accept_regression_requires_author(self):
        with self.assertRaisesRegex(BandError, "author signature"):
            self._edit(stub=StubLLM(winner="old"), accept_regression=True)

    def test_accept_regression_with_author_commits_and_records(self):
        result, _ = self._edit(stub=StubLLM(winner="old"), accept_regression=True, author="Brian")
        self.assertTrue(result["committed"])
        revs = self.db.list_chapter_revisions(self.chapter_id)
        edit = revs[-1]
        self.assertTrue(edit["meta"]["accepted_regression"])
        self.assertEqual(edit["created_by"], "Brian")

    def test_malformed_delta_check_fails_closed(self):
        with self.assertRaisesRegex(BandError, "malformed"):
            self._edit(stub=StubLLM(delta_raw="all good boss"))
        self.assertEqual(self._text()[0], CHAPTER)

    def test_malformed_pairwise_fails_closed(self):
        with self.assertRaisesRegex(BandError, "refusing to score"):
            self._edit(stub=StubLLM(pairwise_raw='{"winner": "both!"}'))

    def test_ungrounded_delta_quote_discarded(self):
        # Quote not present in the passage → finding discarded → clean pass.
        result, _ = self._edit(
            stub=StubLLM(
                new_band="Mara led the GOAT past the wagons before dawn. ",
                delta_quote="this sentence appears nowhere at all",
            )
        )
        self.assertTrue(result["committed"])


class TestApprovedChapters(BandBase):
    def test_edit_approved_without_author_refused(self):
        self._set_status("approved")
        stale = band.fingerprint(CHAPTER)
        with self.assertRaisesRegex(BandError, "author signature"):
            self._edit(base_fingerprint=stale)

    def test_edit_approved_with_author_demotes(self):
        self._set_status("approved")
        result, _ = self._edit(author="Brian")
        self.assertTrue(result["committed"])
        self.assertTrue(result["demoted_from_approved"])
        self.assertEqual(self._text()[1], "drafted")

    def test_restore_approved_without_author_refused(self):
        self._edit()
        self._set_status("approved")
        with self.assertRaisesRegex(BandError, "author signature"):
            band.restore_revision(self.db, chapter_id=self.chapter_id, rev=1)


class TestRestore(BandBase):
    def test_restore_appends_new_revision(self):
        self._edit()  # rev1 checkpoint, rev2 edit
        result = band.restore_revision(self.db, chapter_id=self.chapter_id, rev=1)
        self.assertTrue(result["committed"])
        self.assertEqual(self._text()[0], CHAPTER)
        revs = self.db.list_chapter_revisions(self.chapter_id)
        self.assertEqual(len(revs), 3)  # history survives, nothing deleted
        self.assertEqual(revs[-1]["meta"]["restored_from_rev"], 1)
        self.assertEqual(revs[-1]["parent_rev"], 2)

    def test_restore_current_text_refused(self):
        self._edit()
        head = self.db.list_chapter_revisions(self.chapter_id)[-1]
        with self.assertRaisesRegex(BandError, "already the current text"):
            band.restore_revision(self.db, chapter_id=self.chapter_id, rev=head["rev"])

    def test_restore_unknown_rev_refused(self):
        with self.assertRaisesRegex(BandError, "not found"):
            band.restore_revision(self.db, chapter_id=self.chapter_id, rev=99)


class TestClaimAndLineage(BandBase):
    def test_concurrent_edit_refused(self):
        lock = band._chapter_lock(self.chapter_id)
        self.assertTrue(lock.acquire(blocking=False))
        try:
            with self.assertRaises(BandBusy):
                self._edit()
            with self.assertRaises(BandBusy):
                band.restore_revision(self.db, chapter_id=self.chapter_id, rev=1)
        finally:
            lock.release()

    def test_create_chapter_revision_lineage_defaults(self):
        r1 = self.db.create_chapter_revision(self.chapter_id, self.work_id, "one")
        r2 = self.db.create_chapter_revision(
            self.chapter_id, self.work_id, "two", origin="human", created_by="Brian"
        )
        self.assertIsNone(r1["parent_rev"])
        self.assertEqual(r2["parent_rev"], r1["rev"])
        revs = self.db.list_chapter_revisions(self.chapter_id)
        self.assertEqual([r["origin"] for r in revs], ["ai_generated", "human"])
        with self.assertRaises(ValueError):
            self.db.create_chapter_revision(self.chapter_id, self.work_id, "x", origin="alien")

    def test_band_text_echo_guards_offset_drift(self):
        # Astral chars: "𝕄ara…" — a UTF-16 client would report shifted
        # offsets; the band_text echo refuses the mismatch instead of
        # editing an unselected span.
        text = "The 𝕄oon rose. Mara counted the wagons twice. Tobin slept."
        cid = self._seed_chapter(4, text=text)
        start = text.index("Mara")
        end = text.index("Tobin")
        with (
            patch("orivellum.capabilities.llm.llm_call", StubLLM()),
            self.assertRaisesRegex(BandError, "band text mismatch"),
        ):
            band.surgical_edit(
                self.db,
                _cfg(),
                chapter_id=cid,
                start=start + 1,
                end=end + 1,
                instruction="x",
                base_fingerprint=band.fingerprint(text),
                band_text=text[start:end],
            )
        # Correct code-point offsets + matching echo commit cleanly.
        with patch("orivellum.capabilities.llm.llm_call", StubLLM()):
            result = band.surgical_edit(
                self.db,
                _cfg(),
                chapter_id=cid,
                start=start,
                end=end,
                instruction="make it three times",
                base_fingerprint=band.fingerprint(text),
                band_text=text[start:end],
            )
        self.assertTrue(result["committed"])
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT text FROM book_chapters WHERE id=?", (cid,)
            ).fetchone()
        self.assertTrue(row["text"].startswith("The 𝕄oon rose. "))
        self.assertTrue(row["text"].endswith("Tobin slept."))

    def test_checkpoint_refuses_on_concurrent_write(self):
        # A writer (e.g. LOOM) lands between chapter load and checkpoint:
        # the atomic checkpoint validates the live fingerprint and refuses
        # BEFORE writing anything — never a stale checkpoint in the lineage.
        ch = {"id": self.chapter_id, "work_id": self.work_id}
        with self.db._lock:
            self.db._conn.execute(
                "UPDATE book_chapters SET text=? WHERE id=?",
                (CHAPTER + " New sentence from a concurrent draft.", self.chapter_id),
            )
            self.db._conn.commit()
        with self.assertRaisesRegex(BandError, "before the checkpoint"):
            band._checkpoint_current(self.db, ch, expected_fp=band.fingerprint(CHAPTER))
        self.assertEqual(self.db.list_chapter_revisions(self.chapter_id), [])

    def test_no_text_chapter_refused(self):
        cid = self._seed_chapter(3, text="")
        with (
            patch("orivellum.capabilities.llm.llm_call", StubLLM()),
            self.assertRaisesRegex(BandError, "no text"),
        ):
            band.surgical_edit(
                self.db,
                _cfg(),
                chapter_id=cid,
                start=0,
                end=1,
                instruction="x",
                base_fingerprint=band.fingerprint(""),
            )


if __name__ == "__main__":
    unittest.main()
