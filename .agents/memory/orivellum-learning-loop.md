---
name: Orivellum learning loop
description: Evidence scoring, contradiction detection, and semantic/hybrid search design decisions
---

# Learning loop (MONARCH-inspired)

- `capabilities/evidence.py` — deterministic confidence scoring: weights base 0.45 / corroboration 0.25 / recency 0.10 / review 0.20; base by knowledge kind, LLM-origin items capped at 0.70. `rescore_work` ALWAYS persists `meta.evidence` components (even if score unchanged) — a review round flagged skipping stable items as a bug.
- Contradiction detection: structured heuristic groups by (subject, predicate) then splits by object — one representative pair per differing object pair (avoids O(n²)); negation heuristic capped at 200 pairs/subject. Conflicts batch-inserted via `db.create_conflicts_batch` (single commit + single audit row).
- Conflict resolution: keep_a/keep_b marks the loser `rejected`; keep_both just sets resolution. Endpoints under `/api/governance/conflicts` (system.py router prefix is `/api`, NOT `/api/system`).
- `capabilities/embeddings.py` — POSTs `${serving.base_url}/embeddings` with `cfg.serving.embedder_model`; returns None on any failure so ALL callers fall back to FTS silently. Vectors are float32 BLOBs in the existing `vectors` table; pure-Python cosine (no numpy in deps).
- **Hybrid hit shape rule:** semantic knowledge hits must select the full canonical knowledge columns (source_doc_id, predicate, object, meta, created_at) — chat provenance/citations break otherwise. Over-fetch each source (2×limit) before dedup-merge or overlap shortens results.
- Nightshift gained 3 passes (evidence rescore, contradiction detect, embedding backfill 300/night), each in its own try/except; report lines added.
- Manual trigger: `POST /api/governance/rescore`.
- Tests in `tests/test_evidence.py` (20); no shared `db` fixture in conftest — tests define their own tmp OrivellumDB fixture; `create_work`/`create_document` return dicts, not ids.
