"""Forge PLAN phase — generate a governed site plan via llm_call()."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from orivellum.capabilities.llm import llm_call

logger = logging.getLogger(__name__)

PLAN_SYSTEM = """You are a senior web-product architect.
Your job is to produce a concise but complete JSON site plan for a governed
static website.  Output ONLY valid JSON — no markdown fences, no prose.

The JSON must have these top-level keys:
  site_name   string — short slug-safe name
  title       string — human display title
  description string — one-sentence pitch
  pages       array  — each item: { "slug": string, "title": string, "purpose": string, "sections": [string] }
  nav         array  — page slugs in nav order
  palette_hint string — one-sentence mood/colour direction (e.g. "dark navy, gold accents, editorial")
  target_audience string
  tone        string — e.g. "professional", "playful", "literary"
  design_brief object — extracted from the brief:
      { "non_negotiables": [string]  — things the user explicitly requires,
        "identity": string           — brand personality and emotional tone,
        "primary_cta": string        — the ONE main action a visitor should take,
        "inspiration": string        — referenced styles or sites, "" if none }

Structural constraints (hard rules):
- No page may have more than 6 sections.
- Every page's sections list starts with its single most important message.
- The home page must state the primary CTA in its first section.
"""


def _enforce_plan_constraints(plan: dict) -> None:
    """Enforce structural constraints programmatically — prompt rules alone
    are not reliable with small local models."""
    for page in plan.get("pages", []):
        sections = page.get("sections")
        if isinstance(sections, list) and len(sections) > 6:
            page["sections"] = sections[:6]


def create_plan(
    cfg: object,
    db: object,
    brief: str,
    work_context: str = "",
    instruction: str = "",
    on_event: Callable | None = None,
) -> dict:
    """Call the LLM to produce a site plan and return the parsed dict."""
    user_msg = f"Brief:\n{brief}"
    if work_context:
        user_msg += f"\n\nWork context (knowledge from the linked Work):\n{work_context}"
    if instruction:
        user_msg += f"\n\nAdditional instruction:\n{instruction}"

    if on_event:
        on_event("plan_start", "Generating site plan…")

    result = llm_call(
        [
            {"role": "system", "content": PLAN_SYSTEM},
            {"role": "user", "content": user_msg},
        ],
        cfg=cfg,
        db=db,
        purpose="forge.plan",
        timeout=60,
        max_tokens=1500,
    )

    if not result.ok or not result.text:
        raise RuntimeError(f"Plan generation failed: {result.error}")

    try:
        plan = json.loads(result.text.strip())
    except json.JSONDecodeError:
        # Best-effort: try to extract JSON from a fenced block
        text = result.text
        for fence in ("```json", "```"):
            if fence in text:
                text = text.split(fence, 1)[1].rsplit("```", 1)[0]
                break
        try:
            plan = json.loads(text.strip())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Plan JSON parse error: {exc}") from exc

    _enforce_plan_constraints(plan)

    if on_event:
        page_count = len(plan.get("pages", []))
        on_event("plan_ready", f"Site plan ready — {page_count} page(s) planned.", {"plan": plan})

    return plan
