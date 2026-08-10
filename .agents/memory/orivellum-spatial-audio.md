---
name: Spatial audiobook rendering
description: Design rules for the optional spatial pass on audiobook renders (panning, wide mode, ambience beds)
---

# Spatial audiobook rendering

Capability lives in `capabilities/spatial.py`; wired into both the sync route and the async worker in the studio routes.

**Rules:**
- Panning happens on per-render temp copies at concat time — the dry TTS segment cache is NEVER modified. Panned parts are all-or-nothing: mixing mono and stereo inputs breaks the concat demuxer, so any per-part failure falls back to the dry mono parts.
- Narrator (and silences) are always dead center; cast voices get deterministic hash-derived positions capped at ±0.35 so re-renders are stable.
- Order: pan at concat → loudnorm mastering (stereo, preserves balance) → optional post-master pass (stereowiden for "wide" + alimiter at −3 dBTP; ambience bed looped + sidechaincompress duck ~−20 dB under speech, amix normalize=0) → QA gate.
- **Fallback policy:** the optional enhancement can never fail a render. Every spatial stage (pan, finish pass, QA on the result) falls back to the plain mastered output on failure.
- Settings are per-Work in `works.meta["spatial_audio"]` with GET/PUT endpoints mirroring the voice-casting pattern; render request fields are overrides where None = "use saved settings".
- **Why:** UI races taught us the client must omit spatial overrides until that Work's settings have resolved (gate on a loaded-for-work marker), or a stale/false override silently disables a saved setting. Optimistic saves need rollback + a save sequence counter.
- Ambience doc must validate as audio at PUT time (kind or content_path extension), not degrade at render time.
- Spatial outputs carry a `_spatial` filename tag and " (spatial)" registered title so the outputs gallery distinguishes them.
- Trailers never render audio (script/plan only) — spatial applies to audiobook renders exclusively.
