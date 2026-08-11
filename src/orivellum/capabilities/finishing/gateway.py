#!/usr/bin/env python3
"""
Shared AI_GATEWAY boundary for the Finishing Suite (PRESS + ATELIER).

Same design as the platform's existing gateway: the deterministic ~90% of both
systems is built and tested against a MockGateway with synthetic outputs, so the
governance holds by construction BEFORE any real model endpoint exists. At A-01
handoff you swap MockGateway -> LemonadeGateway (config change), and the real
local model / image model is wired in. No cloud keys, no private data in the
build environment.

Two generative capabilities are defined here as CONTRACTS:
  1. original_epigraph(chapter_context) -> an ORIGINAL epigraph (never a real
     quote or scripture) that speaks to the soul of the chapter.
  2. cover_versions(brief, n) -> n candidate cover-art versions for a brief.

The mock never fabricates a real-looking attribution and never claims a source
it cannot verify: for epigraphs it returns author-original text with an in-world
or blank attribution and flags it UNVERIFIED_DRAFT for human approval; if asked
to produce a *quoted* epigraph it ABSTAINS, because it cannot verify a real
source. Guessing is a defect, not a feature.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass
class EpigraphResult:
    text: str
    attribution: str  # in-world fictional source, or "" for none
    kind: str  # always "original" here
    status: str  # UNVERIFIED_DRAFT (needs human sign-off) or ABSTAINED
    reason: str = ""


@dataclass
class CoverVersion:
    version_id: str
    prompt: str
    status: str  # DRAFT (mock) or ABSTAINED
    asset_ref: str = ""  # path/ref to generated art at A-01; empty in mock
    notes: str = ""


class Gateway:
    """Base contract. Subclasses implement the two capabilities."""

    name = "base"

    def original_epigraph(self, chapter_context: dict) -> EpigraphResult:
        raise NotImplementedError

    def cover_versions(self, brief: dict, n: int = 3) -> list[CoverVersion]:
        raise NotImplementedError


class MockGateway(Gateway):
    """Deterministic, offline. Produces clearly-labelled DRAFT artifacts and
    abstains on anything it cannot verify. Used for build + test."""

    name = "mock"

    def original_epigraph(self, chapter_context: dict) -> EpigraphResult:
        want = (chapter_context or {}).get("want_quote", False)
        if want:
            return EpigraphResult(
                text="",
                attribution="",
                kind="original",
                status="ABSTAINED",
                reason="Real-quote/scripture epigraph requested; source cannot be "
                "verified in mock. Policy forbids fabricated attributions.",
            )
        soul = (chapter_context or {}).get("soul", "the chapter's central tension")
        seed = hashlib.sha256(str(chapter_context).encode()).hexdigest()[:6]
        text = (
            f"[ORIGINAL EPIGRAPH DRAFT · {seed}] A line, written for this book "
            f"alone, that speaks to {soul}."
        )
        attribution = (chapter_context or {}).get("in_world_source", "")
        return EpigraphResult(
            text=text, attribution=attribution, kind="original", status="UNVERIFIED_DRAFT"
        )

    def cover_versions(self, brief: dict, n: int = 3) -> list[CoverVersion]:
        n = max(1, min(int(n), 8))
        base = str(brief)
        out = []
        for i in range(n):
            vid = hashlib.sha256(f"{base}#{i}".encode()).hexdigest()[:8]
            prompt = self._compose_prompt(brief, i)
            out.append(
                CoverVersion(
                    version_id=vid,
                    prompt=prompt,
                    status="DRAFT",
                    notes="Mock: no raster produced; real art at A-01.",
                )
            )
        return out

    @staticmethod
    def _compose_prompt(brief: dict, variant: int) -> str:
        b = brief or {}
        parts = [
            f"Book: {b.get('title', '(untitled)')}",
            f"Series: {b.get('series', '(standalone)')}",
            f"Mood: {b.get('mood', '')}",
            f"Palette: {b.get('palette', '')}",
            f"Imagery: {b.get('imagery', '')}",
            f"Composition: {b.get('composition', '')}",
            f"Variant: {variant} (vary accent + focal image; keep series constants)",
        ]
        return " | ".join(p for p in parts if p.split(": ", 1)[1])


class LemonadeGateway(Gateway):
    """Real gateway: epigraphs through the platform's ``llm_call`` and cover
    art through the studio image-generation pipeline.

    The abstain contract is preserved exactly as in the mock:
    - A requested *quoted* epigraph is refused without ever calling a model —
      no source can be verified, so fabricating an attribution is forbidden.
    - Any model failure (endpoint down, malformed output) ABSTAINS instead of
      returning placeholder text. Guessing is a defect, not a feature.
    - Successful epigraphs are ORIGINAL text flagged ``UNVERIFIED_DRAFT`` and
      still require human approval before they count.
    """

    name = "lemonade"

    def original_epigraph(self, chapter_context: dict) -> EpigraphResult:
        ctx = chapter_context or {}
        if ctx.get("want_quote", False):
            return EpigraphResult(
                text="",
                attribution="",
                kind="original",
                status="ABSTAINED",
                reason="Real-quote/scripture epigraph requested; the source cannot "
                "be verified. Policy forbids fabricated attributions.",
            )
        try:
            from orivellum.api._deps import get_config, get_db
            from orivellum.capabilities.llm import llm_call

            soul = ctx.get("soul") or "the chapter's central tension"
            chapter = ctx.get("chapter") or "(untitled chapter)"
            in_world = ctx.get("in_world_source") or ""
            attribution_rule = (
                f'Set "attribution" to exactly {in_world!r} (an in-world fictional source).'
                if in_world
                else 'Set "attribution" to "" — do NOT invent an author, book, or source.'
            )
            result = llm_call(
                [
                    {
                        "role": "system",
                        "content": (
                            "You write ORIGINAL epigraphs — short, evocative lines "
                            "composed for this book alone. You never quote or "
                            "paraphrase any real text, scripture, or author, and you "
                            "never fabricate an attribution. Respond with valid JSON "
                            'only: {"text": "...", "attribution": "..."}.'
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Chapter: {chapter}\n"
                            f"The epigraph must speak to: {soul}\n"
                            f"{attribution_rule}\n"
                            "1-3 lines, no surrounding quotes, no markdown."
                        ),
                    },
                ],
                cfg=get_config(),
                db=get_db(),
                purpose="finishing.epigraph",
                timeout=45.0,
                temperature=0.8,
                max_tokens=200,
            )
            if not result.ok or not result.text:
                raise RuntimeError(result.error or "LLM returned no text")
            data = _parse_json_object(result.text)
            text = (data.get("text") or "").strip()
            if not text:
                raise RuntimeError("LLM response contained no epigraph text")
            attribution = in_world if in_world else ""
            return EpigraphResult(
                text=text, attribution=attribution, kind="original", status="UNVERIFIED_DRAFT"
            )
        except Exception as exc:  # abstain-over-guess: never emit placeholder text
            return EpigraphResult(
                text="",
                attribution="",
                kind="original",
                status="ABSTAINED",
                reason=f"Text model unavailable or returned unusable output: {exc}",
            )

    def cover_versions(self, brief: dict, n: int = 3) -> list[CoverVersion]:
        n = max(1, min(int(n), 8))
        out: list[CoverVersion] = []
        for i in range(n):
            prompt = MockGateway._compose_prompt(brief, i)
            vid = hashlib.sha256(f"{prompt}#{i}".encode()).hexdigest()[:8]
            asset_ref, notes, status = "", "", "DRAFT"
            try:
                asset_ref = _generate_cover_asset(prompt)
                notes = "Rendered via studio image pipeline."
            except Exception as exc:
                status = "ABSTAINED"
                notes = f"Image backend unavailable: {exc}"
            out.append(
                CoverVersion(
                    version_id=vid,
                    prompt=prompt,
                    status=status,
                    asset_ref=asset_ref,
                    notes=notes,
                )
            )
        return out


def _parse_json_object(text: str) -> dict:
    """Parse the first JSON object out of an LLM response, or raise."""
    import json
    import re

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        data = json.loads(m.group())
        if isinstance(data, dict):
            return data
    raise RuntimeError(f"Response was not a JSON object: {text[:120]}")


def _generate_cover_asset(prompt: str) -> str:
    """Render one cover raster through the studio image pipeline.

    Returns the persisted output filename. Raises on any failure so the
    caller can record an ABSTAINED version instead of a fake asset ref.
    Runs the async generator with ``asyncio.run`` — finishing routes execute
    in a worker thread, so no event loop is running here.
    """
    import asyncio

    from orivellum.api.routes.studio import ImageGenRequest, generate_image

    body = ImageGenRequest(prompt=prompt, width=832, height=1216, steps=28)
    result = asyncio.run(generate_image(body))
    item = (result.get("data") or [{}])[0]
    ref = item.get("output_path") or item.get("url") or ""
    if not ref:
        raise RuntimeError("image pipeline returned no asset reference")
    return ref


def get_gateway(name: str = "mock") -> Gateway:
    return {"mock": MockGateway, "lemonade": LemonadeGateway}.get(name, MockGateway)()
