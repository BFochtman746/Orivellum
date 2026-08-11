---
name: Work render pause/resume
description: Resume design for long Work audiobook renders — cache-key agreement, engine-availability rule, UI detach pattern.
---

# Work audiobook pause/resume

> Aug 2026: document renders now follow the same survive-unmount policy as
> work renders — never cancel on unmount, rediscover via a `/active` endpoint,
> and the start route 409s with the live job_id (checked atomically with the
> registry insert) so remount-discovery racing Generate can't double-render.
> Async UI callbacks must compare against a live-target ref (currentDocRef /
> currentWorkRef), never closure state captured before an await.

- Resume works purely through the persistent segment cache: a re-render with the same voice/speed fast-forwards cached segments. `_chapter_segment_texts` / `_work_credits_texts` are the single source of truth for segment texts — the worker AND the resume-info endpoint must both use them or cache keys drift.
- **Rule:** resume-info only counts cache entries an actually reachable engine could reuse (`_kokoro_probably_available()` — no-load probe: loaded, or package + model files present). **Why:** claiming "resumable" when the engine is gone breaks the guarantee. CI runners have no Kokoro, so resume tests must monkeypatch availability just like they fake the engine.
- Job status snapshots must be copied UNDER `_work_tts_jobs_lock` — the worker mutates the same dict concurrently.
- **UI pattern:** switching Work/mode detaches the UI from a running job (clear interval + job id state) while the server job keeps rendering; every poll closure checks `jobIdRef.current === job_id` before applying state and holds its own interval handle. Document-mode still lacks this guard (queued as a task).
