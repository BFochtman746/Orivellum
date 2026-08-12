"""Tests for the Chao1 + Good–Turing coverage estimator.

Verifies the estimator math against fixtures with known frequency
distributions, the bias-corrected f₂ = 0 fallback, confidence-interval
behaviour, sampling-completeness bands, upper-bound framing, and the
per-class mention counting over the knowledge table.
"""

from __future__ import annotations

import math

from orivellum.capabilities.coverage_estimate import (
    UNDER_SAMPLED_BELOW,
    WELL_SAMPLED_AT,
    chao1_estimate,
    class_coverage,
    estimate_coverage,
    mention_frequencies,
    sampling_band,
)

# ── estimator math ────────────────────────────────────────────────────────────


def test_chao1_classic_known_distribution():
    """f2 > 0 uses the classic form: Ŝ = S_obs + f1²/(2·f2)."""
    # 5 species: counts 1,1,1,2,3 → S_obs=5, n=8, f1=3, f2=1
    est = chao1_estimate([1, 1, 1, 2, 3])
    assert est["n"] == 8
    assert est["s_obs"] == 5
    assert est["f1"] == 3
    assert est["f2"] == 1
    assert est["s_est"] == 5 + 9 / 2  # 9.5
    assert est["unseen_est"] == 4.5
    assert not est["bias_corrected"]
    # Good–Turing: C = 1 − f1/n = 1 − 3/8
    assert math.isclose(est["good_turing"], 1 - 3 / 8)
    # Completeness (upper bound on coverage): S_obs / Ŝ
    assert math.isclose(est["completeness"], 5 / 9.5, abs_tol=1e-4)


def test_chao1_bias_corrected_when_no_doubletons():
    """f2 = 0 must use Ŝ = S_obs + f1(f1−1)/(2(f2+1)) — never divide by zero."""
    # 4 species, all singletons → S_obs=4, f1=4, f2=0
    est = chao1_estimate([1, 1, 1, 1])
    assert est["f2"] == 0
    assert est["bias_corrected"]
    assert est["s_est"] == 4 + (4 * 3) / 2  # 10.0
    assert est["unseen_est"] == 6.0
    assert est["good_turing"] == 0.0  # every observation is a singleton


def test_chao1_complete_sample_no_singletons():
    """No singletons → nothing suggests unseen species: Ŝ = S_obs, C = 1."""
    est = chao1_estimate([5, 3, 2, 4])
    assert est["f1"] == 0
    assert est["s_est"] == est["s_obs"] == 4
    assert est["unseen_est"] == 0.0
    assert est["good_turing"] == 1.0
    assert est["completeness"] == 1.0
    # Degenerate CI collapses onto the estimate
    assert est["s_est_low"] == est["s_est_high"] == est["s_est"]


def test_chao1_empty_distribution_is_honest_no_data():
    """No observations → no estimate — never a fabricated 0% or 100%."""
    est = chao1_estimate([])
    assert est["n"] == 0
    assert est["s_est"] is None
    assert est["completeness"] is None
    assert est["good_turing"] is None
    assert sampling_band(est["completeness"]) == "no_data"


def test_chao1_ignores_nonpositive_counts():
    est = chao1_estimate([0, -1, 2, 1])
    assert est["s_obs"] == 2
    assert est["n"] == 3


def test_chao1_tiny_sample_single_singleton():
    """A single observed species mentioned once: estimator stays finite."""
    est = chao1_estimate([1])
    # f1=1, f2=0 → bias-corrected: Ŝ = 1 + (1·0)/2 = 1
    assert est["s_est"] == 1.0
    assert est["unseen_est"] == 0.0
    assert est["good_turing"] == 0.0
    assert est["completeness"] == 1.0


def test_chao1_confidence_interval_brackets_estimate():
    """95% CI must contain the point estimate, and CI bounds must order."""
    est = chao1_estimate([1] * 10 + [2] * 3 + [5] * 2)
    assert est["s_est_low"] <= est["s_est"] <= est["s_est_high"]
    assert est["s_obs"] <= est["s_est_low"]
    # Completeness CI inverts: low completeness pairs with high richness
    assert est["completeness_low"] <= est["completeness"] <= est["completeness_high"]


def test_completeness_is_upper_bound_direction():
    """More singletons ⇒ larger unseen pool ⇒ lower coverage upper bound."""
    many_singletons = chao1_estimate([1] * 20 + [2] * 2)
    few_singletons = chao1_estimate([1] * 2 + [2] * 2 + [10] * 18)
    assert many_singletons["completeness"] < few_singletons["completeness"]


# ── bands ─────────────────────────────────────────────────────────────────────


def test_sampling_bands():
    assert sampling_band(None) == "no_data"
    assert sampling_band(0.1) == "under_sampled"
    assert sampling_band(UNDER_SAMPLED_BELOW - 1e-9) == "under_sampled"
    assert sampling_band(UNDER_SAMPLED_BELOW) == "moderate"
    assert sampling_band(WELL_SAMPLED_AT - 1e-9) == "moderate"
    assert sampling_band(WELL_SAMPLED_AT) == "well_sampled"
    assert sampling_band(1.0) == "well_sampled"


# ── framing ───────────────────────────────────────────────────────────────────


def test_summary_uses_at_most_framing_and_unseen_count():
    """Never a bare percentage: 'at most' + estimated unseen count."""
    cov = class_coverage("entity", [1, 1, 1, 2, 3])
    assert cov["summary"].startswith("At most ")
    assert "unseen" in cov["summary"]
    # The unseen count appears in the text (4.5 → rendered as 4 or 5)
    assert any(tok in cov["summary"] for tok in ("4", "5"))


def test_summary_no_data():
    cov = class_coverage("entity", [])
    assert cov["band"] == "no_data"
    assert "cannot be estimated" in cov["summary"]


# ── mention counting over the knowledge table ────────────────────────────────


def _make_db(tmp_path):
    from orivellum.database.db import OrivellumDB

    return OrivellumDB(str(tmp_path / "test.db"))


def _add_mention(db, work_id, kind, subject, text=None, review_status="auto"):
    return db.create_knowledge_item(
        work_id=work_id,
        kind=kind,
        text=text or f"{subject} mention",
        subject=subject,
        review_status=review_status,
    )


def test_mention_frequencies_groups_by_class_and_normalised_subject(tmp_path):
    db = _make_db(tmp_path)
    work = db.create_work("W")
    wid = work["id"]
    # "Alpha" mentioned 3× with case/whitespace variance → one species, count 3
    _add_mention(db, wid, "entity", "Alpha")
    _add_mention(db, wid, "entity", "alpha ")
    _add_mention(db, wid, "entity", " ALPHA")
    _add_mention(db, wid, "entity", "Beta")
    _add_mention(db, wid, "character", "David")
    # Non-mention kinds must be excluded
    _add_mention(db, wid, "fact", "irrelevant")
    _add_mention(db, wid, "summary", "irrelevant")

    freqs = mention_frequencies(db, wid)
    assert sorted(freqs["entity"]) == [1, 3]
    assert freqs["character"] == [1]
    assert "fact" not in freqs
    assert "summary" not in freqs


def test_mention_frequencies_excludes_rejected(tmp_path):
    db = _make_db(tmp_path)
    work = db.create_work("W")
    wid = work["id"]
    _add_mention(db, wid, "entity", "Kept")
    _add_mention(db, wid, "entity", "Thrown", review_status="rejected")
    freqs = mention_frequencies(db, wid)
    assert freqs["entity"] == [1]


def test_mention_frequencies_falls_back_to_text_without_subject(tmp_path):
    db = _make_db(tmp_path)
    work = db.create_work("W")
    wid = work["id"]
    db.create_knowledge_item(work_id=wid, kind="concept", text="Covenant", subject=None)
    db.create_knowledge_item(work_id=wid, kind="concept", text="covenant", subject="")
    freqs = mention_frequencies(db, wid)
    assert freqs["concept"] == [2]


def test_estimate_coverage_corpus_vs_work_scope(tmp_path):
    db = _make_db(tmp_path)
    w1 = db.create_work("W1")["id"]
    w2 = db.create_work("W2")["id"]
    for s in ("A", "B", "C"):
        _add_mention(db, w1, "entity", s)
    _add_mention(db, w2, "entity", "Z")

    work_report = estimate_coverage(db, w1)
    corpus_report = estimate_coverage(db, None)
    assert work_report["overall"]["s_obs"] == 3
    assert corpus_report["overall"]["s_obs"] == 4


def test_estimate_coverage_report_shape_and_flags(tmp_path):
    db = _make_db(tmp_path)
    wid = db.create_work("W")["id"]
    # entity: heavily repeated → well sampled (no singletons). Texts must be
    # distinct — create_knowledge_item dedupes identical text.
    for i in range(5):
        _add_mention(db, wid, "entity", "Saul", text=f"Saul mention {i}")
        _add_mention(db, wid, "entity", "David", text=f"David mention {i}")
    # theme: all singletons → under-sampled
    for s in ("kingship", "exile", "covenant", "silence"):
        _add_mention(db, wid, "theme", s)

    report = estimate_coverage(db, wid)
    assert report["method"] == "chao1_good_turing"
    assert report["framing"] == "upper_bound"
    assert "does not measure interpretive understanding" in report["scope_note"]
    assert "theme" in report["under_sampled_classes"]
    assert "entity" in report["well_sampled_classes"]
    by_class = {c["class"]: c for c in report["classes"]}
    assert by_class["entity"]["completeness"] == 1.0
    assert by_class["theme"]["band"] == "under_sampled"
    # Overall pools every mention class
    assert report["overall"]["s_obs"] == 6
