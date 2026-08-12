"""Security floor tests for the threat-intelligence feed layer.

Pins the declared data-boundary design:
  * hostnames only are matched (never full URLs)
  * feed outage preserves the last snapshot and marks it stale — no "safe"
    verdict is ever synthesised from a missing feed
  * stale matches carry reduced confidence, and the feed name says stale
  * a match is evidence only (list output; caller decides)
No network — the fetcher is monkeypatched.
"""

from __future__ import annotations

import pytest

from orivellum.capabilities.mail import threat_intel


@pytest.fixture(autouse=True)
def _clean_cache():
    threat_intel._feed_cache.clear()
    yield
    threat_intel._feed_cache.clear()


def _load_feed(monkeypatch, text):
    monkeypatch.setattr(threat_intel, "_fetch_text", lambda _url: text)


# ── Normalisation ─────────────────────────────────────────────────────────────


def test_normalise_url_extracts_lowercase_hostname():
    assert threat_intel._normalise_url("https://EVIL.example.com/path?q=1") == "evil.example.com"
    assert threat_intel._normalise_url("evil.example.com/login") == "evil.example.com"
    assert threat_intel._normalise_url("") == ""


# ── Feed refresh ──────────────────────────────────────────────────────────────


def test_refresh_openphish_parses_hosts(monkeypatch):
    _load_feed(monkeypatch, "https://bad.example.com/x\nhttps://worse.example.org/y\n")
    assert threat_intel.refresh_openphish() == 2
    cache = threat_intel._feed_cache["openphish"]
    assert cache["hosts"] == {"bad.example.com", "worse.example.org"}
    assert cache["stale"] is False


def test_refresh_urlhaus_skips_comments(monkeypatch):
    _load_feed(monkeypatch, "# comment\n\nhttps://mal.example.net/payload\n")
    assert threat_intel.refresh_urlhaus() == 1
    assert threat_intel._feed_cache["urlhaus"]["hosts"] == {"mal.example.net"}


def test_feed_outage_preserves_snapshot_and_marks_stale(monkeypatch):
    _load_feed(monkeypatch, "https://bad.example.com/x\n")
    threat_intel.refresh_openphish()
    _load_feed(monkeypatch, "")  # outage
    assert threat_intel.refresh_openphish() == 0
    cache = threat_intel._feed_cache["openphish"]
    assert cache["stale"] is True
    assert cache["hosts"] == {"bad.example.com"}, "outage must never drop the last snapshot"


def test_outage_with_no_prior_snapshot_loads_nothing(monkeypatch):
    _load_feed(monkeypatch, "")
    assert threat_intel.refresh_openphish() == 0
    assert "openphish" not in threat_intel._feed_cache


# ── Message inspection ────────────────────────────────────────────────────────


def test_inspect_matches_by_hostname_only(monkeypatch):
    _load_feed(monkeypatch, "https://bad.example.com/original-path\n")
    threat_intel.refresh_openphish()
    # Different path/query than the feed entry — must still match on hostname.
    evidence = threat_intel.inspect_message("click https://bad.example.com/OTHER?x=2 now")
    assert len(evidence) == 1
    assert evidence[0].indicator == "bad.example.com"
    assert evidence[0].indicator_type == "domain"
    assert evidence[0].confidence == 0.85


def test_inspect_no_urls_or_no_match_returns_empty(monkeypatch):
    _load_feed(monkeypatch, "https://bad.example.com/x\n")
    threat_intel.refresh_openphish()
    assert threat_intel.inspect_message("no links here") == []
    assert threat_intel.inspect_message("see https://good.example.com/fine") == []


def test_stale_feed_match_has_reduced_confidence(monkeypatch):
    _load_feed(monkeypatch, "https://bad.example.com/x\n")
    threat_intel.refresh_openphish()
    _load_feed(monkeypatch, "")
    threat_intel.refresh_openphish()  # now stale
    evidence = threat_intel.inspect_message("https://bad.example.com/y")
    assert len(evidence) == 1
    assert evidence[0].confidence == 0.6
    assert "stale" in evidence[0].feed


def test_feed_status_summary(monkeypatch):
    _load_feed(monkeypatch, "https://bad.example.com/x\n")
    threat_intel.refresh_openphish()
    status = threat_intel.feed_status()
    assert status["openphish"]["count"] == 1
    assert status["openphish"]["stale"] is False
    assert status["openphish"]["hash"]
