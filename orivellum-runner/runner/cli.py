"""python -m runner <command>

run     --job code|xlsx --target <zip|dir|xlsx> [--label baseline]
resume  --run <id>
report  --run <id>
list
findings --run <id> [--severity CRITICAL]
"""

import argparse
import json
import sys
from pathlib import Path

from . import harness, llm, report, store  # noqa: F401 (json/llm: kept for job modules & debugging)
from .config import CFG
from .jobs import code as code_job
from .jobs import research as research_job
from .jobs import xlsx as xlsx_job

JOBS = {"code": code_job, "xlsx": xlsx_job, "research": research_job}


def _run_dir(run_id):
    d = Path(CFG.runs_dir) / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_run(a):
    job = JOBS[a.job]
    # research takes a topic string; file jobs take a path
    if getattr(job, "PATH_TARGET", True):
        target = str(Path(a.target).resolve())
    else:
        target = a.target.strip()
    store.init()
    print(f"planning {a.job} run over {target} …")
    tmp = Path(CFG.runs_dir) / "_staging"
    tmp.mkdir(parents=True, exist_ok=True)
    plan = job.plan(target, tmp)
    units = plan.pop("units")
    run_id = store.start_run(a.job, target, a.label, plan)
    store.add_units(run_id, units)
    print(
        f"run {run_id}: {len(units)} units queued  "
        f"(budgets: {CFG.max_units} units / {CFG.max_minutes} min / "
        f"{CFG.max_tokens:,} tokens)"
    )
    if plan.get("unavailable"):
        print("  unavailable tools:", ", ".join(plan["unavailable"]))
    print(f"  model: {'MOCK — structure only' if CFG.mock else CFG.base_url}")
    return _execute(run_id, job)


def _execute(run_id, job, resume=False):
    res = harness.execute(run_id, job, job.unit_worker, job.final_pass, resume=resume)
    p = report.write(run_id)
    items = job.plan_items(run_id)
    tp = report.training_plan(run_id, items) if items else None
    f = store.findings(run_id)
    crit = sum(1 for x in f if x["severity"] == "CRITICAL")
    high = sum(1 for x in f if x["severity"] == "HIGH")
    print(f"\n{res['status']}: {res['stop_reason']}")
    print(f"  units: {res['totals']['units']}")
    print(f"  findings: {len(f)}  (CRITICAL {crit}, HIGH {high})")
    print(f"  report: {p}")
    if tp:
        print(f"  training plan: {tp}")
    return 1 if crit else 0


def cmd_resume(a):
    run = store.get_run(a.run)
    if not run:
        print("no such run")
        return 1
    job = JOBS[run["job"]]
    stranded = store.requeue_running(a.run)
    if stranded:
        print(f"  requeued {stranded} unit(s) stranded by an interrupted run")
    print(
        f"resuming run {a.run} ({run['job']}) — "
        f"{store.unit_counts(a.run).get('queued', 0)} units left"
    )
    return _execute(a.run, job, resume=True)


def cmd_report(a):
    print(report.render(a.run))
    return 0


def cmd_list(_a):
    store.init()
    with store.conn() as c:
        rows = [
            dict(r)
            for r in c.execute(
                "SELECT id,job,target,status,started,finished FROM runs ORDER BY id DESC LIMIT 30"
            )
        ]
    if not rows:
        print("no runs yet")
        return 0
    print(f"{'ID':<5}{'JOB':<7}{'STATUS':<10}{'STARTED':<21}TARGET")
    for r in rows:
        print(
            f"{r['id']:<5}{r['job']:<7}{r['status']:<10}{r['started']:<21}{Path(r['target']).name}"
        )
    return 0


def cmd_verify(a):
    """Re-run a saved test manifest against a workbook — the regression suite
    the xlsx job builds. Exit 0 only on PASS."""
    from .jobs import xlsx_engine as engine

    manifest = json.loads(Path(a.tests).read_text())
    res = engine.run_manifest(str(Path(a.target).resolve()), manifest)
    print(f"{res['status']}: {res['passed']}/{res['total']} formula cases pass")
    if res.get("error"):
        print(f"  engine: {res['error']}")
    for f in res.get("failed", [])[:20]:
        print(
            f"  FAIL {f['sheet']}!{f['cell']}: expected {f['expected']}, "
            f"got {f.get('actual')} ({f['why']})"
        )
    if res.get("stale_cache"):
        print(f"  stale cached values: {', '.join(res['stale_cache'][:10])}")
    return 0 if res["status"] == "PASS" else 1


def cmd_findings(a):
    f = store.findings(a.run)
    if a.severity:
        f = [x for x in f if x["severity"] == a.severity.upper()]
    for x in f:
        print(f"[{x['severity']:<8}] {x['code']:<20} {x['ref'] or ''}")
        print(f"           {x['title']}")
        if x["fix"]:
            print(f"           FIX: {x['fix']}")
    print(f"\n{len(f)} finding(s)")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="runner")
    s = p.add_subparsers(dest="cmd", required=True)
    r = s.add_parser("run")
    r.add_argument("--job", choices=list(JOBS), required=True)
    r.add_argument("--target", required=True)
    r.add_argument("--label", default="baseline")
    r.set_defaults(fn=cmd_run)
    rs = s.add_parser("resume")
    rs.add_argument("--run", type=int, required=True)
    rs.set_defaults(fn=cmd_resume)
    rp = s.add_parser("report")
    rp.add_argument("--run", type=int, required=True)
    rp.set_defaults(fn=cmd_report)
    s.add_parser("list").set_defaults(fn=cmd_list)
    fd = s.add_parser("findings")
    fd.add_argument("--run", type=int, required=True)
    fd.add_argument("--severity")
    fd.set_defaults(fn=cmd_findings)
    vf = s.add_parser("verify")
    vf.add_argument("--target", required=True)
    vf.add_argument("--tests", required=True)
    vf.set_defaults(fn=cmd_verify)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
