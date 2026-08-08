"""Free threat-intelligence feeds for the A-01 Mail Steward.

Supported feeds (all free, no registration required for basic use):
  OpenPhish  — phishing URL feed (public list, ~720-minute refresh)
  URLhaus    — malware-distributing URLs (Abuse.ch; ~5-minute refresh)

Design (SECURITY-AND-ACTION-POLICY §Data boundaries):
  - Extracted hostnames only are matched; full URLs are not sent externally.
  - A single feed match is evidence only — it never triggers automatic deletion.
  - Feed results are tagged with source, refresh time, and confidence.
  - On feed outage the last snapshot is preserved and marked stale; no
    "safe" verdict is synthesised from a missing feed.

Ref: https://openphish.com/  |  https://urlhaus.abuse.ch/api/
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
import urllib.request
from typing import Any
from urllib.parse import urlparse

from orivellum.capabilities.mail.models import ThreatEvidence

logger = logging.getLogger("orivellum.mail.threat")

_OPENPHISH_URL   = "https://openphish.com/feed.txt"
_URLHAUS_URL     = "https://urlhaus.abuse.ch/downloads/text/"
_HTTP_TIMEOUT    = 30

# In-memory feed snapshots
_feed_cache: dict[str, dict[str, Any]] = {}


def _normalise_url(raw: str) -> str:
    """Extract and lowercase the hostname from a URL-like string."""
    raw = raw.strip()
    if not raw.startswith("http"):
        raw = "https://" + raw
    try:
        return urlparse(raw).hostname or ""
    except Exception:
        return ""


def _fetch_text(url: str) -> str:
    """Download a text file, return empty string on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Orivellum-A01-ThreatIntel/1.0"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        logger.warning("threat_intel: feed fetch failed %s: %s", url, exc)
        return ""


def refresh_openphish() -> int:
    """Download the OpenPhish public feed and update the in-memory cache."""
    text = _fetch_text(_OPENPHISH_URL)
    if not text:
        if "openphish" in _feed_cache:
            _feed_cache["openphish"]["stale"] = True
        return 0
    hosts: set[str] = set()
    for line in text.splitlines():
        h = _normalise_url(line)
        if h:
            hosts.add(h)
    file_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    _feed_cache["openphish"] = {
        "hosts": hosts,
        "fetched_at": time.time(),
        "hash": file_hash,
        "stale": False,
        "count": len(hosts),
    }
    logger.info("threat_intel: openphish refreshed — %d hosts", len(hosts))
    return len(hosts)


def refresh_urlhaus() -> int:
    """Download the URLhaus plain-text list and update the in-memory cache."""
    text = _fetch_text(_URLHAUS_URL)
    if not text:
        if "urlhaus" in _feed_cache:
            _feed_cache["urlhaus"]["stale"] = True
        return 0
    hosts: set[str] = set()
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        h = _normalise_url(line)
        if h:
            hosts.add(h)
    file_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
    _feed_cache["urlhaus"] = {
        "hosts": hosts,
        "fetched_at": time.time(),
        "hash": file_hash,
        "stale": False,
        "count": len(hosts),
    }
    logger.info("threat_intel: urlhaus refreshed — %d hosts", len(hosts))
    return len(hosts)


def _extract_urls(text: str) -> list[str]:
    """Extract URL-like strings from message body text."""
    return re.findall(r'https?://[^\s<>"\']+', text)


def inspect_message(message_body: str) -> list[ThreatEvidence]:
    """Check URLs in the message body against all loaded threat feeds.

    Returns a list of ThreatEvidence (may be empty).
    A single match is evidence only — the caller decides what to do with it.
    """
    urls = _extract_urls(message_body)
    if not urls:
        return []

    hostnames = {_normalise_url(u): u for u in urls if _normalise_url(u)}
    evidence: list[ThreatEvidence] = []

    for feed_name, cache in _feed_cache.items():
        hosts: set[str] = cache.get("hosts", set())
        stale = cache.get("stale", False)
        for host, original_url in hostnames.items():
            if host and host in hosts:
                evidence.append(ThreatEvidence(
                    feed=feed_name + (" (stale)" if stale else ""),
                    indicator=host,
                    indicator_type="domain",
                    confidence=0.6 if stale else 0.85,
                    description=f"Matched in {feed_name} feed",
                ))

    return evidence


def feed_status() -> dict[str, Any]:
    """Return a summary of loaded feeds for the system status endpoint."""
    result = {}
    for name, cache in _feed_cache.items():
        result[name] = {
            "count": cache.get("count", 0),
            "fetched_at": cache.get("fetched_at"),
            "stale": cache.get("stale", False),
            "hash": cache.get("hash", ""),
        }
    return result
