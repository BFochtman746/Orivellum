"""The report. Its first duty is to say what the run could NOT do."""

from collections import Counter
from pathlib import Path

from . import store
from .config import CFG

SEV_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]


def render(run_id):
    run = store.get_run(run_id)
    f = store.findings(run_id)
    fails = store.failed_units(run_id)
    counts = run["totals"].get("units", {})
    sev = Counter(x["severity"] for x in f)
    L = []
    L.append(f"# Run {run_id} — {run['job']} — {Path(run['target']).name}\n")
    L.append(
        f"**Started** {run['started']}  ·  **Finished** {run['finished'] or '—'}  ·  "
        f"**Status** `{run['status']}`\n"
    )
    L.append(f"**Why it stopped:** {run['stop_reason']}\n")

    L.append("\n## Completeness — read this before the findings\n")
    L.append(f"- Units processed: **{counts.get('done', 0)}** of {sum(counts.values())}\n")
    if counts.get("failed"):
        L.append(f"- Units FAILED: **{counts['failed']}**\n")
    if run["totals"].get("remaining"):
        L.append(
            f"- Units never reached: **{run['totals']['remaining']}** — this report is PARTIAL\n"
        )
    L.append(
        f"- Elapsed: {run['totals'].get('elapsed_min', '?')} min  ·  "
        f"model calls: {run['totals'].get('llm', {}).get('calls', 0)}  ·  "
        f"est. tokens: {run['totals'].get('llm', {}).get('est_tokens', 0):,}\n"
    )
    plan = run.get("plan") or {}
    if plan.get("unavailable"):
        L.append(
            f"- Tools unavailable (reported as unknown, **not** as clean): "
            f"{', '.join(plan['unavailable'])}\n"
        )

    L.append("\n## Findings\n")
    if not f:
        L.append(
            "Nothing was flagged. With scanners unavailable that means *unexamined*, not *clean*.\n"
        )
    else:
        L.append("| Severity | Code | Where | Finding |\n| --- | --- | --- | --- |\n")
        for x in f[:400]:
            L.append(
                f"| {x['severity']} | `{x['code']}` | `{(x['ref'] or '')[:52]}` | {x['title']} |\n"
            )
        if len(f) > 400:
            L.append(f"\n_{len(f) - 400} further findings in the database._\n")
        L.append(
            "\n**By severity:** " + " · ".join(f"{s} {sev[s]}" for s in SEV_ORDER if sev[s]) + "\n"
        )

    top = [x for x in f if x["severity"] in ("CRITICAL", "HIGH")][:12]
    if top:
        L.append("\n## What to fix first\n")
        for x in top:
            L.append(f"\n**{x['code']} — {x['title']}**  \n`{x['ref']}`\n")
            if x["detail"]:
                L.append(f"\n{x['detail']}\n")
            if x["fix"]:
                L.append(f"\n_Fix:_ {x['fix']}\n")

    summary = run["totals"].get("summary") or {}
    if summary.get("sections"):
        for title, body in summary["sections"]:
            L.append(f"\n## {title}\n\n{body}\n")

    if fails:
        L.append("\n## Units that failed\n")
        L.append("| # | Ref | Attempts | Error |\n| --- | --- | --- | --- |\n")
        for u in fails[:60]:
            L.append(
                f"| {u['ord']} | `{u['ref'][:52]}` | {u['attempts']} | {(u['err'] or '')[:90]} |\n"
            )

    notes = store.get_notes(run_id)
    if notes:
        L.append("\n## Run log\n\n```\n")
        for n in notes[-60:]:
            L.append(f"{n['at']}  {n['text']}\n")
        L.append("```\n")
    return "".join(L)


def write(run_id):
    d = Path(CFG.runs_dir) / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "REPORT.md"
    p.write_text(render(run_id), encoding="utf-8")
    return str(p)


def training_plan(run_id, items):
    """A study plan derived from the findings. Facts and procedures are what
    generated plans are good at; judgement is what the walkthrough is for."""
    L = [
        f"# Training plan — run {run_id}\n",
        "\nOrdered by prerequisite, not by severity. Work top to bottom; each "
        "block names what to read, what to check in the target, and the question "
        "you should be able to answer before moving on.\n",
    ]
    for i, it in enumerate(items, 1):
        L.append(f"\n## {i}. {it['topic']}\n")
        L.append(f"\n**Why it matters here:** {it['why']}\n")
        if it.get("evidence"):
            L.append(
                f"\n**Where it shows up:** {', '.join('`' + e + '`' for e in it['evidence'][:6])}\n"
            )
        if it.get("read"):
            L.append(f"\n**Read:** {it['read']}\n")
        L.append(f"\n**Check it yourself:** {it['check']}\n")
        L.append(f"\n**You know it when you can answer:** {it['question']}\n")
    L.append(
        "\n---\n\nGaps this plan does not close: judgement calls about "
        "design and risk tolerance. Those are for the walkthrough, not a "
        "reading list.\n"
    )
    d = Path(CFG.runs_dir) / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "TRAINING_PLAN.md"
    p.write_text("".join(L), encoding="utf-8")
    return str(p)
