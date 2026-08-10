"""Checkpoint database. The reason a killed run resumes instead of restarting."""
import json, sqlite3, time
from pathlib import Path
from .config import CFG

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started TEXT, finished TEXT, job TEXT, target TEXT, label TEXT,
  status TEXT DEFAULT 'running',        -- running|done|stopped|failed
  stop_reason TEXT, plan JSON, totals JSON);
CREATE TABLE IF NOT EXISTS units(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, ord INTEGER, kind TEXT, ref TEXT,
  status TEXT DEFAULT 'queued',         -- queued|done|failed|skipped
  attempts INTEGER DEFAULT 0,
  payload JSON, digest JSON, err TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS findings(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER, severity TEXT, code TEXT, ref TEXT,
  title TEXT, detail TEXT, fix TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER, at TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS ix_units ON units(run_id, status);
"""

def conn():
    Path(CFG.db).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(CFG.db, timeout=30); c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL"); return c

def init():
    with conn() as c: c.executescript(SCHEMA)

def now(): return time.strftime("%Y-%m-%dT%H:%M:%S")

def start_run(job, target, label, plan):
    init()
    with conn() as c:
        return c.execute("INSERT INTO runs(started,job,target,label,plan) VALUES(?,?,?,?,?)",
                         (now(), job, target, label, json.dumps(plan))).lastrowid

def add_units(run_id, units):
    with conn() as c:
        c.executemany("INSERT INTO units(run_id,ord,kind,ref,payload) VALUES(?,?,?,?,?)",
                      [(run_id, i, u["kind"], u["ref"], json.dumps(u.get("payload") or {}))
                       for i, u in enumerate(units)])

def next_unit(run_id):
    """Atomically CLAIM the next queued unit (status -> running) so two
    resumed processes can never both execute — and both emit — one unit."""
    with conn() as c:
        while True:
            r = c.execute("SELECT * FROM units WHERE run_id=? AND status='queued' "
                          "ORDER BY ord LIMIT 1", (run_id,)).fetchone()
            if not r: return None
            claimed = c.execute("UPDATE units SET status='running' "
                                "WHERE id=? AND status='queued'", (r["id"],)).rowcount
            if claimed:
                d = dict(r); d["payload"] = json.loads(d["payload"] or "{}"); return d

def requeue_running(run_id):
    """A crash can strand units in 'running'; resume puts them back."""
    with conn() as c:
        return c.execute("UPDATE units SET status='queued' "
                         "WHERE run_id=? AND status='running'", (run_id,)).rowcount

def finish_unit(uid, digest=None, err=None):
    with conn() as c:
        c.execute("UPDATE units SET status=?, digest=?, err=?, attempts=attempts+1, at=? WHERE id=?",
                  ("failed" if err else "done", json.dumps(digest or {}), err, now(), uid))

def retry_unit(uid):
    with conn() as c:
        c.execute("UPDATE units SET status='queued', attempts=attempts+1 WHERE id=?", (uid,))

def unit_counts(run_id):
    with conn() as c:
        return {r["status"]: r["n"] for r in c.execute(
            "SELECT status, COUNT(*) n FROM units WHERE run_id=? GROUP BY status", (run_id,))}

def digests(run_id, kind=None, limit=100000):
    q = "SELECT * FROM units WHERE run_id=? AND status='done'"; a=[run_id]
    if kind: q += " AND kind=?"; a.append(kind)
    q += " ORDER BY ord LIMIT ?"; a.append(limit)
    with conn() as c:
        out=[]
        for r in c.execute(q, a):
            d = dict(r); d["digest"] = json.loads(d["digest"] or "{}")
            d["payload"] = json.loads(d["payload"] or "{}"); out.append(d)
        return out

def failed_units(run_id):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT ord,kind,ref,err,attempts FROM units WHERE run_id=? AND status='failed' ORDER BY ord",
            (run_id,))]

def add_finding(run_id, severity, code, ref, title, detail="", fix="", source="analysis",
                unique=False):
    with conn() as c:
        if unique:
            # Repository-level findings are recomputed by every final pass, so
            # replace rather than accumulate.
            c.execute("DELETE FROM findings WHERE run_id=? AND code=? AND ref=?",
                      (run_id, code, ref))
        c.execute("""INSERT INTO findings(run_id,severity,code,ref,title,detail,fix,source)
                     VALUES(?,?,?,?,?,?,?,?)""",
                  (run_id, severity, code, ref, title, detail, fix, source))

def findings(run_id):
    order = {"CRITICAL":0,"HIGH":1,"MEDIUM":2,"LOW":3,"INFO":4}
    with conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM findings WHERE run_id=?", (run_id,))]
    return sorted(rows, key=lambda f: (order.get(f["severity"], 9), f["code"], f["ref"] or ""))

def note(run_id, text):
    with conn() as c:
        c.execute("INSERT INTO notes(run_id,at,text) VALUES(?,?,?)", (run_id, now(), text))

def get_notes(run_id):
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT at,text FROM notes WHERE run_id=? ORDER BY id",(run_id,))]

def end_run(run_id, status, reason, totals):
    with conn() as c:
        c.execute("UPDATE runs SET finished=?, status=?, stop_reason=?, totals=? WHERE id=?",
                  (now(), status, reason, json.dumps(totals), run_id))

def get_run(run_id):
    with conn() as c:
        r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r: return None
        d = dict(r)
        d["plan"] = json.loads(d["plan"] or "{}")
        d["totals"] = json.loads(d["totals"] or "{}")
        return d

def open_runs():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id,job,target,label,started FROM runs WHERE status='running' ORDER BY id")]
