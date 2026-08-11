"""Series continuity tests (task: canon, voice, and continuity across a trilogy).

Covers:
- Series CRUD + membership constraints (one series per Work, unique volumes,
  delete refused while series-scoped canon exists)
- Canon scoping: series facts require work_id NULL; visible to member books
  only; earlier volumes' facts bind later volumes and NEVER the reverse;
  other series' facts stay invisible
- Per-book overrides: guards (target must be an active series/global fact
  visible to the book; one active override per book) and visibility (the
  override hides the target for THAT book only)
- LOOM inheritance: replay_world_state folds earlier volumes first;
  personas fall back to the nearest earlier volume (local approval wins);
  voice baselines resolve own-first then nearest-prior
- /api/series routes: CRUD + overview math + cross-book finding labels
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from orivellum.capabilities.loom import _personas_for_cast, replay_world_state
from orivellum.database.canon_store import CanonFactError, CanonStore
from orivellum.database.db import OrivellumDB
from orivellum.database.series_store import (
    SeriesError,
    SeriesStore,
    resolve_assay_baseline,
)
from tests.conftest import AUTH_HEADERS


def _now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _seed_chapter(db: OrivellumDB, work_id: str, seq: int, title: str, text: str) -> str:
    """Insert a real book_chapters row (findings are LAW 3 grounded)."""
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


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.store = SeriesStore(self.db)
        self.canon = CanonStore(self.db)
        self.book1 = self.db.create_work(title="Book One")
        self.book2 = self.db.create_work(title="Book Two")
        self.book3 = self.db.create_work(title="Book Three")
        self.series = self.store.create_series(title="The Trilogy")
        self.sid = self.series["id"]

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _add_all(self):
        self.store.add_member(self.sid, self.book1["id"], volume=1)
        self.store.add_member(self.sid, self.book2["id"], volume=2)
        self.store.add_member(self.sid, self.book3["id"], volume=3)


class SeriesMembershipTests(_Base):
    def test_members_ordered_by_volume(self):
        self.store.add_member(self.sid, self.book2["id"], volume=2)
        self.store.add_member(self.sid, self.book1["id"], volume=1)
        s = self.store.get_series(self.sid)
        self.assertEqual([m["work_id"] for m in s["members"]],
                         [self.book1["id"], self.book2["id"]])
        self.assertEqual(s["members"][0]["work_title"], "Book One")

    def test_work_cannot_join_two_series(self):
        other = self.store.create_series(title="Other Series")
        self.store.add_member(self.sid, self.book1["id"], volume=1)
        with self.assertRaises(SeriesError):
            self.store.add_member(other["id"], self.book1["id"], volume=1)

    def test_volume_unique_within_series(self):
        self.store.add_member(self.sid, self.book1["id"], volume=1)
        with self.assertRaises(SeriesError):
            self.store.add_member(self.sid, self.book2["id"], volume=1)

    def test_volume_must_be_positive(self):
        with self.assertRaises(SeriesError):
            self.store.add_member(self.sid, self.book1["id"], volume=0)

    def test_prior_volume_work_ids_direction(self):
        self._add_all()
        self.assertEqual(self.store.prior_volume_work_ids(self.book3["id"]),
                         [self.book1["id"], self.book2["id"]])
        self.assertEqual(self.store.prior_volume_work_ids(self.book1["id"]), [])
        # A work outside any series inherits nothing.
        lone = self.db.create_work(title="Standalone")
        self.assertEqual(self.store.prior_volume_work_ids(lone["id"]), [])

    def test_set_member_volume_conflict_refused(self):
        self._add_all()
        with self.assertRaises(SeriesError):
            self.store.set_member_volume(self.sid, self.book3["id"], volume=1)
        # And a valid re-volume works.
        self.store.remove_member(self.sid, self.book3["id"])
        s = self.store.set_member_volume(self.sid, self.book2["id"], volume=5)
        self.assertEqual(s["members"][-1]["volume"], 5)

    def test_delete_refused_while_series_canon_exists(self):
        fact = self.canon.create_fact(
            statement="The moon is shattered", classification="INVENTED",
            signed_by="author", series_id=self.sid,
        )
        self.assertEqual(self.store.delete_series(self.sid), "has_canon")
        self.assertIsNotNone(self.store.get_series(self.sid))
        # Retracting the series canon unblocks deletion — only ACTIVE canon
        # holds authority; a retracted fact must not make a series immortal.
        self.canon.retract_fact(fact["id"], signed_by="author")
        self.assertEqual(self.store.delete_series(self.sid), "ok")
        self.assertIsNone(self.store.get_series(self.sid))

    def test_delete_empty_series_ok(self):
        self.store.add_member(self.sid, self.book1["id"], volume=1)
        self.assertEqual(self.store.delete_series(self.sid), "ok")
        self.assertIsNone(self.store.get_series(self.sid))


class SeriesCanonScopingTests(_Base):
    def _series_fact(self, statement="The capital is Vael"):
        return self.canon.create_fact(
            statement=statement, classification="INVENTED",
            signed_by="author", series_id=self.sid,
        )

    def test_series_fact_requires_null_work_id(self):
        with self.assertRaises(CanonFactError):
            self.canon.create_fact(
                statement="x", classification="INVENTED", signed_by="author",
                work_id=self.book1["id"], series_id=self.sid,
            )

    def test_series_fact_requires_existing_series(self):
        with self.assertRaises(CanonFactError):
            self.canon.create_fact(
                statement="x", classification="INVENTED", signed_by="author",
                series_id="nope",
            )

    def test_series_fact_visible_to_members_only(self):
        self._add_all()
        fact = self._series_fact()
        outsider = self.db.create_work(title="Unrelated")
        ids_in = {f["id"] for f in self.canon.list_facts(work_id=self.book2["id"])}
        ids_out = {f["id"] for f in self.canon.list_facts(work_id=outsider["id"])}
        self.assertIn(fact["id"], ids_in)
        self.assertNotIn(fact["id"], ids_out)

    def test_earlier_volume_binds_later_never_reverse(self):
        self._add_all()
        f1 = self.canon.create_fact(
            statement="The bridge burned in the war", classification="INVENTED",
            signed_by="author", work_id=self.book1["id"],
        )
        f3 = self.canon.create_fact(
            statement="The bridge was rebuilt", classification="INVENTED",
            signed_by="author", work_id=self.book3["id"],
        )
        book3_ids = {f["id"] for f in self.canon.list_facts(work_id=self.book3["id"])}
        book1_ids = {f["id"] for f in self.canon.list_facts(work_id=self.book1["id"])}
        self.assertIn(f1["id"], book3_ids, "book 1 canon must bind book 3")
        self.assertNotIn(f3["id"], book1_ids, "book 3 canon must NOT leak backward")

    def test_other_series_facts_invisible(self):
        self._add_all()
        other = self.store.create_series(title="Other")
        other_work = self.db.create_work(title="Other Book")
        self.store.add_member(other["id"], other_work["id"], volume=1)
        foreign = self.canon.create_fact(
            statement="Foreign law", classification="INVENTED",
            signed_by="author", series_id=other["id"],
        )
        ids = {f["id"] for f in self.canon.list_facts(work_id=self.book2["id"])}
        self.assertNotIn(foreign["id"], ids)

    def test_legacy_global_facts_still_visible(self):
        fact = self.canon.create_fact(
            statement="Global truth", classification="INVENTED", signed_by="author",
        )
        ids = {f["id"] for f in self.canon.list_facts(work_id=self.book1["id"])}
        self.assertIn(fact["id"], ids)


class SeriesOverrideTests(_Base):
    def setUp(self):
        super().setUp()
        self._add_all()
        self.base = self.canon.create_fact(
            statement="Magic is forbidden", classification="INVENTED",
            signed_by="author", series_id=self.sid,
        )

    def _override(self, work_id, statement="Magic is legalized this book"):
        return self.canon.create_fact(
            statement=statement, classification="INVENTED", signed_by="author",
            work_id=work_id, overrides=self.base["id"],
        )

    def test_override_requires_book_scope(self):
        with self.assertRaises(CanonFactError):
            self.canon.create_fact(
                statement="x", classification="INVENTED", signed_by="author",
                overrides=self.base["id"],
            )

    def test_override_target_must_be_visible_to_book(self):
        outsider = self.db.create_work(title="Outsider")
        with self.assertRaises(CanonFactError):
            self._override(outsider["id"])

    def test_override_target_must_be_series_or_global(self):
        book_fact = self.canon.create_fact(
            statement="Local fact", classification="INVENTED",
            signed_by="author", work_id=self.book1["id"],
        )
        with self.assertRaises(CanonFactError):
            self.canon.create_fact(
                statement="x", classification="INVENTED", signed_by="author",
                work_id=self.book2["id"], overrides=book_fact["id"],
            )

    def test_only_one_active_override_per_book(self):
        self._override(self.book2["id"])
        with self.assertRaises(CanonFactError):
            self._override(self.book2["id"], statement="Another override")

    def test_override_hides_target_for_that_book_only(self):
        ov = self._override(self.book2["id"])
        book2_ids = {f["id"] for f in self.canon.list_facts(work_id=self.book2["id"])}
        book3_ids = {f["id"] for f in self.canon.list_facts(work_id=self.book3["id"])}
        self.assertNotIn(self.base["id"], book2_ids, "overridden fact must be hidden")
        self.assertIn(ov["id"], book2_ids, "the override IS book 2's version")
        self.assertIn(self.base["id"], book3_ids, "other volumes keep the base fact")

    def test_series_fact_cannot_be_an_override(self):
        with self.assertRaises(CanonFactError):
            self.canon.create_fact(
                statement="x", classification="INVENTED", signed_by="author",
                series_id=self.sid, overrides=self.base["id"],
            )

    def test_override_of_nonexistent_work_refused(self):
        with self.assertRaises(CanonFactError):
            self.canon.create_fact(
                statement="x", classification="INVENTED", signed_by="author",
                work_id="no-such-work", overrides=self.base["id"],
            )

    def test_superseding_an_override_keeps_the_override(self):
        """Revising an override must NOT resurrect the series fact."""
        ov = self._override(self.book2["id"])
        revised = self.canon.create_fact(
            statement="Magic is licensed this book", classification="INVENTED",
            signed_by="author", work_id=self.book2["id"], supersedes=ov["id"],
        )
        self.assertEqual(revised["overrides"], self.base["id"],
                         "replacement must inherit the override target")
        book2_ids = {f["id"] for f in self.canon.list_facts(work_id=self.book2["id"])}
        self.assertNotIn(self.base["id"], book2_ids,
                         "series fact must stay hidden after the revision")
        self.assertIn(revised["id"], book2_ids)

    def test_superseding_an_override_cannot_retarget(self):
        other_base = self.canon.create_fact(
            statement="Second series law", classification="INVENTED",
            signed_by="author", series_id=self.sid,
        )
        ov = self._override(self.book2["id"])
        with self.assertRaises(CanonFactError):
            self.canon.create_fact(
                statement="x", classification="INVENTED", signed_by="author",
                work_id=self.book2["id"], supersedes=ov["id"],
                overrides=other_base["id"],
            )

    def test_superseding_an_override_from_another_book_refused(self):
        ov = self._override(self.book2["id"])
        with self.assertRaises(CanonFactError):
            self.canon.create_fact(
                statement="x", classification="INVENTED", signed_by="author",
                work_id=self.book3["id"], supersedes=ov["id"],
            )

    def test_retracting_an_override_restores_the_series_fact(self):
        """Retract = the author removes the departure — series canon applies."""
        ov = self._override(self.book2["id"])
        self.canon.retract_fact(ov["id"], signed_by="author")
        book2_ids = {f["id"] for f in self.canon.list_facts(work_id=self.book2["id"])}
        self.assertIn(self.base["id"], book2_ids,
                      "retracting the override restores the series fact")


class SeriesContinuityGuardTests(_Base):
    """Membership mutations must never silently rewrite established canon."""

    def setUp(self):
        super().setUp()
        self._add_all()

    def _book_fact(self, work_id, statement="A binding fact"):
        return self.canon.create_fact(
            statement=statement, classification="INVENTED",
            signed_by="author", work_id=work_id,
        )

    def test_remove_earlier_volume_with_canon_refused(self):
        self._book_fact(self.book1["id"])
        with self.assertRaises(SeriesError):
            self.store.remove_member(self.sid, self.book1["id"])

    def test_remove_latest_volume_with_canon_ok(self):
        self._book_fact(self.book3["id"])
        self.assertTrue(self.store.remove_member(self.sid, self.book3["id"]))

    def test_remove_earlier_volume_ok_after_retraction(self):
        fact = self._book_fact(self.book1["id"])
        self.canon.retract_fact(fact["id"], signed_by="author")
        self.assertTrue(self.store.remove_member(self.sid, self.book1["id"]))

    def test_remove_member_with_dangling_override_refused(self):
        base = self.canon.create_fact(
            statement="Series law", classification="INVENTED",
            signed_by="author", series_id=self.sid,
        )
        self.canon.create_fact(
            statement="Departure", classification="INVENTED", signed_by="author",
            work_id=self.book3["id"], overrides=base["id"],
        )
        with self.assertRaises(SeriesError):
            self.store.remove_member(self.sid, self.book3["id"])

    def test_reorder_refused_once_canon_established(self):
        self._book_fact(self.book1["id"])
        with self.assertRaises(SeriesError):
            self.store.set_member_volume(self.sid, self.book3["id"], volume=9)

    def test_delete_series_with_binding_canon_refused(self):
        self._book_fact(self.book1["id"])
        self.assertEqual(self.store.delete_series(self.sid), "has_continuity")
        self.assertIsNotNone(self.store.get_series(self.sid))

    def test_add_canonized_work_ahead_of_existing_volumes_refused(self):
        """Inserting an already-canonized book earlier would bind later books
        to canon they were never verified against."""
        newcomer = self.db.create_work(title="Prequel")
        self.canon.create_fact(
            statement="Prequel law", classification="INVENTED",
            signed_by="author", work_id=newcomer["id"],
        )
        self.store.remove_member(self.sid, self.book1["id"])  # free volume 1
        with self.assertRaises(SeriesError):
            self.store.add_member(self.sid, newcomer["id"], volume=1)
        # Joining as the LATEST volume is fine — nothing later depends on it.
        s = self.store.add_member(self.sid, newcomer["id"], volume=4)
        self.assertEqual(s["members"][-1]["work_id"], newcomer["id"])

    def test_supersede_keeps_series_scope(self):
        """A revision changes what a fact SAYS, never where it applies."""
        base = self.canon.create_fact(
            statement="Series law", classification="INVENTED",
            signed_by="author", series_id=self.sid,
        )
        # Bare supersede (no series_id passed) inherits the series scope.
        revised = self.canon.create_fact(
            statement="Series law, clarified", classification="INVENTED",
            signed_by="author", supersedes=base["id"],
        )
        self.assertEqual(revised["series_id"], self.sid)
        # Rescoping to another series, to a book, or dropping to a different
        # explicit scope is refused.
        other = self.store.create_series(title="Other")
        with self.assertRaises(CanonFactError):
            self.canon.create_fact(
                statement="x", classification="INVENTED", signed_by="author",
                supersedes=revised["id"], series_id=other["id"],
            )
        with self.assertRaises(CanonFactError):
            self.canon.create_fact(
                statement="x", classification="INVENTED", signed_by="author",
                supersedes=revised["id"], work_id=self.book1["id"],
            )

    def test_supersede_keeps_book_scope(self):
        fact = self._book_fact(self.book1["id"])
        with self.assertRaises(CanonFactError):
            self.canon.create_fact(
                statement="x", classification="INVENTED", signed_by="author",
                supersedes=fact["id"],  # work_id omitted → global: refused
            )
        revised = self.canon.create_fact(
            statement="revised", classification="INVENTED", signed_by="author",
            work_id=self.book1["id"], supersedes=fact["id"],
        )
        self.assertEqual(revised["work_id"], self.book1["id"])

    def test_supersede_cannot_turn_fact_into_override(self):
        base = self.canon.create_fact(
            statement="Series law", classification="INVENTED",
            signed_by="author", series_id=self.sid,
        )
        plain = self._book_fact(self.book2["id"])
        with self.assertRaises(CanonFactError):
            self.canon.create_fact(
                statement="x", classification="INVENTED", signed_by="author",
                work_id=self.book2["id"], supersedes=plain["id"],
                overrides=base["id"],
            )


class LoomInheritanceTests(_Base):
    def setUp(self):
        super().setUp()
        self._add_all()

    def test_replay_folds_earlier_volumes_first(self):
        self.db.create_graph_node(
            work_id=self.book1["id"], chapter_id=None, node_type="Location",
            name="Vael", description="capital city, intact",
            evidence_quote="the capital of Vael", evidence_offset=0,
        )
        self.db.create_graph_node(
            work_id=self.book2["id"], chapter_id=None, node_type="Location",
            name="Vael", description="capital city, in ruins",
            evidence_quote="Vael lay in ruins", evidence_offset=0,
        )
        result = replay_world_state(self.db, self.book2["id"], upto_seq=99)
        self.assertEqual(result["prior_volumes"], 1)
        state = self.db.get_world_state(self.book2["id"])
        self.assertIn("Location:Vael", state)
        # Current book folds LAST — its state wins on overlap.
        self.assertIn("ruins", state["Location:Vael"]["value"])
        # Book 1 sees only its own state, never book 2's.
        replay_world_state(self.db, self.book1["id"], upto_seq=99)
        state1 = self.db.get_world_state(self.book1["id"])
        self.assertIn("intact", state1["Location:Vael"]["value"])

    def test_persona_inherited_from_nearest_prior_volume(self):
        pid = self.db.create_loom_persona(self.book1["id"], "Mara", {"voice": "clipped"})
        self.db.resolve_loom_persona(pid, decision="approved", author="author")
        personas = _personas_for_cast(self.db, self.book3["id"], ["Mara"])
        self.assertEqual(personas[0]["inherited_from_work_id"], self.book1["id"])

    def test_local_persona_beats_inherited(self):
        p1 = self.db.create_loom_persona(self.book1["id"], "Mara", {"voice": "clipped"})
        self.db.resolve_loom_persona(p1, decision="approved", author="author")
        p2 = self.db.create_loom_persona(self.book2["id"], "Mara", {"voice": "weary"})
        self.db.resolve_loom_persona(p2, decision="approved", author="author")
        personas = _personas_for_cast(self.db, self.book2["id"], ["Mara"])
        self.assertEqual(personas[0]["payload"]["voice"], "weary")
        self.assertNotIn("inherited_from_work_id", personas[0])

    def test_unapproved_prior_persona_never_inherited(self):
        self.db.create_loom_persona(self.book1["id"], "Ghost", {"voice": "?"})  # proposed
        from orivellum.capabilities.loom import LoomError
        with self.assertRaises(LoomError):
            _personas_for_cast(self.db, self.book2["id"], ["Ghost"])

    def test_voice_baseline_resolution_order(self):
        self.db.set_assay_baseline(self.book1["id"], "voice_envelope", {"tone": "v1"})
        # Book 3 inherits from the NEAREST prior volume that has one.
        r3 = resolve_assay_baseline(self.db, self.book3["id"], "voice_envelope")
        self.assertTrue(r3["inherited"])
        self.assertEqual(r3["source_work_id"], self.book1["id"])
        self.db.set_assay_baseline(self.book2["id"], "voice_envelope", {"tone": "v2"})
        r3b = resolve_assay_baseline(self.db, self.book3["id"], "voice_envelope")
        self.assertEqual(r3b["source_work_id"], self.book2["id"])
        # Own baseline is an explicit override — never inherited.
        self.db.set_assay_baseline(self.book3["id"], "voice_envelope", {"tone": "v3"})
        r3c = resolve_assay_baseline(self.db, self.book3["id"], "voice_envelope")
        self.assertFalse(r3c["inherited"])
        self.assertEqual(r3c["payload"]["tone"], "v3")
        # Volume 1 with no baseline resolves to None (nothing earlier).
        lone = self.db.create_work(title="Standalone")
        self.assertIsNone(resolve_assay_baseline(self.db, lone["id"], "voice_envelope"))


class SeriesApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        from orivellum.api import _deps
        from orivellum.api.app import app
        from orivellum.configuration.config import OrivellumConfig

        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        _deps.init(db=self.db, cfg=OrivellumConfig(data_dir=self._tmp.name))
        self.client = TestClient(app, raise_server_exceptions=True, headers=AUTH_HEADERS)
        self.book1 = self.db.create_work(title="Book One")
        self.book2 = self.db.create_work(title="Book Two")

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _make_series(self):
        r = self.client.post("/api/series", json={"title": "The Trilogy"})
        self.assertEqual(r.status_code, 200, r.text)
        sid = r.json()["id"]
        for wid, vol in ((self.book1["id"], 1), (self.book2["id"], 2)):
            r = self.client.post(
                f"/api/series/{sid}/members", json={"work_id": wid, "volume": vol}
            )
            self.assertEqual(r.status_code, 200, r.text)
        return sid

    def test_protected_member_removal_is_409(self):
        """Continuity-protected removals surface as an actionable refusal."""
        sid = self._make_series()
        CanonStore(self.db).create_fact(
            statement="Book one law", classification="INVENTED",
            signed_by="author", work_id=self.book1["id"],
        )
        r = self.client.delete(f"/api/series/{sid}/members/{self.book1['id']}")
        self.assertEqual(r.status_code, 409, r.text)
        self.assertIn("later volumes", r.json()["detail"])
        # The unprotected latest volume still removes cleanly.
        r = self.client.delete(f"/api/series/{sid}/members/{self.book2['id']}")
        self.assertEqual(r.status_code, 200, r.text)

    def test_crud_and_membership_conflicts(self):
        sid = self._make_series()
        r = self.client.get(f"/api/series/{sid}")
        self.assertEqual([m["volume"] for m in r.json()["members"]], [1, 2])
        # Duplicate volume via API → 422 refusal, not a 500.
        w3 = self.db.create_work(title="Book Three")
        r = self.client.post(
            f"/api/series/{sid}/members", json={"work_id": w3["id"], "volume": 2}
        )
        self.assertEqual(r.status_code, 422)
        r = self.client.get("/api/series")
        self.assertEqual(r.json()["series"][0]["member_count"], 2)

    def test_overview_counts_and_cross_book_labels(self):
        sid = self._make_series()
        canon = CanonStore(self.db)
        canon.create_fact(
            statement="Series law", classification="INVENTED",
            signed_by="author", series_id=sid,
        )
        f1 = canon.create_fact(
            statement="The tower fell", classification="INVENTED",
            signed_by="author", work_id=self.book1["id"],
        )
        # A book-2 finding that contradicts book 1's canon fact — grounded
        # in a real book-2 chapter (LAW 3 is enforced at the write path).
        text = "the tower stood tall over the valley that morning."
        ch_id = _seed_chapter(self.db, self.book2["id"], 1, "One", text)
        fid = self.db.create_narrative_finding(
            work_id=self.book2["id"], chapter_id=ch_id,
            category="worldbuilding", subtype="core_rules",
            fact_quote="The tower fell", fact_chapter=0, fact_offset=0,
            contradiction_quote="the tower stood", contradiction_chapter=1,
            contradiction_offset=0, canon_class="INVENTED",
            canon_fact_id=f1["id"], dedupe_key="xbook-tower",
        )
        self.assertIsNotNone(fid)
        r = self.client.get(f"/api/series/{sid}/overview")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["series_canon_facts"], 1)
        vol2 = next(v for v in body["volumes"] if v["volume"] == 2)
        self.assertEqual(vol2["cross_book_findings"], 1)
        self.assertEqual(vol2["continuity"], "attention")
        self.assertEqual(body["continuity"], "attention")
        vol1 = next(v for v in body["volumes"] if v["volume"] == 1)
        self.assertEqual(vol1["canon_facts"], 1)
        self.assertEqual(vol1["continuity"], "ok")
        # The findings surface labels the source book.
        r = self.client.get(f"/api/works/{self.book2['id']}/findings")
        finding = r.json()["findings"][0]
        self.assertEqual(finding["cross_book"]["work_id"], self.book1["id"])
        self.assertEqual(finding["cross_book"]["title"], "Book One")
        self.assertEqual(finding["cross_book"]["volume"], 1)

    def test_delete_with_series_canon_is_409(self):
        sid = self._make_series()
        CanonStore(self.db).create_fact(
            statement="Series law", classification="INVENTED",
            signed_by="author", series_id=sid,
        )
        r = self.client.delete(f"/api/series/{sid}")
        self.assertEqual(r.status_code, 409)


if __name__ == "__main__":
    unittest.main()
