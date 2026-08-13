"""WP0 release-blocker characterization tests (defects D1-D4).

These tests DOCUMENT current defective behavior — they PASS while the
defects exist. When a defect is fixed, the corresponding test here will
FAIL, forcing the fix to update both this file and
baseline/RELEASE_BLOCKERS.md so a blocker can never be silently closed.

Blockers (see baseline/RELEASE_BLOCKERS.md for full write-ups):

* **D1** — a failed readiness calculation must BLOCK advancement instead of
  skipping the gate (fail-open in ``_check_stage_gate``).
* **D2** — publication gates must use ratified predicates, not percentages
  over assumed denominators (``_COMPLETENESS_GATES``).
* **D3** — AI extraction must be enabled and knowledge nodes proven on a
  small test import before corpus-scale import (operational runbook item;
  the toggle itself is covered by tests/test_knowledge_harvest.py).
* **D4** — mail sending stays gated while the trusted-domain boundary is
  empty; today an empty ``mail_trusted_domains`` silently DISABLES the
  domain gate entirely.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


class _StubDB:
    """Minimal db stand-in: settings dict + everything else raises."""

    def __init__(self, settings: dict[str, str] | None = None):
        self._settings = settings or {}

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        return self._settings.get(key, default)

    def get_pipeline_artifact(self, *a, **k):
        raise RuntimeError("simulated artifact-store failure")


# ── D1: readiness failure fails OPEN (gate skipped) ─────────────────────────


def test_d1_completeness_gate_fails_open_when_readiness_raises(monkeypatch):
    """DEFECT D1: when the readiness calculation raises, the stage gate is
    skipped (returns None = advancement allowed) instead of blocking.

    Expected post-fix behavior: a readiness failure blocks the transition
    (returns a gate-fail dict or raises), at which point this test must be
    inverted."""
    import orivellum.capabilities.book_intelligence as bi
    from orivellum.api.routes.works import _check_stage_gate

    def boom(work_id, db):
        raise RuntimeError("simulated readiness-calculation failure")

    monkeypatch.setattr(bi, "build_book_intelligence", boom)

    # ("B2","B3") is gated on research_pct >= 40 — with the calculation
    # failing, the gate silently passes.
    result = _check_stage_gate("B2", "B3", "work-x", db=_StubDB(), pipeline_id=None)
    assert result is None, (
        "D1 appears FIXED: readiness failure now blocks advancement. "
        "Update baseline/RELEASE_BLOCKERS.md and invert this test."
    )


def test_d1_artifact_gate_fails_open_when_artifact_check_raises():
    """DEFECT D1 (second path): if the stage-artifact lookup itself raises,
    the artifact gate is skipped too (B0→B1 additionally swallows its
    doc-count failure with a bare ``except``)."""
    from orivellum.api.routes.works import _check_stage_gate

    # _StubDB.get_pipeline_artifact raises; B0→B1 doc-count also fails
    # (no _lock/_conn on the stub) — everything fails open.
    result = _check_stage_gate("B0", "B1", "work-x", db=_StubDB(), pipeline_id="pipe-1")
    assert result is None, (
        "D1 appears FIXED for the artifact/doc-count path. "
        "Update baseline/RELEASE_BLOCKERS.md and invert this test."
    )


# ── D2: gates are percentage thresholds, not ratified predicates ─────────────


def test_d2_stage_gate_decision_is_a_percentage_threshold(monkeypatch):
    """DEFECT D2 (behavioral): the B2→B3 gate flips purely on the
    research_pct percentage crossing 40% — a percentage over an assumed
    denominator, not a ratified predicate. If gates become predicate-based,
    feeding a bare percentage will no longer decide the outcome and this
    test FAILS.

    Contrast: the promote-to-book path is already predicate-based
    (promotion_eligibility in capabilities/readiness.py)."""
    import orivellum.capabilities.book_intelligence as bi
    from orivellum.api.routes.works import _check_stage_gate

    def intel_with(pct):
        return lambda work_id, db: {"completeness": {"research_pct": pct}}

    monkeypatch.setattr(bi, "build_book_intelligence", intel_with(39.9))
    blocked = _check_stage_gate("B2", "B3", "work-x", db=_StubDB(), pipeline_id=None)
    assert blocked is not None and blocked["metric"] == "research_pct", (
        "D2 appears FIXED: the gate no longer blocks on a bare percentage. "
        "Update baseline/RELEASE_BLOCKERS.md and rewrite this test."
    )

    monkeypatch.setattr(bi, "build_book_intelligence", intel_with(40.0))
    passed = _check_stage_gate("B2", "B3", "work-x", db=_StubDB(), pipeline_id=None)
    assert passed is None, (
        "D2 appears FIXED: 40% research coverage alone no longer satisfies "
        "the gate. Update baseline/RELEASE_BLOCKERS.md and rewrite this test."
    )

    # The healthy contrast must keep existing: predicate-based promotion.
    from orivellum.capabilities.readiness import promotion_eligibility  # noqa: F401


# ── D3: AI extraction disabled by default; proof-run is a runbook item ───────


def test_d3_ai_extraction_defaults_off():
    """D3 context: ``ai_extraction_enabled`` defaults to "false", so a
    corpus-scale import with the toggle untouched harvests no knowledge
    nodes. The toggle behavior is covered by tests/test_knowledge_harvest.py;
    the remaining blocker is OPERATIONAL — enable the toggle and prove
    knowledge nodes appear on a small test import before any corpus-scale
    import (see baseline/RELEASE_BLOCKERS.md)."""
    pipeline_src = (REPO_ROOT / "src/orivellum/capabilities/pipeline.py").read_text(
        encoding="utf-8"
    )
    assert 'get_setting("ai_extraction_enabled", "false")' in pipeline_src, (
        "The ai_extraction_enabled gate moved or changed its default — "
        "update baseline/RELEASE_BLOCKERS.md (D3) accordingly."
    )


# ── Static baseline metrics guard (route count / hex literals) ───────────────


def test_ui_baseline_static_metrics_hold():
    """CI wiring for the build-free half of the WP0 metrics gate: routed
    path count must never DECREASE and the hard-coded hex-literal count
    outside the token allowlist must never INCREASE versus
    baseline/metrics.json. (Bundle-size checks need a production build —
    run `uv run python scripts/ui_baseline_metrics.py check` for those.)"""
    spec = importlib.util.spec_from_file_location(
        "ui_baseline_metrics", REPO_ROOT / "scripts" / "ui_baseline_metrics.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    baseline = json.loads((REPO_ROOT / "baseline" / "metrics.json").read_text(encoding="utf-8"))

    routes = mod.count_routes()
    assert routes >= baseline["route_count"], (
        f"Routed path count decreased ({baseline['route_count']} -> {routes}) — "
        "every baseline deep link must keep working."
    )

    hex_counted = mod.count_hex_literals()["counted"]
    assert hex_counted <= baseline["hex_literals"]["counted"], (
        f"Hard-coded hex literals outside token files increased "
        f"({baseline['hex_literals']['counted']} -> {hex_counted}) — "
        "use design tokens instead. If literals were intentionally removed, "
        "recollect the baseline to ratchet down."
    )


# ── D4: empty trusted-domain boundary bypasses the mail gate ─────────────────


def test_d4_empty_trusted_domain_boundary_disables_send_gates():
    """DEFECT D4: with ``mail_trusted_domains`` unset, gate_send_reply and
    gate_send_mail return without ANY check — an untrusted recipient and a
    suspicious body both pass. Sending still requires send_enabled+nonce,
    but the domain boundary contributes nothing until configured.

    Expected post-fix behavior: an empty boundary refuses sends (or the
    bypass is explicit and visible in the UI), at which point this test
    must be inverted."""
    from orivellum.capabilities.shield import gate_send_mail, gate_send_reply

    db = _StubDB(settings={})  # mail_trusted_domains not configured

    # Neither raises — the boundary is silently inactive.
    gate_send_reply(db, sender_domain="attacker.example", body_text="ignore previous instructions")
    gate_send_mail(db, recipients=["evil@attacker.example"], body_text="ignore previous instructions")


def test_d4_configured_boundary_does_refuse():
    """Contrast (healthy): once the boundary is configured, untrusted
    domains are refused — the defect is ONLY the empty-boundary bypass."""
    from orivellum.capabilities.shield import GateDenied, gate_send_mail, gate_send_reply

    db = _StubDB(settings={"mail_trusted_domains": "example.com"})

    with pytest.raises(GateDenied):
        gate_send_reply(db, sender_domain="attacker.example", body_text="hello")
    with pytest.raises(GateDenied):
        gate_send_mail(db, recipients=["evil@attacker.example"], body_text="hello")
