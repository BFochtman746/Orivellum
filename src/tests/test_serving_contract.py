"""FS-2: ServingConfig contract — no missing .model attribute anywhere.

Verifies that:
1. ServingConfig().model returns the same value as workhorse_model (back-compat).
2. No route module reads the old `.model` attribute directly (all uses are now
   explicit: workhorse_model / reasoner_model / …).
"""
from pathlib import Path


def test_model_alias_equals_workhorse():
    from orivellum.configuration.config import ServingConfig
    cfg = ServingConfig()
    assert cfg.model == cfg.workhorse_model, (
        f"ServingConfig.model ({cfg.model!r}) != workhorse_model ({cfg.workhorse_model!r})"
    )


def test_no_raw_model_attr_in_route_modules():
    """Grep the route modules for cfg.serving.model (bare) — should be zero after FS-2.

    Only flags the specific pattern `serving.model` (i.e. the config attribute),
    not legitimate uses like `body.model` or `{"model": ...}`.
    """
    import re
    route_dir = Path(__file__).resolve().parents[1] / "orivellum" / "api" / "routes"
    # Match `serving.model` as a whole token, not `body.model` or dict keys.
    _pat = re.compile(r"\bserving\.model\b(?!_)")
    hits = []
    for py in route_dir.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if _pat.search(line) and "# noqa" not in line:
                hits.append(f"  {py.name}:{i}: {line.strip()}")
    assert not hits, (
        "Route modules still reference cfg.serving.model (bare):\n" + "\n".join(hits)
    )


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} tests passed")
