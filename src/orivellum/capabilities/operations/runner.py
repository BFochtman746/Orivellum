"""The durable operation runner.

``run_operation`` is the whole loop: walk the steps in order, skip the ones
already done (their results are re-loaded so later steps can still see them),
execute the rest, and checkpoint every state change in SQLite. Between steps
— and inside long-running steps via ``ctx.should_stop()`` — the loop re-reads
the operation state from the DB so pause/cancel take effect promptly.

The loop itself never raises: every outcome lands in the operations tables.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from orivellum.capabilities.operations import hooks, store
from orivellum.capabilities.operations.registry import (
    OpContext,
    OperationInterrupted,
    get_op_registry,
)

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.operations.runner")


def _emit(kind: str, title: str, body: str = "") -> None:
    try:
        if hooks.HOOKS.notify is not None:
            hooks.HOOKS.notify(kind, title, body=body, url="/operations")
    except Exception:  # notifications are best-effort
        logger.debug("notification emit failed", exc_info=True)


def _loads(raw: str | None) -> dict:
    try:
        return json.loads(raw or "{}")
    except Exception:
        return {}


def _execute_step(
    db: OrivellumDB,
    cfg: OrivellumConfig | None,
    op: dict,
    op_params: dict,
    results: dict[int, dict],
    step: dict,
    action,
    run_token: str,
) -> tuple[str, dict | Exception | None]:
    """Run one step; return ("ok", result) | ("interrupted", None) | ("failed", exc)."""
    op_id = op["id"]

    def _should_stop() -> bool:
        state, token = store.get_operation_claim(db, op_id)
        return state != "running" or token != run_token

    ctx = OpContext(
        db=db,
        cfg=cfg,
        operation_id=op_id,
        work_id=op.get("work_id"),
        params=op_params,
        results=results,
        should_stop=_should_stop,
    )
    step_params = {**op_params, **_loads(step.get("params"))}
    try:
        return "ok", (action.execute(ctx, step_params) or {})
    except OperationInterrupted:
        return "interrupted", None
    except Exception as exc:
        logger.error(
            "Operation %s step %d failed: %s", op_id, step["step_index"], exc, exc_info=True
        )
        return "failed", exc


def run_operation(db: OrivellumDB, cfg: OrivellumConfig | None, op_id: str, run_token: str) -> None:
    """Execute an already-claimed (state=running) operation to completion.

    ``run_token`` is the token issued by :func:`store.claim_operation`. Every
    state transition is fenced by it, so a runner whose claim was superseded
    (a newer resume) silently no-ops instead of corrupting the newer run.
    """
    op = store.get_operation(db, op_id)
    if not op or op["state"] != "running" or op.get("run_token") != run_token:
        return
    registry = get_op_registry()
    op_params = _loads(op.get("params"))
    results: dict[int, dict] = {}

    for step in store.list_steps(db, op_id):
        if step["state"] == "done":
            results[step["step_index"]] = _loads(step.get("result"))
            continue
        if not _process_step(db, cfg, op, op_params, results, step, registry, run_token):
            return

    if store.mark_operation_done(db, op_id, run_token):
        _emit("operation_done", f"Operation finished: {op['title']}")


def _process_step(
    db: OrivellumDB,
    cfg: OrivellumConfig | None,
    op: dict,
    op_params: dict,
    results: dict[int, dict],
    step: dict,
    registry: dict,
    run_token: str,
) -> bool:
    """Run one not-yet-done step; return True iff the loop should continue."""
    op_id = op["id"]

    # Honour pause/cancel (or a superseding claim) since the last step.
    state, token = store.get_operation_claim(db, op_id)
    if state != "running" or token != run_token:
        return False

    action = registry.get(step["action_id"])
    if action is None:
        store.mark_step_running(db, step["id"], run_token)
        store.mark_step_failed(db, step["id"], f"Unknown action '{step['action_id']}'", run_token)
        store.mark_operation_failed(db, op_id, f"Unknown action '{step['action_id']}'", run_token)
        return False

    if not store.mark_step_running(db, step["id"], run_token):
        # Someone else touched this step (cancel / newer claim) — bail.
        return False

    status, payload = _execute_step(db, cfg, op, op_params, results, step, action, run_token)
    if status == "interrupted":
        store.revert_step(db, step["id"], run_token)
        return False
    if status == "failed":
        store.mark_step_failed(db, step["id"], str(payload), run_token)
        if store.mark_operation_failed(
            db, op_id, f"'{step['label']}' failed: {payload}", run_token
        ):
            _emit("operation_failed", f"Operation failed: {op['title']}", str(payload)[:200])
        return False

    result = payload if isinstance(payload, dict) else {}
    if not store.mark_step_done(db, step["id"], result, run_token):
        # Our claim was superseded mid-step — the newer runner owns it now.
        return False
    results[step["step_index"]] = result
    return True


def start_operation_run(db: OrivellumDB, cfg: OrivellumConfig | None, op_id: str) -> bool:
    """Claim the operation and hand the loop to the shared executor.

    Returns False (and releases the claim) if it could not be scheduled.
    """
    submit_bg = hooks.HOOKS.submit_bg
    if submit_bg is None:
        logger.error("Operations executor hook not configured — cannot start %s", op_id)
        return False
    token = store.claim_operation(db, op_id)
    if not token:
        return False

    op = store.get_operation(db, op_id) or {}
    ok = submit_bg(
        run_operation,
        db,
        cfg,
        op_id,
        token,
        kind="operation",
        label=str(op.get("title") or op_id)[:80],
    )
    if not ok:
        store.release_claim(db, op_id, error="The server was too busy — try again.")
    return ok
