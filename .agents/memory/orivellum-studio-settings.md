---
name: Per-Work Studio settings persistence
description: How per-Work Studio preferences (spatial audio, default narrator) persist and the sync pattern to reuse
---

# Per-Work Studio settings persistence

Per-Work Studio preferences live in `works.meta` (e.g. `voice_casting`, `narrator_voice`, spatial settings) and are auto-persisted from the UI via small testable sync classes (`spatialSettings.ts`, `narratorSync.ts`) rather than component effects alone.

**Rule:** any new per-Work Studio preference should follow the same contract:
- load-baseline guard — nothing saves until the Work's GET resolved, so opening a Work never overwrites its saved value with the picker default
- debounced, latest-wins saves on a single promise chain; work-switch cancels pending saves; flush on unmount
- failed saves leave the baseline unchanged so the same pick retries

**Why:** a completion review rejected narrator persistence that relied on the Chapter Voices "Save voices" button — that button only exists when the Work has ready chapters, and a manual picker change never enabled it. Persistence for a form control must not depend on a save affordance that may not be rendered.

**How to apply:** the casting endpoint `PUT /studio/works/{id}/casting` accepts partial updates — `sections` omitted = narrator-only save leaving casting untouched; `narrator_voice` `""` clears, `None` leaves unchanged. Mirror this partial-update shape for new meta preferences.
