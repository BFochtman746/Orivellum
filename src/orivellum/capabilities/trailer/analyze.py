"""
Stage 1 — ANALYZE.
Read representative passages and produce a grounded Book Brief.
(Ported from media_studio; uses OrivellumLLM instead of urllib LLM.)
"""

from __future__ import annotations

from .io_orivellum import sample_passages

SYSTEM = (
    "You are a literary development analyst preparing a book for a trailer. "
    "You read the provided passages and report ONLY what the text supports. "
    "If a field is unclear from the passages, say 'unclear' rather than inventing. "
    "Be concrete and visual where the text allows."
)

SCHEMA = """{
  "title": str, "logline": str (<=40 words), "genre": str, "subgenre": str,
  "period_setting": str, "tone": [str], "themes": [str],
  "protagonist": str, "central_stakes": str, "emotional_arc": str,
  "visual_motifs": [str], "audience": str, "comparable_titles": [str],
  "content_sensitivities": [str]
}"""


def run(llm, cfg: dict, full_text: str, title_hint: str = "") -> dict:
    passages = sample_passages(full_text)
    user = (
        f"Working title hint (may be blank): {title_hint!r}\n\n"
        "Analyze the book from these representative passages spanning its arc. "
        "Extract a development brief for a book trailer.\n\n"
        f"PASSAGES:\n{passages}"
    )
    brief = llm.json(
        model=cfg["llm"].get("analysis_model", "default"),
        system=SYSTEM,
        user=user,
        schema_hint=SCHEMA,
    )
    if title_hint and (not brief.get("title") or brief["title"].startswith("(")):
        brief["title"] = title_hint
    return brief
