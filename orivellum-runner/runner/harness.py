"""The loop. It does not ask permission and it does not ask you to continue.

Stop conditions are the only way a run ends: every unit done, a hard budget
reached, or an unrecoverable error. Each is recorded as a stop_reason so the
report can say WHY it stopped rather than implying it finished.
"""
import time
from .config import CFG
from . import store, llm

class Budget:
    def __init__(self):
        self.t0 = time.time()
        self.units = 0
    def check(self):
        mins = (time.time() - self.t0) / 60
        if mins >= CFG.max_minutes:
            return f"wall-clock budget reached ({CFG.max_minutes} min)"
        if self.units >= CFG.max_units:
            return f"unit budget reached ({CFG.max_units} units)"
        if llm.used()["est_tokens"] >= CFG.max_tokens:
            return f"token budget reached (~{CFG.max_tokens} tokens)"
        return None
    def elapsed_min(self):
        return round((time.time() - self.t0) / 60, 2)

def execute(run_id, job, on_unit, on_finish=None, resume=False):
    """job: module with a `unit_worker(run_id, unit)` returning a digest dict.
    Raising inside a worker fails that unit and the loop keeps going."""
    b = Budget()
    stop_reason = None
    if resume:
        store.note(run_id, "resumed from checkpoint")

    while True:
        reason = b.check()
        if reason:
            stop_reason = reason; break
        u = store.next_unit(run_id)
        if u is None:
            break
        try:
            digest = on_unit(run_id, u)
            store.finish_unit(u["id"], digest=digest)
        except Exception as e:                                   # noqa: BLE001
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
        status = "done"; stop_reason = "all units processed"
    elif stop_reason:
        status = "stopped"
    else:
        status = "stopped"; stop_reason = "loop exited with work remaining"

    totals = {"units": counts, "elapsed_min": b.elapsed_min(),
              "llm": llm.used(), "remaining": remaining}
    if on_finish:
        try:
            totals["summary"] = on_finish(run_id)
        except Exception as e:                                   # noqa: BLE001
            store.note(run_id, f"final pass failed: {type(e).__name__}: {e}")
            totals["summary"] = {"error": str(e)[:200]}
    store.end_run(run_id, status, stop_reason, totals)
    return {"run_id": run_id, "status": status, "stop_reason": stop_reason, "totals": totals}
