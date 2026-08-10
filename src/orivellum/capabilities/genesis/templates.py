"""
Embedded template markdown content for all ten GENESIS stages.
Keys match STAGE_BY_CODE[code][2] (the template slug).
"""

TEMPLATE_CONTENT: dict[str, str] = {
    "G0_spark_slate": """\
# G0 — Spark Slate
Mode: <<FILL: COLD | LIBRARY>>

## Candidate sparks (>=3)
| # | Spark (one line) | Provenance (source_pointer or AUTHOR-GENERATED / IDEA-ONLY) | Heat 0-3 | Reach 0-3 | Fit 0-3 |
|---|------------------|--------------------------------------------------------------|----------|-----------|---------|
| 1 | <<FILL>> | <<FILL>> |  |  |  |
| 2 | <<FILL>> | <<FILL>> |  |  |  |
| 3 | <<FILL>> | <<FILL>> |  |  |  |

## Chosen spark
<<FILL: which spark carries forward, and why>>

Exit gate: one spark chosen; every LIBRARY spark has a verifiable source_pointer
or is relabelled IDEA-ONLY.
""",
    "G1_premise": """\
# G1 — Premise Forge

## What-if ladder (3 rungs)
1. <<FILL>>
2. <<FILL>>
3. <<FILL>>

## Five load-bearing elements
- Protagonist: <<FILL>>
- Desire (external goal): <<FILL>>
- Opposition: <<FILL>>
- Stakes: <<FILL>>
- Change (what is different by the end): <<FILL>>

## Premise paragraph (5-7 sentences)
<<FILL>>

## Logline (<=60 words)
<<FILL>>

## Dramatic question (whole-book yes/no)
<<FILL>>

## Central argument
- Thesis: <<FILL>>
- Antithesis: <<FILL>>
- Suspected synthesis (theme): <<FILL>>
""",
    "G2_viability": """\
# G2 — Viability Gate

## Conflict engine (renewable source of conflict for the full length)
<<FILL>>

## "So what?" / why me, why now
<<FILL>>

## Comparable titles (X meets Y)
<<FILL>>

## Scope
- Target length (chapters): <<FILL>>
- Target acts: <<FILL>>
- Rough calendar: <<FILL>>

## Pre-mortem risk watchlist (top 3 failure causes)
1. <<FILL>>
2. <<FILL>>
3. <<FILL>>

## VERDICT
<<FILL: GO | PARK | KILL>>
""",
    "G3_canon_seed": """\
# G3 — Canon Seed (World, Pillar 1)

## Canon facts (tiered)
| Fact | Tier (HISTORICAL / INFERRED / INVENTED) | source_pointer (required for HISTORICAL & INFERRED) |
|------|-----------------------------------------|-----------------------------------------------------|
| <<FILL>> | <<FILL>> | <<FILL>> |
| <<FILL>> | <<FILL>> | <<FILL>> |

## Research-question backlog
- <<FILL>>
- <<FILL>>

## Anachronism sweep notes
<<FILL>>

Exit gate: zero HISTORICAL/INFERRED facts without a source_pointer.
""",
    "G4_character_web": """\
# G4 — Character Web (Character, Pillar 2)

## Principal dossiers
### <<FILL: name>>  (function: <<FILL>>)
- Want (external): <<FILL>>
- Need (internal truth): <<FILL>>
- Wound: <<FILL>>
- Lie believed: <<FILL>>
- Ghost (backstory event): <<FILL>>
- Arc hypothesis (overcome / deepen / defend): <<FILL>>

### <<FILL: antagonist name>>  (function: antagonist)
- Want / Need / Wound / Lie / Ghost / Arc: <<FILL>>

## Relationship matrix (one line per principal pair)
- <<FILL A>> <-> <<FILL B>>: <<FILL charge / friction>>

## Cast-economy pass
<<FILL: any merges made or explicitly declined>>
""",
    "G5_structure_beats": """\
# G5 — Structure & Beats (Structure, Pillar 3)

## Named spine
<<FILL: e.g. Four-act / Save-the-Cat / Hero's Journey / Story Circle / Snowflake>>

## Tentpole scenes (3-7 you can already see)
1. <<FILL>>
2. <<FILL>>
3. <<FILL>>

## Beat sheet (each beat states its job: turn / escalate / reveal / reverse)
| Beat | Job | Act | Rough chapter span |
|------|-----|-----|--------------------|
| <<FILL>> | <<FILL>> | <<FILL>> | <<FILL>> |

## Per-act pacing shape (Dimension 13)
- Act 1: <<FILL>>
- Act 2: <<FILL>>
- Act 3: <<FILL>>
- Act 4: <<FILL>>
""",
    "G6_voice_spec": """\
# G6 — Voice Specification (Voice, Pillar 4)

## Invariants (no TBD allowed at the gate)
- POV: <<FILL>>
- Person / tense: <<FILL>>
- Narrative distance: <<FILL>>
- Register / diction / rhythm: <<FILL>>
- Motif system (recurring images): <<FILL>>

## Style sheet
<<FILL: spellings, capitalizations, divine-name treatment, dialogue & chapter-title conventions>>

## Golden calibration samples (~150 words each)
### Interior moment
<<FILL>>
### Scene of conflict
<<FILL>>
### Lament
<<FILL>>
""",
    "G7_standard_binding": """\
# G7 — Standard Binding

## Governing standard
<<FILL: e.g. 17-dimension mastery standard reconciled with FORGE via the Standards Concordance>>

## Concordance decision of record
<<FILL: which dimensions retire as scorers, which remain as go/no-go gates>>

## Binding table
| Act / chapter range | Instrument | Pass condition |
|---------------------|------------|----------------|
| <<FILL>> | <<FILL>> | <<FILL>> |

## Notes
<<FILL: any dimension-specific notes or exceptions>>
""",
    "G8_chapter_blueprint": """\
# G8 — Chapter Blueprint (Advanced Prep)

## Chapter grid
| # | Act | POV | Chapter goal | Conflict | Turn / value-shift | Beats | Canon touched | Std dims |
|---|-----|-----|--------------|----------|--------------------|-------|---------------|----------|
| 1 | <<FILL>> | <<FILL>> | <<FILL>> | <<FILL>> | <<FILL>> | <<FILL>> | <<FILL>> | <<FILL>> |

## Promise / progress / payoff audit
<<FILL: each premise promise -> its escalation -> its payoff row>>

## Risk watchlist retirement (from G2)
- Risk 1 -> defused at: <<FILL>>
- Risk 2 -> defused at: <<FILL>>
- Risk 3 -> defused at: <<FILL>>
""",
    "G9_ready_to_write": """\
# G9 — Ready-to-Write (Assembled Book Bible)

This document assembles the sealed origination package handed to BPOS B0.
Fill the summary; the seal command computes the manifest + hashes.

## Book Bible contents
- Premise & central argument: see G1
- Canon seed (tiered): see G3
- Character web: see G4
- Structure & beats: see G5
- Voice spec + golden samples: see G6
- Standard binding: see G7
- Chapter blueprint: see G8

## Author sign-off statement
<<FILL: I, the author, affirm this origination is ready to write.>>
""",
}
