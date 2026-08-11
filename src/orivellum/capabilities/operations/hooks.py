"""Injection points for api-layer services.

The architecture contract forbids capabilities from importing ``orivellum.api``
(routes → capabilities → database, never upward). The api layer therefore
injects the services the operations capability needs — the notifier, the
background executor, and the studio render module — at import time via
:func:`configure` (done in ``orivellum.api.routes.operations``).

Unconfigured hooks fail loudly where the behaviour matters (starting a run,
rendering) and degrade silently only where best-effort is correct (the
runner's own progress notifications).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class _Hooks:
    notify: Callable[..., Any] | None = None
    submit_bg: Callable[..., bool] | None = None
    studio: Any = None


HOOKS = _Hooks()


def configure(
    *,
    notify: Callable[..., Any] | None = None,
    submit_bg: Callable[..., bool] | None = None,
    studio: Any = None,
) -> None:
    if notify is not None:
        HOOKS.notify = notify
    if submit_bg is not None:
        HOOKS.submit_bg = submit_bg
    if studio is not None:
        HOOKS.studio = studio
