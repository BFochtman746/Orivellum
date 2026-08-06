"""
Stage 4 — PLAN.
Turn the chosen concept + method into the full production package:
per-shot prompts, timed narration, music brief, title plates, assembly
sheet, and asset manifest.
(Ported from media_studio; adapted for OrivellumLLM.)
"""
from __future__ import annotations

SHOT_SYS = (
    "You are a trailer storyboard artist writing generation-ready prompts. "
    "For each beat produce a vivid, self-contained visual prompt and a matching "
    "motion prompt for image-to-video. No brand names, no real living people. "
    "Honor the book's period, tone, and motifs exactly."
)
SHOT_SCHEMA = """{"shots":[{
  "beat": str, "duration": int, "description": str,
  "image_prompt": str, "motion_prompt": str, "negative_prompt": str,
  "on_screen_text": str
}]}"""

NARR_SYS = (
    "You are a trailer copywriter. Write sparse, evocative voice-over that "
    "lets images breathe. Match the book's voice. Never oversell."
)
NARR_SCHEMA = """{"lines":[{"t_start": int, "text": str, "emotion": str, "pace": str}]}"""

MUSIC_SYS = "You write MusicGen prompts for trailer scores. Text prompt only; no vocals unless asked."
MUSIC_SCHEMA = """{"prompt": str, "tempo_bpm": int, "mood": str, "length_seconds": int, "structure": str}"""


def run(llm, cfg: dict, brief: dict, concept: dict, method: dict) -> dict:
    dur = concept.get("duration", cfg["defaults"].get("target_duration_seconds", 75))

    shots = llm.json(
        model=cfg["llm"].get("writing_model", "default"),
        system=SHOT_SYS,
        schema_hint=SHOT_SCHEMA,
        user=_shot_user(brief, concept, dur),
    ).get("shots", [])
    _apply_render_settings(shots, method, cfg)

    narration = llm.json(
        model=cfg["llm"].get("writing_model", "default"),
        system=NARR_SYS,
        schema_hint=NARR_SCHEMA,
        user=_narr_user(brief, concept, dur),
    ).get("lines", [])
    _apply_pronunciation(narration, cfg)

    music = llm.json(
        model=cfg["llm"].get("writing_model", "default"),
        system=MUSIC_SYS,
        schema_hint=MUSIC_SCHEMA,
        user=_music_user(brief, concept, dur),
    )

    titles = _title_plates(shots, brief)
    assembly = _assembly(shots, narration, music, cfg, dur)
    manifest = _manifest(shots, narration, music, titles, cfg)

    return {
        "duration": dur,
        "shots": shots,
        "narration": narration,
        "music": music,
        "titles": titles,
        "assembly": assembly,
        "manifest": manifest,
    }


# --------------------------------------------------------------------------
# Prompt builders
# --------------------------------------------------------------------------

def _shot_user(brief: dict, concept: dict, dur: int) -> str:
    return (
        f"BOOK BRIEF:\n{_kv(brief)}\n\n"
        f"CONCEPT: {concept.get('name')} — {concept.get('angle')}\n"
        f"Visual style: {concept.get('visual_style')}\n"
        f"Beats: {concept.get('beats')}\n\n"
        f"Total duration ~{dur}s. Produce one shot per beat with generation-ready prompts."
    )


def _narr_user(brief: dict, concept: dict, dur: int) -> str:
    return (
        f"BOOK: {brief.get('title')} — {brief.get('logline')}\n"
        f"Tone: {brief.get('tone')}\n"
        f"Concept voice direction: {concept.get('voice_direction')}\n\n"
        f"Write timed VO across ~{dur}s. Fewer, stronger lines beat many weak ones."
    )


def _music_user(brief: dict, concept: dict, dur: int) -> str:
    return (
        f"Score a ~{dur}s trailer. Mood/tone: {brief.get('tone')}. "
        f"Music direction: {concept.get('music_direction')}. "
        "Give a MusicGen text prompt (instrumentation, mood, tempo), and a simple structure."
    )


# --------------------------------------------------------------------------
# Deterministic enrichment
# --------------------------------------------------------------------------

def _apply_render_settings(shots: list[dict], method: dict, cfg: dict) -> None:
    a = method.get("assignments", {})
    hero_budget = 2
    aspects = cfg["defaults"].get("aspect_ratios", ["16:9"])
    res_map = {"16:9": "1280x720", "9:16": "720x1280", "1:1": "768x768"}
    default_res = res_map.get(aspects[0] if aspects else "16:9", "1280x720")

    for i, s in enumerate(shots):
        is_title = bool(s.get("on_screen_text"))
        if is_title and a.get("title_plates"):
            s["image_model"] = a["title_plates"]["id"]
        elif hero_budget > 0 and i == 0 and a.get("still_image"):
            s["image_model"] = a["still_image"]["id"]
            hero_budget -= 1
        elif a.get("still_image"):
            s["image_model"] = a["still_image"]["id"]
        else:
            s["image_model"] = "dreamshaper-sdxl"

        s["video_model"] = (a.get("motion_default") or {}).get("id", "wan-2.2")
        s["resolution"] = default_res
        s["frames"] = max(24, int(s.get("duration", 8) * 3))
        s["steps"] = 30
        s["seed_policy"] = "fixed_per_shot"
        s["upscale"] = "2x"
        s.setdefault("negative_prompt", "blurry, watermark, text, modern objects, anachronism")


def _apply_pronunciation(narration: list[dict], cfg: dict) -> None:
    overrides = cfg["defaults"].get("pronunciation_overrides", {})
    for ln in narration:
        pron_parts = [f"{k} = /{v}/" for k, v in overrides.items() if k.lower() in ln.get("text", "").lower()]
        ln["pronunciation"] = ", ".join(pron_parts) if pron_parts else ""


def _title_plates(shots: list[dict], brief: dict) -> list[dict]:
    plates = []
    for i, s in enumerate(shots):
        if s.get("on_screen_text"):
            plates.append({
                "text": s["on_screen_text"],
                "for_shot": i,
                "style": "title card — bold serif on dark, letter-spaced",
            })
    # Always ensure a title plate with the book title
    if not any(p.get("text") == brief.get("title") for p in plates):
        last_shot = len(shots) - 1 if shots else 0
        plates.append({
            "text": brief.get("title", "(title)"),
            "for_shot": last_shot,
            "style": "closing title — full-screen, centred, fade in",
        })
    return plates


def _assembly(
    shots: list[dict],
    narration: list[dict],
    music: dict,
    cfg: dict,
    dur: int,
) -> dict:
    # Build a simple linear V1 track
    t = 0
    v1: list[dict] = []
    for i, s in enumerate(shots):
        shot_dur = s.get("duration", max(4, dur // max(len(shots), 1)))
        v1.append({"shot": i, "in": t, "dur": shot_dur})
        t += shot_dur

    a1 = [{"t": ln.get("t_start", 0), "line": ln.get("text", "")} for ln in narration]
    a2 = [{"t": 0, "file": "score.wav", "duck_under_vo_db": -6}]

    aspects = cfg["defaults"].get("aspect_ratios", ["16:9"])
    masters = [
        {"aspect": ar, "note": f"export at {ar}"}
        for ar in aspects
    ]
    loudness = cfg["defaults"].get("loudness_lufs", -14)

    return {
        "timeline": {
            "V1_video": v1,
            "A1_narration": a1,
            "A2_music": a2,
        },
        "transitions": "dissolve 0.5s between shots; final cut to black 0.3s",
        "audio_mix": {
            "A1_narration_lufs": loudness + 2,
            "A2_music_lufs": loudness - 6,
            "master_lufs": loudness,
        },
        "masters": masters,
        "export": {
            "codec": "H.264 (libx264), AAC 192kbps",
            "fps": 24,
            "duration_s": dur,
        },
    }


def _manifest(
    shots: list[dict],
    narration: list[dict],
    music: dict,
    titles: list[dict],
    cfg: dict,
) -> dict:
    items = []
    for i, s in enumerate(shots):
        items.append({"id": f"shot_{i:02d}_still", "type": "image",
                      "model": s.get("image_model"), "status": "pending"})
        items.append({"id": f"shot_{i:02d}_motion", "type": "video",
                      "model": s.get("video_model"), "status": "pending"})
    for i, ln in enumerate(narration):
        items.append({"id": f"narr_{i:02d}", "type": "audio_tts", "text": ln.get("text"),
                      "status": "pending"})
    items.append({"id": "score", "type": "audio_music",
                  "prompt": music.get("prompt", ""), "status": "pending"})
    for i, tp in enumerate(titles):
        items.append({"id": f"title_{i:02d}", "type": "title_plate",
                      "text": tp.get("text", ""), "status": "pending"})
    return {
        "items": items,
        "total": len(items),
        "save_process_recall": True,
    }


def _kv(d: dict) -> str:
    out = []
    for k, v in d.items():
        if isinstance(v, list):
            out.append(f"{k}: {', '.join(str(x) for x in v)}")
        else:
            out.append(f"{k}: {v}")
    return "\n".join(out)
