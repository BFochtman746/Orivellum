"""
config.py — build the trailer pipeline config dict from Orivellum's
ServingConfig.

The config shape mirrors the studio.yaml format from the original media_studio
so the ported stages (analyze / concept / method / plan) remain unchanged.
"""
from __future__ import annotations


def build_trailer_config(offline: bool = False) -> dict:
    """Return the trailer pipeline config, using Orivellum's ServingConfig as
    the authoritative source of model names/endpoints."""
    try:
        from orivellum.configuration.config import get_config
        cfg = get_config()
        base_url = getattr(cfg.serving, "base_url", "") or ""
        workhorse = getattr(cfg.serving, "workhorse_model", "") or "default"
        analysis_model = getattr(cfg.serving, "analysis_model", "") or workhorse
    except Exception:
        base_url = ""
        workhorse = "default"
        analysis_model = "default"

    # Offline mode when no LLM is reachable
    effective_offline = offline or (not base_url)

    return {
        "offline": effective_offline,
        "llm": {
            "base_url": base_url,
            "api_key": "not-needed",
            "analysis_model": analysis_model,
            "writing_model": workhorse,
            "timeout_seconds": 300,
            "temperature": 0.4,
        },
        "defaults": {
            "commercial_intent": "commercial",
            "target_duration_seconds": 75,
            "concepts_to_generate": 3,
            "aspect_ratios": ["16:9", "9:16", "1:1"],
            "loudness_lufs": -14,
            "pronunciation_overrides": {},
        },
        # Registry is inlined in method.py — not read from a YAML file
        "registry": None,
    }
