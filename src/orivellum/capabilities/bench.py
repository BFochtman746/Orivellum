"""Performance benchmarks — the numbers that make every other change provable.

Three experiments, each answering one question the system otherwise cannot:

  ttft        How long until the first token at the context lengths actually
              served?  Short prompts hide KV-cache and long-context costs, so
              this sweeps several prompt sizes instead of testing one.
  generation  Sustained decode rate (tokens/sec).  Token generation is
              memory-bandwidth bound, so this is the number speculative
              decoding is supposed to move.
  cache       Does the server reuse the prompt prefix?  Runs the same long
              prefix twice with a different suffix.  If run 2's TTFT is not
              dramatically lower, prefix caching is not working — usually
              because something volatile sits at the FRONT of the prompt.

Every probe is recorded in ``llm_calls`` (purpose ``bench.<kind>``) and each
run's summary lands in ``bench_runs`` so results are comparable over time.

All functions are synchronous — call them from the background executor or a
worker thread, never from the event loop directly.
"""
from __future__ import annotations

import json
import logging
import statistics
import time
from typing import Any

logger = logging.getLogger("orivellum.bench")

# Neutral filler used to pad prompts to a target size.  Deliberately boring
# prose so models don't burn tokens reacting to the content itself.
_FILLER = (
    "The certifier verifies the load path, the sling angle and the rated "
    "capacity before the lift proceeds. "
)

_DEFAULT_SWEEP_CHARS = (1_000, 8_000, 32_000)


# ──────────────────────────────────────────────────────────────────────────────
# Low-level streaming probe
# ──────────────────────────────────────────────────────────────────────────────

def stream_probe(
    base_url: str,
    model: str,
    messages: list[dict],
    *,
    max_tokens: int = 64,
    timeout: float = 180.0,
) -> dict:
    """One streaming chat call, measured.

    Returns ``{ok, error, ttft_ms, total_ms, n_tokens, tok_per_s, text}``.
    ``n_tokens`` prefers the provider usage block from the final chunk and
    falls back to the delta count (one delta per token on llama.cpp-family
    servers).  ``tok_per_s`` is the pure decode rate — tokens after the first
    over the time after the first — so prompt processing does not pollute it.
    """
    import httpx

    started = time.monotonic()
    first_tok: float | None = None
    n_deltas = 0
    usage_completion: int | None = None
    text_parts: list[str] = []
    err: str | None = None

    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream(
                "POST",
                f"{base_url}/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "max_tokens": max_tokens,
                },
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    chunk = line[6:]
                    if chunk.strip() == "[DONE]":
                        break
                    try:
                        d = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    usage = d.get("usage")
                    if isinstance(usage, dict):
                        usage_completion = (
                            usage.get("completion_tokens") or usage_completion
                        )
                    choices = d.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    piece = (
                        (delta.get("content") or "")
                        + (delta.get("reasoning_content") or "")
                    )
                    if piece:
                        if first_tok is None:
                            first_tok = time.monotonic()
                        n_deltas += 1
                        text_parts.append(piece)
    except Exception as exc:  # network / HTTP / protocol errors
        err = f"{type(exc).__name__}: {exc}"[:300]

    now = time.monotonic()
    total_ms = (now - started) * 1000.0
    ttft_ms = (first_tok - started) * 1000.0 if first_tok is not None else None
    from orivellum.capabilities.llm import decode_tok_per_s

    n_tokens = usage_completion or (n_deltas if n_deltas else None)
    decode_s = (now - first_tok) if first_tok is not None else 0.0
    tok_per_s = decode_tok_per_s(n_tokens, decode_s)
    ok = err is None and first_tok is not None
    return {
        "ok": ok,
        "error": err,
        "ttft_ms": round(ttft_ms, 1) if ttft_ms is not None else None,
        "total_ms": round(total_ms, 1),
        "n_tokens": n_tokens,
        "tok_per_s": round(tok_per_s, 2) if tok_per_s is not None else None,
        "text": "".join(text_parts),
    }


def _prompt_of_chars(n_chars: int, question: str) -> list[dict]:
    filler = (_FILLER * (n_chars // len(_FILLER) + 1))[:n_chars]
    return [
        {"role": "system", "content": "You are a terse assistant. Answer in one short sentence."},
        {"role": "user", "content": f"{filler}\n\n{question}"},
    ]


def _record(db: Any, kind: str, model: str, probe: dict) -> None:
    from orivellum.capabilities.llm import record_llm_call

    record_llm_call(
        db, purpose=f"bench.{kind}", model=model,
        latency_ms=int(probe["total_ms"]),
        completion_tokens=probe.get("n_tokens"),
        ok=probe["ok"], error=probe.get("error"),
        ttft_ms=probe.get("ttft_ms"), tok_per_s=probe.get("tok_per_s"),
        streamed=True,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Experiments
# ──────────────────────────────────────────────────────────────────────────────

def run_ttft_sweep(
    cfg: Any,
    db: Any,
    *,
    sizes_chars: tuple[int, ...] = _DEFAULT_SWEEP_CHARS,
    label: str = "",
) -> dict:
    """TTFT at increasing prompt sizes.  One probe per size, sequential."""
    base_url = cfg.serving.base_url
    model = cfg.serving.workhorse_model
    points = []
    for size in sizes_chars:
        probe = stream_probe(
            base_url, model,
            _prompt_of_chars(size, "What does the certifier verify? One sentence."),
            max_tokens=32,
        )
        _record(db, "ttft", model, probe)
        points.append({
            "prompt_chars": size,
            "ttft_ms": probe["ttft_ms"],
            "tok_per_s": probe["tok_per_s"],
            "ok": probe["ok"],
            "error": probe["error"],
        })
        if not probe["ok"]:
            break  # server unreachable — no point hammering larger sizes
    summary = {
        "model": model,
        "points": points,
        "all_ok": all(p["ok"] for p in points),
    }
    return save_bench_run(db, "ttft", label, summary)


def run_generation_bench(
    cfg: Any,
    db: Any,
    *,
    rounds: int = 3,
    max_tokens: int = 256,
    label: str = "",
) -> dict:
    """Sustained decode rate: median tok/s over a few medium generations."""
    base_url = cfg.serving.base_url
    model = cfg.serving.workhorse_model
    rates: list[float] = []
    probes = []
    for i in range(rounds):
        probe = stream_probe(
            base_url, model,
            [{"role": "user",
              "content": "Write a plain, factual paragraph describing how a "
                         "public library catalogs new arrivals. No lists."}],
            max_tokens=max_tokens,
        )
        _record(db, "generation", model, probe)
        probes.append({k: probe[k] for k in
                       ("ok", "error", "ttft_ms", "tok_per_s", "n_tokens")})
        if probe["ok"] and probe["tok_per_s"]:
            rates.append(probe["tok_per_s"])
        if not probe["ok"]:
            break
    summary = {
        "model": model,
        "rounds": probes,
        "median_tok_per_s": round(statistics.median(rates), 2) if rates else None,
        "all_ok": all(p["ok"] for p in probes),
    }
    return save_bench_run(db, "generation", label, summary)


def run_cache_probe(
    cfg: Any,
    db: Any,
    *,
    prefix_chars: int = 16_000,
    label: str = "",
) -> dict:
    """Prefix-cache check: identical long prefix, different suffix, twice.

    If the second TTFT is not far lower than the first, the server is not
    reusing the prompt prefix — usually because something volatile (time,
    randomized context) sits at the front of the prompt.
    """
    base_url = cfg.serving.base_url
    model = cfg.serving.workhorse_model
    q1 = "What angle is checked before the lift? One sentence."
    q2 = "What capacity is checked before the lift? One sentence."
    p1 = stream_probe(base_url, model, _prompt_of_chars(prefix_chars, q1), max_tokens=32)
    _record(db, "cache", model, p1)
    p2: dict = {"ok": False, "error": "skipped: first probe failed",
                "ttft_ms": None, "total_ms": 0.0, "n_tokens": None, "tok_per_s": None}
    if p1["ok"]:
        p2 = stream_probe(base_url, model, _prompt_of_chars(prefix_chars, q2), max_tokens=32)
        _record(db, "cache", model, p2)
    speedup = None
    cache_working = None
    if p1["ok"] and p2["ok"] and p1["ttft_ms"] and p2["ttft_ms"]:
        speedup = round(p1["ttft_ms"] / p2["ttft_ms"], 2)
        cache_working = p2["ttft_ms"] < 0.5 * p1["ttft_ms"]
    summary = {
        "model": model,
        "prefix_chars": prefix_chars,
        "ttft_cold_ms": p1["ttft_ms"],
        "ttft_warm_ms": p2["ttft_ms"],
        "ttft_speedup": speedup,
        "cache_working": cache_working,
        "all_ok": p1["ok"] and p2["ok"],
        "errors": [e for e in (p1["error"], p2["error"]) if e],
    }
    return save_bench_run(db, "cache", label, summary)


BENCH_KINDS = {
    "ttft": run_ttft_sweep,
    "generation": run_generation_bench,
    "cache": run_cache_probe,
}


# ──────────────────────────────────────────────────────────────────────────────
# Persistence
# ──────────────────────────────────────────────────────────────────────────────

def save_bench_run(db: Any, kind: str, label: str, summary: dict) -> dict:
    """Insert one bench_runs row; returns the stored record."""
    with db._lock:
        cur = db._conn.execute(
            "INSERT INTO bench_runs (kind, label, summary) VALUES (?,?,?)",
            (kind, label or "", json.dumps(summary)),
        )
        db._conn.commit()
        row_id = cur.lastrowid
        row = db._conn.execute(
            "SELECT id, ts, kind, label, summary FROM bench_runs WHERE id=?",
            (row_id,),
        ).fetchone()
    return _run_dict(row)


def list_bench_runs(db: Any, kind: str | None = None, limit: int = 20) -> list[dict]:
    limit = max(1, min(int(limit), 100))
    q = "SELECT id, ts, kind, label, summary FROM bench_runs"
    args: list = []
    if kind:
        q += " WHERE kind=?"
        args.append(kind)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with db._lock:
        rows = db._conn.execute(q, args).fetchall()
    return [_run_dict(r) for r in rows]


def _run_dict(row) -> dict:
    d = {"id": row[0], "ts": row[1], "kind": row[2], "label": row[3]}
    try:
        d["summary"] = json.loads(row[4] or "{}")
    except json.JSONDecodeError:
        d["summary"] = {}
    return d


# ──────────────────────────────────────────────────────────────────────────────
# Telemetry aggregation (reads llm_calls)
# ──────────────────────────────────────────────────────────────────────────────

def telemetry_summary(db: Any, *, hours: int = 24, purpose: str | None = None) -> dict:
    """Aggregate llm_calls over a window: counts, latency, TTFT, decode rate.

    Percentiles are computed in Python (SQLite has no native percentile);
    the window is capped so this stays cheap.
    """
    hours = max(1, min(int(hours), 24 * 30))
    q = (
        "SELECT purpose, latency_ms, ttft_ms, tok_per_s, ok FROM llm_calls "
        "WHERE ts >= datetime('now', ?)"
    )
    args: list = [f"-{hours} hours"]
    if purpose:
        q += " AND purpose=?"
        args.append(purpose)
    q += " ORDER BY id DESC LIMIT 5000"
    with db._lock:
        rows = db._conn.execute(q, args).fetchall()

    by_purpose: dict[str, dict] = {}
    for p, latency, ttft, tps, ok in rows:
        b = by_purpose.setdefault(p or "", {
            "calls": 0, "errors": 0,
            "_lat": [], "_ttft": [], "_tps": [],
        })
        b["calls"] += 1
        if not ok:
            b["errors"] += 1
        if latency is not None:
            b["_lat"].append(latency)
        if ttft is not None:
            b["_ttft"].append(ttft)
        if tps is not None:
            b["_tps"].append(tps)

    def _pct(vals: list, frac: float):
        if not vals:
            return None
        s = sorted(vals)
        return round(s[min(len(s) - 1, int(frac * len(s)))], 1)

    out = {}
    for p, b in by_purpose.items():
        out[p] = {
            "calls": b["calls"],
            "errors": b["errors"],
            "latency_ms_p50": _pct(b["_lat"], 0.50),
            "latency_ms_p95": _pct(b["_lat"], 0.95),
            "ttft_ms_p50": _pct(b["_ttft"], 0.50),
            "ttft_ms_p95": _pct(b["_ttft"], 0.95),
            "tok_per_s_median": _pct(b["_tps"], 0.50),
            "measured_ttft": len(b["_ttft"]),
        }
    return {"hours": hours, "purposes": out, "total_calls": len(rows)}
