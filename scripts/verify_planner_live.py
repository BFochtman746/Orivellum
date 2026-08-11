#!/usr/bin/env python3
"""Live verification battery for the AI job planner (POST /api/operations/plan).

The planner's validation/repair logic is fully covered by mocked-LLM tests
(tests/test_operations_planner.py). What those cannot prove is how well the
REAL local model (Lemonade on the Strix Halo) follows the strict JSON
contract. This script runs a battery of realistic prompts through the actual
``plan_job`` path — real registry, real Works from your DB, real voice
catalog, real LLM — and reports, per prompt:

- the outcome (valid plan / clarifying question / clear error)
- whether the outcome matched what that prompt SHOULD produce
- how many LLM calls were needed (2 = the one repair retry fired)
- a hallucination check: every step in an accepted plan must use a
  registered action, and every voice must be from the catalog (this is
  guaranteed by validation — the script re-checks it independently)

Run it ON YOUR PC (where Lemonade is reachable), from the project root:

    uv run python scripts/verify_planner_live.py

Optional:
    --model MODEL_ID   force a specific model instead of the configured one
    --prompt "TEXT"    run one ad-hoc prompt instead of the battery

Exit code 0 = every prompt produced an acceptable outcome; 1 otherwise.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Allow running from the project root without installing the package.
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, os.path.join(_repo_root, "src"))


def _build_battery(works: list[dict]) -> list[dict]:
    """Realistic prompts + what each one is allowed to produce.

    ``expect`` is a set of acceptable statuses; ``check`` (optional) further
    inspects an ok plan. A prompt FAILS when the status is outside ``expect``
    or the check rejects the plan — e.g. a nonsense request that comes back
    as a runnable plan.
    """
    titles = [w.get("title") or "" for w in works if w.get("title")]
    first = titles[0] if titles else "My Book"

    def _has_render_with_george(plan: dict) -> str | None:
        steps = plan.get("steps") or []
        render = [s for s in steps if s["action_id"] == "render_audiobook"]
        if not render:
            return "no render_audiobook step in the plan"
        voice = (render[0].get("params") or {}).get("voice")
        if voice not in (None, "bm_george"):
            return f"expected voice bm_george (or default), got {voice!r}"
        return None

    def _no_steps_allowed(plan: dict) -> str | None:
        return "nonsense produced a runnable plan" if plan.get("steps") else None

    return [
        {
            "name": "audiobook with a named voice",
            "prompt": (
                f'Turn "{first}" into an audiobook narrated by George, '
                "and let me know when it's done."
            ),
            "expect": {"ok"},
            "check": _has_render_with_george,
        },
        {
            "name": "study plan, ambiguous Work name",
            "prompt": "Build a study plan for my book.",
            # With one Work an ok plan is fine; with several it must clarify.
            "expect": {"ok", "clarify"} if len(titles) <= 1 else {"clarify"},
        },
        {
            "name": "job naming no Work",
            "prompt": "Render the audiobook once processing finishes.",
            "expect": {"ok", "clarify"} if len(titles) <= 1 else {"clarify"},
        },
        {
            "name": "nonsense request",
            "prompt": "Blorp the quantum sandwich until purple.",
            "expect": {"clarify", "error"},
            "check": _no_steps_allowed,
        },
        {
            "name": "multi-step with options",
            "prompt": (
                f'Wait for "{first}" to finish processing, render it as an '
                "audiobook at 1.2x speed without credits, then notify me."
            ),
            "expect": {"ok"},
        },
    ]


def _hallucination_problems(result: dict, registry: dict, voice_ids: set[str]) -> list[str]:
    """Independent re-check that nothing unregistered slipped into an ok plan."""
    if result.get("status") != "ok":
        return []
    problems = []
    for s in result["plan"].get("steps") or []:
        if s["action_id"] not in registry:
            problems.append(f"HALLUCINATED action: {s['action_id']}")
        voice = (s.get("params") or {}).get("voice")
        if voice and voice_ids and voice not in voice_ids:
            problems.append(f"HALLUCINATED voice: {voice}")
    return problems


def _case_problems(case: dict, result: dict, registry: dict, voice_ids: set[str]) -> list[str]:
    """All acceptance problems for one battery case (empty = PASS)."""
    status = result.get("status")
    problems = []
    if status not in case["expect"]:
        problems.append(f"expected {sorted(case['expect'])}, got '{status}'")
    problems += _hallucination_problems(result, registry, voice_ids)
    if status == "ok" and case.get("check"):
        extra = case["check"](result["plan"])
        if extra:
            problems.append(extra)
    # An unreachable model fails every case honestly — say so clearly.
    if status == "error" and "not reachable" in (result.get("message") or ""):
        problems = ["LLM endpoint unreachable — run this on the PC where Lemonade is up"]
    return problems


def _print_result(result: dict) -> None:
    status = result.get("status")
    if status == "ok":
        plan = result["plan"]
        steps = " → ".join(s["action_id"] for s in plan.get("steps") or [])
        print(f"  plan   : {plan.get('title')!r} on {plan.get('work_title')!r}: {steps}")
        for s in plan.get("steps") or []:
            if s.get("params"):
                print(f"           {s['action_id']} params={s['params']}")
    elif status == "clarify":
        print(f"  asks   : {result.get('question')}")
    else:
        print(f"  error  : {result.get('message')}")
        for p in result.get("problems") or []:
            print(f"           - {p}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the planner live-verification battery.")
    parser.add_argument("--model", help="Force a specific model id")
    parser.add_argument("--prompt", help="Run one ad-hoc prompt instead of the battery")
    args = parser.parse_args()

    from orivellum.api.routes import operations as _operations_routes  # noqa: F401
    from orivellum.capabilities.operations import planner
    from orivellum.capabilities.operations.registry import get_op_registry
    from orivellum.configuration.config import load_config
    from orivellum.database.db import OrivellumDB

    # Importing the operations routes configured the studio hook (voice
    # catalog); get_op_registry() self-populates builtins + wrapped actions.
    cfg = load_config()
    data_dir = os.environ.get("ORIVELLUM_DATA_DIR", "data")
    db = OrivellumDB(os.path.join(data_dir, "orivellum.db"))

    registry = get_op_registry()
    voice_ids = {v["id"] for v in planner._voice_catalog()}  # noqa: SLF001
    works = db.list_works(limit=200)

    # Count LLM calls per prompt so repair retries are visible (2 calls = the
    # one repair retry fired; the planner never makes a third).
    calls = {"n": 0}
    real_llm_text = planner._llm_text  # noqa: SLF001

    def counting_llm_text(messages, db_, cfg_, model_):
        calls["n"] += 1
        return real_llm_text(messages, db_, cfg_, model_)

    planner._llm_text = counting_llm_text  # noqa: SLF001

    battery = (
        [{"name": "ad-hoc", "prompt": args.prompt, "expect": {"ok", "clarify", "error"}}]
        if args.prompt
        else _build_battery(works)
    )

    print(f"Works in library: {len(works)} | Actions registered: {len(registry)}")
    print(f"Model: {args.model or db.get_setting('workhorse_model_override', '') or '(config)'}")
    print("=" * 72)

    failures = 0
    repairs = 0
    for case in battery:
        calls["n"] = 0
        t0 = time.monotonic()
        result = planner.plan_job(db, cfg, case["prompt"], model=args.model)
        elapsed = time.monotonic() - t0
        status = result.get("status")
        repaired = calls["n"] >= 2
        repairs += int(repaired)

        problems = _case_problems(case, result, registry, voice_ids)
        verdict = "PASS" if not problems else "FAIL"
        failures += int(bool(problems))
        print(f"\n[{verdict}] {case['name']}  ({elapsed:.1f}s, llm_calls={calls['n']})")
        print(f"  prompt : {case['prompt']}")
        print(f"  status : {status}" + ("  (repair retry used)" if repaired else ""))
        _print_result(result)
        for p in problems:
            print(f"  !! {p}")

    print("\n" + "=" * 72)
    print(
        f"{len(battery) - failures}/{len(battery)} prompts acceptable | "
        f"repair retry needed on {repairs} prompt(s)"
    )
    if repairs > len(battery) // 2:
        print(
            "NOTE: the repair retry fired on most prompts — consider tightening "
            "the system prompt in planner._build_messages before trusting first-try output."
        )
    db.close()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
