"""Operation-action registry — typed steps an operation can run.

Two kinds of actions live here:

1. Built-in step actions (``builtin.py``) — long-running/polling steps such as
   waiting for extraction or rendering an audiobook. They receive an
   ``OpContext`` so they can respect pause/cancel requests mid-poll.
2. Wrapped one-shot actions — every action already registered with the
   existing actions framework (``capabilities/actions``) is exposed as an
   operation step under the id ``action:<name>`` for free.

Actions must be idempotent-ish: a step interrupted by pause/restart is reset
to *pending* and re-executed from scratch on resume.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.operations.registry")


class OperationInterrupted(Exception):
    """Raised by a step action when the operation was paused or cancelled.

    The runner reverts the step to *pending* (so resume redoes it) and exits
    without touching the operation state — the pause/cancel already set it.
    """


@dataclass
class OpContext:
    """Everything a step action gets to work with."""

    db: OrivellumDB
    cfg: OrivellumConfig | None
    operation_id: str
    work_id: str | None
    params: dict  # operation-level params (merged under step params)
    results: dict[int, dict] = field(default_factory=dict)  # step_index → result
    should_stop: Callable[[], bool] = lambda: False


@dataclass
class OpAction:
    """A typed, registered step action."""

    id: str
    label: str
    description: str
    params_schema: dict
    execute: Callable[[OpContext, dict], dict[str, Any]]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "params_schema": self.params_schema,
        }


_REGISTRY: dict[str, OpAction] = {}


def register(action: OpAction) -> None:
    _REGISTRY[action.id] = action


def unregister(action_id: str) -> None:
    _REGISTRY.pop(action_id, None)


def _wrap_oneshot_actions() -> None:
    """Expose every existing one-shot action as an operation step."""
    from orivellum.capabilities.actions import get_registry as _oneshot_registry

    for a in _oneshot_registry().values():
        op_id = f"action:{a.name}"
        if op_id in _REGISTRY:
            continue

        def _make(inner: Any) -> Callable[[OpContext, dict], dict]:
            def _exec(ctx: OpContext, params: dict) -> dict:
                return inner.execute(params, ctx.db, ctx.cfg)

            return _exec

        register(
            OpAction(
                id=op_id,
                label=a.name.replace("_", " ").title(),
                description=a.description,
                params_schema=a.input_schema,
                execute=_make(a),
            )
        )


def get_op_registry() -> dict[str, OpAction]:
    """Return the full registry, populating built-ins + wrappers on first use."""
    if not any(not k.startswith("action:") for k in _REGISTRY):
        from orivellum.capabilities.operations.builtin import register_builtin_actions

        register_builtin_actions()
    try:
        _wrap_oneshot_actions()
    except Exception as exc:  # never let a broken one-shot action kill operations
        logger.warning("Could not wrap one-shot actions: %s", exc)
    return _REGISTRY
