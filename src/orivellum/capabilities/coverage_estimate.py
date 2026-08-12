"""Unseen-species coverage estimation (Chao1 + Good–Turing).

Replaces the self-referential ``coverage_pct`` with honest estimates of how
much has NOT been seen, using only the frequency distribution of entity/term
mentions already in the corpus (no Domain Model, no world graph, no external
frame):

  • **Chao1 richness** — Ŝ = S_obs + f₁²/(2f₂), or the bias-corrected form
    Ŝ = S_obs + f₁(f₁−1)/(2(f₂+1)) when there are no doubletons (f₂ = 0).
    Chao1 is a LOWER bound on true richness, so the derived coverage figure
    S_obs/Ŝ is an UPPER bound — the error direction is conservative.
  • **Good–Turing sample coverage** — C = 1 − f₁/n: the estimated proportion
    of the underlying population the sample represents.
  • **Sampling completeness** — S_obs/Ŝ with a 95% confidence interval
    (log-normal CI on the unseen count, Chao 1987).

Every surfaced figure must use "at most" framing (the estimate is an upper
bound) and report the estimated unseen count — never a bare percentage.

Scope caveat (surfaced in every report): this measures ENTITY coverage — how
completely the corpus's people/places/terms have been sampled. It says
nothing about the interpretive layer or how well the material is understood.
"""

from __future__ import annotations

import datetime
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from orivellum.database.db import OrivellumDB

# Sampling-completeness bands (per the review: >90% well-sampled,
# <70% warns that a great deal remains undetected).
UNDER_SAMPLED_BELOW = 0.70
WELL_SAMPLED_AT = 0.90

# Knowledge kinds that represent entity/term mentions. Interpretive kinds
# (summary, claim, excerpt, …) are deliberately excluded — Chao1 over those
# would masquerade as a measure of understanding, which it is not.
MENTION_CLASSES = ("entity", "character", "concept", "event", "setting", "theme")

_Z95 = 1.959963984540054  # two-sided 95% normal quantile


def chao1_estimate(frequencies: Iterable[int]) -> dict:
    """Estimate richness/coverage from a mention-frequency distribution.

    Args:
        frequencies: one count per distinct observed species (entity/term) —
            how many times each was mentioned. Zero/negative counts are
            ignored (a species with zero observations was not observed).

    Returns a dict with:
        n, s_obs, f1, f2            — sample size and frequency counts
        s_est, s_est_low, s_est_high — Chao1 richness with 95% CI (lower bound
                                       on true richness; ``None`` when n == 0)
        unseen_est, unseen_low, unseen_high — estimated species NOT yet seen
        good_turing                 — sample coverage C = 1 − f₁/n
        completeness, completeness_low, completeness_high
                                    — S_obs/Ŝ (UPPER bound on true coverage)
        bias_corrected              — True when the f₂ = 0 form was used
    """
    freqs = [int(f) for f in frequencies if int(f) > 0]
    n = sum(freqs)
    s_obs = len(freqs)
    f1 = sum(1 for f in freqs if f == 1)
    f2 = sum(1 for f in freqs if f == 2)

    if n == 0:
        return {
            "n": 0,
            "s_obs": 0,
            "f1": 0,
            "f2": 0,
            "s_est": None,
            "s_est_low": None,
            "s_est_high": None,
            "unseen_est": None,
            "unseen_low": None,
            "unseen_high": None,
            "good_turing": None,
            "completeness": None,
            "completeness_low": None,
            "completeness_high": None,
            "bias_corrected": False,
        }

    bias_corrected = f2 == 0
    if f2 > 0:
        s_est = s_obs + (f1 * f1) / (2.0 * f2)
        r = f1 / f2
        variance = f2 * (r**2 / 2.0 + r**3 + r**4 / 4.0)
    else:
        # Bias-corrected form — mandatory when there are no doubletons,
        # where the classic form divides by zero.
        s_est = s_obs + (f1 * (f1 - 1)) / (2.0 * (f2 + 1))
        if f1 > 0:
            variance = (
                f1 * (f1 - 1) / 2.0 + f1 * (2 * f1 - 1) ** 2 / 4.0 - (f1**4) / (4.0 * s_est)
                if s_est > 0
                else 0.0
            )
        else:
            variance = 0.0
    variance = max(0.0, variance)

    # 95% CI via the log-normal approximation on T = Ŝ − S_obs (Chao 1987).
    unseen = s_est - s_obs
    if unseen > 0 and variance > 0:
        k = math.exp(_Z95 * math.sqrt(math.log(1.0 + variance / (unseen * unseen))))
        s_low = s_obs + unseen / k
        s_high = s_obs + unseen * k
    else:
        s_low = s_high = s_est

    good_turing = 1.0 - (f1 / n)
    # Coverage upper bound: Chao1 under-estimates richness, so S_obs/Ŝ
    # over-estimates coverage → "at most". CI bounds invert (higher richness
    # bound ⇒ lower completeness bound).
    completeness = s_obs / s_est if s_est > 0 else None
    completeness_low = s_obs / s_high if s_high and s_high > 0 else None
    completeness_high = s_obs / s_low if s_low and s_low > 0 else None

    return {
        "n": n,
        "s_obs": s_obs,
        "f1": f1,
        "f2": f2,
        "s_est": round(s_est, 2),
        "s_est_low": round(s_low, 2),
        "s_est_high": round(s_high, 2),
        "unseen_est": round(max(0.0, unseen), 2),
        "unseen_low": round(max(0.0, s_low - s_obs), 2),
        "unseen_high": round(max(0.0, s_high - s_obs), 2),
        "good_turing": round(good_turing, 4),
        "completeness": round(completeness, 4) if completeness is not None else None,
        "completeness_low": round(completeness_low, 4) if completeness_low is not None else None,
        "completeness_high": round(completeness_high, 4) if completeness_high is not None else None,
        "bias_corrected": bias_corrected,
    }


def sampling_band(completeness: float | None) -> str:
    """Classify sampling completeness: under_sampled / moderate / well_sampled.

    ``no_data`` when there is nothing to estimate from.
    """
    if completeness is None:
        return "no_data"
    if completeness < UNDER_SAMPLED_BELOW:
        return "under_sampled"
    if completeness >= WELL_SAMPLED_AT:
        return "well_sampled"
    return "moderate"


def _summary_line(cls: str, est: dict) -> str:
    """Human framing for one class — always "at most", always the unseen count."""
    if est["completeness"] is None:
        return f"No {cls} mentions yet — coverage cannot be estimated."
    pct = est["completeness"] * 100
    unseen = est["unseen_est"]
    lo, hi = est["unseen_low"], est["unseen_high"]
    ci = f" (95% CI {lo:.0f}–{hi:.0f})" if hi and hi > lo else ""
    return f"At most {pct:.0f}% of {cls} items seen — an estimated {unseen:.0f} more unseen{ci}."


def class_coverage(cls: str, frequencies: Iterable[int]) -> dict:
    """Full per-class coverage record: estimates + band + framing."""
    est = chao1_estimate(frequencies)
    band = sampling_band(est["completeness"])
    return {"class": cls, **est, "band": band, "summary": _summary_line(cls, est)}


def mention_frequencies(db: OrivellumDB, work_id: str | None = None) -> dict[str, list[int]]:
    """Per-class mention-frequency distributions from extracted knowledge.

    Species = a distinct mentioned entity/term within a class (normalised on
    the knowledge item's subject, falling back to its text); frequency = how
    many knowledge rows mention it. Rejected items are excluded — a mention
    the user threw out is not evidence of coverage.

    ``work_id=None`` computes corpus-wide distributions.
    """
    placeholders = ",".join("?" * len(MENTION_CLASSES))
    sql = (
        "SELECT kind, COUNT(*) AS cnt FROM knowledge "
        f"WHERE kind IN ({placeholders}) "
        "AND COALESCE(review_status,'') NOT IN ('rejected','quarantined_reprojection') "
    )
    params: list = list(MENTION_CLASSES)
    if work_id is not None:
        sql += "AND work_id = ? "
        params.append(work_id)
    sql += "GROUP BY kind, LOWER(TRIM(COALESCE(NULLIF(TRIM(subject),''), text)))"
    with db._lock:
        rows = db._conn.execute(sql, params).fetchall()
    out: dict[str, list[int]] = {cls: [] for cls in MENTION_CLASSES}
    for r in rows:
        out[r["kind"]].append(r["cnt"])
    return out


def estimate_coverage(db: OrivellumDB, work_id: str | None = None) -> dict:
    """Per-class + overall Chao1/Good–Turing coverage report.

    The overall figure pools every entity/term mention across classes — it is
    still an upper bound, and it still measures entity coverage only.
    """
    by_class = mention_frequencies(db, work_id)
    classes = [class_coverage(cls, freqs) for cls, freqs in by_class.items() if freqs]
    pooled: list[int] = [f for freqs in by_class.values() for f in freqs]
    overall = class_coverage("entity/term", pooled)
    overall.pop("class", None)
    return {
        "method": "chao1_good_turing",
        "framing": "upper_bound",
        "scope_note": (
            "Estimates entity/term coverage from mention frequencies. "
            "An upper bound: true coverage is at most this. "
            "It does not measure interpretive understanding."
        ),
        "overall": overall,
        "classes": sorted(classes, key=lambda c: c["class"]),
        "under_sampled_classes": sorted(
            c["class"] for c in classes if c["band"] == "under_sampled"
        ),
        "well_sampled_classes": sorted(c["class"] for c in classes if c["band"] == "well_sampled"),
        "evaluated_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
