"""Finishing Suite — PRESS (manuscript finalization) + ATELIER (cover/series design)."""
from __future__ import annotations

from pathlib import Path


def configure(data_dir: str) -> None:
    """Point both subsystems at their DB files inside *data_dir*."""
    from . import press, atelier
    press.configure(data_dir)
    atelier.configure(data_dir)
    press.cmd_init(None)
    atelier.cmd_init(None)
