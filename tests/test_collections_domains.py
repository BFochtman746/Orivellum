"""Collections, canon domains, and safe conversions (task: keep collections,
series, and shared canon distinct).

Covers:
- Migration integrity: new tables + new series_member / canon_fact columns
- Collections (book_collection): CRUD, mixed membership (series + standalone),
  provenance `collection` table stays untouched, domain-guarded removal
- Canon domains: membership (series/work/collection), domain-scoped facts
  visible to every served book via the ONE shared FACT_VISIBILITY_SQL clause,
  invisible to outsiders; exclusive scope enforced
- Supersession inherits domain scope; cross-scope supersede refused
- Book overrides of domain facts gated on membership
- Multi-order series members: descriptive orders freely editable even when
  canon exists; volume changes still refused (order is authority)
- Conversions: deterministic recommendation; standalone→series preserves the
  Work and promotes NO canon; per-item promotion retract+establish; ledger
  reversal restores the prior shape
- API routes: auth required, CRUD + preview + reverse smoke
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from orivellum.database.canon_store import (
    CanonFactError,
    CanonStore,
    domain_serves_work,
)
from orivellum.database.db import OrivellumDB
from orivellum.database.series_store import SeriesError, SeriesStore
from orivellum.database.structure_store import (
    CollectionStore,
    ConversionService,
    DomainStore,
    StructureError,
    recommend_classification,
)
from tests.conftest import AUTH_HEADERS


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = OrivellumDB(str(Path(self._tmp.name) / "test.db"))
        self.series_store = SeriesStore(self.db)
        self.canon = CanonStore(self.db)
        self.collections = CollectionStore(self.db)
        self.domains = DomainStore(self.db)
        self.conversions = ConversionService(self.db)

    def tearDown(self):
        self.db.close()
        self._tmp.cleanup()

    def _work(self, title: str) -> dict:
        return self.db.create_work(title=title)

    def _series_with(self, title: str, *works: dict) -> dict:
        s = self.series_store.create_series(title=title)
        for i, w in enumerate(works, start=1):
            self.series_store.add_member(s["id"], w["id"], volume=i)
        return s

    def _fact(self, **kw) -> dict:
        kw.setdefault("classification", "INVENTED")
        kw.setdefault("signed_by", "author")
        return self.canon.create_fact(**kw)


class MigrationTests(_Base):
    def test_new_tables_and_columns_exist(self):
        conn = self.db.read_conn()
        for tbl in (
            "book_collection",
            "book_collection_member",
            "canon_domain",
            "canon_domain_member",
            "conversion_ledger",
        ):
            conn.execute(f"SELECT * FROM {tbl} LIMIT 0")
        sm_cols = {r[1] for r in conn.execute("PRAGMA table_info(series_member)")}
        self.assertTrue(
            {"chronology_order", "publication_order", "relationship_type"} <= sm_cols
        )
        cf_cols = {r[1] for r in conn.execute("PRAGMA table_info(canon_fact)")}
        self.assertIn("domain_id", cf_cols)

    def test_existing_members_default_relationship_volume(self):
        w = self._work("Solo")
        s = self._series_with("S", w)
        m = self.series_store.list_members(s["id"])[0]
        self.assertEqual(m["relationship_type"], "volume")


class CollectionTests(_Base):
    def test_mixed_membership_two_series_plus_standalone(self):
        a1, a2 = self._work("A1"), self._work("A2")
        b1 = self._work("B1")
        solo = self._work("Standalone")
        s_a = self._series_with("Alpha", a1, a2)
        s_b = self._series_with("Beta", b1)
        c = self.collections.create(title="The Family", collection_type="branded-theme")
        self.collections.add_member(c["id"], member_kind="series", member_id=s_a["id"])
        self.collections.add_member(c["id"], member_kind="series", member_id=s_b["id"])
        self.collections.add_member(c["id"], member_kind="work", member_id=solo["id"])
        got = self.collections.get(c["id"])
        kinds = sorted((m["member_kind"], m["title"]) for m in got["members"])
        self.assertEqual(
            kinds, [("series", "Alpha"), ("series", "Beta"), ("work", "Standalone")]
        )
        # reachable from every contained book, directly or via series
        for wid in (a1["id"], b1["id"], solo["id"]):
            colls = self.collections.collections_for_work(wid)
            self.assertEqual([x["id"] for x in colls], [c["id"]])

    def test_membership_is_branding_only_no_canon_or_order_effects(self):
        w1, w2 = self._work("W1"), self._work("W2")
        c = self.collections.create(title="Brand")
        self.collections.add_member(c["id"], member_kind="work", member_id=w1["id"])
        self.collections.add_member(c["id"], member_kind="work", member_id=w2["id"])
        self._fact(statement="W1 fact.", work_id=w1["id"])
        # sibling in the same collection sees NOTHING — no shared canon
        visible = self.canon.list_facts(work_id=w2["id"])
        self.assertEqual([f for f in visible if f["statement"] == "W1 fact."], [])

    def test_duplicate_and_missing_members_refused(self):
        w = self._work("W")
        c = self.collections.create(title="C")
        self.collections.add_member(c["id"], member_kind="work", member_id=w["id"])
        with self.assertRaises(StructureError):
            self.collections.add_member(c["id"], member_kind="work", member_id=w["id"])
        with self.assertRaises(StructureError):
            self.collections.add_member(c["id"], member_kind="series", member_id="nope")
        with self.assertRaises(StructureError):
            self.collections.add_member(c["id"], member_kind="chapter", member_id=w["id"])

    def test_provenance_collection_table_untouched(self):
        conn = self.db.read_conn()
        before = conn.execute("SELECT COUNT(*) AS n FROM collection").fetchone()["n"]
        c = self.collections.create(title="Reader family")
        w = self._work("W")
        self.collections.add_member(c["id"], member_kind="work", member_id=w["id"])
        after = conn.execute("SELECT COUNT(*) AS n FROM collection").fetchone()["n"]
        self.assertEqual(before, after)

    def test_add_member_to_domain_served_collection_requires_confirmation(self):
        w = self._work("W")
        newcomer = self._work("Newcomer")
        c = self.collections.create(title="C")
        self.collections.add_member(c["id"], member_kind="work", member_id=w["id"])
        d = self.domains.create(title="U")
        self.domains.add_member(d["id"], member_kind="collection", member_id=c["id"])
        self._fact(statement="Universe law.", domain_id=d["id"])
        # joining now binds the domain's canon — never silent
        with self.assertRaises(StructureError):
            self.collections.add_member(
                c["id"], member_kind="work", member_id=newcomer["id"]
            )
        # nothing was written by the refusal
        self.assertEqual(len(self.collections.get(c["id"])["members"]), 1)
        # explicit confirmation goes through, and the facts then bind
        self.collections.add_member(
            c["id"],
            member_kind="work",
            member_id=newcomer["id"],
            confirm_canon_binding=True,
        )
        visible = [x["statement"] for x in self.canon.list_facts(work_id=newcomer["id"])]
        self.assertIn("Universe law.", visible)

    def test_remove_member_checks_every_binding_domain(self):
        w = self._work("W")
        c = self.collections.create(title="C")
        self.collections.add_member(c["id"], member_kind="work", member_id=w["id"])
        d1 = self.domains.create(title="D1")
        d2 = self.domains.create(title="D2")
        self.domains.add_member(d1["id"], member_kind="collection", member_id=c["id"])
        self.domains.add_member(d2["id"], member_kind="collection", member_id=c["id"])
        # member is independently in d1 but NOT d2 — d2's facts still block
        self.domains.add_member(d1["id"], member_kind="work", member_id=w["id"])
        self._fact(statement="D1 law.", domain_id=d1["id"])
        self._fact(statement="D2 law.", domain_id=d2["id"])
        with self.assertRaises(StructureError) as ctx:
            self.collections.remove_member(c["id"], member_kind="work", member_id=w["id"])
        self.assertIn("D2", str(ctx.exception))
        # once d2's fact is retracted, removal is fine (d1 path is independent)
        for f in self.canon.list_facts(domain_id=d2["id"]):
            self.canon.retract_fact(f["id"], signed_by="author")
        self.assertTrue(
            self.collections.remove_member(c["id"], member_kind="work", member_id=w["id"])
        )

    def test_remove_member_allowed_via_other_collection_path(self):
        w = self._work("W")
        c1 = self.collections.create(title="C1")
        c2 = self.collections.create(title="C2")
        self.collections.add_member(c1["id"], member_kind="work", member_id=w["id"])
        self.collections.add_member(c2["id"], member_kind="work", member_id=w["id"])
        d = self.domains.create(title="D")
        self.domains.add_member(d["id"], member_kind="collection", member_id=c1["id"])
        self.domains.add_member(d["id"], member_kind="collection", member_id=c2["id"])
        self._fact(statement="Law.", domain_id=d["id"])
        # leaving c1 is fine — the domain still reaches w through c2
        self.assertTrue(
            self.collections.remove_member(c1["id"], member_kind="work", member_id=w["id"])
        )
        self.assertIn(
            "Law.", [x["statement"] for x in self.canon.list_facts(work_id=w["id"])]
        )

    def test_delete_refused_while_domain_member_and_removal_guard(self):
        w = self._work("W")
        s = self._series_with("S", w)
        c = self.collections.create(title="C")
        self.collections.add_member(c["id"], member_kind="series", member_id=s["id"])
        d = self.domains.create(title="Universe")
        self.domains.add_member(d["id"], member_kind="collection", member_id=c["id"])
        self.assertEqual(self.collections.delete(c["id"]), "in_domain")
        # once the domain has facts, removing the series from the collection
        # would silently unbind that canon — refused
        self._fact(statement="Shared law.", domain_id=d["id"])
        with self.assertRaises(StructureError):
            self.collections.remove_member(
                c["id"], member_kind="series", member_id=s["id"]
            )


class DomainVisibilityTests(_Base):
    def test_domain_fact_binds_every_served_book_across_two_series(self):
        a1, a2 = self._work("A1"), self._work("A2")
        b1 = self._work("B1")
        outsider = self._work("Outsider")
        s_a = self._series_with("Alpha", a1, a2)
        s_b = self._series_with("Beta", b1)
        d = self.domains.create(title="Shared World")
        self.domains.add_member(d["id"], member_kind="series", member_id=s_a["id"])
        self.domains.add_member(d["id"], member_kind="series", member_id=s_b["id"])
        f = self._fact(statement="The moon is shattered.", domain_id=d["id"])
        conn = self.db.read_conn()
        for wid in (a1["id"], a2["id"], b1["id"]):
            self.assertTrue(domain_serves_work(conn, d["id"], wid))
            visible = [x["id"] for x in self.canon.list_facts(work_id=wid)]
            self.assertIn(f["id"], visible, wid)
        self.assertFalse(domain_serves_work(conn, d["id"], outsider["id"]))
        self.assertNotIn(
            f["id"], [x["id"] for x in self.canon.list_facts(work_id=outsider["id"])]
        )

    def test_domain_via_collection_path(self):
        w = self._work("In collection")
        c = self.collections.create(title="C")
        self.collections.add_member(c["id"], member_kind="work", member_id=w["id"])
        d = self.domains.create(title="U")
        self.domains.add_member(d["id"], member_kind="collection", member_id=c["id"])
        f = self._fact(statement="Universe rule.", domain_id=d["id"])
        self.assertIn(f["id"], [x["id"] for x in self.canon.list_facts(work_id=w["id"])])

    def test_domain_scope_is_exclusive(self):
        w = self._work("W")
        s = self._series_with("S", w)
        d = self.domains.create(title="D")
        with self.assertRaises(CanonFactError):
            self._fact(statement="Bad.", work_id=w["id"], domain_id=d["id"])
        with self.assertRaises(CanonFactError):
            self._fact(statement="Bad.", series_id=s["id"], domain_id=d["id"])
        with self.assertRaises(CanonFactError):
            self._fact(statement="Bad.", domain_id="no-such-domain")

    def test_legacy_global_facts_exclude_domain_scoped(self):
        d = self.domains.create(title="D")
        self._fact(statement="Domain-only.", domain_id=d["id"])
        w = self._work("Unserved")
        # unserved book sees no domain facts; true globals still visible
        g = self._fact(statement="Everywhere.")
        visible = [x["statement"] for x in self.canon.list_facts(work_id=w["id"])]
        self.assertIn("Everywhere.", visible)
        self.assertNotIn("Domain-only.", visible)

    def test_member_removal_refused_while_facts_active(self):
        w = self._work("W")
        s = self._series_with("S", w)
        d = self.domains.create(title="D")
        self.domains.add_member(d["id"], member_kind="series", member_id=s["id"])
        f = self._fact(statement="Law.", domain_id=d["id"])
        with self.assertRaises(StructureError):
            self.domains.remove_member(d["id"], member_kind="series", member_id=s["id"])
        self.assertEqual(self.domains.delete(d["id"]), "has_canon")
        self.canon.retract_fact(f["id"], signed_by="author")
        self.assertTrue(
            self.domains.remove_member(d["id"], member_kind="series", member_id=s["id"])
        )
        self.assertEqual(self.domains.delete(d["id"]), "ok")


class DomainSupersedeOverrideTests(_Base):
    def _served(self):
        w = self._work("Book")
        s = self._series_with("S", w)
        d = self.domains.create(title="D")
        self.domains.add_member(d["id"], member_kind="series", member_id=s["id"])
        return w, s, d

    def test_supersede_inherits_domain_scope(self):
        w, s, d = self._served()
        f = self._fact(statement="Old law.", domain_id=d["id"])
        f2 = self._fact(statement="New law.", supersedes=f["id"])
        self.assertEqual(f2["domain_id"], d["id"])
        self.assertIsNone(f2["work_id"])
        self.assertEqual(self.canon.get_fact(f["id"])["status"], "superseded")

    def test_supersede_refuses_rescoping(self):
        w, s, d = self._served()
        f = self._fact(statement="Domain law.", domain_id=d["id"])
        with self.assertRaises(CanonFactError):
            self._fact(statement="Now book-scoped?", work_id=w["id"], supersedes=f["id"])
        with self.assertRaises(CanonFactError):
            self._fact(statement="Now series?", series_id=s["id"], supersedes=f["id"])

    def test_override_of_domain_fact_gated_on_membership(self):
        w, s, d = self._served()
        outsider = self._work("Outsider")
        f = self._fact(statement="The sky is green.", domain_id=d["id"])
        ov = self._fact(
            statement="In THIS book the sky is violet.",
            work_id=w["id"],
            overrides=f["id"],
        )
        # served book: override hides the domain fact, shows the override
        ids = {x["id"] for x in self.canon.list_facts(work_id=w["id"])}
        self.assertIn(ov["id"], ids)
        self.assertNotIn(f["id"], ids)
        with self.assertRaises(CanonFactError):
            self._fact(
                statement="Outsider override.",
                work_id=outsider["id"],
                overrides=f["id"],
            )

    def test_list_facts_domain_filter(self):
        w, s, d = self._served()
        f = self._fact(statement="Domain law.", domain_id=d["id"])
        self._fact(statement="Book law.", work_id=w["id"])
        rows = self.canon.list_facts(domain_id=d["id"])
        self.assertEqual([r["id"] for r in rows], [f["id"]])


class MultiOrderTests(_Base):
    def test_descriptive_orders_freely_editable_even_with_canon(self):
        w1, w2 = self._work("V1"), self._work("V2")
        s = self._series_with("S", w1, w2)
        self._fact(statement="Book 1 canon.", work_id=w1["id"])
        # volume change refused — order is authority
        with self.assertRaises(SeriesError):
            self.series_store.set_member_volume(s["id"], w2["id"], volume=5)
        # descriptive dimensions still editable
        self.series_store.set_member_orders(
            s["id"],
            w2["id"],
            chronology_order=1,
            publication_order=2,
            relationship_type="prequel",
        )
        m = next(
            m for m in self.series_store.list_members(s["id"]) if m["work_id"] == w2["id"]
        )
        self.assertEqual(m["chronology_order"], 1)
        self.assertEqual(m["publication_order"], 2)
        self.assertEqual(m["relationship_type"], "prequel")
        self.assertEqual(m["volume"], 2)  # authority order untouched

    def test_set_orders_validation(self):
        w = self._work("W")
        s = self._series_with("S", w)
        with self.assertRaises(SeriesError):
            self.series_store.set_member_orders(s["id"], w["id"])
        with self.assertRaises(SeriesError):
            self.series_store.set_member_orders(
                s["id"], w["id"], relationship_type="reboot"
            )
        with self.assertRaises(SeriesError):
            self.series_store.set_member_orders(s["id"], "not-a-member", chronology_order=1)


class RecommendationTests(_Base):
    def test_recommendations_are_deterministic(self):
        self.assertEqual(recommend_classification({})["recommendation"], "standalone")
        self.assertEqual(
            recommend_classification(
                {"recurring_cast": True, "unresolved_arc": True}
            )["recommendation"],
            "new-series",
        )
        self.assertEqual(
            recommend_classification({"existing_series_id": "s1"})["recommendation"],
            "next-in-series",
        )
        self.assertEqual(
            recommend_classification(
                {"existing_series_id": "s1", "before_existing": True}
            )["recommendation"],
            "prequel-novella-companion",
        )
        self.assertEqual(
            recommend_classification({"shared_world_only": True})["recommendation"],
            "shared-universe",
        )
        self.assertEqual(
            recommend_classification({"branding_only": True})["recommendation"],
            "collection-only",
        )
        # always explains itself and never hides the alternatives
        r = recommend_classification({"recurring_cast": True, "unresolved_arc": True})
        self.assertTrue(r["reasons"])
        self.assertIn("standalone", r["alternatives"])


class ConversionTests(_Base):
    def test_standalone_to_series_preserves_work_and_promotes_nothing(self):
        w = self._work("Solo")
        book_fact = self._fact(statement="Solo's private fact.", work_id=w["id"])
        res = self.conversions.convert_standalone_to_series(
            w["id"], series_title="New Series"
        )
        self.assertTrue(res["created_series"])
        sid = res["series"]["id"]
        members = self.series_store.list_members(sid)
        self.assertEqual([m["work_id"] for m in members], [w["id"]])
        # the Work row itself is untouched — same id, same title
        self.assertEqual(self.db.get_work(w["id"])["title"], "Solo")
        # NO silent canon promotion: the fact is still book-scoped
        f = self.canon.get_fact(book_fact["id"])
        self.assertEqual(f["work_id"], w["id"])
        self.assertIsNone(f["series_id"])
        self.assertIsNone(f["domain_id"])
        # ledgered
        entries = self.conversions.list_ledger(subject_id=w["id"])
        self.assertEqual(entries[0]["kind"], "standalone_to_series")

    def test_convert_refused_when_already_in_series(self):
        w = self._work("W")
        self._series_with("S", w)
        with self.assertRaises(StructureError):
            self.conversions.convert_standalone_to_series(w["id"], series_title="T")

    def test_reverse_membership_conversion_deletes_created_empty_series(self):
        w = self._work("Solo")
        res = self.conversions.convert_standalone_to_series(w["id"], series_title="T")
        out = self.conversions.reverse(res["ledger_id"])
        self.assertEqual(out["result"], "ok")
        self.assertTrue(out["deleted_series"])
        self.assertIsNone(self.series_store.series_for_work(w["id"]))
        with self.assertRaises(StructureError):
            self.conversions.reverse(res["ledger_id"])  # already reversed

    def test_link_preview_is_read_only_and_honest(self):
        w1, w2 = self._work("V1"), self._work("V2")
        s = self._series_with("S", w1, w2)
        self._fact(statement="Series law.", series_id=s["id"])
        self._fact(statement="Book 1 law.", work_id=w1["id"])
        newbie = self._work("Newbie")
        self._fact(statement="Newbie's own.", work_id=newbie["id"])
        before = len(self.canon.list_facts(work_id=newbie["id"], include_series=False))
        preview = self.conversions.link_preview(newbie["id"], series_id=s["id"])
        self.assertEqual(preview["proposed_volume"], 3)
        self.assertEqual(preview["own_fact_count"], 1)
        gained = {g["statement"] for g in preview["gained_facts"]}
        self.assertIn("Series law.", gained)
        self.assertIn("Book 1 law.", gained)
        # nothing changed
        self.assertEqual(
            len(self.canon.list_facts(work_id=newbie["id"], include_series=False)), before
        )
        self.assertIsNone(self.series_store.series_for_work(newbie["id"]))

    def test_promote_fact_to_series_retract_then_establish(self):
        w1, w2 = self._work("V1"), self._work("V2")
        s = self._series_with("S", w1, w2)
        f = self._fact(statement="Hero is left-handed.", work_id=w1["id"])
        out = self.conversions.promote_facts(
            [f["id"]], target_series_id=s["id"], signed_by="author"
        )
        self.assertEqual(out["promoted"], 1)
        new_id = out["results"][0]["new_fact_id"]
        old = self.canon.get_fact(f["id"])
        self.assertEqual(old["status"], "retracted")
        new = self.canon.get_fact(new_id)
        self.assertEqual(new["series_id"], s["id"])
        self.assertIsNone(new["work_id"])
        # now visible to the later volume too
        self.assertIn(new_id, [x["id"] for x in self.canon.list_facts(work_id=w2["id"])])

    def test_promote_refusals_are_per_item(self):
        w1, w2 = self._work("V1"), self._work("V2")
        s = self._series_with("S", w1, w2)
        outside = self._work("Outside")
        good = self._fact(statement="Promotable.", work_id=w1["id"])
        bad = self._fact(statement="Not a member.", work_id=outside["id"])
        base = self._fact(statement="Base.", series_id=s["id"])
        ov = self._fact(statement="Override.", work_id=w1["id"], overrides=base["id"])
        out = self.conversions.promote_facts(
            [good["id"], bad["id"], ov["id"], "ghost"],
            target_series_id=s["id"],
            signed_by="author",
        )
        self.assertEqual(out["promoted"], 1)
        self.assertEqual(out["refused"], 3)
        by_id = {r["fact_id"]: r for r in out["results"]}
        self.assertEqual(by_id[good["id"]]["result"], "ok")
        self.assertEqual(by_id[bad["id"]]["result"], "refused")
        self.assertEqual(by_id[ov["id"]]["result"], "refused")  # overrides never promote
        self.assertEqual(by_id["ghost"]["result"], "not_found")
        # refused facts untouched
        self.assertEqual(self.canon.get_fact(bad["id"])["status"], "active")

    def test_promote_to_domain_and_reverse(self):
        w = self._work("Book")
        s = self._series_with("S", w)
        d = self.domains.create(title="D")
        self.domains.add_member(d["id"], member_kind="series", member_id=s["id"])
        f = self._fact(statement="World law.", work_id=w["id"])
        out = self.conversions.promote_facts(
            [f["id"]], target_domain_id=d["id"], signed_by="author"
        )
        self.assertEqual(out["promoted"], 1)
        new_id = out["results"][0]["new_fact_id"]
        self.assertEqual(self.canon.get_fact(new_id)["domain_id"], d["id"])
        ledger = self.conversions.list_ledger(subject_id=f["id"])
        rev = self.conversions.reverse(ledger[0]["id"])
        self.assertEqual(rev["result"], "ok")
        self.assertEqual(self.canon.get_fact(new_id)["status"], "retracted")
        restored = self.canon.get_fact(rev["restored_fact_id"])
        self.assertEqual(restored["work_id"], w["id"])
        self.assertEqual(restored["statement"], "World law.")

    def test_conversion_is_atomic_ledger_failure_rolls_back_membership(self):
        from unittest import mock

        w = self._work("Solo")
        with mock.patch.object(
            ConversionService, "_ledger", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                self.conversions.convert_standalone_to_series(
                    w["id"], series_title="Doomed"
                )
        # nothing survives a half-finished conversion — no membership, no series
        self.assertIsNone(self.series_store.series_for_work(w["id"]))
        self.assertEqual(
            [s for s in self.series_store.list_series() if s["title"] == "Doomed"], []
        )
        self.assertEqual(self.conversions.list_ledger(subject_id=w["id"]), [])

    def test_reversal_is_atomic_restore_failure_keeps_promoted_fact(self):
        from unittest import mock

        w = self._work("Book")
        s = self._series_with("S", w)
        f = self._fact(statement="Law.", work_id=w["id"])
        out = self.conversions.promote_facts(
            [f["id"]], target_series_id=s["id"], signed_by="author"
        )
        new_id = out["results"][0]["new_fact_id"]
        ledger_id = self.conversions.list_ledger(subject_id=f["id"])[0]["id"]
        with mock.patch.object(
            CanonStore, "create_fact", side_effect=CanonFactError("cannot restore")
        ):
            with self.assertRaises(CanonFactError):
                self.conversions.reverse(ledger_id)
        # the retraction rolled back with the failed restore — canon never lost
        self.assertEqual(self.canon.get_fact(new_id)["status"], "active")
        entry = self.conversions.list_ledger(subject_id=f["id"])[0]
        self.assertIsNone(entry["reversed_by"])
        # and the reversal still works once the obstacle is gone
        rev = self.conversions.reverse(ledger_id)
        self.assertEqual(rev["result"], "ok")

    def test_promote_requires_exactly_one_target_and_signature(self):
        w = self._work("W")
        f = self._fact(statement="X.", work_id=w["id"])
        with self.assertRaises(StructureError):
            self.conversions.promote_facts([f["id"]], signed_by="a")
        with self.assertRaises(StructureError):
            self.conversions.promote_facts(
                [f["id"]], target_series_id="s", target_domain_id="d", signed_by="a"
            )
        with self.assertRaises(StructureError):
            self.conversions.promote_facts([f["id"]], target_series_id="s", signed_by=" ")


class ApiTests(_Base):
    def setUp(self):
        super().setUp()
        from orivellum.api import _deps
        from orivellum.api.app import app
        from orivellum.configuration.config import OrivellumConfig

        _deps.init(db=self.db, cfg=OrivellumConfig(data_dir=self._tmp.name))
        self.client = TestClient(app, raise_server_exceptions=True)

    def _post(self, url: str, body: dict):
        return self.client.post(url, json=body, headers=AUTH_HEADERS)

    def test_auth_required(self):
        self.assertIn(
            self.client.get("/api/collections").status_code, (401, 403)
        )
        self.assertIn(
            self.client.get("/api/canon-domains").status_code, (401, 403)
        )
        self.assertIn(
            self.client.get("/api/conversions/ledger").status_code, (401, 403)
        )

    def test_collections_crud_and_members(self):
        w = self._work("Solo")
        r = self._post("/api/collections", {"title": "Family", "collection_type": "anthology"})
        self.assertEqual(r.status_code, 200, r.text)
        cid = r.json()["id"]
        r = self._post(
            f"/api/collections/{cid}/members",
            {"member_kind": "work", "member_id": w["id"]},
        )
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(f"/api/collections/{cid}", headers=AUTH_HEADERS)
        self.assertEqual(len(r.json()["members"]), 1)
        r = self.client.patch(
            f"/api/collections/{cid}", json={"status": "active"}, headers=AUTH_HEADERS
        )
        self.assertEqual(r.json()["status"], "active")
        r = self.client.patch(
            f"/api/collections/{cid}", json={"status": "bogus"}, headers=AUTH_HEADERS
        )
        self.assertEqual(r.status_code, 422)
        r = self.client.delete(
            f"/api/collections/{cid}/members/work/{w['id']}", headers=AUTH_HEADERS
        )
        self.assertEqual(r.status_code, 200)
        r = self.client.delete(f"/api/collections/{cid}", headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 200)

    def test_domain_routes_and_fact_listing(self):
        w = self._work("Book")
        s = self._series_with("S", w)
        r = self._post("/api/canon-domains", {"title": "U", "domain_type": "fictional"})
        did = r.json()["id"]
        r = self._post(
            f"/api/canon-domains/{did}/members",
            {"member_kind": "series", "member_id": s["id"]},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self._fact(statement="Domain law.", domain_id=did)
        r = self.client.get(f"/api/canon-domains/{did}/facts", headers=AUTH_HEADERS)
        self.assertEqual(len(r.json()["facts"]), 1)
        # delete refused while canon lives
        r = self.client.delete(f"/api/canon-domains/{did}", headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 409)

    def test_conversion_routes(self):
        w = self._work("Solo")
        r = self._post("/api/conversions/recommend", {"recurring_cast": True, "unresolved_arc": True})
        self.assertEqual(r.json()["recommendation"], "new-series")
        r = self._post(
            "/api/conversions/recommend", {"existing_series_id": "no-such"}
        )
        self.assertEqual(r.status_code, 422)
        r = self._post(
            "/api/conversions/standalone-to-series",
            {"work_id": w["id"], "series_title": "T"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        ledger_id = r.json()["ledger_id"]
        sid = r.json()["series"]["id"]
        r = self.client.get(
            f"/api/conversions/link-preview?work_id={w['id']}&series_id={sid}",
            headers=AUTH_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        r = self._post(f"/api/conversions/{ledger_id}/reverse", {})
        self.assertEqual(r.status_code, 200, r.text)
        r = self._post(f"/api/conversions/{ledger_id}/reverse", {})
        self.assertEqual(r.status_code, 409)

    def test_member_orders_and_reorder_preview_routes(self):
        w1, w2 = self._work("V1"), self._work("V2")
        s = self._series_with("S", w1, w2)
        self._fact(statement="Canon.", work_id=w1["id"])
        r = self.client.patch(
            f"/api/series/{s['id']}/members/{w2['id']}/orders",
            json={"chronology_order": 1, "relationship_type": "prequel"},
            headers=AUTH_HEADERS,
        )
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.get(
            f"/api/series/{s['id']}/reorder-preview?work_id={w2['id']}&volume=1",
            headers=AUTH_HEADERS,
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["allowed"])  # canon exists — order is authority
        self.assertTrue(body["blockers"])
        self.assertTrue(body["impacts"])

    def test_work_scopes_route(self):
        w = self._work("Book")
        s = self._series_with("S", w)
        c = self.collections.create(title="C")
        self.collections.add_member(c["id"], member_kind="series", member_id=s["id"])
        d = self.domains.create(title="D")
        self.domains.add_member(d["id"], member_kind="collection", member_id=c["id"])
        self._fact(statement="Book fact.", work_id=w["id"])
        self._fact(statement="Domain fact.", domain_id=d["id"])
        r = self.client.get(f"/api/works/{w['id']}/scopes", headers=AUTH_HEADERS)
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["series"]["id"], s["id"])
        self.assertEqual([x["id"] for x in body["collections"]], [c["id"]])
        self.assertEqual([x["id"] for x in body["domains"]], [d["id"]])
        self.assertEqual(body["canon_counts"], {"book": 1, "series": 0, "domain": 1})


if __name__ == "__main__":
    unittest.main()
