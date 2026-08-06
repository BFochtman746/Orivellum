"""Shared helpers (ported from media_studio unchanged)."""
from __future__ import annotations
import os


def load_yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError as e:  # pragma: no cover
        raise SystemExit(
            "PyYAML is required. Run:  pip install pyyaml\n"
        ) from e
    if not os.path.exists(path):
        raise SystemExit(f"Config not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)
