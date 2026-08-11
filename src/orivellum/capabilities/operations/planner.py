"""Natural-language job → validated operation plan.

The user describes a job in plain words; the local LLM produces a STRUCTURED
plan composed only of actions registered in the operation registry, with
parameters validated against each action's schema. The LLM never free-runs
tools — it plans once, the user approves, the durable runner executes.

Reliability contract (the whole point of this module):

- The LLM may ONLY use registered actions and their declared parameters.
  Invalid output gets ONE repair retry (with the concrete problems fed back);
  if that also fails the caller gets a clear error — never a silent guess.
- Ambiguity (e.g. which Work?) surfaces as a short clarifying question in the
  result, not a wrong guess.
- Work titles and voice names are resolved server-side against the real
  catalogs; the LLM is never given raw IDs to hallucinate from.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from orivellum.capabilities.operations import hooks
from orivellum.capabilities.operations.registry import get_op_registry

if TYPE_CHECKING:
    from orivellum.capabilities.operations.registry import OpAction

logger = logging.getLogger("orivellum.operations.planner")

_MAX_STEPS = 12
_MAX_WORKS_IN_PROMPT = 60
_MAX_CLARIFY_OPTIONS = 8

# Builtin steps that cannot run without a Work even though their schema marks
# work_id optional (it falls back to the operation-level Work).
_WORK_REQUIRED_ACTIONS = {"wait_for_extraction", "render_audiobook"}

_JSON_TYPE_CHECKS: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
}


# ── LLM access (module-level so tests can monkeypatch it) ────────────────────


def _llm_text(messages: list[dict], db: Any, cfg: Any, model: str | None) -> str | None:
    """One planner LLM call through the gateway. Returns raw text or None."""
    from orivellum.capabilities.llm import llm_call

    resolved = model
    if not resolved:
        try:
            resolved = db.get_setting("workhorse_model_override", "") or None
        except Exception:
            resolved = None
    result = llm_call(
        messages,
        cfg=cfg,
        model=resolved,
        db=db,
        purpose="operations.plan",
        timeout=45,
        temperature=0.1,
        max_tokens=1400,
    )
    return result.text if result.ok else None


# ── Catalogs ─────────────────────────────────────────────────────────────────


def _voice_catalog() -> list[dict]:
    """Narrator voices from the studio module (injected via hooks). Best-effort."""
    studio = hooks.HOOKS.studio
    try:
        return [
            {"id": v["id"], "name": v.get("name", v["id"])}
            for v in studio._VOICE_CATALOG  # noqa: SLF001 — deliberate hook access
        ]
    except Exception:
        return []


def _action_lines(registry: dict[str, OpAction]) -> str:
    lines = []
    for a in registry.values():
        props = (a.params_schema or {}).get("properties", {}) or {}
        ps = ", ".join(
            f"{k}:{(v or {}).get('type', 'any')}" for k, v in props.items() if k != "work_id"
        )
        desc = " ".join((a.description or a.label).split())[:110]
        lines.append(f"- {a.id} — {desc}" + (f" (params: {ps})" if ps else ""))
    return "\n".join(lines)


def _build_messages(job_text: str, registry: dict, works: list[dict], voices: list[dict]) -> list:
    work_lines = "\n".join(f"- {w.get('title') or w['id']}" for w in works[:_MAX_WORKS_IN_PROMPT])
    voice_lines = "\n".join(f"- {v['id']} ({v['name']})" for v in voices)
    system = (
        "You convert a user's plain-language job into a strict JSON plan for an "
        "automation runner.\n\n"
        "Rules:\n"
        "- Use ONLY the actions listed below, with ONLY their listed parameters. "
        "Never invent actions or parameters.\n"
        "- Never put work_id in step params — name the Work once at the top level.\n"
        "- If a step depends on document content, put wait_for_extraction before it.\n"
        "- Voices must be one of the listed voice ids.\n"
        "- If you cannot tell which Work (or anything else essential) the user "
        'means, set "clarification" to one short question and leave steps empty. '
        "Never guess.\n"
        "- Respond with JSON only — no code fences, no commentary.\n\n"
        "Output shape:\n"
        '{"title": "<short operation title>", '
        '"work": "<EXACT Work title from the list, or null>", '
        '"clarification": null, '
        '"steps": [{"action_id": "<id>", "label": "<short human label>", "params": {}}]}\n\n'
        f"Actions:\n{_action_lines(registry)}\n\n"
        f"Works (refer to them by exact title):\n{work_lines or '- (none yet)'}\n"
        + (f"\nVoices:\n{voice_lines}\n" if voice_lines else "")
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": job_text[:2000]},
    ]


# ── Parsing & validation ─────────────────────────────────────────────────────


def _parse_plan(raw: str) -> tuple[dict | None, str | None]:
    """Parse the LLM response into a plan dict. Returns (plan, error)."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None, "The response contained no JSON object."
    try:
        plan = json.loads(text[start : end + 1])
    except Exception as exc:
        return None, f"The response was not valid JSON: {exc}"
    if not isinstance(plan, dict):
        return None, "The response JSON must be an object."
    return plan, None


def validate_steps(steps: Any, registry: dict[str, OpAction]) -> list[str]:
    """Return every problem with *steps* against the action registry.

    Shared by the planner (repair loop) and the save-playbook path so a plan
    can never be stored or run with actions or parameters that don't exist.
    """
    errors: list[str] = []
    if not isinstance(steps, list) or not steps:
        return ["The plan must contain at least one step."]
    if len(steps) > _MAX_STEPS:
        return [f"Too many steps ({len(steps)}); the maximum is {_MAX_STEPS}."]
    for i, s in enumerate(steps, 1):
        errors.extend(_step_problems(i, s, registry))
    return errors


def _step_problems(i: int, s: Any, registry: dict[str, OpAction]) -> list[str]:
    """Every problem with one step: action existence, label, param names/types."""
    if not isinstance(s, dict):
        return [f"Step {i} must be an object."]
    aid = s.get("action_id")
    action = registry.get(aid) if isinstance(aid, str) else None
    if action is None:
        return [f"Step {i}: unknown action {aid!r} — use only the listed actions."]
    errors: list[str] = []
    if not isinstance(s.get("label") or "", str):
        errors.append(f"Step {i}: label must be a string.")
    params = s.get("params") or {}
    if not isinstance(params, dict):
        errors.append(f"Step {i} ({aid}): params must be an object.")
        return errors
    props = (action.params_schema or {}).get("properties", {}) or {}
    for key, value in params.items():
        if key == "work_id":
            errors.append(
                f"Step {i} ({aid}): never set work_id in step params — "
                "the Work is chosen at the top level."
            )
        elif key not in props:
            errors.append(f"Step {i} ({aid}): unknown parameter {key!r}.")
        else:
            expected = (props.get(key) or {}).get("type")
            check = _JSON_TYPE_CHECKS.get(expected)
            if check and not check(value):
                errors.append(
                    f"Step {i} ({aid}): parameter {key!r} must be a {expected}, "
                    f"got {type(value).__name__}."
                )
    return errors


def _resolve_voices(steps: list[dict], voices: list[dict]) -> list[str]:
    """Resolve human voice names ('George') to catalog ids in place."""
    if not voices:
        return []
    by_id = {v["id"] for v in voices}
    errors: list[str] = []
    for i, s in enumerate(steps, 1):
        params = s.get("params") or {}
        val = params.get("voice")
        if not val or not isinstance(val, str) or val in by_id:
            continue
        low = val.strip().lower()
        matches = [v for v in voices if v["name"].lower() == low or v["id"].lower() == low]
        if not matches:
            matches = [v for v in voices if low in v["name"].lower()]
        if len(matches) == 1:
            params["voice"] = matches[0]["id"]
        else:
            names = ", ".join(v["name"] for v in voices[:12])
            errors.append(
                f"Step {i}: unknown voice {val!r} — choose a listed voice id (names: {names})."
            )
    return errors


def _resolve_work(
    plan: dict, steps: list[dict], works: list[dict], registry: dict
) -> tuple[str | None, str | None, str | None]:
    """Resolve the plan's Work reference. Returns (work_id, work_title, clarify_question)."""
    ref = plan.get("work")
    titles = [(w["id"], w.get("title") or w["id"]) for w in works]

    def _options() -> str:
        names = ", ".join(f"'{t}'" for _, t in titles[:_MAX_CLARIFY_OPTIONS])
        return f" Your Works include: {names}." if names else ""

    if isinstance(ref, str) and ref.strip():
        low = ref.strip().lower()
        exact = [(i, t) for i, t in titles if t.lower() == low]
        if len(exact) == 1:
            return exact[0][0], exact[0][1], None
        partial = [(i, t) for i, t in titles if low in t.lower()]
        if len(partial) == 1:
            return partial[0][0], partial[0][1], None
        if len(partial) > 1:
            names = ", ".join(f"'{t}'" for _, t in partial[:_MAX_CLARIFY_OPTIONS])
            return None, None, f"Which Work did you mean — {names}?"
        return None, None, f"I couldn't find a Work called '{ref.strip()}'.{_options()}"

    # No Work named — only a problem when a step actually needs one.
    def _needs_work(s: dict) -> bool:
        aid = s.get("action_id", "")
        if aid in _WORK_REQUIRED_ACTIONS:
            return True
        action = registry.get(aid)
        required = (action.params_schema or {}).get("required", []) if action else []
        return "work_id" in required and "work_id" not in (s.get("params") or {})

    if any(_needs_work(s) for s in steps):
        return None, None, f"Which Work should this run on?{_options()}"
    return None, None, None


# ── Entry point ──────────────────────────────────────────────────────────────


def plan_job(db: Any, cfg: Any, job_text: str, *, model: str | None = None) -> dict:
    """Turn *job_text* into a validated operation plan.

    Returns one of:
      {"status": "ok", "plan": {title, work_id, work_title, steps}}
      {"status": "clarify", "question": "<short question>"}
      {"status": "error", "message": "<what went wrong>", "problems": [...]}
    """
    registry = get_op_registry()
    works = db.list_works(limit=200)
    voices = _voice_catalog()
    messages = _build_messages(job_text, registry, works, voices)

    raw = _llm_text(messages, db, cfg, model)
    if raw is None:
        return {
            "status": "error",
            "message": "The planning model is not reachable right now — try again shortly.",
            "problems": [],
        }

    plan, parse_err = _parse_plan(raw)
    problems = [parse_err] if parse_err else _plan_problems(plan, registry, voices)

    if problems:
        # ONE repair retry with the concrete problems fed back — never more.
        repair = messages + [
            {"role": "assistant", "content": raw[:4000]},
            {
                "role": "user",
                "content": (
                    "That plan is invalid:\n- "
                    + "\n- ".join(problems)
                    + "\nReturn the corrected JSON only, following every rule."
                ),
            },
        ]
        raw2 = _llm_text(repair, db, cfg, model)
        plan2, parse_err2 = _parse_plan(raw2) if raw2 is not None else (None, "No response.")
        problems2 = [parse_err2] if parse_err2 else _plan_problems(plan2, registry, voices)
        if problems2:
            logger.info("Plan rejected after repair retry: %s", problems2)
            return {
                "status": "error",
                "message": "I couldn't turn that into a valid plan. Try describing the job "
                "differently, or build the steps by hand.",
                "problems": problems2,
            }
        plan = plan2

    assert plan is not None  # guaranteed: problems empty implies parse succeeded

    clarification = plan.get("clarification")
    if isinstance(clarification, str) and clarification.strip():
        return {"status": "clarify", "question": clarification.strip()[:400]}

    steps = plan.get("steps") or []
    work_id, work_title, clarify = _resolve_work(plan, steps, works, registry)
    if clarify:
        return {"status": "clarify", "question": clarify}

    out_steps = [
        {
            "action_id": s["action_id"],
            "label": (s.get("label") or registry[s["action_id"]].label)[:120],
            "params": s.get("params") or {},
        }
        for s in steps
    ]
    title = (plan.get("title") or job_text)[:120] if isinstance(plan.get("title"), str) else ""
    return {
        "status": "ok",
        "plan": {
            "title": title.strip() or job_text[:120],
            "work_id": work_id,
            "work_title": work_title,
            "steps": out_steps,
        },
    }


def _plan_problems(plan: dict | None, registry: dict, voices: list[dict]) -> list[str]:
    """All validation problems for a parsed plan (empty steps OK when clarifying)."""
    if plan is None:
        return ["The response contained no plan."]
    clarification = plan.get("clarification")
    if isinstance(clarification, str) and clarification.strip():
        return []
    steps = plan.get("steps")
    problems = validate_steps(steps, registry)
    if not problems:
        problems = _resolve_voices(steps, voices)
    return problems
