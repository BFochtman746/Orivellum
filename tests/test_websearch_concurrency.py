"""Regression tests for research-pipeline concurrency (Task: catch research slowdowns).

The pipeline was restructured so that:
  1. The primary-query search and every mode lane hit Tavily IMMEDIATELY,
     while the LLM query planner runs concurrently in the same pool;
     variant searches are submitted the moment the planner finishes.
  2. YouTube transcripts are fetched concurrently, not one at a time.

If a future refactor re-serializes either path, research silently gets several
seconds slower with no error.  These tests make that regression loud:

  - The planner mock BLOCKS until the first Tavily call has been observed.
    If searches are (wrongly) gated on the planner again, the planner times
    out waiting and the test fails.
  - The transcript mocks track peak in-flight concurrency.  If fetches are
    (wrongly) serialized again, peak concurrency is 1 and the test fails.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from orivellum.capabilities import websearch
from orivellum.capabilities.websearch import SearchMode, research_web

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result(url: str, title: str = "t") -> dict:
    return {"url": url, "title": title, "content": "lorem ipsum " * 30}


class _PlannerResult:
    def __init__(self, text: str):
        self.text = text


# ---------------------------------------------------------------------------
# 1 — Searches must start while the query planner is still thinking
# ---------------------------------------------------------------------------

class TestPlannerOverlapsSearches:
    def test_first_search_fires_before_planner_returns(self):
        """The planner blocks until a Tavily call is seen.  If searches were
        re-serialized behind the planner, this event would never be set and
        the planner would time out — failing the test."""
        first_search_started = threading.Event()
        events: list[str] = []
        lock = threading.Lock()

        def fake_tavily(query, **kw):
            with lock:
                events.append(f"search:{query}")
            first_search_started.set()
            return [_result(f"https://example.com/{abs(hash(query)) % 1000}", query)]

        def fake_llm(messages, **kw):
            # Planner waits for proof that a search already started.
            overlapped = first_search_started.wait(timeout=5)
            assert overlapped, (
                "REGRESSION: no Tavily search started while the query planner "
                "was still running — the pipeline has been re-serialized."
            )
            with lock:
                events.append("planner:done")
            return _PlannerResult("variant alpha\nvariant beta")

        with patch.object(websearch, "_call_tavily", fake_tavily):
            _ctx, _cites, diag = research_web(
                "quantum error correction",   # no news/biblical signals
                profile="balanced",
                llm_call_fn=fake_llm,
            )

        # Ordering proof: at least one search event precedes planner completion
        planner_idx = events.index("planner:done")
        assert any(e.startswith("search:") for e in events[:planner_idx]), (
            f"No search ran before the planner finished. Event order: {events}"
        )
        # Variant searches still ran after the planner
        assert "search:variant alpha" in events
        assert "search:variant beta" in events

    def test_diagnostics_exact_values(self):
        """queries_planned / queries_executed must keep their exact semantics."""
        calls: list[str] = []
        lock = threading.Lock()

        def fake_tavily(query, **kw):
            with lock:
                calls.append(query)
            return [_result(f"https://example.com/{len(calls)}", query)]

        def fake_llm(messages, **kw):
            return _PlannerResult("variant alpha\nvariant beta")

        with patch.object(websearch, "_call_tavily", fake_tavily):
            _ctx, _cites, diag = research_web(
                "quantum error correction",
                profile="balanced",       # n_variants=2 → primary + 2 variants
                llm_call_fn=fake_llm,
            )

        # planned = primary + 2 accepted variants
        assert diag.queries_planned == 3
        # executed = 3 web tasks (WEB is the only auto-detected mode here)
        assert diag.queries_executed == 3
        assert sorted(calls) == sorted(
            ["quantum error correction", "variant alpha", "variant beta"]
        )
        assert diag.provider_errors == []

    def test_variants_deduped_and_capped(self):
        """Duplicate/excess planner output must not spawn extra searches."""
        calls: list[str] = []
        lock = threading.Lock()

        def fake_tavily(query, **kw):
            with lock:
                calls.append(query)
            return [_result(f"https://example.com/{len(calls)}", query)]

        def fake_llm(messages, **kw):
            # First line duplicates the primary query (case-insensitively):
            # it must never spawn a second identical search.
            return _PlannerResult(
                "Quantum Error Correction\nvariant alpha\nvariant beta"
            )

        with patch.object(websearch, "_call_tavily", fake_tavily):
            _ctx, _cites, diag = research_web(
                "quantum error correction",
                profile="balanced",       # cap: primary + 2 variants
                llm_call_fn=fake_llm,
            )

        # No duplicate searches ever
        assert len(calls) == len({c.casefold() for c in calls})
        assert calls.count("quantum error correction") == 1
        # Cap holds: never more than primary + n_variants searches
        assert diag.queries_planned <= 3
        assert diag.queries_planned == len(calls) == diag.queries_executed
        assert "variant alpha" in calls

    def test_planner_failure_never_blocks_pipeline(self):
        def fake_tavily(query, **kw):
            return [_result("https://example.com/1", query)]

        def bad_llm(messages, **kw):
            raise RuntimeError("llm down")

        with patch.object(websearch, "_call_tavily", fake_tavily):
            ctx, cites, diag = research_web(
                "quantum error correction",
                profile="balanced",
                llm_call_fn=bad_llm,
            )

        assert diag.queries_planned == 1
        assert diag.queries_executed == 1
        assert cites  # primary search still produced usable results


# ---------------------------------------------------------------------------
# 2 — YouTube transcripts must download concurrently
# ---------------------------------------------------------------------------

class TestYoutubeTranscriptConcurrency:
    def test_transcripts_fetched_concurrently_with_mixed_outcomes(self):
        """Staggered transcript fetches: track peak in-flight concurrency and
        verify _has_transcript flags land on the right results."""
        video_urls = [f"https://www.youtube.com/watch?v=vid{i}" for i in range(4)]

        def fake_tavily(query, **kw):
            return [_result(u, f"video {i}") for i, u in enumerate(video_urls)]

        active = 0
        peak = 0
        lock = threading.Lock()

        def fake_transcript(video_id):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.05)   # hold the slot so overlap is observable
            with lock:
                active -= 1
            # vid0 and vid2 succeed; vid1 and vid3 have no captions
            if video_id in ("vid0", "vid2"):
                return f"transcript for {video_id}"
            return None

        with patch.object(websearch, "_call_tavily", fake_tavily), \
             patch.object(websearch, "_fetch_youtube_transcript", fake_transcript):
            results = websearch._search_youtube("some talk")

        assert peak >= 2, (
            f"REGRESSION: transcript fetches ran serially (peak concurrency "
            f"{peak}) — they must be dispatched concurrently."
        )

        by_url = {r["url"]: r for r in results}
        assert len(by_url) == 4
        assert by_url[video_urls[0]]["_has_transcript"] is True
        assert by_url[video_urls[0]]["raw_content"] == "transcript for vid0"
        assert by_url[video_urls[2]]["_has_transcript"] is True
        assert by_url[video_urls[2]]["raw_content"] == "transcript for vid2"
        assert by_url[video_urls[1]]["_has_transcript"] is False
        assert "raw_content" not in by_url[video_urls[1]]
        assert by_url[video_urls[3]]["_has_transcript"] is False

    def test_results_without_video_ids_skip_transcript_fetch(self):
        def fake_tavily(query, **kw):
            return [_result("https://www.youtube.com/@somechannel", "channel page")]

        def fake_transcript(video_id):  # pragma: no cover — must not be called
            raise AssertionError("transcript fetch attempted for non-video URL")

        with patch.object(websearch, "_call_tavily", fake_tavily), \
             patch.object(websearch, "_fetch_youtube_transcript", fake_transcript):
            results = websearch._search_youtube("some channel")

        assert results[0]["_has_transcript"] is False
        assert results[0]["_source_type"] == "youtube"


# ---------------------------------------------------------------------------
# 3 — Mode lanes fire in the first wave alongside the primary search
# ---------------------------------------------------------------------------

class TestModeLanesInFirstWave:
    def test_all_requested_lanes_start_before_planner_returns(self):
        """Every requested mode lane (not just the primary search) must hit
        the network BEFORE the planner finishes.  The planner mock blocks
        until web + news + academic have all started; a regression that
        gates any lane on the planner deadlocks the wait and fails loudly."""
        seen_topics: list[str] = []
        lock = threading.Lock()
        started = {"web": threading.Event(),
                   "news": threading.Event(),
                   "academic": threading.Event()}

        def fake_tavily(query, *, topic="general", include_domains=None, **kw):
            if include_domains and "arxiv.org" in include_domains:
                lane = "academic"
            elif topic == "news":
                lane = "news"
            else:
                lane = "web"
            started[lane].set()
            with lock:
                seen_topics.append(lane)
            return [_result(f"https://example.com/{len(seen_topics)}", query)]

        def fake_llm(messages, **kw):
            for lane, event in started.items():
                assert event.wait(timeout=5), (
                    f"REGRESSION: the {lane} lane did not start while the "
                    "query planner was still running — mode lanes have been "
                    "re-serialized behind the planner."
                )
            with lock:
                seen_topics.append("planner:done")
            return _PlannerResult("variant alpha")

        with patch.object(websearch, "_call_tavily", fake_tavily):
            _ctx, _cites, diag = research_web(
                "quantum error correction",
                profile="balanced",
                modes=[SearchMode.NEWS, SearchMode.ACADEMIC],
                llm_call_fn=fake_llm,
            )

        # Ordering proof: all three lanes started before the planner finished
        planner_idx = seen_topics.index("planner:done")
        first_wave = seen_topics[:planner_idx]
        assert {"web", "news", "academic"} <= set(first_wave), (
            f"Not every lane started before the planner finished: {seen_topics}"
        )
        # Variant search still ran after the planner
        lanes_only = [t for t in seen_topics if t != "planner:done"]
        assert lanes_only.count("web") >= 2   # primary + variant
        assert diag.queries_executed == len(lanes_only)
