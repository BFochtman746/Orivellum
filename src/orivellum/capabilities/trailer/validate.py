"""
Stage 5 — VALIDATE.
Fail-closed production-readiness gate.
(Ported from media_studio unchanged.)
"""

from __future__ import annotations


def check(brief: dict, concept: dict, method: dict, plan: dict) -> dict:
    findings: list[dict] = []

    def need(cond: bool, code: str, msg: str) -> None:
        if not cond:
            findings.append({"code": code, "severity": "critical", "msg": msg})

    # brief
    need(brief.get("logline"), "BRIEF-1", "Book brief missing logline.")
    need(brief.get("tone"), "BRIEF-2", "Book brief missing tone.")

    # concept
    need(concept and concept.get("beats"), "CONC-1", "No concept beats.")

    # method
    a = (method or {}).get("assignments", {})
    need(a.get("still_image"), "METH-1", "No still-image model selected.")
    need(a.get("motion_default"), "METH-2", "No motion/video model selected.")
    need(a.get("narration_voice"), "METH-3", "No narration voice selected.")
    need(a.get("music"), "METH-4", "No music model selected.")

    # shots
    shots = plan.get("shots", [])
    need(shots, "SHOT-0", "No shots planned.")
    for i, s in enumerate(shots):
        need(s.get("image_prompt"), f"SHOT-{i}-IMG", f"Shot {i} missing image_prompt.")
        need(s.get("motion_prompt"), f"SHOT-{i}-MOT", f"Shot {i} missing motion_prompt.")
        need(
            s.get("negative_prompt") is not None,
            f"SHOT-{i}-NEG",
            f"Shot {i} missing negative_prompt.",
        )
        need(
            s.get("frames") and s.get("resolution"),
            f"SHOT-{i}-SET",
            f"Shot {i} missing render settings.",
        )

    # narration covers the runtime
    dur = plan.get("duration", 0)
    narr = plan.get("narration", [])
    need(narr, "NARR-0", "No narration lines.")
    if narr:
        last = max(ln.get("t_start", 0) for ln in narr)
        need(last <= dur, "NARR-1", f"Narration starts ({last}s) after runtime ({dur}s).")

    # music + assembly + manifest
    need(plan.get("music", {}).get("prompt"), "MUS-1", "Music brief missing prompt.")
    need(plan.get("assembly", {}).get("timeline"), "ASM-1", "Assembly timeline missing.")
    man = plan.get("manifest", {})
    need(man.get("items"), "MAN-1", "Asset manifest empty.")
    need(
        man.get("save_process_recall") is True,
        "MAN-2",
        "Manifest must assert Save/Process/Recall for every output.",
    )

    # assembly references every shot
    tl = plan.get("assembly", {}).get("timeline", {}).get("V1_video", [])
    need(len(tl) == len(shots), "ASM-2", "Assembly does not reference every shot.")

    crit = [f for f in findings if f["severity"] == "critical"]
    return {
        "status": "READY" if not crit else "BLOCKED",
        "critical": len(crit),
        "findings": findings,
    }
