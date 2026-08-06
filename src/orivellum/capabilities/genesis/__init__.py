"""
GENESIS — Book Origination System capability package.
Ported from the standalone genesis.py CLI; runs entirely in-process,
stores everything in the main Orivellum SQLite DB.
"""
from .gates import (
    STAGES, STAGE_CODES, STAGE_BY_CODE,
    ledger_append, sha256_text, canonical, now_iso,
    get_stage_status, next_open_stage,
)
from .templates import TEMPLATE_CONTENT
from .seal import compute_seal, verify_ledger

__all__ = [
    "STAGES", "STAGE_CODES", "STAGE_BY_CODE",
    "TEMPLATE_CONTENT",
    "ledger_append", "sha256_text", "canonical", "now_iso",
    "get_stage_status", "next_open_stage",
    "compute_seal", "verify_ledger",
]
