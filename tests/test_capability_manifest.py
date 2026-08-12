"""Capability manifest gate — unit rules + full-repo integration pass.

The gate (scripts/check_capability_manifest.py) is the CI contract that keeps
every live API operation classified by product fate, and keeps shipped
operations honest against the typed OpenAPI contract and the real UI route
table. These tests exercise each failure condition with injected fixtures,
then run the real gate against the actual repository state.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from check_capability_manifest import check  # noqa: E402

STATUSES = ["pwa", "external_api", "admin_tooling", "internal", "experimental", "archived"]


def _manifest(operations: dict, backlog: list[str] | None = None) -> dict:
    return {
        "statuses": STATUSES,
        "contract_backlog": backlog or [],
        "operations": operations,
    }


def _live(*ops: tuple[str, str, str]) -> list[dict]:
    return [{"method": m, "path": p, "module": mod, "in_schema": True} for m, p, mod in ops]


UI_ROUTES = {"/", "/canon", "/works/:workId"}


def _run(manifest, live, spec_ops=frozenset(), ui_routes=frozenset(UI_ROUTES), baseline=None):
    if baseline is None:
        # by default, treat the fixture's own backlog as the frozen baseline
        baseline = set(manifest.get("contract_backlog", []))
    return check(
        manifest=manifest,
        live=live,
        spec_ops=set(spec_ops),
        ui_routes=set(ui_routes),
        backlog_baseline=set(baseline),
    )


class TestGateRules:
    def test_clean_manifest_passes(self):
        live = _live(("GET", "/api/canon/facts", "canon"))
        manifest = _manifest(
            {"GET /api/canon/facts": {"status": "pwa", "module": "canon", "ui_route": "/canon"}}
        )
        assert _run(manifest, live, spec_ops={("GET", "/api/canon/facts")}) == []

    def test_unclassified_live_operation_fails(self):
        live = _live(("GET", "/api/canon/facts", "canon"), ("POST", "/api/new/thing", "new"))
        manifest = _manifest(
            {"GET /api/canon/facts": {"status": "pwa", "module": "canon", "ui_route": "/canon"}}
        )
        problems = _run(manifest, live, spec_ops={("GET", "/api/canon/facts")})
        assert any("UNCLASSIFIED" in p and "POST /api/new/thing" in p for p in problems)

    def test_stale_manifest_entry_fails(self):
        manifest = _manifest({"GET /api/gone": {"status": "internal", "module": "old"}})
        problems = _run(manifest, _live())
        assert any("STALE" in p and "GET /api/gone" in p for p in problems)

    def test_module_drift_fails(self):
        live = _live(("GET", "/api/x", "moved_here"))
        manifest = _manifest({"GET /api/x": {"status": "internal", "module": "old_home"}})
        problems = _run(manifest, live)
        assert any("MODULE DRIFT" in p for p in problems)

    def test_invalid_status_fails(self):
        live = _live(("GET", "/api/x", "m"))
        manifest = _manifest({"GET /api/x": {"status": "shipped", "module": "m"}})
        problems = _run(manifest, live)
        assert any("BAD STATUS" in p for p in problems)

    def test_pwa_without_ui_route_fails(self):
        live = _live(("GET", "/api/x", "m"))
        manifest = _manifest({"GET /api/x": {"status": "pwa", "module": "m"}})
        problems = _run(manifest, live, spec_ops={("GET", "/api/x")})
        assert any("NO UI OWNER" in p for p in problems)

    def test_dead_ui_route_fails(self):
        live = _live(("GET", "/api/x", "m"))
        manifest = _manifest(
            {"GET /api/x": {"status": "pwa", "module": "m", "ui_route": "/nowhere"}}
        )
        problems = _run(manifest, live, spec_ops={("GET", "/api/x")})
        assert any("DEAD UI ROUTE" in p and "/nowhere" in p for p in problems)

    def test_shipped_but_uncontracted_fails_unless_backlogged(self):
        live = _live(("GET", "/api/x", "m"))
        entry = {"GET /api/x": {"status": "pwa", "module": "m", "ui_route": "/canon"}}
        problems = _run(_manifest(entry), live)  # not in spec, no backlog
        assert any("UNCONTRACTED" in p for p in problems)
        # grandfathered in the shrink-only backlog → allowed
        assert _run(_manifest(entry, backlog=["GET /api/x"]), live) == []

    def test_param_names_normalized_for_contract_match(self):
        live = _live(("GET", "/api/works/{work_id}/facts", "works"))
        manifest = _manifest(
            {
                "GET /api/works/{work_id}/facts": {
                    "status": "pwa",
                    "module": "works",
                    "ui_route": "/works/:workId",
                }
            }
        )
        # spec uses {workId}; shapes must match positionally
        assert _run(manifest, live, spec_ops={("GET", "/api/works/{}/facts")}) == []

    def test_specced_backlog_entry_is_stale(self):
        live = _live(("GET", "/api/x", "m"))
        manifest = _manifest(
            {"GET /api/x": {"status": "pwa", "module": "m", "ui_route": "/canon"}},
            backlog=["GET /api/x"],
        )
        problems = _run(manifest, live, spec_ops={("GET", "/api/x")})
        assert any("BACKLOG STALE" in p for p in problems)

    def test_backlog_entry_for_unknown_operation_is_stale(self):
        manifest = _manifest({}, backlog=["GET /api/never-existed"])
        problems = _run(manifest, _live())
        assert any("BACKLOG STALE" in p and "never-existed" in p for p in problems)

    def test_non_pwa_backlog_entry_is_stale(self):
        live = _live(("GET", "/api/x", "m"))
        manifest = _manifest(
            {"GET /api/x": {"status": "experimental", "module": "m"}}, backlog=["GET /api/x"]
        )
        problems = _run(manifest, live)
        assert any("BACKLOG STALE" in p for p in problems)

    def test_duplicate_backlog_entries_fail(self):
        live = _live(("GET", "/api/x", "m"))
        manifest = _manifest(
            {"GET /api/x": {"status": "pwa", "module": "m", "ui_route": "/canon"}},
            backlog=["GET /api/x", "GET /api/x"],
        )
        problems = _run(manifest, live)
        assert any("duplicate" in p for p in problems)

    def test_backlog_growth_beyond_frozen_baseline_fails(self):
        live = _live(("GET", "/api/x", "m"))
        manifest = _manifest(
            {"GET /api/x": {"status": "pwa", "module": "m", "ui_route": "/canon"}},
            backlog=["GET /api/x"],
        )
        # the frozen baseline does NOT contain the key → growth is refused
        problems = _run(manifest, live, baseline=set())
        assert any("BACKLOG GROWTH" in p and "GET /api/x" in p for p in problems)

    def test_registration_mismatch_fails(self):
        live = _live(("GET", "/api/x", "m"))
        manifest = _manifest({"GET /api/x": {"status": "pwa", "module": "m", "ui_route": "/canon"}})
        # app registers an op the module walk never saw (defined outside routes pkg)
        problems = check(
            manifest=manifest,
            live=live,
            spec_ops={("GET", "/api/x")},
            ui_routes=set(UI_ROUTES),
            backlog_baseline=set(),
            registered_ops={("GET", "/api/x"), ("POST", "/api/rogue")},
        )
        assert any("REGISTRATION" in p and "outside the routes package" in p for p in problems)

    def test_unregistered_router_module_fails(self):
        live = _live(("GET", "/api/x", "m"), ("GET", "/api/dead", "dead"))
        manifest = _manifest(
            {
                "GET /api/x": {"status": "pwa", "module": "m", "ui_route": "/canon"},
                "GET /api/dead": {"status": "internal", "module": "dead"},
            }
        )
        problems = check(
            manifest=manifest,
            live=live,
            spec_ops={("GET", "/api/x")},
            ui_routes=set(UI_ROUTES),
            backlog_baseline=set(),
            registered_ops={("GET", "/api/x")},
        )
        assert any("REGISTRATION" in p and "never registers it" in p for p in problems)

    def test_contract_op_classified_internal_is_contradicted(self):
        live = _live(("GET", "/api/x", "m"))
        manifest = _manifest({"GET /api/x": {"status": "internal", "module": "m"}})
        problems = _run(manifest, live, spec_ops={("GET", "/api/x")})
        assert any("CONTRADICTED" in p for p in problems)

    def test_external_api_in_contract_is_fine(self):
        live = _live(("POST", "/api/mcp", "mcp"))
        manifest = _manifest({"POST /api/mcp": {"status": "external_api", "module": "mcp"}})
        assert _run(manifest, live, spec_ops={("POST", "/api/mcp")}) == []


class TestRealRepository:
    """The actual manifest must be consistent with the actual app, spec, and UI."""

    @pytest.fixture(scope="class")
    def problems(self):
        return check()

    def test_gate_passes(self, problems):
        assert problems == [], "\n".join(problems)

    def test_ui_route_table_parsed(self):
        from check_capability_manifest import load_ui_routes

        routes = load_ui_routes()
        # sanity: the parser must find the real route table, not an empty set
        assert len(routes) > 20
        assert "/canon" in routes and "/works/:workId" in routes

    def test_spec_parsed(self):
        from check_capability_manifest import load_spec_ops

        ops = load_spec_ops()
        assert len(ops) > 100  # curated contract is large; empty parse = broken gate
