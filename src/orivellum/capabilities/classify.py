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
from enum import Enum, StrEnum
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
    "progress.json",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yaml.lock",
    "uv.lock",
    "poetry.lock",
    "requirements.txt",
    "pyproject.toml",
    "manifest.json",
    "manifest.txt",
    ".replit",
    ".gitignore",
    "makefile",
    "dockerfile",
    "tsconfig.json",
    "pnpm-workspace.yaml",
    "replit.nix",
}
_SYSTEM_EXT = {
    ".lock",
    ".log",
    ".pyc",
    ".pyo",
    ".map",
    ".tmp",
    ".bak",
    ".cache",
    ".toml",
    ".ini",
    ".cfg",
    ".lockb",
    ".sha256",
    ".sig",
}
_SYSTEM_PATH = re.compile(
    r"(^|/)(node_modules|__pycache__|\.git|dist|build|\.venv|venv|"
    r"\.replit-artifact|artifacts)/",
    re.I,
)

# The exact shapes that polluted the corpus (migration batches, run/report ids).
_ARTIFACT_NAME = re.compile(
    r"(migration[_\- ]?batch"  # A01_MIGRATION_BATCH_011...
    r"|^a0\d[_\-]"  # A01_ / A02_ prefixes
    r"|\bRP[-_ ]?\d{2,}"  # RP-011 Core Function
    r"|\bRun[-_ ]?\d{2,}"  # Run-001 Not Run
    r"|_v\d+\.\d+\.\d+"  # ..._v1.0.0 versioned artifact
    r"|\bbaseline\b|\bqualification\b|\bregression\b|\bfixture\b)",
    re.I,
)

_CONVERSATION_NAME = re.compile(
    r"(chat[_\- ]?export|conversation[_\- ]?\d|message[_\- ]?log|"
    r"transcript|\bchat\b.*\.(json|jsonl|txt)$)",
    re.I,
)

# Creative canon: manuscript/chapter/scene markers, or the known series.
_CANON_NAME = re.compile(
    r"(ash[_\- ]?and[_\- ]?silence"
    r"|\bchapter[_\- ]?\d"
    r"|\bch\d{1,3}\b"
    r"|\bmanuscript\b|\bscene\b|\bact[_\- ]?\d"
    r"|\bdraft[_\- ]?\d|series[_\- ]?bible|book[_\- ]?bible)",
    re.I,
)
_CANON_PATH = re.compile(r"(^|/)(works|manuscript|canon|chapters)/", re.I)

# Readable document extensions default to SOURCE unless matched above.
_READABLE_EXT = {
    ".docx",
    ".pdf",
    ".txt",
    ".md",
    ".epub",
    ".odt",
    ".rtf",
    ".pptx",
    ".xlsx",
    ".csv",
    ".html",
}


class DocType(StrEnum):
    """The finer dimension: which ontology and which pipeline apply.

    Tier answers "may this become a Work"; doc_type answers "what may touch
    this".  test_catalog and reference are both SOURCE — only one of them
    should ever be harvested as narrative.
    """

    MANUSCRIPT = "manuscript"  # chapter prose, drafts — may become a Book
    REFERENCE = "reference"  # handbooks, lexica, commentaries
    DOCTRINE = "doctrine"  # specs, engine contracts, policies
    TEST_CATALOG = "test_catalog"  # rp016-test-catalog.json and kin
    CODE = "code"  # .py, .ts, .tsx, …
    WORKBOOK = "workbook"  # .xlsx / tabular data
    CORRESPONDENCE = "correspondence"  # mail, chat exports
    GENERATED = "generated"  # reports/exports this system produced
    UNKNOWN = "unknown"  # residue — refuses harvest


# The single most protective rule: these doc_types refuse to be harvested.
# `unknown` alone would have prevented 88,891 knowledge items being extracted
# from unclassified material.
HARVEST_REFUSED_DOC_TYPES = frozenset({DocType.UNKNOWN, DocType.GENERATED, DocType.CORRESPONDENCE})

VALID_DOC_TYPES = frozenset(t.value for t in DocType)

# ── doc_type deterministic rule tables ─────────────────────────────────────────

_CODE_EXT = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".swift",
    ".kt",
    ".sh",
    ".bash",
    ".ps1",
    ".psm1",
    ".bat",
    ".sql",
    ".css",
    ".scss",
    ".vue",
    ".svelte",
}

_WORKBOOK_EXT = {".xlsx", ".xls", ".xlsm", ".csv", ".tsv", ".ods"}

_MAIL_EXT = {".eml", ".msg", ".mbox"}

# Filename conventions already in the corpus: rp###-test-catalog, baselines,
# qualification/regression suites, fixtures.
_TEST_CATALOG_NAME = re.compile(
    r"(rp[-_ ]?\d{2,}[-_ ]?test[-_ ]?catalog"
    r"|test[-_ ]?catalog"
    r"|test[-_ ]?suite"
    r"|test[-_ ]?cases?\b"
    r"|\bqualification\b|\bregression\b|\bfixture\b|\bbaseline\b)",
    re.I,
)

# JSON test catalogs have a recognisable key set even when the name doesn't say so.
_TEST_CATALOG_KEYS = re.compile(
    r"\"(test_id|test_case|testcase_id|expected_result|expected_output|"
    r"assertion|pass_criteria|test_steps)\"",
    re.I,
)

_DOCTRINE_NAME = re.compile(
    r"(\bspec(ification)?s?\b|\bdoctrine\b|\bpolic(y|ies)\b|\bcontract\b"
    r"|\bcharter\b|\bgovernance\b|\bstandards?\b|\bprotocol\b"
    r"|engine[-_ ]?contract|acceptance[-_ ]?criteria)",
    re.I,
)

_GENERATED_NAME = re.compile(
    r"(\breport\b|\bexport\b|\bdigest\b|\bsummary[-_ ]?output\b"
    r"|\bgenerated\b|\bautogen\b|orivellum[-_ ](report|export|plan)"
    r"|^tts clip\b)",
    re.I,
)

# ≥2 "Chapter N" headings at line starts = manuscript structure.
_CHAPTER_HEADING = re.compile(r"^\s{0,4}(chapter|ch\.?)\s+([0-9ivxlc]+)\b", re.I | re.M)

_REFERENCE_EXT = {".pdf", ".docx", ".doc", ".odt", ".rtf", ".epub", ".md", ".txt", ".html", ".htm"}

# meta keys any of which mark a file this system itself wrote
_GENERATED_META_KEYS = ("generated_by", "system_generated", "workshop_run", "forge_build")


@dataclass(frozen=True)
class DocTypeClassification:
    doc_type: DocType
    rule: str  # short rule name; doc_type_by becomes 'rule:<name>'
    confidence: float  # 1.0 deterministic; <0.6 fallback


def classify_doc_type(  # noqa: C901 — flat, ordered rule table
    name: str,
    *,
    kind: str | None = None,
    sample_text: str | None = None,
    source_path: str | None = None,
    meta: dict | None = None,
) -> DocTypeClassification:
    """Deterministic doc_type rules — first match wins, no model call ever.

    Ambiguous residue returns UNKNOWN (which refuses harvest); a model may
    later PROPOSE a type through the review queue, never apply one.
    """
    raw = (name or "").strip()
    low = raw.lower()
    # Underscores/hyphens are word characters, so "style_policy" would defeat
    # every \b-anchored rule — normalise separators for name-based matching.
    norm = re.sub(r"[_\-]+", " ", low)
    path = (source_path or name or "").lower()
    ext = PurePosixPath(low).suffix
    text = (sample_text or "")[:20000]

    # 1. Files this system itself produced.
    if meta and any(meta.get(k) for k in _GENERATED_META_KEYS):
        return DocTypeClassification(DocType.GENERATED, "system-meta", 1.0)
    if _GENERATED_NAME.search(norm):
        return DocTypeClassification(DocType.GENERATED, "generated-name", 0.9)

    # 2. Mail / chat exports.
    if ext in _MAIL_EXT or kind == "email" or path.startswith("mail:"):
        return DocTypeClassification(DocType.CORRESPONDENCE, "mail", 1.0)
    if _CONVERSATION_NAME.search(low):
        return DocTypeClassification(DocType.CORRESPONDENCE, "conversation-export", 0.95)

    # 3. Test catalogs — by name, or by JSON key shape.
    if _TEST_CATALOG_NAME.search(norm):
        return DocTypeClassification(DocType.TEST_CATALOG, "catalog-name", 1.0)
    if (ext == ".json" or kind == "json") and text and _TEST_CATALOG_KEYS.search(text):
        return DocTypeClassification(DocType.TEST_CATALOG, "catalog-json-keys", 0.95)

    # 4. Code and workbooks by extension.
    if ext in _CODE_EXT:
        return DocTypeClassification(DocType.CODE, "code-extension", 1.0)
    if ext in _WORKBOOK_EXT or kind in ("excel", "csv"):
        return DocTypeClassification(DocType.WORKBOOK, "workbook-extension", 1.0)

    # 5. Doctrine — governance/spec naming.
    if _DOCTRINE_NAME.search(norm):
        return DocTypeClassification(DocType.DOCTRINE, "doctrine-name", 0.9)

    # 6. Manuscript — canonical name markers, or ≥2 Chapter-N headings in text.
    if _CANON_NAME.search(low) or _CANON_PATH.search(path):
        return DocTypeClassification(DocType.MANUSCRIPT, "canon-name", 0.95)
    if text and len(_CHAPTER_HEADING.findall(text)) >= 2:
        return DocTypeClassification(DocType.MANUSCRIPT, "chapter-structure", 0.9)

    # 7. Readable document with none of the above → reference.
    if ext in _REFERENCE_EXT or (kind and kind in {"pdf", "docx", "text", "markdown", "html"}):
        return DocTypeClassification(DocType.REFERENCE, "readable-document", 0.8)

    # 8. Residue — unknown, refuses harvest until a human classifies it.
    return DocTypeClassification(DocType.UNKNOWN, "fallback", 0.3)


def assert_tier_may_become_work(tier: str | None, context: str = "become a Work") -> None:
    """Raise ValueError when *tier* is excluded from Work creation.

    ARTIFACT and SYSTEM objects must never produce a Work — this is the
    enforced refusal, not documentation.
    """
    if tier and any(tier == t.value for t in EXCLUDED_FROM_WORKS):
        raise ValueError(
            f"A {tier!r}-tier object may never {context} — "
            "migration/build artifacts and system files are excluded from Works."
        )


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
    path = source_path or name or ""
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
