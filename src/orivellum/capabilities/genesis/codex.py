"""
GENESIS Brainstorm Codex — embedded from BRAINSTORM_CODEX.md.
"""

CODEX_TEXT = """\
# BRAINSTORM CODEX

## G0 G1 — Idea generation
- **"What if?" ladder** — state a what-if, then ask "what if?" of the *answer*, three rungs deep. The third rung is usually where the real book hides.
- **Premise collision** — force two unrelated obsessions into one frame; the friction is the story.
- **Obsession inventory** — list what you cannot stop thinking about. Heat you already have beats heat you must manufacture.
- **Library mining** — harvest sparks from your own corpus (prior fragments, the existing world). Each keeps its provenance pointer.
- **History / scripture mining** — for historical fiction, a real event or a gap in an attested account is a legitimate spark engine.
- **Inversion** — take a familiar story and reverse its moral center; write from the silenced position.
- **The forbidden question** — the thing you're afraid to write is often the book only you can write.
- **Constraint seeding** — pick the constraints first (POV, setting, taboo) and let them force originality.
- **Single-image start** — begin from one vivid image and interrogate it: who, when, why does it burn?

## G1 G2 — Premise development
- **Logline formula** — *When [inciting event], a [flawed protagonist] must [goal] or else [stakes].* If you can't fill it, you don't have a premise yet.
- **Dramatic question** — the single yes/no the whole book answers.
- **Central argument** — thesis vs antithesis → the synthesis is your theme. A book argues; it does not merely depict.
- **"So what?" stress test** — say the premise aloud and answer "so what?" until you reach something that actually matters.
- **Comps ("X meets Y")** — locate the book in a tradition; exposes the reader promise.
- **Five-second pitch** — if it doesn't survive compression to one breath, the core isn't clear yet.

## G3 — World and canon
- **Iceberg / from the waterline** — build only what the story touches; imply the rest. Depth is felt, not dumped.
- **Tiered canon table** — every fact carries an authority tier (historical / inferred / invented) and a source pointer.
- **"Day in the life" test** — walk an ordinary day in the world; gaps in world-logic surface fast.
- **Research-question backlog** — turn every "not sure" into a tracked question, never a silent guess.
- **Anachronism sweep** — audit material culture, language, and theology against the period.

## G4 — Character
- **Want vs need** — the external goal drives plot; the internal need drives arc. Their collision is the engine.
- **Wound → lie → arc** — a past wound plants a false belief; the arc is what the story does to that belief (overcome / deepen / defend).
- **The ghost** — the specific backstory event behind the wound. Concrete beats abstract.
- **Hot-seat interview** — put the character in a chair and ask hard questions in their voice; reveals motive and diction cheaply.
- **Foil mapping** — pair each principal with the character who most exposes their flaw by contrast.
- **Negation of the negation** — push the antagonist's value to its worst extreme.
- **Cast economy** — for every character ask "could another character do this job?" Merge ruthlessly.

## G5 — Structure
- **Three-act / four-act** — setup, confrontation, resolution; the four-act split halves the middle at a strong midpoint.
- **Save-the-Cat beat sheet** — ~15 beats from opening image to final image.
- **Hero's Journey** — ~12 stages from ordinary world through ordeal to return.
- **Story Circle** — 8 steps (you → need → go → search → find → take → return → change).
- **Seven-point structure** — hook, plot turns, pinches, midpoint, resolution; built backward from the ending.
- **Snowflake expansion** — grow by expansion: one sentence → one paragraph → one page → character sheets → scene list.
- **Tentpole-first** — place the scenes you can already see, then bridge.
- **Reverse outline** — write the ending, then work backward.

## G8 — Prep and de-risking
- **Pre-mortem** — assume failure; name the causes; defuse them in the grid.
- **Synopsis-first** — write the 2-page synopsis before chapter 1; if the synopsis is boring, the book is.
- **Value-shift grid** — track each chapter's emotional charge flip; flat rows are cut candidates.
- **Promise / progress / payoff audit** — every promise the premise makes must have escalation and a payoff on the grid.
"""

# Map stage codes to the codex section heading prefixes that cover them
_STAGE_HEADING_MAP: dict[str, list[str]] = {
    "G0": ["G0 G1"],
    "G1": ["G0 G1", "G1 G2"],
    "G2": ["G1 G2"],
    "G3": ["G3"],
    "G4": ["G4"],
    "G5": ["G5"],
    "G6": [],
    "G7": [],
    "G8": ["G8"],
    "G9": [],
}


def get_codex_for_stage(code: str) -> str:
    headings = _STAGE_HEADING_MAP.get(code, [])
    if not headings:
        return f"No specific codex section for {code}. Use the full codex for inspiration."
    blocks = CODEX_TEXT.split("## ")
    found = []
    for heading in headings:
        for block in blocks:
            if block.startswith(heading):
                found.append("## " + block.strip())
    return "\n\n".join(found) if found else CODEX_TEXT
