"""
llm_adapter.py — thin LLM wrapper that routes calls through Orivellum's
llm_call() helper (telemetry, retries, circuit-breaker) rather than
hitting the model endpoint directly.

Falls back to the offline-stub generator from the original media_studio
code when no model endpoint is configured or when the caller explicitly
requests offline mode.
"""

from __future__ import annotations

import json
import logging
import os

logger = logging.getLogger(__name__)


class OrivellumLLM:
    """Drop-in replacement for media_studio's LLM class."""

    def __init__(self, cfg: dict, offline: bool = False):
        self.cfg = cfg
        self.offline = offline or os.environ.get("MEDIA_STUDIO_OFFLINE") == "1"
        # Track which calls fell back to offline stubs for transparency
        self.fallback_stages: list[str] = []

    # ------------------------------------------------------------------
    def json(self, *, model: str, system: str, user: str, schema_hint: str) -> dict:
        """Ask the model for a JSON object. Returns a parsed dict."""
        if self.offline:
            return _offline_stub(schema_hint, user)

        prompt = (
            user + "\n\nRespond with ONE valid JSON object and nothing else. "
            "No markdown fences, no commentary.\nShape:\n" + schema_hint
        )

        try:
            from orivellum.capabilities.llm import llm_call

            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]
            # Pass the raw config object so llm_call picks up base_url/model
            raw_cfg = self.cfg.get("_cfg_obj")
            result = llm_call(
                messages,
                cfg=raw_cfg,
                purpose=f"trailer:{model}",
                timeout=float(self.cfg.get("llm", {}).get("timeout_seconds", 300)),
                temperature=float(self.cfg.get("llm", {}).get("temperature", 0.4)),
            )
            if result.ok and result.text:
                return _parse_json(result.text)
            # Live call failed — fall through to offline stub
            logger.warning(
                "Trailer LLM call failed (ok=%s, error=%s); using offline stub.",
                result.ok,
                result.error,
            )
        except Exception as exc:
            logger.warning("Trailer LLM call raised %s; using offline stub.", exc)

        # Record that this stage fell back
        self.fallback_stages.append(model)
        return _offline_stub(schema_hint, user)


# ---------------------------------------------------------------------------
def _parse_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if "{" in raw:
            raw = raw[raw.find("{") :]
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("Model did not return JSON:\n" + raw[:400])
    return json.loads(raw[start : end + 1])


# ---------------------------------------------------------------------------
# Offline deterministic stub — mirrors the media_studio original so the full
# pipeline can run without a live model for testing / demo purposes.
# ---------------------------------------------------------------------------


def _offline_stub(schema_hint: str, user: str) -> dict:
    h = schema_hint.lower()
    if "image_prompt" in h or "motion_prompt" in h:
        return {
            "shots": [
                {
                    "beat": f"beat_{i}",
                    "duration": 10,
                    "description": f"Placeholder shot {i}",
                    "image_prompt": f"cinematic wide-angle shot #{i}, muted tones, "
                    "ancient setting, atmospheric lighting",
                    "motion_prompt": "slow dolly forward, dust particles, subtle haze",
                    "negative_prompt": "modern objects, text, watermarks, blurry faces",
                    "on_screen_text": "" if i not in (0, 4) else "TITLE",
                }
                for i in range(5)
            ]
        }
    if "logline" in h:
        return {
            "title": "(untitled work)",
            "logline": "A test logline generated offline — run with a live LLM for the real read.",
            "genre": "Historical Fiction",
            "subgenre": "epic / drama",
            "period_setting": "ancient world",
            "tone": ["somber", "hopeful", "reverent"],
            "themes": ["faith", "loss", "redemption"],
            "protagonist": "the central figure",
            "central_stakes": "survival of a people and a promise",
            "emotional_arc": "descent into darkness, then a turn toward light",
            "visual_motifs": ["ash and dust", "candle/flame", "stone and cloth"],
            "audience": "readers of literary historical fiction",
            "comparable_titles": ["The Red Tent", "Pillars of the Earth"],
            "content_sensitivities": ["handle with reverence"],
        }
    if "concepts" in h:
        return {
            "concepts": [
                {
                    "name": "Atmospheric Mood",
                    "angle": "mood-first, imagery over narration",
                    "rationale": "Lets tone carry; cheapest to render; safe for reverent material.",
                    "duration": 75,
                    "beats": ["cold open", "motif build", "logline card", "turn", "title + CTA"],
                    "visual_style": "painterly, low-key, candlelit",
                    "voice_direction": "measured, reverent, low",
                    "music_direction": "sparse strings + low drone, slow swell",
                },
                {
                    "name": "Narrative Hook",
                    "angle": "a single question drives the cut",
                    "rationale": "Strong hook; needs tight shot continuity.",
                    "duration": 70,
                    "beats": ["question posed", "stakes", "obstacle", "reversal", "title"],
                    "visual_style": "cinematic, warm",
                    "voice_direction": "intimate, urgent",
                    "music_direction": "rising ostinato",
                },
                {
                    "name": "Voice & Scripture",
                    "angle": "VO-led over tableaux imagery",
                    "rationale": "Distinctive; leans on narration quality.",
                    "duration": 80,
                    "beats": ["silence/black", "first line", "images", "crescendo", "title"],
                    "visual_style": "chiaroscuro tableaux",
                    "voice_direction": "oratorical, deliberate",
                    "music_direction": "choral pads, restrained",
                },
            ]
        }
    if "shot" in h and "narration" not in h:
        return {
            "shots": [
                {
                    "beat": b,
                    "duration": 15,
                    "description": f"placeholder beat {i + 1}",
                    "on_screen_text": "" if i not in (0, 4) else "TITLE / CARD",
                }
                for i, b in enumerate(["cold open", "motif", "logline card", "turn", "title"])
            ]
        }
    if "narration" in h or '"text"' in h:
        return {
            "lines": [
                {
                    "t_start": 0,
                    "text": "In the year the fires came…",
                    "emotion": "somber",
                    "pace": "slow",
                },
                {
                    "t_start": 20,
                    "text": "one promise remained.",
                    "emotion": "reverent",
                    "pace": "slow",
                },
                {
                    "t_start": 55,
                    "text": "(title of the work)",
                    "emotion": "resolute",
                    "pace": "measured",
                },
            ]
        }
    if "music" in h or "tempo_bpm" in h:
        return {
            "prompt": "sparse cinematic strings, low drone, slow swell, no drums, no vocals",
            "tempo_bpm": 60,
            "mood": "somber, reverent, hopeful",
            "length_seconds": 75,
            "structure": "quiet intro → slow build → single swell → resolve",
        }
    return {}
