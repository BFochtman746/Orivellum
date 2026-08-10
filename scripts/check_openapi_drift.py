"""OpenAPI drift gate — the hand-written spec must match the live app.

The UI's hooks and Zod schemas are generated from lib/api-spec/openapi.yaml
(ADR 0010). This gate fails when the spec references a path or method the
FastAPI app does not actually expose — the exact failure mode where the
frontend compiles against endpoints that no longer exist.

The spec is intentionally a curated SUBSET of the app (internal endpoints are
not all specced), so the check is one-directional: spec ⊆ app.

Scope: this is a route-shape/existence check. Path parameters are compared by
POSITION, not name ({workId} vs {work_id} both match {}), and request/response
schemas are not compared — semantic drift inside an operation is out of scope
and is covered by the generated client's TypeScript compile in the JS CI job.

Usage: python scripts/check_openapi_drift.py
Exit 0 = spec matches, 1 = drift.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = ROOT / "lib" / "api-spec" / "openapi.yaml"
METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_PARAM = __import__("re").compile(r"\{[^}]+\}")


def norm(path: str) -> str:
    """Spec says {workId}, FastAPI says {work_id} — compare shapes, not names."""
    return _PARAM.sub("{}", path)


def load_spec_ops() -> tuple[str, set[tuple[str, str]]]:
    import yaml

    spec = yaml.safe_load(SPEC.read_text(encoding="utf-8"))
    base = (spec.get("servers") or [{}])[0].get("url", "").rstrip("/")
    ops = set()
    for path, item in (spec.get("paths") or {}).items():
        for method in item:
            if method in METHODS:
                ops.add((method.upper(), norm(base + path)))
    return base, ops


def load_app_ops() -> set[tuple[str, str]]:
    sys.path.insert(0, str(ROOT / "src"))
    with tempfile.TemporaryDirectory() as tmp:
        from orivellum.api import _deps
        from orivellum.api.app import app
        from orivellum.configuration.config import OrivellumConfig
        from orivellum.database.db import OrivellumDB

        db = OrivellumDB(str(pathlib.Path(tmp) / "drift.db"))
        _deps.init(db=db, cfg=OrivellumConfig(data_dir=tmp))
        schema = app.openapi()
    ops = set()
    for path, item in (schema.get("paths") or {}).items():
        for method in item:
            if method in METHODS:
                ops.add((method.upper(), norm(path)))
    return ops


def main() -> int:
    base, spec_ops = load_spec_ops()
    app_ops = load_app_ops()
    missing = sorted(spec_ops - app_ops)
    if missing:
        print(f"OpenAPI drift: {len(missing)} spec operation(s) do not exist in the app:")
        for method, path in missing:
            print(f"  - {method} {path}")
        print("Either restore the endpoint or remove it from lib/api-spec/openapi.yaml")
        print("(and regenerate the client so the UI stops referencing it).")
        return 1
    print(f"OpenAPI drift check OK ({len(spec_ops)} specced operations all exist in the app).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
