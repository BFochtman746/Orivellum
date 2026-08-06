---
name: Chapter-first novel architecture
description: Per-chapter extraction, chapter-tagged knowledge, chat chapter scoping, and cap fixes for novel-length documents.
---

## What was built

### Schema v84
- `knowledge.chapter_id TEXT REFERENCES book_chapters(id) ON DELETE SET NULL`
- `idx_knowledge_chapter` index
- `create_knowledge_item()` gains `chapter_id: str | None = None` parameter (backward-compatible)

### Caps lifted
- `_EXTRACTED_TEXT_CAP` in `chunking.py`: 100 000 → 2 000 000
- `pipeline.py` stored text: `result.full_text[:100_000]` → `result.full_text`
- `knowledge_harvest.py` entity scan: `result.full_text[:50_000]` → `result.full_text`
- (`_MAX_LLM_CHUNKS` still applies to the old page-based path; chapter path replaces it for structured docs)

### Two-line chapter heading detection (`chapters.py`)
- `_peek_next_line(text, pos)` — after matching `Chapter N` with no inline title, checks the next non-empty line (≤100 chars, not another heading) and uses it as the title
- `ExtractedChapter` gains `scene_count: int` (computed in `__post_init__` via `_count_scenes`)
- `_count_scenes` removes each matched pattern from a working copy before the next to prevent double-counting
- Scene breaks detected: `\n* * *\n`, `\n# # #\n`, `\n***\n`, `\n---\n`, `\n###\n`

### Per-chapter fiction harvest (`knowledge_harvest.py: llm_harvest_by_chapters`)
- Queries `book_chapters` for the doc directly (no IDs passed — self-contained)
- Sends up to `_MAX_CHAPTER_CHARS = 6_000` chars per chapter to LLM
- `_FICTION_CHAPTER_PROMPT` extracts: characters (×8), events (×6), settings (×4), relationships (×5), themes (×3), foreshadowing (×3)
- All items get `review_status='ai_auto'` and `chapter_id=<chapter id>`
- Characters + relationships also written to entity graph via `upsert_entity` / `create_entity_edge`

### Pipeline routing (`pipeline.py` step 5)
- Flag `_has_chapters = (n_chapters >= 2)` set during step 4.5
- If `_has_chapters` and AI enabled → `llm_harvest_by_chapters()` replaces old `llm_harvest()`
- Unstructured docs (no chapters) still use old page-based path unchanged

### `upsert_book_chapters` meta (`db.py`)
- Now reads `ch.get("meta")` from each chapter dict and stores it (JSON) instead of hardcoded `'{}'`
- Pipeline passes `"meta": {"scene_count": c.scene_count}` per chapter

### Chapter health endpoint (`works.py GET /works/{id}/chapters`)
- SQL gains: `bc.meta`, `(SELECT COUNT(*) FROM knowledge k WHERE k.chapter_id = bc.id) as knowledge_count`
- Python loop parses `meta` JSON → exposes `scene_count` per chapter in the API response

### Chat chapter scoping (`conversations.py _build_system_prompt`)
- Regex `\bchapter\s+(\d+|one|two|...|twenty)\b` detects chapter references in the user query
- Looks up the chapter by `seq = chapter_num - 1` (0-indexed) for the conv's linked Work
- Injects `CHAPTER CONTEXT — <title>:\n<text[:3000]>` + up to 12 chapter-tagged knowledge items into `base` before the main hybrid search runs

**Why:** chapters are 0-indexed in `book_chapters.seq` (seq=0 = Chapter 1).

## Key invariants
- `chapter_id` is always NULL for rule-based (`auto`) items; only LLM items from chapter harvest carry it
- Old callers of `create_knowledge_item()` work unchanged (chapter_id defaults to None)
- `llm_harvest_by_chapters` is only called when `_has_chapters=True` (≥2 chapters extracted)
