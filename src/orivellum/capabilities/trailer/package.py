"""
Stage 6 — PACKAGE.
Assemble the final production package dict that gets stored in the DB.
The Orivellum version returns a dict (not files) so the result can be
stored in the trailers.package_json column and served through the API.

Human-readable markdown strings are embedded as sub-keys so the frontend
can render them without a separate file system.
"""
from __future__ import annotations
import json
import datetime


def build_short(*, brief: dict, concept: dict, method: dict, plan: dict, validation: dict) -> dict:
    """Return the short-form (30 s 9:16) production package dict."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    status = validation["status"]
    badge = "READY" if status == "READY" else f"BLOCKED ({validation['critical']} critical)"

    shot_prompts = {}
    for i, s in enumerate(plan.get("shots", [])):
        shot_prompts[f"shot_{i:02d}"] = (
            f"# SHOT {i:02d} — {s.get('beat', '')}  [{s.get('beat_type','').upper()}]\n\n"
            f"[IMAGE MODEL] {s.get('image_model')}\n"
            f"[POSITIVE]\n{s.get('image_prompt', '')}\n\n"
            f"[NEGATIVE]\n{s.get('negative_prompt', '')}\n\n"
            f"[VIDEO MODEL] {s.get('video_model')} (image-to-video)\n"
            f"[MOTION]\n{s.get('motion_prompt', '')}\n\n"
            f"[SETTINGS] {s.get('resolution')} · {s.get('frames')} frames "
            f"· {s.get('steps')} steps · {s.get('duration')}s\n"
            f"[VERTICAL FRAMING] {s.get('vertical_framing_note', '')}\n"
            f"[SEED] {s.get('seed_policy')}\n"
            f"[UPSCALE] {s.get('upscale')}\n"
        )

    return {
        "brief": brief,
        "concept": concept,
        "method": method,
        "plan": plan,
        "validation": validation,
        "generated": stamp,
        "status": status,
        "status_badge": badge,
        "format": "short",
        "aspect_ratio": "9:16",
        "duration_s": 30,
        "platform_targets": plan.get("platform_targets",
                                     ["Instagram Reels", "TikTok", "YouTube Shorts"]),
        "docs": {
            "production_package": _short_master_md(brief, concept, plan, validation, stamp),
            "book_brief": _brief_md(brief),
            "concepts": _concepts_md(concept, plan),
            "method": _method_md(method),
            "shotlist": _short_shotlist_md(plan),
            "narration_script": _narration_md(plan),
            "music_brief": _music_md(plan),
            "titles": _titles_md(plan),
            "assembly_sheet": _assembly_md(plan),
        },
        "shot_prompts": shot_prompts,
    }


def _short_master_md(brief, concept, plan, val, stamp) -> str:
    status = val["status"]
    badge = "✅ READY" if status == "READY" else f"⛔ BLOCKED ({val['critical']} critical)"
    platforms = ", ".join(plan.get("platform_targets",
                                   ["Instagram Reels", "TikTok", "YouTube Shorts"]))
    lines = [
        f"# Social Clip Package — {brief.get('title', '(untitled)')}",
        "",
        f"*Generated {stamp} by Trailer Architect. Status: **{badge}***",
        "",
        f"**Format.** 30 s · 9:16 vertical · {platforms}",
        f"**Logline.** {brief.get('logline', '')}",
        "",
        f"**Concept.** {concept.get('name')} — {concept.get('angle')}",
        "",
        "## Shots",
        "",
    ]
    for i, s in enumerate(plan.get("shots", [])):
        lines.append(
            f"- Shot {i:02d} [{s.get('beat_type','').upper()}] "
            f"**{s.get('beat','')}** · {s.get('duration')}s · {s.get('resolution')}"
        )
    lines += [
        "",
        "## Next Step",
        "",
        "1. Generate each shot still at 720×1280 (9:16 native).",
        "2. Add auto-captions via CapCut/DaVinci Resolve — bottom 20% safe zone.",
        "3. Export at 30 fps H.264; master LUFS per ASSEMBLY_SHEET.",
        "",
    ]
    if status != "READY":
        lines += ["## ⛔ Blocking findings", ""]
        lines += [f"- **{f['code']}** — {f['msg']}" for f in val["findings"]]
    return "\n".join(lines)


def _short_shotlist_md(plan: dict) -> str:
    lines = ["# Short-Form Shot List (9:16 · 30 s)", ""]
    for i, s in enumerate(plan.get("shots", [])):
        lines += [
            f"## Shot {i:02d} [{s.get('beat_type','').upper()}] — {s.get('beat', '')}  "
            f"({s.get('duration', '?')}s)",
            f"**Description:** {s.get('description', '')}",
            f"**Vertical framing:** {s.get('vertical_framing_note', '')}",
            f"**Image model:** {s.get('image_model', '?')}  ·  {s.get('resolution')}",
            f"**Image prompt:** {s.get('image_prompt', '')}",
            f"**Motion prompt:** {s.get('motion_prompt', '')}",
            f"**Negative:** {s.get('negative_prompt', '')}",
            f"**On-screen text:** {s.get('on_screen_text', '')}",
            "",
        ]
    return "\n".join(lines)


def build(*, brief: dict, concept: dict, method: dict, plan: dict, validation: dict) -> dict:
    """Return the full (landscape) production package as a dict."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    status = validation["status"]
    badge = "READY" if status == "READY" else f"BLOCKED ({validation['critical']} critical)"

    # Per-shot prompt strings (for copy-paste into ComfyUI)
    shot_prompts = {}
    for i, s in enumerate(plan.get("shots", [])):
        shot_prompts[f"shot_{i:02d}"] = (
            f"# SHOT {i:02d} — {s.get('beat', '')}\n\n"
            f"[IMAGE MODEL] {s.get('image_model')}\n"
            f"[POSITIVE]\n{s.get('image_prompt', '')}\n\n"
            f"[NEGATIVE]\n{s.get('negative_prompt', '')}\n\n"
            f"[VIDEO MODEL] {s.get('video_model')} (image-to-video)\n"
            f"[MOTION]\n{s.get('motion_prompt', '')}\n\n"
            f"[SETTINGS] {s.get('resolution')} · {s.get('frames')} frames "
            f"· {s.get('steps')} steps · {s.get('duration')}s\n"
            f"[SEED] {s.get('seed_policy')}\n"
            f"[UPSCALE] {s.get('upscale')}\n"
        )

    return {
        # Machine bundle
        "brief": brief,
        "concept": concept,
        "method": method,
        "plan": plan,
        "validation": validation,
        "generated": stamp,
        "status": status,
        "status_badge": badge,
        # Human-readable markdown snippets (for the UI)
        "docs": {
            "production_package": _master_md(brief, concept, method, plan, validation, stamp),
            "book_brief": _brief_md(brief),
            "concepts": _concepts_md(concept, plan),
            "method": _method_md(method),
            "shotlist": _shotlist_md(plan),
            "narration_script": _narration_md(plan),
            "music_brief": _music_md(plan),
            "titles": _titles_md(plan),
            "assembly_sheet": _assembly_md(plan),
        },
        "shot_prompts": shot_prompts,
    }


# ---------------------------------------------------------------------------
# Markdown renderers
# ---------------------------------------------------------------------------

def _master_md(brief, concept, method, plan, val, stamp) -> str:
    status = val["status"]
    badge = "✅ READY" if status == "READY" else f"⛔ BLOCKED ({val['critical']} critical)"
    masters_str = ", ".join(m["aspect"] for m in plan.get("assembly", {}).get("masters", []))
    lines = [
        f"# Production Package — {brief.get('title', '(untitled)')}",
        "",
        f"*Generated {stamp} by Trailer Architect. Status: **{badge}***",
        "",
        f"**Logline.** {brief.get('logline', '')}",
        "",
        f"**Chosen concept.** {concept.get('name')} — {concept.get('angle')}",
        "",
        f"**Runtime.** ~{plan.get('duration')}s · **Masters.** {masters_str}",
        "",
        "## What's in this package",
        "",
        "- `BOOK_BRIEF` — the grounded read of the book",
        "- `CONCEPTS` — all concepts + scores (why this one won)",
        "- `METHOD` — which model does what, and why",
        "- `SHOTLIST` — generation-ready image + motion prompts",
        "- `NARRATION_SCRIPT` — timed VO with voice/emotion",
        "- `MUSIC_BRIEF` — the MusicGen prompt",
        "- `TITLES` — title/quote plates",
        "- `ASSEMBLY_SHEET` — the editor timeline + audio mix + export",
        "",
    ]
    if status != "READY":
        lines += ["## ⛔ Blocking findings", ""]
        lines += [f"- **{f['code']}** — {f['msg']}" for f in val["findings"]]
        lines += [""]
    lines += [
        "## Next Step",
        "",
        "1. Open the SHOTLIST; generate each still (approve, then lock seed).",
        "2. Run each approved still through image-to-video (shot prompt files).",
        "3. Synthesise NARRATION_SCRIPT on your TTS service; render MUSIC_BRIEF on MusicGen.",
        "4. Assemble per ASSEMBLY_SHEET; export all master aspects.",
        "",
    ]
    return "\n".join(lines)


def _brief_md(brief: dict) -> str:
    lines = [f"# Book Brief — {brief.get('title', '(untitled)')}", ""]
    for k, v in brief.items():
        if isinstance(v, list):
            lines.append(f"**{k}:** {', '.join(str(x) for x in v)}")
        else:
            lines.append(f"**{k}:** {v}")
    return "\n".join(lines) + "\n"


def _concepts_md(concept: dict, plan: dict) -> str:
    all_concepts = plan.get("_all_concepts", [concept]) if plan else [concept]
    lines = ["# Trailer Concepts", ""]
    for c in all_concepts:
        score = c.get("score_total", "n/a")
        chosen = " ← **CHOSEN**" if c.get("name") == concept.get("name") else ""
        lines += [
            f"## {c.get('name')}{chosen}",
            f"**Score:** {score}  |  **Duration:** {c.get('duration')}s",
            f"**Angle:** {c.get('angle')}",
            f"**Rationale:** {c.get('rationale')}",
            f"**Beats:** {' → '.join(c.get('beats', []))}",
            f"**Visual style:** {c.get('visual_style')}",
            "",
        ]
    return "\n".join(lines)


def _method_md(method: dict) -> str:
    a = method.get("assignments", {})
    lines = ["# Production Method", "", f"**Pipeline:** {method.get('pipeline')}", ""]
    lines += ["## Model Assignments", ""]
    for role, model in a.items():
        if model:
            lines.append(f"- **{role}:** {model['id']} ({model.get('license_class')})")
    lines += ["", "## Notes", ""]
    for note in method.get("notes", []):
        lines.append(f"- {note}")
    return "\n".join(lines) + "\n"


def _shotlist_md(plan: dict) -> str:
    lines = ["# Shot List", ""]
    for i, s in enumerate(plan.get("shots", [])):
        lines += [
            f"## Shot {i:02d} — {s.get('beat', '')}  ({s.get('duration', '?')}s)",
            f"**Description:** {s.get('description', '')}",
            f"**Image model:** {s.get('image_model', '?')}",
            f"**Image prompt:** {s.get('image_prompt', '')}",
            f"**Motion prompt:** {s.get('motion_prompt', '')}",
            f"**Negative:** {s.get('negative_prompt', '')}",
            f"**Settings:** {s.get('resolution')} · {s.get('frames')} frames",
            f"**On-screen text:** {s.get('on_screen_text', '')}",
            "",
        ]
    return "\n".join(lines)


def _narration_md(plan: dict) -> str:
    lines = [
        "# Narration Script",
        "",
        "| t (s) | Line | Emotion | Pace | Pronunciation |",
        "|-------|------|---------|------|---------------|",
    ]
    for ln in plan.get("narration", []):
        lines.append(
            f"| {ln.get('t_start')} | {ln.get('text')} "
            f"| {ln.get('emotion', '')} | {ln.get('pace', '')} "
            f"| {ln.get('pronunciation', '')} |"
        )
    return "\n".join(lines) + "\n"


def _music_md(plan: dict) -> str:
    m = plan.get("music", {})
    return (
        "# Music Brief (MusicGen)\n\n"
        f"**Prompt (paste into MusicGen):**\n\n> {m.get('prompt', '')}\n\n"
        f"- **Mood:** {m.get('mood', '')}\n"
        f"- **Tempo:** {m.get('tempo_bpm', '?')} bpm\n"
        f"- **Length:** {m.get('length_seconds', '?')}s\n"
        f"- **Structure:** {m.get('structure', '')}\n"
        "- **License note:** MusicGen output is commercially usable (MIT). "
        "Do not substitute Stable Audio Open for shipped work.\n"
    )


def _titles_md(plan: dict) -> str:
    lines = ["# Title & Quote Plates", ""]
    for p in plan.get("titles", []):
        lines.append(f"- **\"{p['text']}\"** → shot {p['for_shot']} · {p['style']}")
    return "\n".join(lines) + "\n"


def _assembly_md(plan: dict) -> str:
    a = plan.get("assembly", {})
    tl = a.get("timeline", {})
    lines = ["# Assembly Sheet (DaVinci Resolve / Shotcut)", "", "## Video track (V1)", ""]
    for c in tl.get("V1_video", []):
        lines.append(f"- Shot {c['shot']:02d} @ {c['in']}s for {c['dur']}s")
    lines += ["", "## Narration (A1)", ""]
    for c in tl.get("A1_narration", []):
        lines.append(f"- {c['t']}s — {c['line']}")
    duck = (tl.get("A2_music") or [{}])[0].get("duck_under_vo_db", "?")
    lines += [
        "", "## Music (A2)",
        f"- score.wav @ 0s, duck {duck} dB under VO",
        "",
        "## Transitions", f"- {a.get('transitions', '')}",
        "", "## Audio mix", "",
    ]
    for k, v in a.get("audio_mix", {}).items():
        lines.append(f"- {k}: {v} LUFS")
    lines += ["", "## Masters", ""]
    for m in a.get("masters", []):
        lines.append(f"- {m['aspect']} — {m.get('note', '')}")
    e = a.get("export", {})
    lines += ["", "## Export", f"- {e.get('codec')} · {e.get('fps')} fps · {e.get('duration_s')}s"]
    return "\n".join(lines) + "\n"
