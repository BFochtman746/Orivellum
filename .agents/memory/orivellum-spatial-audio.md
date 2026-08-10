---
name: Spatial audiobook rendering
description: Durable design rules for the optional spatial pass on audiobook renders (panning, wide mode, ambience beds)
---

# Spatial audiobook rendering — design rules

- **Dry cache is sacred.** Spatialization operates on per-render temp copies at concat time; cached TTS segments are never modified. Panning is all-or-nothing per render — mixing mono and stereo inputs breaks the concat demuxer, so any per-part failure means falling back to the dry mono parts.
  **Why:** cache corruption would poison every future render; partial panning corrupts concat output.
- **Optional enhancements never fail a render.** Every spatial stage (pan, post-master polish, QA of the polished result) falls back to the plain mastered output on failure.
- **Stage order matters:** pan → loudness mastering (stereo loudnorm preserves balance) → widen/ambience polish (with a limiter at the mastering true-peak ceiling) → QA gate. Ducking an ambience bed under speech uses sidechain compression (~−20 dB duck per the owner's research doc) with the bed held quiet enough that silence/clipping QA still holds.
- **Placement is deterministic:** narrator and silence always dead center; other voices hash to stable, bounded off-center positions so re-renders sound identical.
- **Per-Work settings, override-or-saved semantics:** settings persist on the Work; render-request fields are overrides where "unset" means "use saved". Validate ambience sources as audio at save time, not render time.
- **Client races (learned via review rejection):** auto-saved per-entity settings need (a) controls gated until that entity's settings have loaded, (b) load responses discarded once a save has started, (c) saves queued so PUTs arrive in user-action order, and (d) failure rollback only when no newer save superseded it. This ordering logic should live in a small testable module, not inline in the component.
- Trailers never render audio (script/plan output only) — spatial treatment applies exclusively to audiobook renders.
