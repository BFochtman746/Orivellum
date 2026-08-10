"""Tests for the data-tier classifier — the file-system upgrade's core guard.

Runs with pytest OR standalone: `python src/tests/test_classify.py`.
Uses the REAL names that polluted the corpus so a regression is caught.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from orivellum.capabilities.classify import EXCLUDED_FROM_WORKS, Tier, classify_object

# (name, source_path, expected_tier)
CASES = [
    # ── the actual pollution: these MUST be ARTIFACT (never Works/knowledge) ──
    ("A01_MIGRATION_BATCH_011_EXCEL365_BIBLE_VAULT.zip", None, Tier.ARTIFACT),
    ("A01_MIGRATION_BATCH_013_MONARCH_KNOWLEDGE.zip", None, Tier.ARTIFACT),
    ("RP-011 Core Function.md", None, Tier.ARTIFACT),
    ("Run-001 Not Run Run.txt", None, Tier.ARTIFACT),
    ("EXCEL365_BIBLE_VAULT_v1.0.0.docx", None, Tier.ARTIFACT),
    # ── system/build/config ──
    ("PROGRESS.json", None, Tier.SYSTEM),
    ("pnpm-lock.yaml", None, Tier.SYSTEM),
    ("app.pyc", None, Tier.SYSTEM),
    ("notes.txt", "repo/node_modules/pkg/notes.txt", Tier.SYSTEM),
    # ── creative canon ──
    ("Ash_and_Silence_Chapter_12.docx", None, Tier.CANON),
    ("chapter_03_draft.md", None, Tier.CANON),
    ("series_bible.md", None, Tier.CANON),
    ("scene_notes.docx", "works/ash/scene_notes.docx", Tier.CANON),
    # ── ingested source/reference ──
    ("2 Kings historical background.docx", None, Tier.SOURCE),
    ("KJV_Bible.txt", None, Tier.SOURCE),
    ("research_flour_prices_samaria.pdf", None, Tier.SOURCE),
    # ── conversation exports ──
    ("chat_export_2026-08.json", None, Tier.CONVERSATION),
    ("conversation_17.jsonl", None, Tier.CONVERSATION),
]


def test_all_cases():
    failures = []
    for name, path, expected in CASES:
        got = classify_object(name, source_path=path).tier
        if got is not expected:
            failures.append(f"  {name!r}: expected {expected.value}, got {got.value}")
    assert not failures, "Misclassifications:\n" + "\n".join(failures)


def test_artifacts_never_become_works():
    # Every ARTIFACT/SYSTEM case must be in the excluded set.
    for name, path, expected in CASES:
        c = classify_object(name, source_path=path)
        if expected in (Tier.ARTIFACT, Tier.SYSTEM):
            assert c.tier in EXCLUDED_FROM_WORKS, f"{name} should be excluded from Works"


def test_deterministic_no_llm_needed_for_known_shapes():
    # The known cases must all resolve deterministically (confidence >= 0.85),
    # i.e. never fall through to the LLM tiebreak / low-confidence default.
    for name, path, _ in CASES:
        c = classify_object(name, source_path=path)
        assert c.confidence >= 0.85, f"{name} fell through: {c.reason} ({c.confidence})"


def test_ambiguous_uses_injected_llm():
    called = {}

    def fake_llm(n, txt):
        called["hit"] = n
        return Tier.SOURCE

    c = classify_object("mystery_blob_xyz", kind=None, llm_tiebreak=fake_llm)
    assert called.get("hit") == "mystery_blob_xyz"
    assert c.reason == "llm tiebreak"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} tests passed")
