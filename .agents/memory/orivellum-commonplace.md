---
name: Commonplace notes system
description: Daily note capture → AI filing proposals → review approval → append-only markdown vault + daily reports.
---

# Commonplace notes

Pipeline is one-directional: inbox → proposed → approved → filed (or rejected). Schema v112 (`note_blocks`, `note_reports`).

- **Server owns all filing structure.** The LLM may only name category IDs from the fixed 16-category policy (`CATEGORIES` in capabilities/notes.py); unknown IDs are replaced with `unsorted` + warning. Never let the model name paths or files.
- **Vault is append-only and marker-idempotent** (`<!-- block:{id} -->`): canonical entry in `vault/Journal/Daily/{day}.md`, link lines in `Journal/_indexes/{Category}.md`, reports in `vault/Reports/`.
- **Approval durability rule:** the review resolver claims `proposed→approved` atomically, then calls `complete_approval()` — fully idempotent (marker-guarded vault writes, tasks deduped per text, knowledge deduped by `meta.block_id`), status flips to `filed` LAST. On any failure the block stays `approved` and nightshift pass 19 replays via `resume_approved()`. **Why:** a disk error mid-filing must never strand a note or lose the user's decision.
- **Action provenance guard:** actions require `stated:true` AND ≥60% word overlap with the note text; unstated due dates are stripped. **Why:** a hallucinated model action must never silently become a task. Review UI shows full action text, never just a count.
- Daily report derives ONLY from approved/filed blocks; mechanical fallback sentence when the LLM is unreachable (never blocks).
- UI app "Commonplace" at /notes (`pages/notes/index.tsx`); proposals resolve in the unified review inbox as `noteblock:{id}` items.
