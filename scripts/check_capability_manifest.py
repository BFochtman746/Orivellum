"""Capability manifest gate — nothing ships unwired, nothing rots unlisted.

Every live API operation must have a declared product fate in
scripts/capability_manifest.json (pwa / external_api / admin_tooling /
internal / experimental / archived).  This turns "built but not wired" from a
recurring forensic-audit finding into a release failure.

Verified bidirectionally against three sources of truth:
  live routes   — the real router table (scripts/generate_capability_inventory.py)
  typed spec    — lib/api-spec/openapi.yaml (the public contract)
  UI routes     — artifacts/orivellum-ui/src/App.tsx (the wouter route table)

Failure conditions:
  1. a live operation has no manifest entry (unclassified capability)
  2. a manifest entry names an operation that no longer exists (stale)
  3. a manifest entry's module contradicts the live router module (moved code)
  4. an invalid status, or a pwa entry without a ui_route
  5. a pwa entry's ui_route does not exist in the UI route table
  6. a pwa operation is absent from the typed contract AND not grandfathered
     in contract_backlog (backlog is SHRINK-ONLY: stale/specced entries fail)
  7. a typed-contract operation whose manifest status is not pwa/external_api
     (a public contract op classified internal/experimental is a contradiction)

Usage: python scripts/check_capability_manifest.py
Exit 0 = manifest consistent, 1 = violations.
"""

from __future__ import annotations

import contextlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from generate_capability_inventory import (  # noqa: E402
    _norm,
    collect_operations,
    registration_problems,
)

MANIFEST = ROOT / "scripts" / "capability_manifest.json"
BACKLOG_BASELINE = ROOT / "scripts" / "capability_contract_backlog_baseline.json"
SPEC = ROOT / "lib" / "api-spec" / "openapi.yaml"
APP_TSX = ROOT / "artifacts" / "orivellum-ui" / "src" / "App.tsx"
SPEC_METHODS = {"get", "post", "put", "patch", "delete"}
# Statuses whose operations are allowed (and expected) in the public contract.
CONTRACT_STATUSES = {"pwa", "external_api"}


def load_spec_ops() -> set[tuple[str, str]]:
    import yaml

    spec = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    base = (spec.get("servers") or [{}])[0].get("url", "").rstrip("/")
    ops: set[tuple[str, str]] = set()
    for path, item in (spec.get("paths") or {}).items():
        for method in item:
            if method in SPEC_METHODS:
                ops.add((method.upper(), _norm(base + path)))
    return ops


def load_ui_routes() -> set[str]:
    """Every wouter route path declared in App.tsx, plus the Home Screen."""
    text = APP_TSX.read_text(encoding="utf-8")
    return set(re.findall(r'<Route path="([^"]+)"', text)) | {"/"}


def _check_entry(
    key: str,
    entry: dict,
    live_op: dict,
    statuses: set[str],
    spec_ops: set[tuple[str, str]],
    ui_routes: set[str],
    seen_backlog: set[str],
) -> list[str]:
    """Rules 3-6b for one manifest entry that matches a live operation."""
    problems: list[str] = []
    status = entry.get("status")
    # 3. module provenance must match the real router table
    if entry.get("module") != live_op["module"]:
        problems.append(
            f"MODULE DRIFT: {key} lives in router '{live_op['module']}' "
            f"but manifest says '{entry.get('module')}'"
        )
    # 4. status validity + pwa needs an owner
    if status not in statuses:
        problems.append(f"BAD STATUS: {key} has status {status!r}")
        return problems
    ui_route = entry.get("ui_route")
    if status == "pwa" and not ui_route:
        problems.append(f"NO UI OWNER: {key} is 'pwa' but names no ui_route")
    # 5. the named UI owner must actually exist
    if ui_route and ui_route not in ui_routes:
        problems.append(
            f"DEAD UI ROUTE: {key} points at {ui_route!r}, which is not in the App.tsx route table"
        )
    # 6. shipped ⇒ in the typed contract, unless grandfathered
    method, path = key.split(" ", 1)
    in_spec = (method, _norm(path)) in spec_ops
    if status == "pwa" and not in_spec and key not in seen_backlog:
        problems.append(
            f"UNCONTRACTED: {key} is shipped ('pwa') but absent from "
            "lib/api-spec/openapi.yaml — spec it (preferred) for typed "
            "client coverage; contract_backlog is shrink-only"
        )
    # 6b. backlog hygiene (shrink-only): specced or non-pwa entries must leave
    if key in seen_backlog:
        if in_spec:
            problems.append(
                f"BACKLOG STALE: {key} is now in the typed contract — "
                "remove it from contract_backlog"
            )
        if status != "pwa":
            problems.append(f"BACKLOG STALE: {key} is not 'pwa' — remove it from contract_backlog")
    return problems


def _load_defaults(manifest, live, spec_ops, ui_routes, backlog_baseline):
    """Fill any inputs the caller (production: none) did not inject."""
    if manifest is None:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if spec_ops is None:
        spec_ops = load_spec_ops()
    if ui_routes is None:
        ui_routes = load_ui_routes()
    if backlog_baseline is None:
        baseline_doc = json.loads(BACKLOG_BASELINE.read_text(encoding="utf-8"))
        backlog_baseline = set(baseline_doc["baseline"])
    if live is None:
        with contextlib.redirect_stdout(sys.stderr):
            live = collect_operations()
    return manifest, live, spec_ops, ui_routes, backlog_baseline


def check(
    *,
    manifest: dict | None = None,
    live: list[dict] | None = None,
    spec_ops: set[tuple[str, str]] | None = None,
    ui_routes: set[str] | None = None,
    registered_ops: set[tuple[str, str]] | None = None,
    backlog_baseline: set[str] | None = None,
) -> list[str]:
    """Return all violations. Args exist for tests; production passes none."""
    fixture_mode = live is not None  # unit tests inject a synthetic router table
    manifest, live, spec_ops, ui_routes, backlog_baseline = _load_defaults(
        manifest, live, spec_ops, ui_routes, backlog_baseline
    )
    statuses = set(manifest["statuses"])
    entries: dict[str, dict] = manifest["operations"]
    backlog = manifest.get("contract_backlog", [])

    problems: list[str] = []
    # 0. the module walk must match the app's REAL registered router table —
    #    an unregistered router module, or a route defined outside the routes
    #    package (even schema-hidden), makes the whole inventory a lie.
    if registered_ops is not None or not fixture_mode:
        with contextlib.redirect_stdout(sys.stderr):
            for p in registration_problems(live, registered_ops):
                problems.append(f"REGISTRATION: {p}")

    live_keys = {f"{o['method']} {o['path']}": o for o in live}
    seen_backlog = set(backlog)

    problems += _check_coverage(live_keys, entries)
    # shrink-only: the backlog may never gain entries beyond the frozen baseline
    for key in sorted(seen_backlog - backlog_baseline):
        problems.append(
            f"BACKLOG GROWTH: {key} is not in the frozen baseline "
            "(scripts/capability_contract_backlog_baseline.json) — new shipped "
            "operations must be added to lib/api-spec/openapi.yaml, not backlogged"
        )
    if len(seen_backlog) != len(backlog):
        problems.append("BACKLOG: contract_backlog contains duplicate entries")

    for key, entry in sorted(entries.items()):
        live_op = live_keys.get(key)
        if live_op is None:
            continue  # already reported as STALE
        problems.extend(
            _check_entry(key, entry, live_op, statuses, spec_ops, ui_routes, seen_backlog)
        )

    # 6c. backlog entries must reference real manifest operations
    for key in sorted(seen_backlog - set(entries)):
        problems.append(f"BACKLOG STALE: {key} is not a manifest operation")

    problems.extend(_check_contract(entries, live_keys, spec_ops))
    return problems


def _check_coverage(live_keys: dict[str, dict], entries: dict[str, dict]) -> list[str]:
    """Rules 1-2: live ⊆ manifest and manifest ⊆ live."""
    problems: list[str] = []
    for key in sorted(set(live_keys) - set(entries)):
        problems.append(
            f"UNCLASSIFIED: {key} ({live_keys[key]['module']}) has no manifest entry — "
            "run scripts/generate_capability_inventory.py --seed and classify it"
        )
    for key in sorted(set(entries) - set(live_keys)):
        problems.append(f"STALE: manifest lists {key} but the app no longer registers it")
    return problems


def _check_contract(
    entries: dict[str, dict],
    live_keys: dict[str, dict],
    spec_ops: set[tuple[str, str]],
) -> list[str]:
    """Rule 7: every contract operation must be classified as publicly shipped."""
    problems: list[str] = []
    entry_norms = {
        (k.split(" ", 1)[0], _norm(k.split(" ", 1)[1])): v
        for k, v in entries.items()
        if k in live_keys
    }
    for method, path in sorted(spec_ops):
        entry = entry_norms.get((method, path))
        if entry is None:
            # spec op missing from the app entirely — check_openapi_drift.py owns that
            continue
        if entry["status"] not in CONTRACT_STATUSES:
            problems.append(
                f"CONTRADICTED: {method} {path} is in the public contract but the "
                f"manifest classifies it '{entry['status']}'"
            )
    return problems


def main() -> int:
    problems = check()
    if problems:
        print(f"Capability manifest gate: {len(problems)} violation(s)")
        for p in problems:
            print(f"  - {p}")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    n = len(manifest["operations"])
    b = len(manifest.get("contract_backlog", []))
    print(f"Capability manifest OK ({n} operations classified, contract backlog {b}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
