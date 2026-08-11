"""Central LLM gateway for Orivellum (MCOS Phase 0).

Every *non-streaming* chat-completion call in the backend should go through
``llm_call()``.  It provides:

  * one place for the OpenAI-compatible HTTP contract
  * consistent timeout / error handling (never raises — returns LLMResult)
  * telemetry: each call records purpose, model, latency and token usage
    into the ``llm_calls`` table (best-effort, never blocks the caller)

The streaming chat path in the conversations route keeps its own request
loop (it must forward SSE chunks) but records telemetry via
``record_llm_call()`` when the stream ends.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("orivellum.llm")


@dataclass
class LLMResult:
    """Outcome of a gateway call.  ``text is None`` iff the call failed."""

    text: str | None
    ok: bool
    model: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    error: str | None = None
    finish_reason: str | None = None
    # Row id of this call in llm_calls — the provenance audit trail
    # (artifact_provenance.llm_call_ids).  None when telemetry was skipped.
    call_id: int | None = None
    # Token-level logprobs (OpenAI ``choices[0].logprobs.content`` list) when
    # the caller requested them via ``extra={"logprobs": True, ...}`` and the
    # endpoint provided them.  None = not measured, never "certain".
    logprobs: list | None = None


def decode_tok_per_s(n_tokens: int | None, decode_seconds: float) -> float | None:
    """Pure decode rate from a streaming call.

    ``decode_seconds`` is the window from FIRST token delivery to stream end,
    which excludes the first token's own generation time — so the numerator
    must exclude it too: ``(n_tokens - 1) / decode_seconds``.  Requires at
    least two tokens and a window > 0.5 s to avoid wild small-sample rates.
    """
    if not n_tokens or n_tokens < 2 or decode_seconds <= 0.5:
        return None
    return (n_tokens - 1) / decode_seconds


def record_llm_call(
    db: Any,
    *,
    purpose: str,
    model: str,
    latency_ms: int,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    ok: bool = True,
    error: str | None = None,
    ttft_ms: float | None = None,
    tok_per_s: float | None = None,
    streamed: bool = False,
) -> int | None:
    """Best-effort telemetry insert.  Never raises.

    Returns the ``llm_calls`` row id (the provenance audit-trail handle) or
    ``None`` when telemetry was skipped/failed.

    ``ttft_ms`` (time-to-first-token) and ``tok_per_s`` (decode rate) are only
    meaningful for streaming calls; leave them ``None`` when unknown — the
    measurement layer treats NULL as "not measured", never as zero.
    """
    if db is None:
        return None
    try:
        with db._lock:
            cur = db._conn.execute(
                "INSERT INTO llm_calls (purpose, model, latency_ms, prompt_tokens,"
                " completion_tokens, ok, error, ttft_ms, tok_per_s, streamed)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    purpose,
                    model,
                    latency_ms,
                    prompt_tokens,
                    completion_tokens,
                    1 if ok else 0,
                    (error or None),
                    round(ttft_ms, 1) if ttft_ms is not None else None,
                    round(tok_per_s, 2) if tok_per_s is not None else None,
                    1 if streamed else 0,
                ),
            )
            db._conn.commit()
            return cur.lastrowid
    except Exception as exc:  # pragma: no cover — telemetry must never break callers
        logger.debug("llm_calls telemetry insert failed: %s", exc)
        return None


def llm_call(
    messages: list[dict],
    *,
    base_url: str | None = None,
    model: str | None = None,
    cfg: Any = None,
    db: Any = None,
    purpose: str = "",
    timeout: float = 30,
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra: dict | None = None,
) -> LLMResult:
    """Synchronous, non-streaming chat completion via the configured endpoint.

    Either pass ``cfg`` (base_url/model default from ``cfg.serving``) or pass
    ``base_url`` + ``model`` explicitly.  Never raises: check ``result.ok``.
    """
    if cfg is not None:
        base_url = base_url or cfg.serving.base_url
        model = model or cfg.serving.workhorse_model
    if not base_url or not model:
        err = "missing base_url/model"
        record_llm_call(
            db,
            purpose=purpose,
            model=model or "",
            latency_ms=0,
            prompt_tokens=None,
            completion_tokens=None,
            ok=False,
            error=err,
        )
        return LLMResult(None, False, model or "", 0, error=err)

    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if extra:
        payload.update(extra)

    started = time.monotonic()
    text: str | None = None
    p_tok: int | None = None
    c_tok: int | None = None
    err: str | None = None
    logprobs: list | None = None
    try:
        import httpx

        with httpx.Client(timeout=timeout) as client:
            resp = client.post(f"{base_url}/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
            lp = choice.get("logprobs") or {}
            if isinstance(lp, dict) and isinstance(lp.get("content"), list):
                logprobs = lp["content"]
            usage = data.get("usage") or {}
            p_tok = usage.get("prompt_tokens")
            c_tok = usage.get("completion_tokens")
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"[:500]
        logger.warning("LLM call failed (purpose=%s): %s", purpose or "?", err)

    latency_ms = int((time.monotonic() - started) * 1000)
    ok = err is None and text is not None
    # Non-streaming decode rate: completion tokens over total wall clock.
    # This under-reports the true decode rate (prompt processing is included)
    # but is directionally useful; streaming calls record the precise rate.
    _tps = c_tok / (latency_ms / 1000.0) if ok and c_tok and latency_ms > 0 else None
    call_id = record_llm_call(
        db,
        purpose=purpose,
        model=model,
        latency_ms=latency_ms,
        prompt_tokens=p_tok,
        completion_tokens=c_tok,
        ok=ok,
        error=err,
        tok_per_s=_tps,
    )
    return LLMResult(
        text,
        ok,
        model,
        latency_ms,
        p_tok,
        c_tok,
        err,
        finish_reason=finish_reason if err is None else None,
        call_id=call_id,
        logprobs=logprobs,
    )
