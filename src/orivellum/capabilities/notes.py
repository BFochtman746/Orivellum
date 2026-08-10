"""Commonplace notes — capture blocks, AI categorization, append-only vault filing.

Pipeline (one direction, never backwards):

    inbox → proposed → approved → filed
                     → rejected

- Capture is frictionless: a block of text lands in today's inbox.
- Processing (nightly via Night Scriptorium, or on demand) asks the LLM to
  classify each block against a fixed category policy. The model can only
  name category IDs — it never names a path or file; the server owns all
  filing structure.
- Proposals surface in the unified review inbox. On approval the block is
  filed to the append-only markdown vault under ``data/vault/`` and becomes
  a searchable knowledge item; stated actions become tasks.
- The daily report is derived from approved/filed blocks only.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from orivellum.capabilities.llm import llm_call

logger = logging.getLogger(__name__)

__all__ = [
    "CATEGORIES",
    "CATEGORY_IDS",
    "today_str",
    "classify_block",
    "process_inbox",
    "file_block",
    "build_daily_report",
]

# ── Category policy (server-owned; the model may only pick from these IDs) ──

CATEGORIES: list[dict[str, str]] = [
    {"id": "personal", "label": "Personal", "index_file": "Personal.md"},
    {"id": "work", "label": "Work", "index_file": "Work.md"},
    {"id": "relationships", "label": "Relationships", "index_file": "Relationships.md"},
    {"id": "health", "label": "Health", "index_file": "Health.md"},
    {"id": "faith", "label": "Faith", "index_file": "Faith.md"},
    {"id": "home", "label": "Home", "index_file": "Home.md"},
    {"id": "finance", "label": "Finance", "index_file": "Finance.md"},
    {"id": "creative", "label": "Creative", "index_file": "Creative.md"},
    {"id": "learning", "label": "Learning", "index_file": "Learning.md"},
    {"id": "projects", "label": "Projects", "index_file": "Projects.md"},
    {"id": "decisions", "label": "Decisions", "index_file": "Decisions.md"},
    {"id": "ideas", "label": "Ideas", "index_file": "Ideas.md"},
    {"id": "problems", "label": "Problems", "index_file": "Problems.md"},
    {"id": "wins", "label": "Wins", "index_file": "Wins.md"},
    {"id": "gratitude", "label": "Gratitude", "index_file": "Gratitude.md"},
    {"id": "unsorted", "label": "Needs Review", "index_file": "Unsorted.md"},
]
CATEGORY_IDS = {c["id"] for c in CATEGORIES}
_INDEX_FILES = {c["id"]: c["index_file"] for c in CATEGORIES}
FALLBACK_CATEGORY = "unsorted"
MAX_CATEGORIES = 5

_KINDS = {"note", "decision", "action", "question", "idea", "reference"}


def today_str() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def vault_root(cfg: Any) -> Path:
    return Path(cfg.data_dir) / "vault"


# ── Classification ───────────────────────────────────────────────────────────

_CLASSIFY_SYSTEM = (
    "You are a filing clerk. You classify one captured note into fixed "
    "categories. You never invent categories, paths, or file names. "
    "Use only what is present in the note — no speculation, no filler. "
    "Return ONLY valid JSON, no markdown fences, no commentary."
)


def _classify_prompt(text: str) -> str:
    cats = ", ".join(sorted(CATEGORY_IDS))
    return (
        "Classify this captured note.\n\n"
        f"NOTE:\n{text[:4000]}\n\n"
        "Return JSON exactly in this shape:\n"
        '{"title":"short title (max 10 words)",'
        '"summary":"one plain sentence",'
        f'"categories":["one to {MAX_CATEGORIES} of: {cats}"],'
        '"kind":"note|decision|action|question|idea|reference",'
        '"actions":[{"text":"...","due":"YYYY-MM-DD or empty","stated":true}],'
        '"open_questions":["..."],'
        '"confidence":0.0}\n\n'
        "Rules: 'actions' only for actions the note EXPLICITLY states — "
        "stated must be true and text close to verbatim. 'due' only if a "
        "date/deadline is stated. Empty arrays are fine. confidence is your "
        "honest 0-1 estimate that the categories are right."
    )


def _parse_json(raw: str) -> dict | None:
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\s*|\s*```$", "", s, flags=re.IGNORECASE)
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else None
    except Exception:
        m = re.search(r"\{.*\}", s, re.DOTALL)
        if m:
            try:
                out = json.loads(m.group(0))
                return out if isinstance(out, dict) else None
            except Exception:
                return None
    return None


def _normalize_proposal(raw: dict, text: str) -> dict:
    """Enforce the policy: unknown categories are replaced with the fallback
    (with a warning); structure is server-owned."""
    warnings: list[str] = []
    cats_in = raw.get("categories") or []
    cats: list[str] = []
    for c in cats_in[:MAX_CATEGORIES]:
        cid = str(c).strip().lower()
        if cid in CATEGORY_IDS:
            if cid not in cats:
                cats.append(cid)
        else:
            warnings.append(f"unknown category '{c}' replaced with '{FALLBACK_CATEGORY}'")
            if FALLBACK_CATEGORY not in cats:
                cats.append(FALLBACK_CATEGORY)
    if not cats:
        cats = [FALLBACK_CATEGORY]
        warnings.append("no category proposed; filed as unsorted")

    kind = str(raw.get("kind") or "note").strip().lower()
    if kind not in _KINDS:
        kind = "note"

    # Actions must be provably stated in the note: besides the model's own
    # stated:true flag, most of the action's words must actually appear in
    # the captured text — a hallucinated action must never become a task.
    note_words = set(re.findall(r"[a-z0-9']+", text.lower()))
    actions = []
    for a in (raw.get("actions") or [])[:10]:
        if not (isinstance(a, dict) and a.get("text") and a.get("stated") is True):
            continue
        a_text = str(a["text"])[:500]
        a_words = [w for w in re.findall(r"[a-z0-9']+", a_text.lower()) if len(w) > 2]
        overlap = sum(1 for w in a_words if w in note_words)
        if a_words and overlap / len(a_words) < 0.6:
            warnings.append(f"dropped action not grounded in the note: '{a_text[:80]}'")
            continue
        due = str(a.get("due") or "")[:10]
        # A due date must itself be stated: keep it only when its digits
        # (day/year fragments) appear somewhere in the note.
        if due and not any(d in text for d in re.findall(r"\d+", due)):
            if not re.search(r"\d", text) and not re.search(
                r"\b(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                text,
                re.IGNORECASE,
            ):
                warnings.append(f"dropped unstated due date '{due}'")
                due = ""
        actions.append({"text": a_text, "due": due, "stated": True})

    try:
        conf = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
    except (TypeError, ValueError):
        conf = 0.5

    return {
        "title": (str(raw.get("title") or "").strip() or text.strip().split("\n")[0][:70]),
        "summary": str(raw.get("summary") or "").strip()[:300],
        "categories": cats,
        "kind": kind,
        "actions": actions,
        "open_questions": [str(q)[:300] for q in (raw.get("open_questions") or [])[:10]],
        "confidence": conf,
        "warnings": warnings,
    }


def classify_block(db: Any, cfg: Any, block: dict) -> dict | None:
    """Ask the LLM to classify one block. Returns a normalized proposal, or
    None when the model is unreachable / unparseable (block stays in inbox)."""
    result = llm_call(
        [
            {"role": "system", "content": _CLASSIFY_SYSTEM},
            {"role": "user", "content": _classify_prompt(block["text"])},
        ],
        cfg=cfg,
        db=db,
        purpose="notes.classify",
        timeout=60,
        temperature=0.2,
        max_tokens=800,
    )
    if not result.ok or not result.text:
        return None
    raw = _parse_json(result.text)
    if raw is None:
        return None
    return _normalize_proposal(raw, block["text"])


def process_inbox(db: Any, cfg: Any, limit: int = 50) -> dict:
    """Classify inbox blocks into proposals. Failures leave the block in the
    inbox with the error recorded — never silently dropped."""
    blocks = db.list_note_blocks(status="inbox", limit=limit)
    proposed = failed = 0
    for b in blocks:
        try:
            proposal = classify_block(db, cfg, b)
        except Exception as exc:  # never let one block kill the run
            logger.warning("notes: classify crashed for %s: %s", b["id"], exc)
            proposal = None
        if proposal is None:
            db.set_note_block_error(
                b["id"],
                "AI was unreachable or returned an unusable answer; will retry on the next run.",
            )
            failed += 1
            continue
        if db.set_note_block_proposal(b["id"], proposal):
            proposed += 1
    return {"scanned": len(blocks), "proposed": proposed, "failed": failed}


# ── Filing (append-only; server owns every path) ────────────────────────────


def _append_once(path: Path, marker: str, content: str) -> bool:
    """Append ``content`` unless ``marker`` is already present (idempotent).
    Returns True when something was written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if marker in existing:
        return False
    with path.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(content)
    return True


def _entry_markdown(block: dict, proposal: dict) -> str:
    lines = [f"## {block['day']} — {proposal.get('title') or 'Note'}", ""]
    lines.append(block["text"].rstrip())
    actions = proposal.get("actions") or []
    if actions:
        lines += ["", "**Actions**"]
        for a in actions:
            due = f" (due {a['due']})" if a.get("due") else ""
            lines.append(f"- [ ] {a['text']}{due}")
    questions = proposal.get("open_questions") or []
    if questions:
        lines += ["", "**Open questions**"]
        lines += [f"- {q}" for q in questions]
    lines += ["", f"<!-- block:{block['id']} source:{block.get('source', 'web')} -->", ""]
    return "\n".join(lines)


def file_block(db: Any, cfg: Any, block: dict, proposal: dict) -> list[str]:
    """Write an approved block into the vault. Canonical entry goes to
    Journal/Daily/<day>.md; each category index gets one link line.
    Append-only and idempotent (re-running never duplicates)."""
    root = vault_root(cfg)
    rel_daily = f"Journal/Daily/{block['day']}.md"
    daily = root / rel_daily
    marker = f"<!-- block:{block['id']}"

    if not daily.exists():
        daily.parent.mkdir(parents=True, exist_ok=True)
        daily.write_text(f"# {block['day']}\n\n", encoding="utf-8")
    _append_once(daily, marker, _entry_markdown(block, proposal))

    paths = [rel_daily]
    title = proposal.get("title") or "Note"
    for cid in proposal.get("categories") or [FALLBACK_CATEGORY]:
        index_name = _INDEX_FILES.get(cid, _INDEX_FILES[FALLBACK_CATEGORY])
        rel_index = f"Journal/_indexes/{index_name}"
        link = f"- {block['day']} — [{title}](../Daily/{block['day']}.md) <!-- block:{block['id']} -->\n"
        _append_once(root / rel_index, marker, link)
        paths.append(rel_index)
    return paths


# ── Approval completion (idempotent; replayable after any partial failure) ──


def complete_approval(db: Any, cfg: Any, block: dict) -> dict:
    """Run every approval side effect for an ``approved`` block, then mark it
    ``filed``. Fully idempotent — safe to replay after a crash at any point:

    - vault writes are marker-guarded (``file_block``),
    - tasks are deduplicated per block+action,
    - the knowledge item is deduplicated by ``meta.block_id``,
    - the status flip to ``filed`` happens last.
    """
    proposal = json.loads(block.get("proposal") or "{}")
    paths = file_block(db, cfg, block, proposal)

    tasks_created = 0
    for a in proposal.get("actions") or []:
        task_text = a["text"] + (f" (due {a['due']})" if a.get("due") else "")
        with db._lock:
            exists = db._conn.execute(
                "SELECT 1 FROM tasks WHERE work_id IS NULL AND text=? LIMIT 1",
                (task_text,),
            ).fetchone()
        if not exists:
            db.create_task(None, task_text)
            tasks_created += 1

    with db._lock:
        have_knowledge = db._conn.execute(
            "SELECT 1 FROM knowledge WHERE json_extract(meta,'$.block_id')=? LIMIT 1",
            (block["id"],),
        ).fetchone()
    if not have_knowledge:
        db.create_knowledge_item(
            work_id=None,
            kind=proposal.get("kind") or "note",
            text=block["text"][:2000],
            subject=proposal.get("title"),
            confidence=float(proposal.get("confidence") or 0.7),
            review_status="approved",
            meta={
                "source": "commonplace",
                "block_id": block["id"],
                "day": block["day"],
                "categories": proposal.get("categories") or [],
            },
        )

    db.mark_note_block_filed(block["id"], paths)
    try:
        build_daily_report(db, cfg, block["day"])
    except Exception as exc:
        logger.warning("notes: report rebuild for %s failed: %s", block["day"], exc)
    return {"filed_paths": paths, "tasks_created": tasks_created}


def resume_approved(db: Any, cfg: Any, limit: int = 50) -> int:
    """Recovery: finish blocks stuck in ``approved`` (approved in the review
    inbox, but a crash or disk error interrupted filing). Returns the number
    of blocks completed."""
    stuck = db.list_note_blocks(status="approved", limit=limit)
    done = 0
    for b in stuck:
        try:
            complete_approval(db, cfg, b)
            done += 1
        except Exception as exc:
            logger.warning("notes: recovery filing failed for %s: %s", b["id"], exc)
            db.set_note_block_error(b["id"], f"Filing failed; will retry: {exc}")
    return done


# ── Daily report (derived from approved/filed blocks only) ──────────────────

_NARRATIVE_PROMPT = (
    "Write 3-5 plain sentences summarising the day from these approved "
    "notes. Use only what is present. No motivational language, no invented "
    "outcomes, no filler. If the day was thin, say so briefly."
)


def build_daily_report(db: Any, cfg: Any, day: str, write_vault: bool = True) -> dict:
    """Compose the daily report for ``day`` from approved/filed blocks,
    store it, and (optionally) write it into the vault."""
    blocks = [
        b for b in db.list_note_blocks(day=day, limit=500) if b["status"] in ("approved", "filed")
    ]
    proposals = []
    for b in blocks:
        try:
            proposals.append((b, json.loads(b["proposal"] or "{}")))
        except Exception:
            proposals.append((b, {}))

    by_cat: dict[str, list[str]] = {}
    actions: list[str] = []
    questions: list[str] = []
    for b, p in proposals:
        point = p.get("title") or b["text"][:70]
        for cid in p.get("categories") or [FALLBACK_CATEGORY]:
            by_cat.setdefault(cid, []).append(point)
        for a in p.get("actions") or []:
            due = f" (due {a['due']})" if a.get("due") else ""
            actions.append(f"{a['text']}{due}")
        questions.extend(p.get("open_questions") or [])

    narrative = ""
    if blocks:
        joined = "\n".join(f"[{b['id'][:8]}] {b['text'][:600]}" for b, _ in proposals)
        result = llm_call(
            [{"role": "user", "content": f"{_NARRATIVE_PROMPT}\n\n{joined}"}],
            cfg=cfg,
            db=db,
            purpose="notes.report",
            timeout=60,
            temperature=0.3,
            max_tokens=400,
        )
        if result.ok and result.text:
            narrative = result.text.strip()
    if not narrative:
        if blocks:
            narrative = (
                f"{len(blocks)} approved note(s) across {len(by_cat)} "
                f"category/categories. {len(actions)} stated action(s), "
                f"{len(questions)} open question(s)."
            )
            narrative += " No model was reachable, so this summary is mechanical."
        else:
            narrative = "Nothing was approved for this day."

    labels = {c["id"]: c["label"] for c in CATEGORIES}
    md = [f"# Daily report — {day}", "", narrative, ""]
    if by_cat:
        md.append("## By category")
        for cid in sorted(by_cat):
            md += ["", f"**{labels.get(cid, cid)}**"]
            md += [f"- {pt}" for pt in by_cat[cid]]
        md.append("")
    if actions:
        md += ["## Actions"] + [f"- [ ] {a}" for a in actions] + [""]
    if questions:
        md += ["## Open questions"] + [f"- {q}" for q in questions] + [""]
    report_md = "\n".join(md).rstrip() + "\n"

    block_ids = [b["id"] for b, _ in proposals]
    db.upsert_note_report(day, report_md, block_ids)
    if write_vault and blocks:
        path = vault_root(cfg) / "Reports" / f"{day}-daily-report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report_md, encoding="utf-8")
    return {"day": day, "report": report_md, "block_count": len(blocks)}


def yesterday_str() -> str:
    return (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
