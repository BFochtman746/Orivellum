"""next_action job — execute a queued Next-action prompt via the LLM.

One unit = one Next action.  The unit payload carries ``prompt``,
``anchor_ref``, and ``action_id`` forwarded by the bridge.

In MOCK mode the worker returns a stub digest immediately and no LLM is
contacted.  This lets the bridge integration tests run the real harness
execution path without an LLM sidecar.

Interface contract (same as every other job):
  plan(target, tmp)  →  {units: [...], ...}   (called only from the CLI)
  unit_worker(run_id, unit)  →  digest dict
  final_pass(run_id)  →  summary dict
  plan_items(run_id)  →  None  (no training curriculum)
"""

import json

from .. import llm, store
from ..config import CFG

# Target is anchor_ref / action JSON, not a file path.
PATH_TARGET = False


# ── CLI entry point ───────────────────────────────────────────────────────

def plan(target: str, _tmp) -> dict:
    """Build a single-unit plan from a JSON-encoded action dict or a raw prompt.

    When called from the CLI (``runner run --job next_action --target ...``)
    the target is either a JSON string ``{"prompt": ..., "anchor_ref": ...,
    "action_id": ...}`` or a plain prompt string.  Either way we emit exactly
    one unit.  The bridge path skips ``plan()`` and calls ``start_run /
    add_units`` directly so units already exist before ``harness.execute()``.
    """
    try:
        data = json.loads(target)
    except (ValueError, TypeError):
        data = {"prompt": str(target), "anchor_ref": str(target), "action_id": ""}
    return {
        "units": [{
            "kind": "next",
            "ref": data.get("anchor_ref", ""),
            "payload": {
                "action_id": data.get("action_id", ""),
                "prompt": data.get("prompt", ""),
                "anchor_ref": data.get("anchor_ref", ""),
            },
        }],
        "source": "bridge",
    }


# ── Harness callbacks ─────────────────────────────────────────────────────

def unit_worker(run_id: int, unit: dict) -> dict:  # noqa: ARG001
    """Execute one Next action prompt.

    In MOCK mode (``CFG.mock=True``) we return a stub digest without any
    model call.  In production we send the prompt to the configured LLM and
    record the reply.  ``llm.chat()`` never raises into the harness — a None
    reply is treated as a failed unit by checking the ``ok`` flag in the
    digest.
    """
    payload = unit.get("payload") or {}
    prompt = str(payload.get("prompt", "")).strip()
    anchor_ref = str(payload.get("anchor_ref", unit.get("ref", "")))
    action_id = str(payload.get("action_id", ""))

    if CFG.mock or not prompt:
        return {
            "action_id": action_id,
            "anchor_ref": anchor_ref,
            "result": "[MOCK] next_action executed",
            "ok": True,
        }

    reply = llm.chat(
        system=(
            "You are an autonomous writing assistant. "
            "Execute the following action on the manuscript corpus."
        ),
        user=prompt,
        max_tokens=800,
        temperature=0.1,
    )
    # Raise rather than return ok=False so the harness marks this unit as
    # 'failed' in the store.  A non-raising digest with ok=False would still
    # be persisted as unit status='done', which would let the bridge
    # incorrectly conclude the run succeeded.
    if reply is None:
        raise RuntimeError(
            "LLM returned no response; Next action could not be executed"
        )
    return {
        "action_id": action_id,
        "anchor_ref": anchor_ref,
        "result": reply,
        "ok": True,
    }


def final_pass(run_id: int) -> dict:
    """Summarise the completed run."""
    counts = store.unit_counts(run_id)
    return {
        "done": counts.get("done", 0),
        "failed": counts.get("failed", 0),
    }


def plan_items(_run_id: int):
    """No training-plan items for next_action runs."""
    return None
