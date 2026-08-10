---
name: Test suite stability
description: Order-dependence and flake patterns in the pytest suite, plus how to run the full suite in the Replit container.
---

# Test suite stability

**Rule 1:** Tests must never call `asyncio.get_event_loop().run_until_complete(...)`. Use `asyncio.run(...)` (or a fresh `new_event_loop`/`set_event_loop(None)` wrapper like test_intent_routing's).
**Why:** On Python 3.12, any earlier test that ran `asyncio.run()` or set `set_event_loop(None)` leaves no current loop, so `get_event_loop()` raises RuntimeError — producing order-dependent failures (this bit the memory endpoint tests).
**How to apply:** grep tests for `get_event_loop().run_until_complete` before trusting a "pre-existing flake" diagnosis; reproduce with `pytest tests/test_stream_timeout.py <victim file>`.

**Rule 2:** Any test that drives a route submitting fire-and-forget jobs to the shared executor (`orivellum.api.executor`) must drain it in tearDown — `executor.shutdown(wait=True)` (it lazily re-creates) — before `TemporaryDirectory.cleanup()`, and create the temp dir with `ignore_cleanup_errors=True`.
**Why:** Background registration jobs (e.g. `_register_output_bg` from the TTS SSE stream) keep writing into the test's data dir after the response completes; rmtree races them (`OSError: Directory not empty`). The ignore flag covers persist.py's untracked raw-thread fallback spawned mid-shutdown.

**Running the full suite in the Replit container:** the 8GB container cannot survive a single-process full run — pytest gets SIGBUS (bus error in SQLite migrations at random tests) or is OOM-killed silently around 60–75%. Run in alphabetical chunks of ~10 files instead; also note background `setsid` runners get reaped by the container, so prefer foreground `timeout 280` batches. Kill tsserver/stale Playwright chromium first to free ~2GB. On the user's 128GB machine a single run is fine.

## Timing-based concurrency tests (Aug 2026)
Never give a "fast path finishes before slow path" test a fixed-sleep window — loaded CI runners flake it (OCR isolation test failed by 0.47s). Start the timing window from an event set inside the slow worker (threading.Event) and widen the slow duration under `CI` env. Sibling perf assertions should carry explicit CI headroom in the budget comment.
