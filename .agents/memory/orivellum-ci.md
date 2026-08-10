---
name: GitHub CI environment differences
description: Why tests pass locally but fail on GitHub runners, and the rules that keep CI green.
---

# CI (GitHub Actions) — environment rules

- **Runners lack local-only assets:** no ffmpeg by default (ci.yml now apt-installs it), no Kokoro ONNX model files (kokoro-dependent tests must skipif on file absence), no real secrets (tests needing TAVILY_API_KEY must stub it with `patch.dict` — the code bails out early when unset even if the network layer is mocked).
- **JS job needs two things:** root package.json `packageManager: "pnpm@x"` pin (pnpm/action-setup fails instantly without it) and `pnpm run typecheck:libs` BEFORE the UI tsc — fresh checkouts have no `lib/*/dist`, so project references fail with TS6305 + cascading implicit-any errors that never appear locally.
- **Timing assertions:** CI shared runners are ~2× slower; perf budgets must either scale on `os.getenv("CI")` or leave wide margins that still distinguish the failure mode (parallel vs sequential).
- **Never patch the same target from concurrent coroutines** (`with patch(...)` inside each of two gathered coroutines): exit-order restores the wrong "original" and permanently leaks the MagicMock into the module, silently breaking every later test in the session. Patch once around the `gather`. This exact bug leaked a mock `PIL.Image.open` and broke 10 unrelated tests.
- Every failed push emails the repo owner ("Run failed: CI") — keep CI green or the user gets spammed.
