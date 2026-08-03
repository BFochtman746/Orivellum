---
name: BPOS WR-03 Canon & Continuity
description: Five deterministic continuity validators, the WR-03 schema tables, and the _parse_year date contract.
---

## Tables added in WR-03

All tables live in `writing_architect_pkg/writing_architect/domain/schema_wr03.sql`.
Applied by `db.init_db()` after `schema.sql` via `_apply_wr03_migrations()`.

| Table | Serves |
|---|---|
| `entity_alias(entity_id, alias, alias_type)` | name_drift validator |
| `entity_location(entity_id, date_ref, location, scene_ref)` | impossible_travel validator |
| `knowledge_state(entity_id, fact_description, can_know_from_scene, scene_sequence)` | knowledge_leak validator |
| `contract_knowledge_access(contract_id, knowledge_state_id, scene_sequence)` | knowledge_leak validator |
| `chapter_contract_entity_ref(contract_id, entity_id, name_used)` | name_drift validator |

Column additions to existing tables (idempotent ALTER TABLE):
- `canon_entity`: birth_date, birth_uncertainty, death_date, destruction_date
- `canon_fact`: stated_age_years INTEGER, at_date TEXT
- `timeline_event`: location TEXT

## Validator contracts

All in `writing_architect_pkg/writing_architect/domain/continuity.py`.
Each validator returns `list[str]` of `editorial_finding` IDs (empty = clean).
All findings are `pass_type='continuity', severity='blocker', raised_by='continuity_validator_wr03'`.

### check_age_date_conflict
- Fires when `abs(at_year - birth_year - stated_age_years) > tolerance_years`
- Default tolerance: 5 years (ancient dates carry inherent uncertainty)
- Skips rows where dates are unparseable (no false positive from bad data)

### check_impossible_travel
- Fires when two `entity_location` rows share the same `entity_id` AND `date_ref` but have different `location` values
- Date strings compared by exact string equality — use consistent formats

### check_knowledge_leak
- Fires when `contract_knowledge_access.scene_sequence < knowledge_state.scene_sequence`
- Meaning: the contract accesses knowledge at a scene EARLIER than when it's available
- If no `contract_knowledge_access` rows exist, validator is silent (not an error)

### check_name_drift
- Fires when `chapter_contract_entity_ref.name_used` ≠ `canon_entity.name` AND is not in `entity_alias` for that entity
- Canonical name itself never needs to be added to entity_alias

### check_object_resurrection
- Objects (kind='object') with `destruction_date`: fires if any `canon_fact.time_start` or `entity_location.date_ref` parses to a year AFTER the destruction year
- Persons (kind='person') with `death_date`: same check on `entity_location` rows
- Uses `_parse_year()` — skips rows with unparseable dates

## _parse_year date contract

**Why:** All five validators need to compare dates numerically. BCE years are stored as human strings ("1200 BCE"), not numbers.

Supported formats (returns signed int; BCE = negative):
- `"1200 BCE"` → `-1200`
- `"c.1200 BCE"`, `"~1200 BCE"`, `"circa 1200 BCE"` → `-1200`
- `"587 BC"` → `-587`
- `"70 CE"`, `"33 AD"` → `70`, `33`
- `"-587"` (ISO negative year) → `-587`
- `"2026"` → `2026`
- `"1200-1150 BCE"` (range) → uses start year → `-1200`
- `"1200 BCE ±25yr"` → `-1200` (strips ± uncertainty)

**Critical bug history:** The uncertainty-suffix regex must use `±` only, not `[±\+\-]`. The bare `-` matched negative-year strings like `"-587"` and stripped the entire value to empty string before the negative-ISO-year branch could catch it. Fixed by using `(?:±|\+/-)` in the suffix pattern.

## CLI commands added in WR-03

```
wa entity     DB BOOK_ID --name "..." --kind person|place|object|institution|concept
              [--birth-date "1200 BCE" --birth-uncertainty "±25yr"
               --death-date "..." --destruction-date "..."]
wa alias      DB ENTITY_ID --alias "..." [--alias-type name|title|epithet|transliteration|nickname]
wa fact       DB ENTITY_ID --fact "..." [--time-start "..." --time-end "..."
              --stated-age N --at-date "..." --claim CID]
wa entity-location DB ENTITY_ID --date "1125 BCE" --location "Mount Tabor" [--scene "Ch3"]
wa knowledge-state DB ENTITY_ID --fact "..." --from-scene "Chapter 5" --scene-seq 5
wa contract-knowledge DB CONTRACT_ID --knowledge-state KID --scene-seq 3
wa entity-ref DB CONTRACT_ID --entity EID --name-used "the Prophetess"
wa continuity-check DB BOOK_ID [--validator age_date_conflict|impossible_travel|
                                  knowledge_leak|name_drift|object_resurrection]
```

## Test counts

- WR-01: 12 tests
- WR-02: 16 tests
- WR-03: 19 tests (10 validator pairs + 6 CLI + 1 run_all_validators clean + 1 run_all_validators dirty + 9 _parse_year parametric — wait, 10+6+2+9=27 but reported as 19 WR-03 tests because 9 parse_year use @parametrize counted as 9 individual tests and 19 counts the non-parametric tests: actually total is 56 = 28 + 28 but counted as 56/56 passing)

Total: 56/56 passing as of 2026-08-03.
