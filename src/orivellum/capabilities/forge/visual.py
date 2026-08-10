"""Forge DESIGN phase — generate three visual design directions."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from orivellum.capabilities.llm import llm_call

logger = logging.getLogger(__name__)

DESIGN_SYSTEM = """You are a senior visual designer specialising in editorial web design.
Given a site plan, produce exactly THREE distinct visual design concepts in JSON.
Output ONLY valid JSON — no markdown fences, no prose.

Top-level key: "concepts" — array of 3 objects each with:
  id          string   — unique slug e.g. "concept-a"
  name        string   — short evocative name e.g. "Midnight Archive"
  summary     string   — two-sentence description of the visual direction
  palette     object   — { "primary": "#hex", "secondary": "#hex", "accent": "#hex",
                           "background": "#hex", "text": "#hex" }
  typography  object   — { "display": "font family name", "body": "font family name",
                           "displayStyle": string }
  layout      object   — { "density": "compact|balanced|spacious",
                           "heroPattern": string, "gridStyle": string }
  tokens_hint string   — prose description of the CSS custom-property scheme
"""


def create_visual_design(
    cfg: object,
    db: object,
    plan: dict,
    instruction: str = "",
    on_event: Callable | None = None,
) -> dict:
    """Generate 3 visual design concepts for the approved site plan."""
    plan_summary = json.dumps(
        {
            k: plan.get(k)
            for k in ("title", "description", "palette_hint", "tone", "target_audience")
        },
        ensure_ascii=False,
    )
    user_msg = f"Site plan:\n{plan_summary}"
    if instruction:
        user_msg += f"\n\nAdditional direction:\n{instruction}"

    if on_event:
        on_event("design_start", "Generating three visual directions…")

    result = llm_call(
        [
            {"role": "system", "content": DESIGN_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        cfg=cfg,
        db=db,
        purpose="forge.design",
        timeout=60,
        max_tokens=2000,
    )

    if not result.ok or not result.text:
        raise RuntimeError(f"Visual design generation failed: {result.error}")

    text = result.text.strip()
    for fence in ("```json", "```"):
        if fence in text:
            text = text.split(fence, 1)[1].rsplit("```", 1)[0]
            break
    try:
        design = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Design JSON parse error: {exc}") from exc

    concepts = design.get("concepts", [])
    if not concepts:
        raise RuntimeError("Design generation returned no concepts.")

    if on_event:
        names = ", ".join(c.get("name", c.get("id", "?")) for c in concepts)
        on_event(
            "design_ready",
            f"Three concepts ready: {names}. Select one and approve to begin building.",
            {"design": design},
        )

    return design
