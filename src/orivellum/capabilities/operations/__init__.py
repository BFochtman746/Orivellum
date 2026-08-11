"""Operations — run a whole multi-step job with one button.

An *operation* is an ordered list of steps, each referencing a typed action
from the operation-action registry. The runner executes steps in order,
checkpointing every step in the database so a run can be paused, survive an API
restart, and resume by skipping the steps that already finished.

Package layout
--------------
- registry.py  — OpAction type + registry (built-ins + wrapped one-shot actions)
- builtin.py   — built-in step actions (wait_for_extraction, render_audiobook, notify)
- store.py     — SQLite CRUD with CAS-style state transitions + restart reconcile
- runner.py    — the durable step loop (submitted via the shared executor)
- playbooks.py — data-defined starter playbooks
"""

from orivellum.capabilities.operations.registry import (  # noqa: F401
    OpAction,
    OpContext,
    OperationInterrupted,
    get_op_registry,
)
