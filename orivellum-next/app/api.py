"""HTTP surface.

Uses FastAPI when it is installed; falls back to a stdlib http.server so this
package runs on a bare Replit box with nothing pip-installed. The routes are
identical either way, so the front end does not care which one is serving.

    python -m app.api          -> serves web/ + the API on :8000
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .clarify import (GateError, cancel_gate, close_gate, open_gate, read_gate,
                      resolve, should_gate)
from .db import DB, load_policy
from .generate import EXAMPLE_PROBES, build_set
from .nextaction import (ActionError, dismiss, latest_set, read_set, select,
                         stats)
from .runner_bridge import Chain, ChainExhausted, enqueue, pending_for_you

# FastAPI types imported at module level so that FastAPI's annotation resolver
# can find them in __globals__.  With `from __future__ import annotations` all
# annotations become strings; FastAPI evaluates those strings against the
# function's __globals__ (the module globals), not the enclosing closure's
# locals.  Importing inside build_fastapi() puts Request only in a local scope
# that __globals__ cannot see, causing a 422 on every GET route.
try:
    from fastapi import FastAPI, HTTPException  # noqa: F401
    from fastapi import Request                  # noqa: F401 — must be in globals
    from fastapi.responses import FileResponse   # noqa: F401
    from fastapi.staticfiles import StaticFiles  # noqa: F401
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False

ROOT = Path(__file__).resolve().parent.parent
POLICY = load_policy(ROOT / "policy" / "next_policy.yaml")
DBPATH = ROOT / "next.db"


def _db() -> DB:
    return DB(DBPATH)


# ── handlers, framework-free so both servers can share them ───────────────

def h_policy(_body=None):
    return POLICY


def h_gate_open(body):
    db = _db()
    try:
        rid = open_gate(
            db, body["thread_id"], body["target"], body["facets"],
            cost_units=body.get("cost_units"), cost_minutes=body.get("cost_minutes"),
            cost_replaces=body.get("cost_replaces", ""),
            reversible=body.get("reversible", True), policy=POLICY,
        )
        return read_gate(db, rid)
    finally:
        db.close()


def h_gate_read(body):
    db = _db()
    try:
        return read_gate(db, body["request_id"])
    finally:
        db.close()


def h_gate_resolve(body):
    db = _db()
    try:
        resolve(db, body["facet_id"], body["value"], body.get("kind", "option"))
        return read_gate(db, body["request_id"])
    finally:
        db.close()


def h_gate_close(body):
    db = _db()
    try:
        return close_gate(db, body["request_id"], skip=bool(body.get("skip")))
    finally:
        db.close()


def h_gate_cancel(body):
    db = _db()
    try:
        cancel_gate(db, body["request_id"])
        return {"ok": True}
    finally:
        db.close()


def h_gate_needed(body):
    need, why = should_gate(
        body.get("cost_units"), body.get("cost_minutes"),
        bool(body.get("reversible", True)), int(body.get("ambiguous_facets", 0)), POLICY)
    return {"gate": need, "why": why}


def h_next_build(body):
    db = _db()
    try:
        probes = body.get("probes")
        if probes is None:
            probes = EXAMPLE_PROBES
        res = build_set(db, body["thread_id"], body.get("from_message", "m"),
                        body.get("answer", ""), probes, POLICY)
        if res.get("set_id"):
            res["set"] = read_set(db, res["set_id"])
        return res
    finally:
        db.close()


def h_next_latest(body):
    db = _db()
    try:
        return latest_set(db, body["thread_id"]) or {"set": None}
    finally:
        db.close()


def h_next_select(body):
    db = _db()
    try:
        return select(db, body["action_id"], body.get("prompt"))
    finally:
        db.close()


def h_next_dismiss(body):
    db = _db()
    try:
        dismiss(db, body["action_id"], body.get("reason", ""))
        return {"ok": True}
    finally:
        db.close()


def h_next_enqueue(body):
    db = _db()
    try:
        chain = Chain(body.get("thread_id", "t"), body.get("budget"))
        try:
            return enqueue(db, body["action_id"], chain)
        except ChainExhausted as exc:
            return {"queued": True, "auto": False, "why": str(exc),
                    "chain": chain.report()}
    finally:
        db.close()


def h_queue(body):
    db = _db()
    try:
        return {"pending": pending_for_you(db, body.get("thread_id"))}
    finally:
        db.close()


def h_stats(_body=None):
    db = _db()
    try:
        return stats(db)
    finally:
        db.close()


ROUTES = {
    "/api/policy": h_policy,
    "/api/gate/needed": h_gate_needed,
    "/api/gate/open": h_gate_open,
    "/api/gate/read": h_gate_read,
    "/api/gate/resolve": h_gate_resolve,
    "/api/gate/close": h_gate_close,
    "/api/gate/cancel": h_gate_cancel,
    "/api/next/build": h_next_build,
    "/api/next/latest": h_next_latest,
    "/api/next/select": h_next_select,
    "/api/next/dismiss": h_next_dismiss,
    "/api/next/enqueue": h_next_enqueue,
    "/api/queue": h_queue,
    "/api/stats": h_stats,
}


# ── FastAPI path ──────────────────────────────────────────────────────────

def build_fastapi():
    # All FastAPI symbols are already imported at module level (see top of file).
    # Re-importing here is not needed and was the source of the annotation bug:
    # Request in a local scope is invisible to FastAPI's __globals__-based resolver.
    app = FastAPI(title="Orivellum Next")

    def register(path, fn):
        async def endpoint(request: Request):
            body = {}
            if request.method == "POST":
                try:
                    body = await request.json()
                except Exception:
                    body = {}
            try:
                return fn(body)
            except (GateError, ActionError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except KeyError as exc:
                raise HTTPException(status_code=400,
                                    detail=f"missing field {exc}") from exc
        app.add_api_route(path, endpoint, methods=["GET", "POST"])

    for p, f in ROUTES.items():
        register(p, f)

    web = ROOT / "web"
    if web.exists():
        app.mount("/web", StaticFiles(directory=str(web)), name="web")

        @app.get("/")
        def index():
            return FileResponse(str(web / "index.html"))
    return app


# ── stdlib fallback ───────────────────────────────────────────────────────

def serve_stdlib(port: int = 8000):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    web = ROOT / "web"

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, payload, ctype="application/json"):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _run(self, body):
            fn = ROUTES.get(self.path.split("?")[0])
            if not fn:
                return self._send(404, {"error": "no such route"})
            try:
                self._send(200, fn(body))
            except (GateError, ActionError) as exc:
                self._send(422, {"error": str(exc)})
            except KeyError as exc:
                self._send(400, {"error": f"missing field {exc}"})
            except Exception as exc:                      # noqa: BLE001
                self._send(500, {"error": repr(exc)})

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ROUTES:
                return self._run({})
            rel = "index.html" if path in ("/", "") else path.lstrip("/")
            rel = rel[4:] if rel.startswith("web/") else rel
            f = (web / rel).resolve()
            if f.is_file() and str(f).startswith(str(web.resolve())):
                ctype = ("text/html" if f.suffix == ".html" else
                         "text/css" if f.suffix == ".css" else
                         "application/javascript" if f.suffix == ".js" else
                         "application/octet-stream")
                return self._send(200, f.read_bytes(), ctype)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                body = {}
            self._run(body)

        def log_message(self, *a):
            pass

    print(f"orivellum-next on http://0.0.0.0:{port}  (stdlib server, no FastAPI)")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    try:
        import uvicorn  # noqa: F401
        from fastapi import FastAPI  # noqa: F401
    except ImportError:
        serve_stdlib()
    else:
        import uvicorn
        uvicorn.run(build_fastapi(), host="0.0.0.0", port=8000)
