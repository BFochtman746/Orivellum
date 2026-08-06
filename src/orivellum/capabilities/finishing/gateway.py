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
    attribution: str          # in-world fictional source, or "" for none
    kind: str                 # always "original" here
    status: str               # UNVERIFIED_DRAFT (needs human sign-off) or ABSTAINED
    reason: str = ""


@dataclass
class CoverVersion:
    version_id: str
    prompt: str
    status: str               # DRAFT (mock) or ABSTAINED
    asset_ref: str = ""       # path/ref to generated art at A-01; empty in mock
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
                text="", attribution="", kind="original",
                status="ABSTAINED",
                reason="Real-quote/scripture epigraph requested; source cannot be "
                       "verified in mock. Policy forbids fabricated attributions.",
            )
        soul = (chapter_context or {}).get("soul", "the chapter's central tension")
        seed = hashlib.sha256(str(chapter_context).encode()).hexdigest()[:6]
        text = (f"[ORIGINAL EPIGRAPH DRAFT · {seed}] A line, written for this book "
                f"alone, that speaks to {soul}.")
        attribution = (chapter_context or {}).get("in_world_source", "")
        return EpigraphResult(text=text, attribution=attribution, kind="original",
                              status="UNVERIFIED_DRAFT")

    def cover_versions(self, brief: dict, n: int = 3) -> list[CoverVersion]:
        n = max(1, min(int(n), 8))
        base = str(brief)
        out = []
        for i in range(n):
            vid = hashlib.sha256(f"{base}#{i}".encode()).hexdigest()[:8]
            prompt = self._compose_prompt(brief, i)
            out.append(CoverVersion(version_id=vid, prompt=prompt, status="DRAFT",
                                    notes="Mock: no raster produced; real art at A-01."))
        return out

    @staticmethod
    def _compose_prompt(brief: dict, variant: int) -> str:
        b = brief or {}
        parts = [
            f"Book: {b.get('title','(untitled)')}",
            f"Series: {b.get('series','(standalone)')}",
            f"Mood: {b.get('mood','')}",
            f"Palette: {b.get('palette','')}",
            f"Imagery: {b.get('imagery','')}",
            f"Composition: {b.get('composition','')}",
            f"Variant: {variant} (vary accent + focal image; keep series constants)",
        ]
        return " | ".join(p for p in parts if p.split(": ", 1)[1])


class LemonadeGateway(Gateway):
    """A-01 handoff stub. Wire the real local text model (epigraphs) and image
    model (covers) here."""
    name = "lemonade"

    def original_epigraph(self, chapter_context: dict) -> EpigraphResult:
        raise NotImplementedError("Wire the local text model at A-01 handoff.")

    def cover_versions(self, brief: dict, n: int = 3) -> list[CoverVersion]:
        raise NotImplementedError("Wire the local image model at A-01 handoff.")


def get_gateway(name: str = "mock") -> Gateway:
    return {"mock": MockGateway, "lemonade": LemonadeGateway}.get(name, MockGateway)()
