"""Capability inventory generator — every live API operation, machine-readable.

Enumerates every router module under orivellum.api.routes (pkgutil walk — no
server, no lifespan) and emits the full operation list with method, path, and
owning router module.  Router prefixes are baked into APIRoute.path at
decoration time, so the walk needs no FastAPI internals.

Cross-check: the app's own OpenAPI schema (which reflects what is actually
REGISTERED on the app) must agree with the module walk — a router module that
exists but was never included in app.py, or an app route defined outside the
routes package, is reported and fails the run.  This keeps the inventory
honest against the real router table in both directions.

This is the "what actually exists" side of the capability manifest gate
(scripts/check_capability_manifest.py): the manifest classifies every one of
these operations by product fate; CI fails when they disagree.

Usage:
  python scripts/generate_capability_inventory.py            # print JSON
  python scripts/generate_capability_inventory.py --seed     # print a manifest
      skeleton for operations MISSING from scripts/capability_manifest.json
      (status "unclassified") — paste into the manifest and classify them.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import pathlib
import pkgutil
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "scripts" / "capability_manifest.json"
METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_PARAM = re.compile(r"\{[^}]+\}")


def _norm(path: str) -> str:
    """Compare path shapes, not param names ({work_id} vs {workId})."""
    return _PARAM.sub("{}", path)


def collect_operations() -> list[dict]:
    """Walk every orivellum.api.routes module and list its operations."""
    sys.path.insert(0, str(ROOT / "src"))
    from fastapi.routing import APIRoute

    import orivellum.api.routes as routes_pkg

    ops: list[dict] = []
    for modinfo in pkgutil.iter_modules(routes_pkg.__path__):
        module = importlib.import_module(f"orivellum.api.routes.{modinfo.name}")
        router = getattr(module, "router", None)
        if router is None:
            continue
        for route in router.routes:
            if not isinstance(route, APIRoute):
                continue
            for method in sorted(route.methods & METHODS):
                ops.append(
                    {
                        "method": method,
                        "path": route.path,
                        "module": modinfo.name,
                        "in_schema": bool(route.include_in_schema),
                    }
                )
    ops.sort(key=lambda o: (o["module"], o["path"], o["method"]))
    return ops


def registered_schema_ops() -> set[tuple[str, str]]:
    """(METHOD, normalized path) for every route REGISTERED on the app."""
    with tempfile.TemporaryDirectory() as tmp:
        from orivellum.api import _deps
        from orivellum.api.app import app
        from orivellum.configuration.config import OrivellumConfig
        from orivellum.database.db import OrivellumDB

        db = OrivellumDB(str(pathlib.Path(tmp) / "inventory.db"))
        _deps.init(db=db, cfg=OrivellumConfig(data_dir=tmp))
        schema = app.openapi()
    ops: set[tuple[str, str]] = set()
    for path, item in (schema.get("paths") or {}).items():
        for method in item:
            if method.upper() in METHODS:
                ops.add((method.upper(), _norm(path)))
    return ops


def cross_check(ops: list[dict]) -> list[str]:
    """Module walk vs app registration — return human-readable discrepancies."""
    app_ops = registered_schema_ops()
    walk_ops = {(o["method"], _norm(o["path"])) for o in ops if o["in_schema"]}
    problems = []
    for method, path in sorted(walk_ops - app_ops):
        problems.append(f"router module defines {method} {path} but the app never registers it")
    for method, path in sorted(app_ops - walk_ops):
        problems.append(f"app registers {method} {path} outside the routes package")
    return problems


def main() -> int:
    # The app configures logging to stdout at import — keep our stdout pure JSON.
    with contextlib.redirect_stdout(sys.stderr):
        ops = collect_operations()
        problems = cross_check(ops)
    if problems:
        for p in problems:
            print(f"INVENTORY MISMATCH: {p}", file=sys.stderr)
        return 1
    if "--seed" in sys.argv:
        known: set[str] = set()
        if MANIFEST.exists():
            known = set(json.loads(MANIFEST.read_text(encoding="utf-8"))["operations"])
        skeleton = {
            f"{o['method']} {o['path']}": {"status": "unclassified", "module": o["module"]}
            for o in ops
            if f"{o['method']} {o['path']}" not in known
        }
        print(json.dumps(skeleton, indent=2))
        print(f"\n{len(skeleton)} unclassified operation(s)", file=sys.stderr)
    else:
        print(json.dumps({"count": len(ops), "operations": ops}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
