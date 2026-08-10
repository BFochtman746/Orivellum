"""
Stage 3 — METHOD.
Select the best production model for each role using the embedded default
registry.  No external YAML file required; the registry is inlined so the
module works out-of-the-box on Orivellum without the media_studio install.
(Ported from media_studio; model registry inlined.)
"""

from __future__ import annotations

# ------------------------------------------------------------------
# Minimal default model registry (inlined; overridable via cfg["registry"])
# ------------------------------------------------------------------
_DEFAULT_REGISTRY = {
    "image": [
        {
            "id": "dreamshaper-sdxl",
            "role": "daily_driver",
            "license_class": "open_restricted",
            "hardware_cost": "fast",
            "service": "comfyui",
        },
        {
            "id": "flux.2-dev",
            "role": "quality_ceiling",
            "license_class": "noncommercial",
            "hardware_cost": "slow",
            "service": "comfyui",
        },
        {
            "id": "qwen-image-2.0",
            "role": "typography",
            "license_class": "apache",
            "hardware_cost": "slow",
            "service": "comfyui",
        },
    ],
    "video": [
        {
            "id": "wan-2.2",
            "role": "workhorse",
            "license_class": "apache",
            "hardware_cost": "slow",
            "service": "comfyui",
        },
        {
            "id": "ltx-2",
            "role": "talking_and_audio",
            "license_class": "apache",
            "hardware_cost": "slow",
            "service": "comfyui",
        },
        {
            "id": "mochi-2",
            "role": "cloning",
            "license_class": "apache",
            "hardware_cost": "slow",
            "service": "comfyui",
        },
    ],
    "voice": [
        {
            "id": "kokoro-82m",
            "role": "hero_quality",
            "license_class": "apache",
            "hardware_cost": "fast",
            "service": "tts_openai",
        },
    ],
    "music": [
        {
            "id": "musicgen-medium",
            "role": "score_commercial_safe",
            "license_class": "mit",
            "hardware_cost": "fast",
            "service": "music_openai",
        },
        {
            "id": "stable-audio-open-1.0",
            "role": "ambient_texture",
            "license_class": "open_restricted",
            "hardware_cost": "slow",
            "service": "music_openai",
        },
    ],
}


def select(
    registry: dict, role: str, modality: str, commercial_intent: str
) -> tuple[dict | None, list]:
    candidates = [
        c
        for c in registry.get(modality, [])
        if c.get("role") == role
        and (commercial_intent != "commercial" or c.get("license_class") not in ("noncommercial",))
    ]
    if not candidates:
        # Widen search: any license when noncommercial
        candidates = registry.get(modality, [])

    w = {
        "fidelity": 0.25,
        "quality": 0.25,
        "feasibility": 0.25,
        "license_fit": 0.15,
        "distinctiveness": 0.10,
    }

    ranked = []
    for c in candidates:
        fidelity = 0.8 if c.get("role") == role else 0.5
        quality = {
            "quality_ceiling": 0.95,
            "hero_quality": 0.9,
            "typography": 0.85,
            "workhorse": 0.75,
            "daily_driver": 0.7,
            "cloning": 0.8,
            "score_commercial_safe": 0.9,
            "ambient_texture": 0.7,
        }.get(c.get("role", ""), 0.7)
        feasibility = {"fast": 0.9, "slow": 0.5}.get(c.get("hardware_cost", "slow"), 0.6)
        license_fit = {
            "apache": 1.0,
            "mit": 1.0,
            "open_restricted": 0.7,
            "noncommercial": 0.3,
        }.get(c.get("license_class", ""), 0.5)
        total = round(
            w["fidelity"] * fidelity
            + w["quality"] * quality
            + w["feasibility"] * feasibility
            + w["license_fit"] * license_fit
            + w["distinctiveness"] * 0.6,
            3,
        )
        ranked.append(
            {
                "id": c["id"],
                "role": c.get("role"),
                "service": c.get("service"),
                "license_class": c.get("license_class"),
                "hardware_cost": c.get("hardware_cost"),
                "score": total,
                "why": f"role={c.get('role')}, license={c.get('license_class')}, cost={c.get('hardware_cost')}",
            }
        )

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return (ranked[0] if ranked else None), ranked


def build_method(registry: dict, concept: dict, cfg: dict) -> dict:
    reg = registry or _DEFAULT_REGISTRY
    ci = cfg["defaults"].get("commercial_intent", "commercial")

    still, still_tbl = select(reg, "daily_driver", "image", ci)
    hero_still, _ = select(reg, "typography", "image", ci)
    motion, motion_tbl = select(reg, "workhorse", "video", ci)
    talking, _ = select(reg, "talking_and_audio", "video", ci)
    voice, voice_tbl = select(reg, "hero_quality", "voice", ci)
    music, music_tbl = select(reg, "score_commercial_safe", "music", ci)

    has_talking = any(
        "talk" in b.lower() or "narrat" in b.lower() or "present" in b.lower()
        for b in concept.get("beats", [])
    )

    return {
        "commercial_intent": ci,
        "pipeline": "still-first (image → image-to-video) for curated shots",
        "assignments": {
            "still_image": still,
            "title_plates": hero_still,
            "motion_default": motion,
            "talking_shots": talking if has_talking else None,
            "narration_voice": voice,
            "music": music,
        },
        "rationale_tables": {
            "image": still_tbl,
            "video": motion_tbl,
            "voice": voice_tbl,
            "music": music_tbl,
        },
        "notes": [
            "Slow (hero) models are reserved for ≤2 shots; the daily driver covers the rest.",
            "Title/quote plates route to the typography model for legible in-image text.",
            f"Commercial intent = {ci}: noncommercial-licensed models were "
            f"{'excluded' if ci == 'commercial' else 'allowed'}.",
        ],
    }
