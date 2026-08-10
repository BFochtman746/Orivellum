"""Data-tier classifier — the missing intake layer.

Every object entering Orivellum gets a TIER so the rest of the system knows
what it is. This is the single fact that stops migration/build artifacts from
becoming "Works" and knowledge nodes.

Deterministic rules decide the clear cases (no AI call). Only genuinely
ambiguous items fall through to an optional injected LLM tiebreak, so this
module is import-safe and unit-testable with zero third-party dependencies.

Tiers:
  CANON        — your creative truth: manuscripts, chapters, series bible.
  SOURCE       — ingested reference/research you brought in (evidence for RAG).
  ARTIFACT     — migration/build/system dumps that must NEVER become Works.
  CONVERSATION — chat history / exported conversations.
  SYSTEM       — config/lock/dependency/build files.

CLAIM is a separate runtime tier owned by the PKLOS ledger, not by file
classification, so it is intentionally not returned here.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath


class Tier(str, Enum):
    CANON = "canon"
    SOURCE = "source"
    ARTIFACT = "artifact"
    CONVERSATION = "conversation"
    SYSTEM = "system"


# Tiers whose members must never be promoted to a Work or a knowledge node.
EXCLUDED_FROM_WORKS = frozenset({Tier.ARTIFACT, Tier.SYSTEM})

# ── deterministic rule tables (first match wins, evaluated in this order) ──────

_SYSTEM_NAMES = {
    "progress.json", "package.json", "package-lock.json", "pnpm-lock.yaml",
    "yaml.lock", "uv.lock", "poetry.lock", "requirements.txt", "pyproject.toml",
    "manifest.json", "manifest.txt", ".replit", ".gitignore", "makefile",
    "dockerfile", "tsconfig.json", "pnpm-workspace.yaml", "replit.nix",
}
_SYSTEM_EXT = {
    ".lock", ".log", ".pyc", ".pyo", ".map", ".tmp", ".bak", ".cache",
    ".toml", ".ini", ".cfg", ".lockb", ".sha256", ".sig",
}
_SYSTEM_PATH = re.compile(
    r"(^|/)(node_modules|__pycache__|\.git|dist|build|\.venv|venv|"
    r"\.replit-artifact|artifacts)/", re.I)

# The exact shapes that polluted the corpus (migration batches, run/report ids).
_ARTIFACT_NAME = re.compile(
    r"(migration[_\- ]?batch"       # A01_MIGRATION_BATCH_011...
    r"|^a0\d[_\-]"                   # A01_ / A02_ prefixes
    r"|\bRP[-_ ]?\d{2,}"            # RP-011 Core Function
    r"|\bRun[-_ ]?\d{2,}"          # Run-001 Not Run
    r"|_v\d+\.\d+\.\d+"             # ..._v1.0.0 versioned artifact
    r"|\bbaseline\b|\bqualification\b|\bregression\b|\bfixture\b)", re.I)

_CONVERSATION_NAME = re.compile(
    r"(chat[_\- ]?export|conversation[_\- ]?\d|message[_\- ]?log|"
    r"transcript|\bchat\b.*\.(json|jsonl|txt)$)", re.I)

# Creative canon: manuscript/chapter/scene markers, or the known series.
_CANON_NAME = re.compile(
    r"(ash[_\- ]?and[_\- ]?silence"
    r"|\bchapter[_\- ]?\d"
    r"|\bch\d{1,3}\b"
    r"|\bmanuscript\b|\bscene\b|\bact[_\- ]?\d"
    r"|\bdraft[_\- ]?\d|series[_\- ]?bible|book[_\- ]?bible)", re.I)
_CANON_PATH = re.compile(r"(^|/)(works|manuscript|canon|chapters)/", re.I)

# Readable document extensions default to SOURCE unless matched above.
_READABLE_EXT = {".docx", ".pdf", ".txt", ".md", ".epub", ".odt", ".rtf",
                 ".pptx", ".xlsx", ".csv", ".html"}


@dataclass(frozen=True)
class Classification:
    tier: Tier
    reason: str
    confidence: float  # 1.0 = deterministic; <0.6 = fell through to default/LLM


def classify_object(
    name: str,
    *,
    kind: str | None = None,
    sample_text: str | None = None,
    source_path: str | None = None,
    llm_tiebreak: Callable[[str, str | None], Tier | None] | None = None,
) -> Classification:
    """Return the tier for one object. Deterministic first; LLM only if ambiguous.

    `llm_tiebreak(name, sample_text) -> Tier | None` is injected by the caller so
    this module never imports the serving stack (keeps it unit-testable).
    """
    raw = (name or "").strip()
    low = raw.lower()
    path = (source_path or name or "")
    ext = PurePosixPath(low).suffix

    # 1. SYSTEM — config/build/dependency files and build directories.
    if low in _SYSTEM_NAMES or ext in _SYSTEM_EXT or _SYSTEM_PATH.search(path):
        return Classification(Tier.SYSTEM, "system/build/config file", 1.0)

    # 2. ARTIFACT — migration/batch/run/version dumps (the real pollution).
    if _ARTIFACT_NAME.search(low):
        return Classification(Tier.ARTIFACT, "migration/build artifact pattern", 1.0)

    # 3. CONVERSATION — chat/transcript exports.
    if _CONVERSATION_NAME.search(low):
        return Classification(Tier.CONVERSATION, "conversation/transcript export", 1.0)

    # 4. CANON — manuscripts, chapters, series bible, known series.
    if _CANON_NAME.search(low) or _CANON_PATH.search(path):
        return Classification(Tier.CANON, "manuscript/canon marker", 0.95)

    # 5. SOURCE — a readable reference document with no artifact/canon marker.
    if ext in _READABLE_EXT or (kind and kind in {"docx", "pdf", "txt", "md", "epub"}):
        return Classification(Tier.SOURCE, "readable reference document", 0.85)

    # 6. Ambiguous — ask the injected LLM if available, else default SOURCE (low conf).
    if llm_tiebreak is not None:
        guess = llm_tiebreak(raw, sample_text)
        if isinstance(guess, Tier):
            return Classification(guess, "llm tiebreak", 0.6)

    return Classification(Tier.SOURCE, "default (unmatched) — review recommended", 0.3)
