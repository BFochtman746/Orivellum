"""Cognition system — meta-prompt gate and deliberation council.

Wraps every chat turn with a lightweight routing layer:
  1. Gate: classify the request as "direct" | "clarify" | "complex"
  2. For "direct" → single AI call (unchanged latency)
  3. For "clarify" → return ONE clarifying question without AI processing
  4. For "complex" → run Author→Critic→Synthesizer council (3 sequential calls)

All functions are synchronous and safe to call from async code via
`asyncio.to_thread()`.  Each function degrades gracefully — if the
gate or council calls fail, we fall through to a direct single call.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("orivellum.cognition")

# How many recent messages to include when classifying
_GATE_HISTORY = 6


# ─── Gate ──────────────────────────────────────────────────────────────────────

_GATE_PROMPT = """You are a routing classifier for an AI assistant.
Given the user's message and conversation context, classify the request as ONE of:
- "direct"  — clear, specific, answerable without clarification
- "clarify" — genuinely ambiguous; needs ONE targeted question before answering
- "complex" — multi-step reasoning, analysis, writing, or synthesis task

Respond ONLY with valid JSON: {"route": "direct"|"clarify"|"complex", "reason": "brief reason"}
Never include code fences or explanation outside the JSON.
"""


def classify(user_text: str, history: list[dict], base_url: str, model: str,
             db: Any = None) -> str:
    """Classify the request. Returns 'direct', 'clarify', or 'complex'.

    Falls back to 'direct' on any error so the chat never blocks.
    """
    context = "\n".join(
        f"{m['role'].upper()}: {m['content'][:200]}"
        for m in history[-_GATE_HISTORY:]
    )
    prompt = (
        f"{_GATE_PROMPT}\n\n"
        f"Conversation context:\n{context}\n\n"
        f"User message: {user_text[:400]}"
    )
    result = _call_sync([{"role": "user", "content": prompt}], base_url, model,
                        timeout=10, purpose="cognition.gate", db=db)
    if not result:
        return "direct"
    try:
        parsed = json.loads(result.strip())
        route  = parsed.get("route", "direct")
        if route not in ("direct", "clarify", "complex"):
            return "direct"
        logger.debug("Cognition gate: %s — %s", route, parsed.get("reason", ""))
        return route
    except json.JSONDecodeError:
        return "direct"


def get_clarifying_question(user_text: str, base_url: str, model: str,
                            db: Any = None) -> str:
    """Ask the AI to generate ONE concise clarifying question."""
    prompt = (
        "The following user request needs clarification before you can answer well. "
        "Ask ONE short, targeted clarifying question. Be direct and concise — no preamble.\n\n"
        f"User request: {user_text}"
    )
    result = _call_sync([{"role": "user", "content": prompt}], base_url, model,
                        timeout=15, purpose="cognition.clarify", db=db)
    return result or "Could you clarify what you mean?"


# ─── Council ───────────────────────────────────────────────────────────────────

_AUTHOR_PROMPT = (
    "You are the Author. Write a thorough first-draft response to the user's request. "
    "Be comprehensive and show your reasoning."
)

_CRITIC_PROMPT = (
    "You are the Critic. Review the draft response below and identify its weaknesses: "
    "gaps, errors, unsupported claims, or missed nuance. Be concise and specific. "
    "Do NOT rewrite the response — only critique it.\n\nDraft:\n{draft}"
)

_SYNTHESIZER_PROMPT = (
    "You are the Synthesizer. Revise the draft using the critique below to produce "
    "a final, polished response. Preserve the draft's strengths, fix its weaknesses, "
    "and add anything missing. Return ONLY the final response.\n\n"
    "Draft:\n{draft}\n\nCritique:\n{critique}"
)


def deliberate(messages: list[dict], base_url: str, model: str,
               db: Any = None) -> str | None:
    """Run the Author→Critic→Synthesizer council.

    Returns the synthesized response, or None if all three calls fail
    (caller should fall through to a direct single call).
    """
    # Author pass
    author_msgs = messages + [{"role": "system", "content": _AUTHOR_PROMPT}]
    draft = _call_sync(author_msgs, base_url, model, timeout=60,
                       purpose="cognition.author", db=db)
    if not draft:
        logger.warning("Council: author call failed — falling through to direct")
        return None

    # Critic pass
    critic_prompt = _CRITIC_PROMPT.format(draft=draft[:3000])
    critique      = _call_sync(
        messages + [{"role": "user", "content": critic_prompt}],
        base_url, model, timeout=30, purpose="cognition.critic", db=db,
    )
    if not critique:
        logger.debug("Council: critic call failed — returning draft")
        return draft  # Author draft is better than nothing

    # Synthesizer pass
    synth_prompt = _SYNTHESIZER_PROMPT.format(draft=draft[:3000], critique=critique[:1500])
    final        = _call_sync(
        messages + [{"role": "user", "content": synth_prompt}],
        base_url, model, timeout=60, purpose="cognition.synth", db=db,
    )
    return final or draft


# ─── Low-level sync call ───────────────────────────────────────────────────────

def _call_sync(
    messages: list[dict],
    base_url: str,
    model: str,
    timeout: int = 30,
    purpose: str = "cognition",
    db: Any = None,
) -> str | None:
    """Thin wrapper over the central ``llm_call`` gateway.

    Preserves the historical contract: returns the reply text, or None on
    any failure (the gateway never raises).
    """
    from orivellum.capabilities.llm import llm_call
    result = llm_call(
        messages, base_url=base_url, model=model,
        timeout=timeout, purpose=purpose, db=db,
    )
    return result.text


# ─── Compass store (per-Work persistent state) ─────────────────────────────────

def read_compass(db: Any, work_id: str) -> dict:
    try:
        with db._lock:
            row = db._conn.execute(
                "SELECT focus, last_reasoning, next_step, updated_at FROM project_compass WHERE work_id=?",
                (work_id,),
            ).fetchone()
        return dict(row) if row else {}
    except Exception:
        return {}


def update_compass(db: Any, work_id: str,
                   focus: str | None = None,
                   reasoning: str | None = None,
                   next_step: str | None = None) -> None:
    """Merge-update the Project Compass for *work_id*.

    Only fields supplied with a non-None value are written; existing values
    for omitted (None) fields are preserved.  This prevents a council call that
    doesn't infer a next_step from accidentally clearing one the user set.
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    try:
        with db._lock:
            # Ensure the row exists first (INSERT OR IGNORE)
            db._conn.execute(
                """INSERT OR IGNORE INTO project_compass(work_id, updated_at)
                   VALUES(?, ?)""",
                (work_id, now),
            )
            # Build a SET clause only for provided fields
            parts: list[str] = ["updated_at = ?"]
            vals: list[Any] = [now]
            if focus is not None:
                parts.append("focus = ?")
                vals.append(focus)
            if reasoning is not None:
                parts.append("last_reasoning = ?")
                vals.append(reasoning)
            if next_step is not None:
                parts.append("next_step = ?")
                vals.append(next_step)
            vals.append(work_id)
            db._conn.execute(
                f"UPDATE project_compass SET {', '.join(parts)} WHERE work_id = ?",
                vals,
            )
            db._conn.commit()
        try:
            db.audit("compass.updated", object_id=work_id, object_type="work",
                     actor="system",
                     detail=",".join(p.split(" = ")[0] for p in parts if p != "updated_at = ?") or "tick")
        except Exception:
            pass
    except Exception as exc:
        logger.warning("Could not update project compass: %s", exc)
