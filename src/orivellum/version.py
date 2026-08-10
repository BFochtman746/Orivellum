"""Code-version provenance stamp (ADR 0004 lineage: outputs must be traceable).

Every generated document records exactly which code produced it. The stamp is
resolved once per process:

1. ``ORIVELLUM_BUILD`` env var, when set (packaged/CI builds), else
2. ``git describe --always --dirty`` in the repo root (dev machines), else
3. ``"unknown"`` — never crash a generation path over provenance.
"""

from __future__ import annotations

import functools
import os
import pathlib
import subprocess

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent


@functools.lru_cache(maxsize=1)
def code_version() -> str:
    """Return the version stamp string, e.g. ``a1b2c3d`` or ``a1b2c3d-dirty``."""
    env = os.environ.get("ORIVELLUM_BUILD", "").strip()
    if env:
        return env
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        stamp = out.stdout.strip()
        if out.returncode == 0 and stamp:
            return stamp
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"
