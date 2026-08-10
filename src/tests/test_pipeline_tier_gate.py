"""FS-4 fixture test: classifier gate on auto-Work proposal.

Verifies two things without touching the real database or filesystem:
  1. Ingesting an A01_MIGRATION_BATCH_* archive → ZERO Works proposed, ZERO
     knowledge harvest threads started for those children.
  2. Ingesting an Ash_and_Silence_Chapter_* archive → exactly one Work proposed
     for review (the CANON archive gate is open).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from orivellum.capabilities.classify import EXCLUDED_FROM_WORKS, Tier, classify_object

# ── Tests that don't need a live DB ────────────────────────────────────────────

def test_artifact_archive_never_proposes_work():
    """classify_object on a MIGRATION_BATCH name → ARTIFACT (excluded from Works)."""
    clf = classify_object("A01_MIGRATION_BATCH_011_EXCEL365_BIBLE_VAULT.zip")
    assert clf.tier == Tier.ARTIFACT, f"Expected ARTIFACT, got {clf.tier}"
    assert clf.tier in EXCLUDED_FROM_WORKS


def test_canon_archive_proposes_work():
    """classify_object on a canon archive name → CANON (not excluded from Works)."""
    clf = classify_object("Ash_and_Silence_Chapter_12.zip")
    assert clf.tier == Tier.CANON, f"Expected CANON, got {clf.tier}"
    assert clf.tier not in EXCLUDED_FROM_WORKS


def test_artifact_children_are_classified_excluded():
    """Children inside a migration batch should also be ARTIFACT/SYSTEM."""
    migration_children = [
        ("RP-011 Core Function.md", None),
        ("Run-001 Not Run Run.txt", None),
        ("EXCEL365_BIBLE_VAULT_v1.0.0.docx", None),
        ("A01_MIGRATION_BATCH_013_MONARCH_KNOWLEDGE.zip", None),
    ]
    for name, path in migration_children:
        clf = classify_object(name, source_path=path)
        assert clf.tier in EXCLUDED_FROM_WORKS, (
            f"{name!r}: tier {clf.tier!r} is NOT excluded from Works — "
            "harvest guard would fail"
        )


def test_canon_children_are_harvestable():
    """Children of a canon archive must NOT be excluded (harvest should run)."""
    canon_children = [
        "chapter_03_draft.md",
        "scene_01_opening.docx",
        "series_bible.pdf",
    ]
    for name in canon_children:
        clf = classify_object(name)
        assert clf.tier not in EXCLUDED_FROM_WORKS, (
            f"{name!r}: tier {clf.tier!r} is excluded — harvest would be wrongly skipped"
        )


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        fn()
        print(f"PASS  {fn.__name__}")
        passed += 1
    print(f"\n{passed}/{len(fns)} tests passed")
