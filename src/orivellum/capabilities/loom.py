"""LOOM (E2) — the chapter drafting engine (Masterpiece Pipeline B5).

MAGNET's multi-agent architecture with ONE critical adaptation: no emergent
goal generation.  The "goal" for every step is the chapter's contracted beat,
read from the blueprint contract on the chapter row; a beat compliance
controller detects stall and drift AGAINST that beat and escalates to the
author as a governance finding — it never changes the story.

The per-chapter loop:
  1. Context assembly — chapter contract; personas of the cast present, each
     restricted to its knowledge horizon at this chapter's act; the current
     world state; the previous chapter's closing passage verbatim; retrieved
     canon facts for entities named in the contract; the voice envelope.
     NOT document snippets.
  2. Action proposal — one character agent per cast member (temp 0.2).
  3. Critic — accept/reject with feedback, bounded retries (temp 0.1).  On
     acceptance the critic emits the implied world-state updates.  The critic
     is NEVER skipped, and the critic model is never the drafting model.
  4. Narrator — selects the actions that fit and writes the prose
     (temp 0.35).  Only the SELECTED actions' world updates are committed.
  5. Commit — world updates with overwrite semantics; the draft lands as a
     NEW chapter revision; approved chapters are never overwritten; every
     llm_call id is recorded into artifact_provenance as ai_generated.
  6. Entropy gate — token-level -logprob spans above threshold get a
     targeted verification pass before the chapter is stored.

Resuming mid-book: ``replay_world_state`` folds the world graph forward
through existing chapters so chapter N+1 is conditioned on the true state
of 1..N, never on memory of how far the draft got.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orivellum.configuration.config import OrivellumConfig
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.loom")


class LoomError(Exception):
    """Raised when drafting cannot proceed; the run row records the reason."""


# Bounded retries per character action (proposal → critic → feedback → retry).
MAX_ACTION_ATTEMPTS = 3
# Closing-passage window fed verbatim to the next chapter.
CLOSING_PASSAGE_CHARS = 1500
# Canon facts retrieved per contract entity.
FACTS_PER_ENTITY = 6
# Entropy gate: mean token -logprob (nats) over a sliding window that flags
# a span for targeted verification (ConStory: error spans run 12-19% hotter).
ENTROPY_WINDOW = 20
ENTROPY_NLL_THRESHOLD = 2.5
MAX_VERIFY_SPANS = 3
# Context block budgets (chars) — same discipline as the context compiler:
# what reaches the prompt is exactly what the report accounts for.
_BUDGETS = {
    "contract": 2_000,
    "personas": 6_000,
    "world_state": 4_000,
    "closing": CLOSING_PASSAGE_CHARS + 100,
    "canon": 4_000,
    "voice": 1_500,
}


def _clip(text: str, budget: int) -> tuple[str, bool]:
    if len(text) <= budget:
        return text, False
    return text[:budget] + "\n…[clipped]", True


# ── Models ────────────────────────────────────────────────────────────────────


def _db_override(db: OrivellumDB, key: str) -> str | None:
    try:
        return (db.get_setting(key, "") or "").strip() or None
    except Exception:
        return None


def drafting_model(db: OrivellumDB, cfg: OrivellumConfig) -> str:
    return _db_override(db, "workhorse_model_override") or cfg.serving.workhorse_model


def critic_model(db: OrivellumDB, cfg: OrivellumConfig) -> str:
    return _db_override(db, "reasoner_model_override") or cfg.serving.reasoner_model


def _require_separated_models(db: OrivellumDB, cfg: OrivellumConfig) -> tuple[str, str]:
    """The drafting models never judge their own output — refuse, don't fudge."""
    drafter, critic = drafting_model(db, cfg), critic_model(db, cfg)
    if not drafter or not critic:
        raise LoomError("drafting requires both a workhorse and a reasoner model")
    if drafter == critic:
        raise LoomError(
            f"critic model {critic!r} is the drafting model — a drafter may "
            "never judge its own output; configure a distinct reasoner model"
        )
    return drafter, critic


# ── Chapter + contract access ─────────────────────────────────────────────────


def _get_chapter(db: OrivellumDB, work_id: str, chapter_id: str) -> dict:
    with db._lock:
        row = db._conn.execute(
            "SELECT * FROM book_chapters WHERE id=? AND work_id=?",
            (chapter_id, work_id),
        ).fetchone()
    if row is None:
        raise LoomError(f"chapter {chapter_id!r} not found in work {work_id!r}")
    ch = dict(row)
    try:
        ch["meta"] = json.loads(ch.get("meta") or "{}")
    except (TypeError, json.JSONDecodeError):
        ch["meta"] = {}
    return ch


def _get_contract(chapter: dict) -> dict:
    contract = chapter.get("meta", {}).get("contract")
    if not isinstance(contract, dict) or not str(contract.get("beat") or "").strip():
        raise LoomError(
            f"chapter seq {chapter.get('seq')} has no contract with a beat — "
            "LOOM drafts only against a contracted beat (no emergent goals)"
        )
    return contract


def _word_range(contract: dict) -> tuple[int, int]:
    wr = contract.get("word_range") or []
    if (
        isinstance(wr, (list, tuple)) and len(wr) == 2
        and all(isinstance(x, (int, float)) for x in wr) and 0 < wr[0] <= wr[1]
    ):
        return int(wr[0]), int(wr[1])
    raise LoomError("chapter contract has no valid word_range [min, max]")


def _prev_chapter(db: OrivellumDB, work_id: str, seq: int) -> dict | None:
    with db._lock:
        row = db._conn.execute(
            """SELECT seq, title, text FROM book_chapters
               WHERE work_id=? AND seq<? AND text IS NOT NULL AND TRIM(text)<>''
               ORDER BY seq DESC LIMIT 1""",
            (work_id, seq),
        ).fetchone()
    return dict(row) if row else None


# ── World state replay (resume mid-book) ─────────────────────────────────────


def replay_world_state(db: OrivellumDB, work_id: str, *, upto_seq: int) -> dict:
    """Rebuild the accumulated world state from the world graph, folding
    chapters 1..upto_seq-1 forward IN ORDER (overwrite semantics), so a
    resumed draft is conditioned on the true state — never a stale table.
    """
    db.clear_world_state(work_id)
    with db._lock:
        rows = db._conn.execute(
            """SELECT n.node_type, n.name, n.description, n.attributes,
                      COALESCE(c.seq, 0) AS seq
               FROM graph_node n
               LEFT JOIN book_chapters c ON c.id = n.chapter_id
               WHERE n.work_id=? AND COALESCE(c.seq, 0) < ?
               ORDER BY seq, n.created_at""",
            (work_id, upto_seq),
        ).fetchall()
    folded = 0
    for r in rows:
        key = f"{r['node_type']}:{r['name']}".strip()
        parts = [p for p in [str(r["description"] or "").strip()] if p]
        try:
            attrs = json.loads(r["attributes"] or "{}")
        except (TypeError, json.JSONDecodeError):
            attrs = {}
        for k, v in sorted(attrs.items()):
            parts.append(f"{k}={v}")
        value = "; ".join(parts) or "(present)"
        db.commit_world_state(work_id, {key: value}, source_chapter_seq=int(r["seq"]))
        folded += 1
    return {"folded_nodes": folded, "keys": len(db.get_world_state(work_id))}


# ── Personas + knowledge horizons ─────────────────────────────────────────────


def _personas_for_cast(db: OrivellumDB, work_id: str, cast: list[str]) -> list[dict]:
    """Approved personas for the cast present.  A missing persona is a
    refusal, not a silent gap — undirected characters are how knowledge and
    memory errors happen."""
    approved = {p["name"].lower(): p for p in db.list_loom_personas(work_id, status="approved")}
    out, missing = [], []
    for name in cast:
        p = approved.get(str(name).strip().lower())
        (out.append(p) if p else missing.append(str(name)))
    if missing:
        raise LoomError(
            "no approved persona for cast member(s): " + ", ".join(missing)
            + " — personas are review-gated and must be approved before drafting"
        )
    return out


def _horizon_fact_ids(persona: dict, act: int) -> list[str]:
    """Union of the persona's knowledge horizon up to and including this act."""
    horizon = persona.get("payload", {}).get("knowledge_horizon") or {}
    ids: list[str] = []
    for act_key, fact_ids in horizon.items():
        try:
            if int(act_key) <= act and isinstance(fact_ids, list):
                ids.extend(str(f) for f in fact_ids)
        except (TypeError, ValueError):
            continue
    return list(dict.fromkeys(ids))


def _facts_by_ids(db: OrivellumDB, work_id: str, fact_ids: list[str]) -> list[dict]:
    if not fact_ids:
        return []
    marks = ",".join("?" for _ in fact_ids)
    with db._lock:
        rows = db._conn.execute(
            f"""SELECT id, statement, classification FROM canon_fact
                WHERE work_id=? AND status='active' AND id IN ({marks})""",
            [work_id, *fact_ids],
        ).fetchall()
    return [dict(r) for r in rows]


def _facts_for_entities(db: OrivellumDB, work_id: str, entities: list[str]) -> list[dict]:
    """Retrieved canon facts for every entity named in the contract."""
    seen: dict[str, dict] = {}
    with db._lock:
        for entity in entities:
            name = str(entity).strip()
            if len(name) < 2:
                continue
            rows = db._conn.execute(
                """SELECT id, statement, classification FROM canon_fact
                   WHERE work_id=? AND status='active' AND statement LIKE ?
                   ORDER BY created_at LIMIT ?""",
                (work_id, f"%{name}%", FACTS_PER_ENTITY),
            ).fetchall()
            for r in rows:
                seen[r["id"]] = dict(r)
    return list(seen.values())


# ── Context assembly ──────────────────────────────────────────────────────────


def _render_persona(persona: dict, allowed_facts: list[dict]) -> str:
    p = persona["payload"]
    lines = [f"### {persona['name']}"]
    for field in ("role", "personality", "goals", "description", "relationships"):
        v = p.get(field)
        if v:
            rendered = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
            lines.append(f"{field}: {rendered}")
    diction = p.get("diction_profile")
    if diction:
        lines.append(f"diction: {json.dumps(diction, ensure_ascii=False)}")
    if allowed_facts:
        lines.append("KNOWS (knowledge horizon — this character knows ONLY these facts):")
        lines.extend(f"  - [{f['classification']}] {f['statement']}" for f in allowed_facts)
    else:
        lines.append("KNOWS: nothing from the canon record yet.")
    return "\n".join(lines)


def _render_world_state(state: dict[str, dict]) -> str:
    if not state:
        return "(no accumulated world state)"
    return "\n".join(
        f"- {k} = {v['value']} (as of ch {v['source_chapter_seq']})"
        for k, v in sorted(state.items())
    )


def assemble_context(
    db: OrivellumDB, cfg: OrivellumConfig, *, work_id: str, chapter: dict
) -> dict:
    """The B5 drafting context — contract, horizon-restricted personas, world
    state, previous closing passage verbatim, retrieved canon, voice envelope.
    Every block is budget-clipped and reported, context-compiler style."""
    contract = _get_contract(chapter)
    act = int(contract.get("act") or 1)
    cast = [str(c) for c in (contract.get("cast") or [])]
    if not cast:
        raise LoomError("chapter contract names no cast — nobody to draft with")
    personas = _personas_for_cast(db, work_id, cast)

    persona_blocks, horizon_map = [], {}
    for p in personas:
        allowed = _facts_by_ids(db, work_id, _horizon_fact_ids(p, act))
        horizon_map[p["name"]] = [f["id"] for f in allowed]
        persona_blocks.append(_render_persona(p, allowed))

    state = db.get_world_state(work_id)
    prev = _prev_chapter(db, work_id, int(chapter["seq"]))
    closing = (prev["text"][-CLOSING_PASSAGE_CHARS:] if prev else "")

    entities = cast + [str(contract.get("location") or "")]
    facts = _facts_for_entities(db, work_id, entities)
    voice = db.get_assay_baseline(work_id, "voice_envelope")

    raw_blocks = {
        "contract": json.dumps(contract, ensure_ascii=False, indent=1),
        "personas": "\n\n".join(persona_blocks),
        "world_state": _render_world_state(state),
        "closing": closing or "(no previous chapter — this is the opening)",
        "canon": "\n".join(
            f"- [{f['classification']}] {f['statement']}" for f in facts
        ) or "(no canon facts retrieved for the named entities)",
        "voice": json.dumps(voice, ensure_ascii=False) if voice else "(no voice envelope)",
    }
    blocks, report = {}, {}
    for name, text in raw_blocks.items():
        clipped, truncated = _clip(text, _BUDGETS[name])
        blocks[name] = clipped
        report[name] = {"budget": _BUDGETS[name], "chars": len(clipped), "truncated": truncated}
    return {
        "work_id": work_id,
        "chapter_seq": int(chapter["seq"]),
        "contract": contract,
        "act": act,
        "cast": cast,
        "personas": personas,
        "horizon_map": horizon_map,
        "blocks": blocks,
        "context_report": report,
        "word_range": _word_range(contract),
    }


# ── JSON parsing (strict) ─────────────────────────────────────────────────────


def _parse_json_obj(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group())
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


# ── Agents ────────────────────────────────────────────────────────────────────


def _gateway(db, cfg, *, model, purpose, system, user, temperature, extra=None,
             timeout=180):
    from orivellum.capabilities.llm import llm_call

    return llm_call(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        cfg=cfg, db=db, model=model, purpose=purpose,
        temperature=temperature, timeout=timeout, extra=extra,
    )


def _persona_block(ctx: dict, name: str) -> str:
    if f"### {name}" in ctx["blocks"]["personas"]:
        block = next(
            b for b in ctx["blocks"]["personas"].split("### ") if b.startswith(name)
        )
        return f"YOUR CHARACTER:\n{block}"
    return f"YOUR CHARACTER: {name}"


def _agent_prompt(ctx: dict, name: str, feedback: str) -> str:
    parts = [
        f"CHAPTER CONTRACT (the beat this chapter MUST accomplish):\n{ctx['blocks']['contract']}",
        _persona_block(ctx, name),
        f"WORLD STATE:\n{ctx['blocks']['world_state']}",
        f"PREVIOUS CHAPTER CLOSES (verbatim):\n{ctx['blocks']['closing']}",
        f"CANON FACTS IN PLAY:\n{ctx['blocks']['canon']}",
    ]
    if feedback:
        parts.append(f"CRITIC FEEDBACK on your last proposal (address it):\n{feedback}")
    parts.append(
        'Propose ONE next action for your character that advances the contracted beat. '
        'JSON only: {"action": "specific physical/verbal action", '
        '"motivation": "why, in character", "dialogue": "line spoken, or empty"}'
    )
    return "\n\n".join(parts)


def _critic_prompt(ctx: dict, name: str, action: dict) -> str:
    return f"""CHAPTER CONTRACT:
{ctx["blocks"]["contract"]}

WORLD STATE:
{ctx["blocks"]["world_state"]}

CHARACTER: {name}
PROPOSED ACTION:
{json.dumps(action, ensure_ascii=False)}

Evaluate the proposal for relevance to the contracted beat, specificity, and
consistency with the character and scene. Reject vague, out-of-character,
implausible, or repetitive actions with concrete feedback. If you accept,
emit the world-state updates the action implies (flat key -> short value).
JSON only: {{"accept": true|false, "feedback": "…",
"world_updates": {{"Character:{name}": "new state", "...": "..."}}}}"""


def _narrator_prompt(ctx: dict, accepted: list[dict]) -> str:
    lo, hi = ctx["word_range"]
    actions = "\n".join(
        f"[{i}] {a['character']}: {json.dumps(a['action'], ensure_ascii=False)}"
        for i, a in enumerate(accepted)
    )
    return f"""CHAPTER CONTRACT:
{ctx["blocks"]["contract"]}

VOICE ENVELOPE (write inside it):
{ctx["blocks"]["voice"]}

WORLD STATE:
{ctx["blocks"]["world_state"]}

PREVIOUS CHAPTER CLOSES (continue seamlessly from this, verbatim context):
{ctx["blocks"]["closing"]}

CRITIC-ACCEPTED CHARACTER ACTIONS (select the subset that fits the scene):
{actions}

Write the chapter prose accomplishing the contracted beat, using ONLY the
actions you select. Target {lo}-{hi} words.
JSON only: {{"selected": [indices of the actions you used], "prose": "the full chapter text"}}"""


# ── Character/critic loop ─────────────────────────────────────────────────────


def _propose_actions(db, cfg, ctx, *, drafter, critic, llm_ids) -> tuple[list[dict], list[str]]:
    """One agent per cast member; the critic gates every syntactically valid
    proposal (never skipped for a real action).  Malformed agent output is
    not an action — it consumes one of the bounded attempts and is sent back
    for retry without reaching the critic.  Returns (accepted proposals,
    stalled character names)."""
    accepted, stalled = [], []
    for name in ctx["cast"]:
        feedback = ""
        won = None
        for _attempt in range(MAX_ACTION_ATTEMPTS):
            r = _gateway(
                db, cfg, model=drafter, purpose="loom.agent.action",
                system=f"You are the character {name}. Stay strictly in character. JSON only.",
                user=_agent_prompt(ctx, name, feedback), temperature=0.2,
            )
            llm_ids.append(r.call_id)
            if not r.ok:
                raise LoomError(f"character agent gateway failure ({name}): {r.error}")
            action = _parse_json_obj(r.text)
            if not action or not str(action.get("action") or "").strip():
                feedback = "Your reply was not the required JSON action object."
                continue
            c = _gateway(
                db, cfg, model=critic, purpose="loom.critic.action",
                system="You are a strict story critic. JSON only.",
                user=_critic_prompt(ctx, name, action), temperature=0.1,
            )
            llm_ids.append(c.call_id)
            if not c.ok:
                raise LoomError(f"critic gateway failure ({name}): {c.error}")
            verdict = _parse_json_obj(c.text)
            if verdict is None:
                feedback = "Critic response malformed; propose a sharper, more specific action."
                continue
            if verdict.get("accept") is True:
                updates = verdict.get("world_updates")
                won = {
                    "character": name,
                    "action": action,
                    "world_updates": updates if isinstance(updates, dict) else {},
                }
                break
            feedback = str(verdict.get("feedback") or "Rejected; be more specific.")
        (accepted.append(won) if won else stalled.append(name))
    return accepted, stalled


# ── Entropy gate ──────────────────────────────────────────────────────────────


def _entropy_spans(logprobs: list | None) -> dict:
    """Sliding-window mean -logprob; windows above threshold merge into
    spans.  logprobs=None → explicitly 'not measured', never 'certain'."""
    if not logprobs:
        return {"available": False, "spans": []}
    toks, nlls = [], []
    for item in logprobs:
        if isinstance(item, dict) and isinstance(item.get("logprob"), (int, float)):
            toks.append(str(item.get("token") or ""))
            nlls.append(-float(item["logprob"]))
    if len(nlls) < ENTROPY_WINDOW:
        return {"available": True, "spans": [], "tokens": len(nlls)}
    hot: list[tuple[int, int]] = []
    for i in range(len(nlls) - ENTROPY_WINDOW + 1):
        window = nlls[i:i + ENTROPY_WINDOW]
        if sum(window) / ENTROPY_WINDOW > ENTROPY_NLL_THRESHOLD:
            if hot and i <= hot[-1][1]:
                hot[-1] = (hot[-1][0], i + ENTROPY_WINDOW)
            else:
                hot.append((i, i + ENTROPY_WINDOW))
    spans = [
        {"text": "".join(toks[a:b]).strip(),
         "mean_nll": round(sum(nlls[a:b]) / (b - a), 3)}
        for a, b in hot[:MAX_VERIFY_SPANS]
    ]
    return {"available": True, "spans": spans, "tokens": len(nlls)}


def _verify_spans(db, cfg, ctx, spans: list[dict], *, critic, llm_ids) -> list[dict]:
    """Targeted verification of high-entropy spans against canon + world
    state BEFORE the chapter is stored.  Failures become findings, never
    silent passes."""
    results = []
    for span in spans:
        r = _gateway(
            db, cfg, model=critic, purpose="loom.entropy.verify",
            system="You verify a suspect passage against the record. JSON only.",
            user=f"""CANON FACTS:
{ctx["blocks"]["canon"]}

WORLD STATE:
{ctx["blocks"]["world_state"]}

SUSPECT PASSAGE (flagged by the entropy gate):
{span["text"]}

Does the passage contradict any canon fact or world-state entry?
JSON only: {{"ok": true|false, "issue": "the contradiction, or empty"}}""",
            temperature=0.0,
        )
        llm_ids.append(r.call_id)
        verdict = _parse_json_obj(r.text) if r.ok else None
        if verdict is None:
            results.append({**span, "verified": False,
                            "issue": f"verification failed: {r.error or 'malformed response'}"})
        else:
            results.append({**span, "verified": bool(verdict.get("ok")),
                            "issue": str(verdict.get("issue") or "")})
    return results


# ── Beat compliance controller ────────────────────────────────────────────────


def _escalation_target(db: OrivellumDB, work_id: str, chapter_id: str) -> tuple[str, str]:
    """Escalations land where the state machine blocks: the book pipeline
    when one exists, else the chapter itself."""
    pipeline = db.get_book_pipeline_for_work(work_id)
    if pipeline:
        return pipeline["id"], "book_pipeline"
    return chapter_id, "book_chapter"


def _beat_check(db, cfg, ctx, prose: str, *, critic, llm_ids) -> dict:
    """Drift detection against the contracted beat — the controller's answer
    to MAGNET's goal generator.  Its response to a miss is a FINDING for the
    author, never a changed goal."""
    r = _gateway(
        db, cfg, model=critic, purpose="loom.critic.beat",
        system="You are a beat compliance controller. JSON only.",
        user=f"""CONTRACTED BEAT:
{json.dumps(ctx["contract"].get("beat"), ensure_ascii=False)}

MUST NOT YET REVEAL:
{json.dumps(ctx["contract"].get("must_not_reveal") or [], ensure_ascii=False)}

CHAPTER PROSE:
{prose[:8000]}

Does the prose accomplish the contracted beat, and does it avoid revealing
anything in the must-not-reveal list?
JSON only: {{"accomplishes_beat": true|false, "premature_reveal": true|false, "feedback": "…"}}""",
        temperature=0.1,
    )
    llm_ids.append(r.call_id)
    if not r.ok:
        return {"checked": False, "error": r.error}
    verdict = _parse_json_obj(r.text)
    if verdict is None:
        return {"checked": False, "error": "malformed beat-check response"}
    return {
        "checked": True,
        "accomplishes_beat": bool(verdict.get("accomplishes_beat")),
        "premature_reveal": bool(verdict.get("premature_reveal")),
        "feedback": str(verdict.get("feedback") or ""),
    }


# ── The per-chapter draft ─────────────────────────────────────────────────────


def _escalate(db, work_id, chapter, description) -> str:
    target_id, target_type = _escalation_target(db, work_id, chapter["id"])
    return db.create_finding(
        object_id=target_id, object_type=target_type, kind="loom_escalation",
        severity="high",
        description=f"LOOM chapter {chapter.get('seq')}: {description}",
        meta={"chapter_id": chapter["id"], "chapter_seq": chapter.get("seq")},
    )


def _store_draft(db, work_id, chapter, prose, meta) -> dict:
    """ONE transaction: re-check the approval claim, insert the revision,
    update the chapter text.  If the chapter was approved since entry,
    NOTHING is persisted — approved chapters are never overwritten, not even
    with a side revision."""
    from orivellum.database.db import _now

    rid = str(uuid.uuid4())
    wc = len(prose.split())
    now = _now()
    with db._lock:
        row = db._conn.execute(
            "SELECT status FROM book_chapters WHERE id=?", (chapter["id"],)
        ).fetchone()
        if row is None or str(row["status"]) == "approved":
            db._conn.rollback()
            raise LoomError(
                f"chapter seq {chapter.get('seq')} was approved mid-run — "
                "approved chapters are never overwritten; draft discarded"
            )
        rev = int(db._conn.execute(
            "SELECT COALESCE(MAX(rev), 0) AS m FROM loom_chapter_revision WHERE chapter_id=?",
            (chapter["id"],),
        ).fetchone()["m"]) + 1
        db._conn.execute(
            """INSERT INTO loom_chapter_revision(id, chapter_id, work_id, rev,
               text, word_count, meta, created_at) VALUES(?,?,?,?,?,?,?,?)""",
            (rid, chapter["id"], work_id, rev, prose, wc, json.dumps(meta), now),
        )
        db._conn.execute(
            "UPDATE book_chapters SET text=?, updated_at=? WHERE id=?",
            (prose, now, chapter["id"]),
        )
        db._conn.commit()
    return {"id": rid, "rev": rev, "word_count": wc, "chapter_text_updated": True}


def _run(db: OrivellumDB, cfg: OrivellumConfig, *, work_id: str, chapter_id: str) -> dict:
    drafter, critic = _require_separated_models(db, cfg)
    chapter = _get_chapter(db, work_id, chapter_id)
    if str(chapter.get("status")) == "approved":
        raise LoomError(
            f"chapter seq {chapter.get('seq')} is approved — approved chapters "
            "are never overwritten; revise through a new revision workflow"
        )
    seq = int(chapter["seq"])
    # Resume mid-book: condition on the TRUE state of 1..N, replayed from the
    # graph, whenever the state table has nothing for this work OR carries
    # entries from this chapter's seq or later (re-drafting an earlier
    # chapter must never see its own — or the future's — state).
    replay = None
    state = db.get_world_state(work_id)
    polluted = any(v["source_chapter_seq"] >= seq for v in state.values())
    if (seq > 1 and not state) or polluted:
        replay = replay_world_state(db, work_id, upto_seq=seq)
    ctx = assemble_context(db, cfg, work_id=work_id, chapter=chapter)

    llm_ids: list[int | None] = []
    evidence: dict[str, Any] = {
        "chapter_seq": seq, "context_report": ctx["context_report"],
        "horizon_map": ctx["horizon_map"], "replay": replay,
        "models": {"drafter": drafter, "critic": critic},
    }

    accepted, stalled = _propose_actions(
        db, cfg, ctx, drafter=drafter, critic=critic, llm_ids=llm_ids)
    evidence["accepted_actions"] = [
        {"character": a["character"], "action": a["action"]} for a in accepted]
    evidence["stalled_characters"] = stalled
    if stalled:
        _escalate(db, work_id, chapter,
                  "beat stall — no critic-accepted action for: " + ", ".join(stalled))
    if not accepted:
        evidence["llm_call_ids"] = [i for i in llm_ids if i is not None]
        return {"status": "escalated", "evidence": evidence,
                "reason": "no character action survived the critic"}

    prose, selected, logprobs = _narrate(
        db, cfg, ctx, accepted, drafter=drafter, llm_ids=llm_ids)
    evidence["selected_actions"] = [a["character"] for a in selected]

    # Beat compliance + word band (the controller: escalate, never rewrite).
    lo, hi = ctx["word_range"]
    wc = len(prose.split())
    beat = _beat_check(db, cfg, ctx, prose, critic=critic, llm_ids=llm_ids)
    evidence["word_count"] = {"count": wc, "range": [lo, hi], "ok": lo <= wc <= hi}
    evidence["beat_check"] = beat

    # Entropy gate BEFORE the chapter is stored.
    evidence["entropy"] = _entropy_gate(
        db, cfg, ctx, logprobs, work_id=work_id, chapter=chapter,
        critic=critic, llm_ids=llm_ids)

    # Store first (atomic approval re-check inside — raises without writing
    # anything if the chapter was approved mid-run), THEN commit world state:
    # a refused draft must leave the world untouched.
    evidence["revision"] = _store_draft(db, work_id, chapter, prose, {
        "beat": ctx["contract"].get("beat"),
        "selected_actions": evidence["selected_actions"],
        "word_count": wc, "entropy": evidence["entropy"], "beat_check": beat,
    })

    # Commit: ONLY the selected actions' world updates (overwrite semantics).
    updates: dict[str, str] = {}
    for a in selected:
        updates.update({str(k): str(v) for k, v in a["world_updates"].items()})
    db.commit_world_state(work_id, updates, source_chapter_seq=seq)
    evidence["world_updates"] = updates
    call_ids = [i for i in llm_ids if i is not None]
    evidence["llm_call_ids"] = call_ids
    for artifact_id, kind in (
        (evidence["revision"]["id"], "chapter_revision"),
        (chapter["id"], "book_chapter"),
    ):
        db.record_provenance(artifact_id, kind, origin="ai_generated",
                             llm_call_ids=call_ids, declared_by="loom")

    problems = _compliance_problems(wc, lo, hi, beat)
    for p in problems:
        _escalate(db, work_id, chapter, p)
    status = "escalated" if (problems or stalled) else "done"
    return {"status": status, "evidence": evidence,
            "reason": "; ".join(problems) if problems else None}


def _narrate(db, cfg, ctx, accepted, *, drafter, llm_ids) -> tuple[str, list[dict], list | None]:
    n = _gateway(
        db, cfg, model=drafter, purpose="loom.narrator",
        system="You are the narrator. JSON only.",
        user=_narrator_prompt(ctx, accepted), temperature=0.35,
        extra={"logprobs": True, "top_logprobs": 1},
    )
    llm_ids.append(n.call_id)
    if not n.ok:
        raise LoomError(f"narrator gateway failure: {n.error}")
    out = _parse_json_obj(n.text)
    prose = str((out or {}).get("prose") or "").strip()
    if not prose:
        raise LoomError("narrator returned no prose (malformed response)")
    sel = (out or {}).get("selected")
    if not isinstance(sel, list):
        raise LoomError(
            "narrator selection missing or malformed — refusing to default "
            "to all actions (only SELECTED actions may commit world updates)"
        )
    selected_idx = sorted({
        int(i) for i in sel
        if isinstance(i, (int, float)) and not isinstance(i, bool)
        and 0 <= int(i) < len(accepted)
    })
    if not selected_idx:
        raise LoomError("narrator selected no valid actions — nothing to commit")
    return prose, [accepted[i] for i in selected_idx], n.logprobs


def _entropy_gate(db, cfg, ctx, logprobs, *, work_id, chapter, critic, llm_ids) -> dict:
    entropy = _entropy_spans(logprobs)
    if entropy["spans"]:
        entropy["verification"] = _verify_spans(
            db, cfg, ctx, entropy["spans"], critic=critic, llm_ids=llm_ids)
        for v in entropy["verification"]:
            if not v["verified"]:
                _escalate(db, work_id, chapter,
                          f"entropy gate: unverified high-uncertainty span — {v['issue'][:200]}")
    return entropy


def _compliance_problems(wc: int, lo: int, hi: int, beat: dict) -> list[str]:
    problems = []
    if not (lo <= wc <= hi):
        problems.append(f"word count {wc} outside contracted range {lo}-{hi}")
    if not beat.get("checked"):
        problems.append(f"beat compliance unverifiable: {beat.get('error')}")
    elif not beat.get("accomplishes_beat") or beat.get("premature_reveal"):
        problems.append("beat drift: " + (beat.get("feedback") or "contracted beat not met"))
    return problems


def run_loom_draft(
    db: OrivellumDB, cfg: OrivellumConfig, *, run_id: str, work_id: str, chapter_id: str
) -> dict:
    """Background entry point.  The run row is the claim; every exit path
    finishes it — done, escalated, or error."""
    try:
        result = _run(db, cfg, work_id=work_id, chapter_id=chapter_id)
    except Exception as exc:
        logger.exception("LOOM draft failed (work=%s chapter=%s)", work_id, chapter_id)
        db.finish_loom_run(run_id, status="error", error=str(exc))
        raise
    db.finish_loom_run(run_id, status=result["status"], evidence=result["evidence"],
                       error=result.get("reason"))
    return result
