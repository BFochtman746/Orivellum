"""ATLAS-O — the typed world graph of what is true in the story (LAW 2).

Builds one structured node/edge graph per Work from chapter prose, following
the ATLAS research recipe:

  Pass 1 — events:     decompose the chapter into scene-level event units.
  Pass 2 — entities:   extract entities in relation to those events.
  Pass 3 — relations:  extract typed edges connecting events and entities.
  Attribute pass:      revisit each node's evidence and extract attributes.
  Verification:        from the second chapter on, propose inconsistencies
                       between the current chapter and ALL prior chapters'
                       world states, then verify each proposal against the
                       actual text.  Unverified proposals are DISCARDED.

Hard rules:
  * Closed schema — seven node types, twenty edge types in five groups.
    Anything the extractor proposes outside the schema is discarded, not
    coerced (ATLAS's measured advantage comes from schema constraint).
  * LAW 3 — every node, edge, and inconsistency carries an evidence quote
    that is GROUNDED: the quote must actually be found in the chapter text,
    and the stored offset is where it was found.  Ungroundable output is
    discarded.
  * Temperature 0.0 for every call — determinism matters more than flair.
  * All calls go through the central ``llm_call`` gateway.

The per-chapter harvest flow (knowledge_harvest.llm_harvest_by_chapters)
invokes :func:`build_work_graph` after harvesting, so the graph is fed by
the same pipeline rather than growing a parallel entity store.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# ── Closed schema (mirrors OrivellumDB.GRAPH_NODE_TYPES / GRAPH_EDGE_TYPES) ──
NODE_TYPES = ("Character", "Event", "Location", "TimePoint", "Object", "Vehicle", "Concept")

# Chapter text window sent to the model per pass.  Long chapters are split
# into overlapping windows so later scenes are covered too; grounding always
# searches the FULL chapter text, so offsets are correct regardless of which
# window surfaced a quote.
_MAX_PASS_CHARS = 16_000
_WINDOW_OVERLAP = 1_000
_MAX_WINDOWS = 8
# World-state render cap for the verification prompt.
_MAX_STATE_CHARS = 6_000
# Per-call timeout (extraction-class, not chat-class).
_TIMEOUT_SEC = 60
# Bounded caps so a runaway response can't flood the tables.
_MAX_EVENTS = 20
_MAX_ENTITIES = 40
_MAX_RELATIONS = 60
_MAX_PROPOSALS = 20


# ---------------------------------------------------------------------------
# Grounding (LAW 3)
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def ground_quote(quote: str, text: str) -> int | None:
    """Return the character offset of *quote* in *text*, or None.

    Exact match first; then a whitespace-normalised, case-insensitive search
    mapped back to a real offset in the original text.  A quote that cannot
    be located is ungroundable — the caller must discard whatever carried it.
    """
    if not quote or not quote.strip() or not text:
        return None
    q = quote.strip()
    idx = text.find(q)
    if idx >= 0:
        return idx
    # Normalised fallback: collapse whitespace, case-insensitive.
    norm_q = _WS.sub(" ", q).lower()
    if not norm_q:
        return None
    # Build normalised text with an offset map back to the original.
    norm_chars: list[str] = []
    offsets: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch.isspace():
            if prev_space:
                continue
            norm_chars.append(" ")
            offsets.append(i)
            prev_space = True
        else:
            norm_chars.append(ch.lower())
            offsets.append(i)
            prev_space = False
    norm_text = "".join(norm_chars)
    j = norm_text.find(norm_q)
    if j < 0:
        return None
    return offsets[j]


# ---------------------------------------------------------------------------
# LLM plumbing
# ---------------------------------------------------------------------------


def _call(prompt: str, *, purpose: str, cfg: Any, db: OrivellumDB) -> Any:
    """One deterministic gateway call.  Returns parsed JSON or None."""
    from orivellum.capabilities.llm import llm_call  # noqa: PLC0415

    result = llm_call(
        [{"role": "user", "content": prompt}],
        cfg=cfg,
        db=db,
        purpose=purpose,
        timeout=_TIMEOUT_SEC,
        temperature=0.0,
    )
    if not result.ok or not result.text:
        logger.warning("atlas: %s call failed: %s", purpose, result.error or "no response")
        return None
    return _parse_json(result.text)


def _parse_json(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        inner = lines[1:-1] if lines and lines[-1].strip() == "```" else lines[1:]
        text = "\n".join(inner)
    try:
        return json.loads(text)
    except Exception:
        return None


def _fence(chapter_text: str, title: str) -> str:
    """Spotlight the manuscript text so embedded instructions are inert data."""
    from orivellum.capabilities.shield import wrap  # noqa: PLC0415

    return wrap(chapter_text[:_MAX_PASS_CHARS], source=f"chapter \u201c{title}\u201d")


# ---------------------------------------------------------------------------
# Prompts (JSON-only, closed schema stated explicitly)
# ---------------------------------------------------------------------------

_EVENTS_PROMPT = """\
You are a precise narrative analyst. Decompose the chapter below into
scene-level EVENT units. Return ONLY a JSON array, no other text:

[{{"name": "...", "description": "...", "evidence_quote": "..."}}]

Rules:
- Up to {max_events} events, in story order.
- "name" is a short label (3-8 words). "description" one sentence.
- "evidence_quote" MUST be an exact, verbatim quotation copied from the
  chapter text (10-40 words). Do not paraphrase, do not invent.

Chapter: {title}

{text}
"""

_ENTITIES_PROMPT = """\
You are a precise narrative analyst. Given the chapter below and its event
list, extract the ENTITIES that appear in relation to those events.
Return ONLY a JSON array, no other text:

[{{"name": "...", "node_type": "...", "description": "...", "evidence_quote": "..."}}]

Rules:
- node_type MUST be exactly one of: Character, Location, TimePoint, Object,
  Vehicle, Concept. Anything else will be discarded.
- Up to {max_entities} entities. One entry per distinct entity.
- "evidence_quote" MUST be an exact, verbatim quotation copied from the
  chapter text where the entity appears. Do not paraphrase, do not invent.

Events already extracted: {events}

Chapter: {title}

{text}
"""

_RELATIONS_PROMPT = """\
You are a precise narrative analyst. Given the chapter below and its node
list, extract the RELATIONS connecting those nodes.
Return ONLY a JSON array, no other text:

[{{"src": "...", "dst": "...", "edge_type": "...", "evidence_quote": "..."}}]

Rules:
- "src" and "dst" MUST be names copied exactly from the node list below.
- edge_type MUST be exactly one of:
  performs, undergoes, experiences (character-to-event roles);
  kinship_with, affinity_with, hostility_with, affiliated_with (social);
  precedes, occurs_after, causes, contrasts_with, references (event-to-event);
  occurs_at, occurs_on, located_at, present_on (spatiotemporal);
  possesses, uses, part_of, is_a (object).
  Anything else will be discarded.
- Up to {max_relations} relations.
- "evidence_quote" MUST be an exact, verbatim quotation copied from the
  chapter text supporting the relation. Do not paraphrase, do not invent.

Nodes: {nodes}

Chapter: {title}

{text}
"""

_ATTRIBUTES_PROMPT = """\
You are a precise narrative analyst. For each node below, revisit its
evidence in the chapter and extract ATTRIBUTES: role, state, temporal
markers, descriptive qualifiers. Return ONLY a JSON object mapping node
name to an attributes object, no other text:

{{"<node name>": {{"role": "...", "state": "...", ...}}}}

Rules:
- Only include attributes the chapter text actually supports.
- Omit nodes with nothing to add. Values are short strings.

Nodes: {nodes}

Chapter: {title}

{text}
"""

_PROPOSE_PROMPT = """\
You are a continuity checker. Compare the CURRENT chapter against the world
state established by ALL PRIOR chapters. Propose inconsistencies — places
where the current chapter contradicts what an earlier chapter established.
Return ONLY a JSON array, no other text:

[{{"description": "...", "current_quote": "...", "prior_chapter_seq": N,
   "prior_quote": "...", "reasoning": "..."}}]

Rules:
- Up to {max_proposals} proposals. Return [] if there are none.
- "current_quote" MUST be an exact, verbatim quotation from the CURRENT
  chapter text. "prior_quote" MUST be an exact, verbatim quotation from the
  prior chapter identified by "prior_chapter_seq" (an integer from the world
  state below). Do not paraphrase, do not invent quotes.
- Only real contradictions of established fact. Not style, not new
  information, not elaboration.

World state from prior chapters:
{state}

CURRENT chapter (seq {seq}): {title}

{text}
"""

_VERIFY_PROMPT = """\
You are a strict verifier. An inconsistency has been proposed between two
passages of the same book. Confirm it ONLY if the two quoted passages, read
plainly, cannot both be true. Return ONLY a JSON object, no other text:

{{"verdict": "confirmed"}} or {{"verdict": "rejected", "reason": "..."}}

Proposed inconsistency: {description}

Passage A (chapter {prior_seq}): "{prior_quote}"

Passage B (chapter {seq}): "{current_quote}"
"""


# ---------------------------------------------------------------------------
# Extraction (three passes + attribute pass)
# ---------------------------------------------------------------------------


def extract_chapter_graph(
    db: OrivellumDB,
    cfg: Any,
    *,
    work_id: str,
    chapter: dict,
) -> dict:
    """Run the three extraction passes + attribute pass over one chapter.

    *chapter* must carry ``id``, ``seq``, ``title``, ``text``.  Existing
    graph rows for the chapter are dropped first, so re-extraction is
    idempotent.  Returns counts: nodes, edges, discarded.
    """
    chapter_id = chapter["id"]
    title = chapter.get("title") or f"Chapter {chapter.get('seq', 0) + 1}"
    # NEVER strip: offsets must index into the text exactly as stored.
    text = chapter.get("text") or ""
    counts = {"nodes": 0, "edges": 0, "discarded": 0}
    if not text.strip():
        return counts

    db.delete_graph_for_chapter(chapter_id)

    # Canon facts for linkage — fetched once per chapter.
    canon_facts = _active_canon(db, work_id)

    node_ids: dict[str, str] = {}
    node_list: list[dict] = []
    for window in _windows(text):
        fenced = _fence(window, title)
        _pass_nodes(
            db, cfg, work_id, chapter_id, title, text, fenced, canon_facts, counts,
            node_ids, node_list,
        )
        _pass_relations(
            db, cfg, work_id, chapter_id, title, text, fenced, node_ids, node_list, counts
        )
        if node_ids:
            _pass_attributes(db, cfg, title, fenced, node_ids, node_list)
    return counts


def _windows(text: str) -> list[str]:
    """Split long chapter text into overlapping model-view windows."""
    if len(text) <= _MAX_PASS_CHARS:
        return [text]
    step = _MAX_PASS_CHARS - _WINDOW_OVERLAP
    wins: list[str] = []
    for start in range(0, len(text), step):
        wins.append(text[start : start + _MAX_PASS_CHARS])
        if len(wins) >= _MAX_WINDOWS or start + _MAX_PASS_CHARS >= len(text):
            break
    return wins


def _pass_nodes(
    db: OrivellumDB,
    cfg: Any,
    work_id: str,
    chapter_id: str,
    title: str,
    text: str,
    fenced: str,
    canon_facts: list[dict],
    counts: dict,
    node_ids: dict[str, str],
    node_list: list[dict],
) -> None:
    """Passes 1+2: events, then entities.  Mutates node_ids/node_list in place.

    Re-proposals of a name already stored (expected across overlapping
    windows) are skipped silently — first observation wins; only schema and
    grounding rejections count as discarded.
    """
    raw_events = _call(
        _EVENTS_PROMPT.format(max_events=_MAX_EVENTS, title=title, text=fenced),
        purpose="atlas.events",
        cfg=cfg,
        db=db,
    )
    events = _valid_items(raw_events, _MAX_EVENTS)

    event_names = [e.get("name", "") for e in events]
    raw_entities = _call(
        _ENTITIES_PROMPT.format(
            max_entities=_MAX_ENTITIES,
            events=json.dumps(event_names, ensure_ascii=False),
            title=title,
            text=fenced,
        ),
        purpose="atlas.entities",
        cfg=cfg,
        db=db,
    )
    entities = _valid_items(raw_entities, _MAX_ENTITIES)

    # Ground and store nodes.  name -> node_id for the relation pass.
    for ev in events:
        key = _norm_name(ev.get("name", ""))
        if key in node_ids:
            continue  # already stored (overlapping window) — first wins
        nid = _store_node(db, work_id, chapter_id, ev, "Event", text, canon_facts, counts)
        if nid:
            node_ids[key] = nid
            node_list.append({"name": (ev.get("name") or "").strip(), "type": "Event"})
    for ent in entities:
        ntype = (ent.get("node_type") or "").strip()
        if ntype == "Event":
            ntype = ""  # events come from pass 1 only — avoid duplicates
        if ntype not in NODE_TYPES:
            counts["discarded"] += 1
            continue
        key = _norm_name(ent.get("name", ""))
        if key in node_ids:
            continue  # already stored — first observation wins
        nid = _store_node(db, work_id, chapter_id, ent, ntype, text, canon_facts, counts)
        if nid:
            node_ids[key] = nid
            node_list.append({"name": (ent.get("name") or "").strip(), "type": ntype})


def _pass_relations(
    db: OrivellumDB,
    cfg: Any,
    work_id: str,
    chapter_id: str,
    title: str,
    text: str,
    fenced: str,
    node_ids: dict[str, str],
    node_list: list[dict],
    counts: dict,
) -> None:
    """Pass 3: typed edges between stored nodes; discard out-of-schema."""
    raw_relations = _call(
        _RELATIONS_PROMPT.format(
            max_relations=_MAX_RELATIONS,
            nodes=json.dumps(node_list, ensure_ascii=False),
            title=title,
            text=fenced,
        ),
        purpose="atlas.relations",
        cfg=cfg,
        db=db,
    )
    for rel in _valid_items(raw_relations, _MAX_RELATIONS):
        src = node_ids.get(_norm_name(rel.get("src", "")))
        dst = node_ids.get(_norm_name(rel.get("dst", "")))
        etype = (rel.get("edge_type") or "").strip()
        offset = ground_quote(rel.get("evidence_quote", ""), text)
        if not src or not dst or src == dst or offset is None:
            counts["discarded"] += 1
            continue
        try:
            db.create_graph_edge(
                work_id=work_id,
                chapter_id=chapter_id,
                src=src,
                dst=dst,
                edge_type=etype,
                evidence_quote=rel["evidence_quote"].strip(),
                evidence_offset=offset,
            )
            counts["edges"] += 1
        except ValueError:
            counts["discarded"] += 1  # out-of-schema edge type — discarded, not coerced


def _pass_attributes(
    db: OrivellumDB,
    cfg: Any,
    title: str,
    fenced: str,
    node_ids: dict[str, str],
    node_list: list[dict],
) -> None:
    """Attribute pass: revisit each stored node's evidence for attributes."""
    raw_attrs = _call(
        _ATTRIBUTES_PROMPT.format(
            nodes=json.dumps([n["name"] for n in node_list], ensure_ascii=False),
            title=title,
            text=fenced,
        ),
        purpose="atlas.attributes",
        cfg=cfg,
        db=db,
    )
    if not isinstance(raw_attrs, dict):
        return
    for name, attrs in raw_attrs.items():
        nid = node_ids.get(_norm_name(name))
        if not nid or not isinstance(attrs, dict) or not attrs:
            continue
        clean = {
            str(k)[:64]: str(v)[:400]
            for k, v in attrs.items()
            if isinstance(v, (str, int, float, bool))
        }
        if clean:
            db.update_graph_node_attributes(nid, clean)


def _norm_name(name: str) -> str:
    return _WS.sub(" ", (name or "").strip()).lower()


def _valid_items(raw: Any, cap: int) -> list[dict]:
    if not isinstance(raw, list):
        return []
    return [x for x in raw[:cap] if isinstance(x, dict)]


def _store_node(
    db: OrivellumDB,
    work_id: str,
    chapter_id: str,
    item: dict,
    node_type: str,
    chapter_text: str,
    canon_facts: list[dict],
    counts: dict,
) -> str | None:
    """Ground one proposed node and store it; discard if ungroundable."""
    name = (item.get("name") or "").strip()
    quote = (item.get("evidence_quote") or "").strip()
    offset = ground_quote(quote, chapter_text)
    if not name or offset is None:
        counts["discarded"] += 1
        return None
    canon_id = _match_canon(name, canon_facts)
    try:
        nid = db.create_graph_node(
            work_id=work_id,
            chapter_id=chapter_id,
            node_type=node_type,
            name=name,
            description=(item.get("description") or "").strip()[:1000],
            evidence_quote=quote,
            evidence_offset=offset,
            canon_fact_id=canon_id,
        )
    except ValueError:
        counts["discarded"] += 1
        return None
    counts["nodes"] += 1
    return nid


# ---------------------------------------------------------------------------
# Canon linkage
# ---------------------------------------------------------------------------


def _active_canon(db: OrivellumDB, work_id: str) -> list[dict]:
    """Active canon facts for the work, series-wide included.  Never raises."""
    try:
        from orivellum.database.canon_store import CanonStore  # noqa: PLC0415

        return CanonStore(db).list_facts(work_id=work_id, status="active", limit=2000)
    except Exception:
        return []


def _match_canon(name: str, facts: list[dict]) -> str | None:
    """Link a node to the first canon fact whose statement names it.

    Whole-word, case-insensitive match; names under 4 characters are never
    matched (too ambiguous to claim canon instantiation).
    """
    if len(name.strip()) < 4:
        return None
    pat = re.compile(r"\b" + re.escape(name.strip()) + r"\b", re.IGNORECASE)
    for f in facts:
        if pat.search(f.get("statement") or ""):
            return f.get("id")
    return None


# ---------------------------------------------------------------------------
# Cross-chapter verification (propose → verify → keep-or-discard)
# ---------------------------------------------------------------------------


def verify_chapter(
    db: OrivellumDB,
    cfg: Any,
    *,
    work_id: str,
    chapter: dict,
    prior_chapters: list[dict],
) -> dict:
    """Propose inconsistencies vs all prior chapters, verify, store survivors.

    Verification is two-stage and both stages must pass:
      1. Deterministic grounding — both quotes must actually be found in
         their respective chapters' text (offsets recorded from grounding).
      2. A temperature-0.0 verifier call must return ``confirmed``.
    Anything else is discarded.  Returns counts: proposed, kept, discarded.
    """
    counts = {"proposed": 0, "kept": 0, "discarded": 0}
    # NEVER strip: offsets must index into the text exactly as stored.
    text = chapter.get("text") or ""
    if not text.strip() or not prior_chapters:
        return counts

    by_seq = {c["seq"]: c for c in prior_chapters}
    title = chapter.get("title") or f"Chapter {chapter.get('seq', 0) + 1}"

    state = _render_world_state(db, work_id, prior_chapters)
    proposals: list[dict] = []
    seen_props: set[tuple] = set()
    for window in _windows(text):
        raw = _call(
            _PROPOSE_PROMPT.format(
                max_proposals=_MAX_PROPOSALS,
                state=state,
                seq=chapter.get("seq"),
                title=title,
                text=_fence(window, title),
            ),
            purpose="atlas.propose",
            cfg=cfg,
            db=db,
        )
        for prop in _valid_items(raw, _MAX_PROPOSALS):
            key = (
                (prop.get("current_quote") or "").strip(),
                (prop.get("prior_quote") or "").strip(),
                prop.get("prior_chapter_seq"),
            )
            if key in seen_props:
                continue  # re-proposed across overlapping windows
            seen_props.add(key)
            proposals.append(prop)

    for prop in proposals[: _MAX_PROPOSALS * _MAX_WINDOWS]:
        counts["proposed"] += 1
        prior_seq = prop.get("prior_chapter_seq")
        prior = by_seq.get(prior_seq) if isinstance(prior_seq, int) else None
        cur_quote = (prop.get("current_quote") or "").strip()
        prior_quote = (prop.get("prior_quote") or "").strip()
        description = (prop.get("description") or "").strip()
        if not prior or not description:
            counts["discarded"] += 1
            continue
        # Stage 1 — deterministic grounding on BOTH sides.
        cur_offset = ground_quote(cur_quote, text)
        prior_offset = ground_quote(prior_quote, (prior.get("text") or ""))
        if cur_offset is None or prior_offset is None:
            counts["discarded"] += 1
            continue
        # Stage 2 — verifier confirms from the quoted evidence alone.
        verdict = _call(
            _VERIFY_PROMPT.format(
                description=description,
                prior_seq=prior_seq,
                prior_quote=prior_quote.replace('"', "'"),
                seq=chapter.get("seq"),
                current_quote=cur_quote.replace('"', "'"),
            ),
            purpose="atlas.verify",
            cfg=cfg,
            db=db,
        )
        if not isinstance(verdict, dict) or verdict.get("verdict") != "confirmed":
            counts["discarded"] += 1
            continue
        db.create_graph_inconsistency(
            work_id=work_id,
            chapter_id=chapter["id"],
            description=description,
            current_quote=cur_quote,
            current_offset=cur_offset,
            prior_chapter_id=prior["id"],
            prior_quote=prior_quote,
            prior_offset=prior_offset,
            reasoning=(prop.get("reasoning") or "").strip()[:2000],
        )
        counts["kept"] += 1
    return counts


def _render_world_state(db: OrivellumDB, work_id: str, prior_chapters: list[dict]) -> str:
    """Compact, budget-capped render of prior chapters' graph state."""
    lines: list[str] = []
    node_names: dict[str, str] = {}
    for ch in prior_chapters:
        nodes = db.list_graph_nodes(chapter_id=ch["id"])
        edges = db.list_graph_edges(chapter_id=ch["id"])
        for n in nodes:
            node_names[n["id"]] = n["name"]
        header = f"[seq {ch['seq']}] {ch.get('title') or ''}".strip()
        parts: list[str] = []
        for n in nodes:
            attrs = n.get("attributes") or {}
            attr_s = (
                " {" + ", ".join(f"{k}={v}" for k, v in list(attrs.items())[:4]) + "}"
                if attrs
                else ""
            )
            parts.append(f"{n['node_type']}:{n['name']}{attr_s}")
        for e in edges:
            s = node_names.get(e["src"], "?")
            d = node_names.get(e["dst"], "?")
            parts.append(f"{s} -{e['edge_type']}-> {d}")
        if parts:
            lines.append(header + "\n  " + "\n  ".join(parts))
    state = "\n".join(lines)
    if len(state) > _MAX_STATE_CHARS:
        # Keep the most recent chapters' state intact — trim from the front.
        state = "…(earlier state truncated)…\n" + state[-_MAX_STATE_CHARS:]
    return state or "(no prior graph state)"


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

# One build at a time per work — concurrent builds would interleave the
# per-chapter delete+rebuild and leave mixed results.  In-process is enough:
# the app is a single process and the DB lock covers row-level safety.
_build_locks: dict[str, threading.Lock] = {}
_build_locks_guard = threading.Lock()


def _work_build_lock(work_id: str) -> threading.Lock:
    with _build_locks_guard:
        return _build_locks.setdefault(work_id, threading.Lock())


def build_work_graph(
    db: OrivellumDB,
    cfg: Any,
    *,
    work_id: str,
    doc_id: str | None = None,
) -> dict:
    """Build (or rebuild) the world graph for a Work's chapters, in order.

    When *doc_id* is given, only that document's chapters are (re)extracted,
    but verification still runs against ALL prior chapters of the work.
    Returns aggregate counts.  Builds for the same work are serialized.
    """
    with _work_build_lock(work_id):
        return _build_work_graph_locked(db, cfg, work_id=work_id, doc_id=doc_id)


def _build_work_graph_locked(
    db: OrivellumDB,
    cfg: Any,
    *,
    work_id: str,
    doc_id: str | None,
) -> dict:
    with db._lock:
        rows = db._conn.execute(
            """SELECT id, seq, title, text, source_doc_id FROM book_chapters
               WHERE work_id=? AND text IS NOT NULL AND text != ''
               ORDER BY seq""",
            (work_id,),
        ).fetchall()
    chapters = [dict(r) for r in rows]
    if not chapters:
        return {"chapters": 0, "nodes": 0, "edges": 0, "inconsistencies": 0, "discarded": 0}

    totals = {"chapters": 0, "nodes": 0, "edges": 0, "inconsistencies": 0, "discarded": 0}
    rebuilt: set[str] = set()
    first_rebuilt_idx: int | None = None
    for i, ch in enumerate(chapters):
        if doc_id and ch.get("source_doc_id") != doc_id:
            continue
        totals["chapters"] += 1
        c = extract_chapter_graph(db, cfg, work_id=work_id, chapter=ch)
        totals["nodes"] += c["nodes"]
        totals["edges"] += c["edges"]
        totals["discarded"] += c["discarded"]
        rebuilt.add(ch["id"])
        if first_rebuilt_idx is None:
            first_rebuilt_idx = i
        if i > 0:
            v = verify_chapter(
                db, cfg, work_id=work_id, chapter=ch, prior_chapters=chapters[:i]
            )
            totals["inconsistencies"] += v["kept"]
            totals["discarded"] += v["discarded"]

    # Partial rebuild: chapters AFTER the rebuilt ones were verified against
    # a prior world state that just changed, so their stored inconsistencies
    # may no longer be evidence-valid.  Drop and re-verify them.
    if doc_id and first_rebuilt_idx is not None:
        for i, ch in enumerate(chapters):
            if i <= first_rebuilt_idx or ch["id"] in rebuilt:
                continue
            db.delete_graph_inconsistencies_for_chapter(ch["id"])
            v = verify_chapter(
                db, cfg, work_id=work_id, chapter=ch, prior_chapters=chapters[:i]
            )
            totals["inconsistencies"] += v["kept"]
            totals["discarded"] += v["discarded"]
    logger.info(
        "atlas: work %s graph built — %d chapters, %d nodes, %d edges, "
        "%d verified inconsistencies, %d discarded",
        work_id[:8],
        totals["chapters"],
        totals["nodes"],
        totals["edges"],
        totals["inconsistencies"],
        totals["discarded"],
    )
    return totals
