"""
GENESIS — Book Origination System capability package.
Ported from the standalone genesis.py CLI; runs entirely in-process,
stores everything in the main Orivellum SQLite DB.
"""
from .gates import (
    STAGE_BY_CODE,
    STAGE_CODES,
    STAGES,
    canonical,
    get_stage_status,
    ledger_append,
    next_open_stage,
    now_iso,
    sha256_text,
)
from .seal import compute_seal, verify_ledger
from .templates import TEMPLATE_CONTENT

__all__ = [
    "STAGES", "STAGE_CODES", "STAGE_BY_CODE",
    "TEMPLATE_CONTENT",
    "ledger_append", "sha256_text", "canonical", "now_iso",
    "get_stage_status", "next_open_stage",
    "compute_seal", "verify_ledger",
]
