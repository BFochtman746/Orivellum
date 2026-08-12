"""The loop. It does not ask permission and it does not ask you to continue.

Stop conditions are the only way a run ends: every unit done, a hard budget
reached, or an unrecoverable error. Each is recorded as a stop_reason so the
report can say WHY it stopped rather than implying it finished.
"""

import threading
import time

from . import llm, store
from .config import CFG


class Budget:
    """Run-level budget enforcer.

    Per-run limits (``max_units``, ``max_minutes``) take precedence over the
    global CFG defaults when provided.  Pass them from the action's declared
    cost so individual actions cannot exceed their own declared budget even if
    the global CFG allows more.

    Minute enforcement is two-layered:
    - ``check()`` fires before each unit if elapsed time has already exceeded
      the budget (fast-fail before dispatch).
    - ``remaining_seconds`` is used as a ``threading.Thread.join`` timeout so
      in-flight workers are interrupted at the deadline, not just before the
      next unit starts.
    """

    def __init__(self, max_units: int | None = None, max_minutes: int | None = None):
        self.t0 = time.time()
        self.units = 0
        self._max_units = max_units if max_units is not None else CFG.max_units
        self._max_minutes = max_minutes if max_minutes is not None else CFG.max_minutes
        self._deadline = self.t0 + self._max_minutes * 60

    @property
    def remaining_seconds(self) -> float:
        """Seconds remaining until the wall-clock deadline (≥ 0)."""
        return max(0.0, self._deadline - time.time())

    def check(self):
        mins = (time.time() - self.t0) / 60
        if mins >= self._max_minutes:
            return f"wall-clock budget reached ({self._max_minutes} min)"
        if self.units >= self._max_units:
            return f"unit budget reached ({self._max_units} units)"
        if llm.used()["est_tokens"] >= CFG.max_tokens:
            return f"token budget reached (~{CFG.max_tokens} tokens)"
        return None

    def elapsed_min(self):
        return round((time.time() - self.t0) / 60, 2)


def execute(run_id, job, on_unit, on_finish=None, resume=False,
            max_units: int | None = None, max_minutes: int | None = None):
    """job: module with a `unit_worker(run_id, unit)` returning a digest dict.
    Raising inside a worker fails that unit and the loop keeps going.

    ``max_units`` and ``max_minutes`` set per-run hard limits that take
    precedence over the global CFG defaults.  Pass the action's declared cost
    so individual actions cannot overrun their budget.
    """
    b = Budget(max_units=max_units, max_minutes=max_minutes)
    stop_reason = None
    if resume:
        store.note(run_id, "resumed from checkpoint")

    while True:
        reason = b.check()
        if reason:
            stop_reason = reason
            break
        u = store.next_unit(run_id)
        if u is None:
            break
        try:
            # Run the worker in a daemon thread and join with the remaining
            # wall-clock budget as the timeout.  This enforces the minute cap
            # during in-flight execution, not just between units.
            result_box: dict = {}

            def _worker() -> None:
                try:
                    result_box["digest"] = on_unit(run_id, u)
                except Exception as _exc:  # noqa: BLE001
                    result_box["exc"] = _exc

            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            t.join(timeout=b.remaining_seconds)

            if t.is_alive():
                # Worker still running — deadline fired.
                raise TimeoutError(
                    f"unit exceeded time budget ({b._max_minutes} min)"
                )
            if "exc" in result_box:
                raise result_box["exc"]
            store.finish_unit(u["id"], digest=result_box["digest"])
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {e}"[:400]
            if u["attempts"] < CFG.max_unit_retries:
                store.retry_unit(u["id"])
                store.note(run_id, f"unit {u['ord']} ({u['ref']}) retrying: {msg}")
            else:
                store.finish_unit(u["id"], err=msg)
                store.note(run_id, f"unit {u['ord']} ({u['ref']}) FAILED: {msg}")
        b.units += 1
        # Compaction boundary: the parent keeps only counts, never unit bodies.
        if b.units % CFG.compact_every == 0:
            c = store.unit_counts(run_id)
            store.note(run_id, f"checkpoint at {b.units} units: {c}")

    counts = store.unit_counts(run_id)
    remaining = counts.get("queued", 0)
    if stop_reason is None and remaining == 0:
        status = "done"
        stop_reason = "all units processed"
    elif stop_reason:
        status = "stopped"
    else:
        status = "stopped"
        stop_reason = "loop exited with work remaining"

    totals = {
        "units": counts,
        "elapsed_min": b.elapsed_min(),
        "llm": llm.used(),
        "remaining": remaining,
    }
    if on_finish:
        try:
            totals["summary"] = on_finish(run_id)
        except Exception as e:  # noqa: BLE001
            store.note(run_id, f"final pass failed: {type(e).__name__}: {e}")
            totals["summary"] = {"error": str(e)[:200]}
    store.end_run(run_id, status, stop_reason, totals)
    return {"run_id": run_id, "status": status, "stop_reason": stop_reason, "totals": totals}
