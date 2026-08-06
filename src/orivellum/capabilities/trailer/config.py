"""
config.py — build the trailer pipeline config dict from Orivellum's
ServingConfig.

Uses load_config() (the real config accessor) and reads base_url /
workhorse_model from cfg.serving.  Falls back to offline mode only when
base_url is genuinely absent (e.g. the config file cannot be found).
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


def build_trailer_config(offline: bool = False) -> dict:
    """Return the trailer pipeline config dict.

    Reads the workspace config.yaml via load_config() and exposes the
    serving endpoint so OrivellumLLM can call the live model.  Offline
    mode is forced only when the caller explicitly requests it OR when
    no base_url is configured.
    """
    base_url = ""
    workhorse = ""
    analysis_model = ""

    try:
        from orivellum.configuration.config import load_config
        cfg = load_config()
        base_url = (cfg.serving.base_url or "").rstrip("/")
        workhorse = cfg.serving.workhorse_model or ""
        # Some configs expose a separate reasoner; fall back to workhorse.
        analysis_model = getattr(cfg.serving, "analysis_model", "") or workhorse
    except Exception as exc:
        logger.debug("Trailer config: could not load ServingConfig (%s); offline mode.", exc)

    effective_offline = offline or (not base_url)

    return {
        "offline": effective_offline,
        # Expose the raw cfg object so llm_adapter can pass it to llm_call()
        "_cfg_obj": _get_cfg_obj(),
        "llm": {
            "base_url": base_url,
            "api_key": "not-needed",
            "analysis_model": analysis_model or workhorse or "default",
            "writing_model": workhorse or "default",
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


def _get_cfg_obj():
    """Return the raw OrivellumConfig object, or None on failure."""
    try:
        from orivellum.configuration.config import load_config
        return load_config()
    except Exception:
        return None
