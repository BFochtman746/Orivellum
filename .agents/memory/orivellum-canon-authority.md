---
name: Canon authority (canon_fact)
description: Classified, sourced trilogy facts — store guards, G3 seeding, ratification via review gate
---

# Canon authority

- `canon_fact` table (schema v123): classification HISTORICAL/INFERRED/INVENTED; status active/superseded/retracted; `work_id NULL` = series-wide. Store lives in `CanonStore` (separate from db.py, WAStore pattern, db.py at file budget).
- Insert-path refusals (never bypass): HISTORICAL needs source_ref; INFERRED needs live parent ids; INVENTED needs signed_by. All raise `CanonFactError` (a ValueError → routes map to 422).
- **Batch seeding must be one transaction.** `create_facts_batch` runs all rows in a single `governed_write` — a late refusal rolls back the whole seed. In-batch parents can't be checked via `read_conn` (WAL won't see uncommitted rows); pass `conn=db._conn` + `extra_active` set.
- **Ratification is claim+insert in ONE governed_write.** No compensation code: the conditional proposal claim and the fact insert commit or roll back together, so an approved proposal can never lack its fact and a refusal auto-releases the claim.
- Proposal scope mapping: `series:*` → work_id NULL; any other scope requires the author to pick a Work explicitly (refuse, never guess). Explicit work_id always wins.
- Author signature is enforced server-side in the review resolver (422 when blank) — UI checks alone are not a boundary.
- G3 gate pass parses the artifact's canon-fact markdown table (`parse_canon_seed`) and seeds via the batch; parse or guard errors block the gate with 422. Re-pass is idempotent (identical active facts skipped, ids still resolve for parents).

**Why:** the whole point of the authority is that nothing enters canon unchecked; partial writes or unsigned ratifications silently corrupt it.
**How to apply:** any new write path into canon_fact must go through CanonStore guards inside one governed_write; never insert directly.
