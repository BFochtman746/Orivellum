"""
Stage 4c — PLAN (square 1:1 Instagram Feed crop).

Generates a 30 s, 3-beat, 768×768 centre-crop package for Instagram Feed
posts and cross-platform scheduling tools.

Editorial differences from plan_short.py (9:16 vertical):
  - Resolution: 768×768 (1:1 native; never landscape or portrait)
  - Centre-third crop rule: subject must stay inside the centre 50% of the
    frame horizontally AND vertically — the top/bottom 25% and left/right 25%
    are cropped away when the 16:9 source is centre-squared.
  - Caption safe zone: bottom 15% reserved for caption overlays.
  - Platform targets: Instagram Feed, LinkedIn, Twitter/X, Facebook
"""

from __future__ import annotations

SQUARE_DUR = 30

SHOT_SYS = (
    "You are a social-media trailer director specialising in 1:1 square clips "
    "(Instagram Feed / LinkedIn / Twitter/X / Facebook). "
    "Generate exactly 3 beats. Beat 0 is the HOOK — must grab attention in the first 3 seconds. "
    "Beat 1 is the PEAK — the single strongest emotional moment from the book. "
    "Beat 2 is the CLOSE — book title card, call to action. "
    "CRITICAL FRAMING RULE: All shots must be composed for a square frame. "
    "The subject must be in the CENTRE THIRD of the image both horizontally and vertically — "
    "the outer 25% on all four sides will be cropped when the 16:9 source is centre-squared. "
    "Avoid placing key subjects near any edge. "
    "Caption safe zone: bottom 15% reserved for overlay text. "
    "No brand names, no real living people. "
    "Honor the book's period, tone, and motifs exactly."
)

SHOT_SCHEMA = """{"shots":[{
  "beat": str,
  "beat_type": "hook|peak|close",
  "duration": int,
  "description": str,
  "image_prompt": str,
  "motion_prompt": str,
  "negative_prompt": str,
  "on_screen_text": str,
  "square_framing_note": str
}]}"""

NARR_SYS = (
    "You are writing voice-over for a 30-second square social clip. "
    "Write 2-3 short punchy lines — no full sentences needed. "
    "Line 0 must land before 3 seconds (the hook; ≤ 10 words). "
    "Line 1 peaks at ~10-15 s. Line 2 (optional) closes at ~25 s. "
    "Never oversell. Match the book's voice."
)
NARR_SCHEMA = """{"lines":[{"t_start": int, "text": str, "emotion": str, "pace": str}]}"""

MUSIC_SYS = (
    "You write MusicGen prompts for 30-second square social-clip scores. "
    "The score must build fast (≤8 s) and hit a high within the first 15 s. "
    "Text prompt only; no vocals."
)
MUSIC_SCHEMA = """{"prompt": str, "tempo_bpm": int, "mood": str,
  "length_seconds": int, "structure": str}"""


def run(llm, cfg: dict, brief: dict, concept: dict, method: dict) -> dict:
    """Return a 30-second 1:1 plan dict, structurally compatible with plan.run() output."""

    shots = llm.json(
        model=cfg["llm"].get("writing_model", "default"),
        system=SHOT_SYS,
        schema_hint=SHOT_SCHEMA,
        user=_shot_user(brief, concept),
    ).get("shots", [])

    shots = shots[:3]
    _normalize_shots(shots)
    _enforce_square_settings(shots, method, cfg)

    narration = llm.json(
        model=cfg["llm"].get("writing_model", "default"),
        system=NARR_SYS,
        schema_hint=NARR_SCHEMA,
        user=_narr_user(brief, concept),
    ).get("lines", [])
    narration = narration[:3]
    _clamp_narration_timing(narration)
    _apply_pronunciation(narration, cfg)

    music = llm.json(
        model=cfg["llm"].get("writing_model", "default"),
        system=MUSIC_SYS,
        schema_hint=MUSIC_SCHEMA,
        user=_music_user(brief, concept),
    )
    if isinstance(music.get("length_seconds"), int):
        music["length_seconds"] = min(music["length_seconds"], SQUARE_DUR)
    else:
        music["length_seconds"] = SQUARE_DUR

    titles = _title_plates(shots, brief)
    assembly = _assembly(shots, narration, music, cfg)

    return {
        "format": "square",
        "duration": SQUARE_DUR,
        "aspect_ratio": "1:1",
        "platform_targets": ["Instagram Feed", "LinkedIn", "Twitter/X", "Facebook"],
        "shots": shots,
        "narration": narration,
        "music": music,
        "titles": titles,
        "assembly": assembly,
        "manifest": _manifest(shots, narration, music, titles),
    }


# ── Prompt builders ────────────────────────────────────────────────────────────


def _shot_user(brief: dict, concept: dict) -> str:
    return (
        f"BOOK BRIEF:\n{_kv(brief)}\n\n"
        f"CONCEPT: {concept.get('name')} — {concept.get('angle')}\n"
        f"Visual style: {concept.get('visual_style')}\n"
        f"Strongest beats (pick 1 for PEAK): {concept.get('beats')}\n\n"
        f"Generate exactly 3 shots for a {SQUARE_DUR}s 1:1 SQUARE clip. "
        "Beat 0: HOOK (≤5s, must grab in first 3s). "
        "Beat 1: PEAK (the single most powerful image — must be centre-composed). "
        "Beat 2: CLOSE (title plate or final image with book title). "
        "Include a square_framing_note per shot: describe where the subject sits "
        "within the 1:1 frame (must be in the centre 50% — outer 25% all sides will be cropped)."
    )


def _narr_user(brief: dict, concept: dict) -> str:
    return (
        f"BOOK: {brief.get('title')} — {brief.get('logline')}\n"
        f"Tone: {brief.get('tone')}\n"
        f"Voice direction: {concept.get('voice_direction')}\n\n"
        f"Write 2-3 punchy VO lines for a {SQUARE_DUR}s square Instagram Feed post. "
        "Line at t=0 is the hook (≤10 words). "
        "Last line is at t=22 or later. "
        "Sparse, evocative — silence is allowed between lines."
    )


def _music_user(brief: dict, concept: dict) -> str:
    return (
        f"Score a {SQUARE_DUR}s 1:1 square social clip. "
        f"Genre/tone: {brief.get('tone')}. "
        f"Music direction: {concept.get('music_direction')}. "
        "Must build to peak within first 15s. Single arc; no extended outro. "
        "Give a MusicGen text prompt (instrumentation, mood, tempo bpm)."
    )


# ── Deterministic normalization ───────────────────────────────────────────────

_BEAT_SEQUENCE = ("hook", "peak", "close")
_DEFAULT_DURS = (5, 15, 10)  # Must sum to SQUARE_DUR (30)


def _normalize_shots(shots: list[dict]) -> None:
    """Enforce HOOK/PEAK/CLOSE beat_type sequence and normalize durations to exactly 30 s."""
    while len(shots) < 3:
        i = len(shots)
        shots.append(
            {
                "beat": _BEAT_SEQUENCE[i].upper(),
                "beat_type": _BEAT_SEQUENCE[i],
                "duration": _DEFAULT_DURS[i],
                "description": f"{_BEAT_SEQUENCE[i].capitalize()} beat (auto-padded)",
                "image_prompt": "(placeholder — replace with book-specific prompt)",
                "motion_prompt": "slow push in, subject centred",
                "negative_prompt": "blurry, watermark, text, modern objects",
                "on_screen_text": "",
                "square_framing_note": "Subject centred; all edges cropped 25% — keep key action in centre 50%",
            }
        )

    for i, s in enumerate(shots):
        s["beat_type"] = _BEAT_SEQUENCE[i]
        if not s.get("beat"):
            s["beat"] = _BEAT_SEQUENCE[i].upper()

    for i, s in enumerate(shots):
        dur = s.get("duration")
        if not isinstance(dur, int) or dur < 2 or dur > SQUARE_DUR - 4:
            s["duration"] = _DEFAULT_DURS[i]

    total = sum(s["duration"] for s in shots)
    if total != SQUARE_DUR:
        shots[1]["duration"] = max(2, shots[1]["duration"] + (SQUARE_DUR - total))
    total2 = sum(s["duration"] for s in shots)
    if total2 != SQUARE_DUR:
        shots[2]["duration"] = max(2, shots[2]["duration"] + (SQUARE_DUR - total2))


def _clamp_narration_timing(narration: list[dict]) -> None:
    if not narration:
        return
    narration[0]["t_start"] = 0
    for ln in narration[1:]:
        t = ln.get("t_start", 0)
        if not isinstance(t, (int, float)):
            t = 0
        ln["t_start"] = max(1, min(int(t), SQUARE_DUR - 2))


# ── Deterministic render settings ─────────────────────────────────────────────


def _enforce_square_settings(shots: list[dict], method: dict, cfg: dict) -> None:
    """Force all shots to 768×768 and apply consistent render settings."""
    a = method.get("assignments", {})

    if not shots:
        for beat_type, beat_name in [("hook", "HOOK"), ("peak", "PEAK"), ("close", "CLOSE")]:
            shots.append(
                {
                    "beat": beat_name,
                    "beat_type": beat_type,
                    "duration": 10,
                    "description": f"{beat_name} beat",
                    "image_prompt": "(generated offline — replace with book-specific prompt)",
                    "motion_prompt": "slow push in, subject centred",
                    "negative_prompt": "blurry, watermark, text, modern objects, anachronism",
                    "on_screen_text": "",
                    "square_framing_note": "Subject centred; outer 25% all sides will be cropped",
                }
            )

    for i, s in enumerate(shots):
        s["resolution"] = "768x768"  # 1:1 native
        dur = s.get("duration") or (5 if i == 0 else (15 if i == 1 else 10))
        s["duration"] = dur
        s["frames"] = max(24, int(dur * 3))
        s["steps"] = 30
        s["seed_policy"] = "fixed_per_shot"
        s["upscale"] = "2x"

        if s.get("beat_type") == "close" and a.get("title_plates"):
            s["image_model"] = a["title_plates"]["id"]
        elif a.get("still_image"):
            s["image_model"] = a["still_image"]["id"]
        else:
            s["image_model"] = "dreamshaper-sdxl"
        s["video_model"] = (a.get("motion_default") or {}).get("id", "wan-2.2")

        s.setdefault(
            "negative_prompt",
            "blurry, watermark, text, modern objects, anachronism, portrait orientation, landscape orientation",
        )
        # Centre-crop framing fallback
        s.setdefault(
            "square_framing_note",
            "Subject centred in 1:1 frame; outer 25% all sides will be cropped — "
            "keep key action inside the centre 50% of the image both horizontally and vertically. "
            "Caption safe zone: bottom 15% clear for text overlays.",
        )


def _apply_pronunciation(narration: list[dict], cfg: dict) -> None:
    overrides = cfg["defaults"].get("pronunciation_overrides", {})
    for ln in narration:
        parts = [
            f"{k} = /{v}/" for k, v in overrides.items() if k.lower() in ln.get("text", "").lower()
        ]
        ln["pronunciation"] = ", ".join(parts) if parts else ""


def _title_plates(shots: list[dict], brief: dict) -> list[dict]:
    plates = []
    for i, s in enumerate(shots):
        if s.get("on_screen_text"):
            plates.append(
                {
                    "text": s["on_screen_text"],
                    "for_shot": i,
                    "style": "square title — bold, centre-frame, safe-zone aware (avoid bottom 15%)",
                }
            )
    if not any(p.get("text") == brief.get("title") for p in plates):
        last = len(shots) - 1 if shots else 0
        plates.append(
            {
                "text": brief.get("title", "(title)"),
                "for_shot": last,
                "style": "closing title — full-bleed square, centred, fade in, large for Feed thumb",
            }
        )
    return plates


def _assembly(shots: list[dict], narration: list[dict], music: dict, cfg: dict) -> dict:
    t = 0
    v1: list[dict] = []
    for i, s in enumerate(shots):
        dur = s.get("duration", 10)
        v1.append({"shot": i, "in": t, "dur": dur})
        t += dur

    a1 = [{"t": ln.get("t_start", 0), "line": ln.get("text", "")} for ln in narration]
    a2 = [{"t": 0, "file": "score.wav", "duck_under_vo_db": -4}]
    loudness = cfg["defaults"].get("loudness_lufs", -14)

    return {
        "timeline": {"V1_video": v1, "A1_narration": a1, "A2_music": a2},
        "transitions": "hard cut between all shots (social-native pacing)",
        "crop_rule": (
            "Centre-third crop: render source at 1280×1280 (or the square diagonal of your 9:16 source), "
            "then export at 768×768 by cropping equal margins on all four sides. "
            "Key subject must remain visible after cropping the outer 25% on all sides."
        ),
        "caption_safe_zone": "bottom 15% reserved for caption overlays",
        "audio_mix": {
            "A1_narration_lufs": loudness + 2,
            "A2_music_lufs": loudness - 4,
            "master_lufs": loudness,
        },
        "masters": [
            {"aspect": "1:1", "note": "primary square — Instagram Feed / LinkedIn / Twitter/X"},
            {
                "aspect": "9:16",
                "note": "optional re-frame for Reels — add vertical padding or extend crop",
            },
        ],
        "export": {
            "codec": "H.264 (libx264), AAC 192kbps",
            "fps": 30,
            "duration_s": SQUARE_DUR,
        },
    }


def _manifest(shots: list[dict], narration: list[dict], music: dict, titles: list[dict]) -> dict:
    items = []
    for i, s in enumerate(shots):
        items.append(
            {
                "id": f"shot_{i:02d}_still",
                "type": "image",
                "model": s.get("image_model"),
                "status": "pending",
            }
        )
        items.append(
            {
                "id": f"shot_{i:02d}_motion",
                "type": "video",
                "model": s.get("video_model"),
                "status": "pending",
            }
        )
    for i, ln in enumerate(narration):
        items.append(
            {
                "id": f"narr_{i:02d}",
                "type": "audio_tts",
                "text": ln.get("text"),
                "status": "pending",
            }
        )
    items.append(
        {
            "id": "score",
            "type": "audio_music",
            "prompt": music.get("prompt", ""),
            "status": "pending",
        }
    )
    for i, tp in enumerate(titles):
        items.append(
            {
                "id": f"title_{i:02d}",
                "type": "title_plate",
                "text": tp.get("text", ""),
                "status": "pending",
            }
        )
    return {"items": items, "total": len(items), "save_process_recall": True}


def _kv(d: dict) -> str:
    out = []
    for k, v in d.items():
        if isinstance(v, list):
            out.append(f"{k}: {', '.join(str(x) for x in v)}")
        else:
            out.append(f"{k}: {v}")
    return "\n".join(out)
