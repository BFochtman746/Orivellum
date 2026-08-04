---
name: Mobile Learn Tab
description: Session-limit logic, concepts view, and API routing for the MobileLearnTab in work/[id].tsx
---

## Session limit (#278)
- `SESSION_LIMIT = 5` correct answers (score ≥ 0.75) per sitting triggers `'session_done'` phase
- Counter is `sessionCorrect` state; reset to 0 when user taps "Keep studying" or `init()` is called
- Phase type: `'loading' | 'seeding' | 'question' | 'assessing' | 'feedback' | 'all_done' | 'error' | 'session_done'`

## Concepts list (#279)
- Endpoint: `GET /api/works/{work_id}/learning/concepts` — lives in `src/orivellum/api/routes/learning.py` (NOT works.py)
- Returns `{ concepts: [{ id, subject, description, score, consecutive_passes, graduated }] }`
- Toggle between "Study" and "Concepts" sub-views via `learnView` state
- Concepts are fetched lazily when user switches to "Concepts" view

**Why:** Keeps the lazy load pattern consistent; avoids blocking the study UI with an extra network call on mount.
