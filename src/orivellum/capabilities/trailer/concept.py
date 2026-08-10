"""
Stage 2 — CONCEPT.
Generate N distinct trailer concepts, score each on a transparent rubric,
and recommend one.
(Ported from media_studio; uses OrivellumLLM.)
"""

from __future__ import annotations

SYSTEM = (
    "You are a trailer director. You propose DISTINCT trailer concepts for a "
    "book — each a different strategy, not a restyle of the same idea. Ground "
    "every concept in the book brief. Keep beats concrete and shootable."
)

SCHEMA = """{"concepts":[{
  "name": str, "angle": str, "rationale": str,
  "duration": int (seconds), "beats": [str],
  "visual_style": str, "voice_direction": str, "music_direction": str
}]}"""

RUBRIC = {
    "fidelity": 0.35,
    "hook": 0.30,
    "feasibility": 0.20,
    "distinctiveness": 0.15,
}


def run(llm, cfg: dict, brief: dict) -> dict:
    n = cfg["defaults"].get("concepts_to_generate", 3)
    dur = cfg["defaults"].get("target_duration_seconds", 75)
    user = (
        f"BOOK BRIEF:\n{_fmt(brief)}\n\n"
        f"Propose exactly {n} distinct trailer concepts, each about {dur}s. "
        "Vary the strategy (e.g. atmospheric/mood, narrative-hook, "
        "voice/scripture-led, character-stakes). 4-6 beats each."
    )
    out = llm.json(
        model=cfg["llm"].get("writing_model", "default"),
        system=SYSTEM,
        user=user,
        schema_hint=SCHEMA,
    )
    concepts = out.get("concepts", [])
    for c in concepts:
        c["scores"], c["score_total"] = _score(c, brief)
    concepts.sort(key=lambda c: c["score_total"], reverse=True)
    return {
        "concepts": concepts,
        "recommended": concepts[0]["name"] if concepts else None,
        "rubric": RUBRIC,
    }


def _score(concept: dict, brief: dict) -> tuple[dict, float]:
    beats = concept.get("beats", [])
    text = (concept.get("angle", "") + " " + concept.get("rationale", "")).lower()

    fidelity = _overlap(concept, brief)
    hook = min(
        1.0,
        0.4
        + 0.15
        * sum(k in text for k in ("question", "hook", "stakes", "reversal", "urgent", "twist")),
    )
    n_shots = max(1, len(beats))
    feasibility = max(0.2, 1.0 - 0.08 * (n_shots - 4))
    if any(
        w in concept.get("visual_style", "").lower()
        for w in ("tableaux", "atmospheric", "painterly", "mood")
    ):
        feasibility = min(1.0, feasibility + 0.15)
    distinctiveness = min(1.0, 0.5 + 0.1 * len(set(b.split()[0].lower() for b in beats if b)))

    total = round(
        RUBRIC["fidelity"] * fidelity
        + RUBRIC["hook"] * hook
        + RUBRIC["feasibility"] * feasibility
        + RUBRIC["distinctiveness"] * distinctiveness,
        3,
    )
    return {
        "fidelity": round(fidelity, 3),
        "hook": round(hook, 3),
        "feasibility": round(feasibility, 3),
        "distinctiveness": round(distinctiveness, 3),
    }, total


def _overlap(concept: dict, brief: dict) -> float:
    brief_words = set(
        w.lower()
        for field in ("themes", "tone", "visual_motifs")
        for item in (brief.get(field) or [])
        for w in str(item).split()
    )
    concept_text = " ".join(
        [
            concept.get("angle", ""),
            concept.get("rationale", ""),
            concept.get("visual_style", ""),
            concept.get("music_direction", ""),
        ]
    ).lower()
    if not brief_words:
        return 0.6
    matches = sum(1 for w in brief_words if w in concept_text)
    return min(1.0, 0.4 + 0.6 * matches / len(brief_words))


def _fmt(d: dict) -> str:
    lines = []
    for k, v in d.items():
        if isinstance(v, list):
            lines.append(f"{k}: {', '.join(str(x) for x in v)}")
        else:
            lines.append(f"{k}: {v}")
    return "\n".join(lines)
