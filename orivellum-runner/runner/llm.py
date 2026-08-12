"""Model client with a token meter and a deterministic mock.

The meter matters more than it looks: budgets are enforced in the harness from
these numbers, so a runaway run stops on its own instead of at 3am.
"""

import json
import re
import threading
import time

from .config import CFG

_lock = threading.Lock()
USED = {"calls": 0, "in_chars": 0, "out_chars": 0, "est_tokens": 0}

# Thread-local deadline (time.time() value) set by the harness before
# dispatching each unit.  chat() reads it so the HTTP request timeout
# honours the per-run wall-clock budget — not just CFG.timeout.
_tls = threading.local()


def set_deadline(deadline: float) -> None:
    """Record the absolute deadline for the current worker thread.

    Called by the harness inside the daemon thread before on_unit() runs.
    The deadline is a ``time.time()`` value; chat() uses the remaining
    seconds as its httpx timeout so in-flight requests are cancelled when
    the budget fires.
    """
    _tls.deadline = deadline


def _request_timeout() -> float:
    """Return the httpx timeout for the current thread.

    If a deadline was set and there is meaningful time remaining, use that;
    otherwise fall back to the global CFG.timeout.  Clamped to at least 1 s
    so a nearly-expired budget doesn't cause an instant spurious failure.
    """
    d = getattr(_tls, "deadline", None)
    if d is None:
        return CFG.timeout
    remaining = d - time.time()
    return max(1.0, min(remaining, CFG.timeout))


def used():
    with _lock:
        return dict(USED)


def _meter(inp, out):
    with _lock:
        USED["calls"] += 1
        USED["in_chars"] += len(inp)
        USED["out_chars"] += len(out)
        USED["est_tokens"] += (len(inp) + len(out)) // 4  # ~4 chars/token


def available():
    if CFG.mock:
        return False
    try:
        import httpx

        return httpx.get(f"{CFG.base_url}/models", timeout=5).status_code == 200
    except Exception:
        return False


def chat(system, user, max_tokens=700, temperature=0.1, schema=None, model=None):
    """Returns text or None. Never raises into the harness — a failed unit is
    recorded as failed, and the run continues."""
    user = user[: CFG.ctx_budget_chars]
    if CFG.mock:
        _meter(system + user, "")
        return None
    try:
        import httpx

        body = {
            "model": model or CFG.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if schema:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "out", "schema": schema, "strict": True},
            }
        r = httpx.post(f"{CFG.base_url}/chat/completions", json=body, timeout=_request_timeout())
        r.raise_for_status()
        out = r.json()["choices"][0]["message"]["content"].strip()
        _meter(system + user, out)
        return out
    except Exception:
        _meter(system + user, "")
        return None


def as_json(text):
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    s, e = t.find("{"), t.rfind("}")
    if s >= 0 and e > s:
        try:
            return json.loads(t[s : e + 1])
        except Exception:
            return None
    return None
