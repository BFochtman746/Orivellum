"""Storage + ledger. Stdlib only, so this runs on Replit with nothing installed."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

GENESIS = "0" * 64


def now() -> str:
    return datetime.now(UTC).isoformat()


def nid() -> str:
    return uuid.uuid4().hex[:16]


def sha(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def canon(p) -> str:
    return json.dumps(p, sort_keys=True, separators=(",", ":"))


class DB:
    def __init__(self, path: str | Path = "next.db"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript((Path(__file__).parent / "schema.sql").read_text("utf-8"))
        self.conn.commit()

    # ── ledger ────────────────────────────────────────────────────────────
    def ledger(self, scope: str, kind: str, payload) -> str:
        row = self.conn.execute(
            "SELECT seq, hash FROM next_ledger WHERE scope=? ORDER BY seq DESC LIMIT 1",
            (scope,),
        ).fetchone()
        seq = (row["seq"] + 1) if row else 0
        prev = row["hash"] if row else GENESIS
        body = canon({"seq": seq, "kind": kind, "payload": payload})
        h = sha(prev + body)
        self.conn.execute(
            "INSERT INTO next_ledger (scope,seq,kind,payload,prev_hash,hash,at) "
            "VALUES (?,?,?,?,?,?,?)",
            (scope, seq, kind, canon(payload), prev, h, now()),
        )
        return h

    def verify(self, scope: str) -> dict:
        prev = GENESIS
        rows = self.conn.execute(
            "SELECT * FROM next_ledger WHERE scope=? ORDER BY seq", (scope,)
        ).fetchall()
        for i, r in enumerate(rows):
            if r["seq"] != i:
                return {"ok": False, "at": i, "why": "sequence gap"}
            body = canon(
                {"seq": r["seq"], "kind": r["kind"], "payload": json.loads(r["payload"])}
            )
            if r["prev_hash"] != prev or sha(prev + body) != r["hash"]:
                return {"ok": False, "at": i, "why": "hash mismatch"}
            prev = r["hash"]
        return {"ok": True, "entries": len(rows)}

    def write(self, scope: str, kind: str, sql: str, params: tuple, payload) -> str:
        try:
            self.conn.execute(sql, params)
            h = self.ledger(scope, kind, payload)
            self.conn.commit()
            return h
        except Exception:
            self.conn.rollback()
            raise

    def event(self, event: str, action_id=None, set_id=None, kind=None,
              recommended=None, detail: str = "") -> None:
        self.conn.execute(
            "INSERT INTO next_event (action_id,set_id,event,kind,recommended,detail,at) "
            "VALUES (?,?,?,?,?,?,?)",
            (action_id, set_id, event, kind, recommended, detail, now()),
        )
        self.conn.commit()

    def q(self, sql: str, p: tuple = ()):
        return self.conn.execute(sql, p).fetchall()

    def q1(self, sql: str, p: tuple = ()):
        return self.conn.execute(sql, p).fetchone()

    def close(self):
        self.conn.close()


# ── policy ────────────────────────────────────────────────────────────────
# Flat key: value reader. Deliberately not a YAML library — stdlib only, and
# every dial here is a scalar. Anything needing nesting belongs in code.

_DEFAULTS = {
    "max_facets": 3,
    "max_actions": 4,
    "min_actions": 2,
    "auto_run_max_units": 200,
    "auto_run_max_minutes": 10,
    "auto_run_requires_reversible": 1,
    "auto_run_enabled": 0,
    "expire_on_new_answer": 1,
    "require_anchor_ref": 1,
    "recommend_min_confidence": 0.55,
}


def load_policy(path: str | Path | None = None) -> dict:
    pol = dict(_DEFAULTS)
    if path and Path(path).exists():
        for raw in Path(path).read_text("utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip("'\"")
            if k not in pol:
                continue
            if re.fullmatch(r"-?\d+", v):
                pol[k] = int(v)
            elif re.fullmatch(r"-?\d*\.\d+", v):
                pol[k] = float(v)
            elif v.lower() in ("true", "yes", "on"):
                pol[k] = 1
            elif v.lower() in ("false", "no", "off"):
                pol[k] = 0
    return pol
