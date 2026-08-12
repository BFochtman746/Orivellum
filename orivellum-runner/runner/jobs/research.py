"""RESEARCH job — take a topic, come back with sourced findings and a curriculum.

Five phases, same doctrine as every other job — deterministic tools FIND,
the model EXPLAINS, never the reverse:

  1. INVENTORY   — what the corpus already holds on the topic.  FTS over the
                   Orivellum database (read-only), computed at plan time.
                   Research never restarts from zero.
  2. GAP INTAKE  — what to research.  From the gap table when it exists, plus
                   cached suggested_queries and topic-profile gap proposals;
                   deterministic seed facets when the tables offer nothing.
                   Each gap is one unit in the queue.
  3. PER-GAP RESEARCH — one clean-context sub-run per gap, calling the
                   existing websearch pipeline (RRF fusion, BM25 passage
                   ranking, source-quality scoring).  Fetched text is screened
                   for injection and fenced before any model call.  The model
                   returns claims; CODE verifies every claim cites a known
                   source and quotes text that actually appears in the
                   evidence.  Unverifiable claims are dropped and counted,
                   never kept.
  4. CURRICULUM  — training-plan items in the existing six-field shape, plus
                   prerequisites and a spaced review schedule.  Derived from
                   the verified digests by code.
  5. REPORT      — leads with completeness and names what it could not find.

A unit is ONE GAP.  Its digest is persisted in the checkpoint DB and as a
JSON artifact under runs/<id>/digests/, so a killed run resumes instead of
restarting and the next milestone (corpus writeback) has a machine-readable
input.
"""

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from .. import llm, shield, store
from ..config import CFG

# The target is a topic string, not a file. cli.cmd_run checks this flag.
PATH_TARGET = False

MAX_GAPS = int(os.getenv("MAX_GAPS", "16"))
RESEARCH_PROFILE = os.getenv("RESEARCH_PROFILE", "balanced")

_WORKSPACE = Path(__file__).resolve().parents[3]


def _orivellum_db():
    return os.getenv("ORIVELLUM_DB", str(_WORKSPACE / "data" / "orivellum.db"))


def _orivellum_src():
    return os.getenv("ORIVELLUM_SRC", str(_WORKSPACE / "src"))


# ── corpus access (READ-ONLY — this job never writes to the Orivellum DB) ───


def _corpus_conn():
    db = _orivellum_db()
    if not Path(db).exists():
        return None
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _tokens(topic):
    return [t for t in re.findall(r"[a-zA-Z0-9]{2,}", (topic or "").lower())][:12]


def _fts_match(topic):
    toks = _tokens(topic)
    return " OR ".join(f'"{t}"' for t in toks) if toks else ""


def _overlaps(text, toks):
    low = (text or "").lower()
    return any(t in low for t in toks)


def inventory(topic):
    """Deterministic phase 1: what the corpus already holds. Never a model."""
    inv = {
        "topic": topic,
        "db": _orivellum_db(),
        "available": False,
        "knowledge_hits": [],
        "chunk_hits": [],
        "documents": [],
        "counts": {},
        "notes": [],
    }
    c = _corpus_conn()
    if c is None:
        inv["notes"].append(
            f"Orivellum DB not found at {inv['db']} — inventory unavailable, "
            "research proceeds from zero (reported, not hidden)."
        )
        return inv
    inv["available"] = True
    match = _fts_match(topic)
    try:
        if match:
            inv["knowledge_hits"] = [
                {"text": r["text"][:400], "kind": r["kind"], "work_id": r["work_id"]}
                for r in c.execute(
                    "SELECT k.text, k.kind, k.work_id FROM knowledge_fts f "
                    "JOIN knowledge k ON k.id = f.knowledge_id "
                    "WHERE knowledge_fts MATCH ? AND k.review_status != 'rejected' "
                    "ORDER BY rank LIMIT 40",
                    (match,),
                )
            ]
    except sqlite3.Error as e:
        inv["notes"].append(f"knowledge FTS unavailable: {e}")
    try:
        if match:
            inv["chunk_hits"] = [
                {"text": r["text"][:400], "doc_id": r["doc_id"]}
                for r in c.execute(
                    "SELECT text, doc_id FROM chunks_fts WHERE chunks_fts MATCH ? "
                    "ORDER BY rank LIMIT 20",
                    (match,),
                )
            ]
    except sqlite3.Error as e:
        inv["notes"].append(f"chunk FTS unavailable: {e}")
    toks = _tokens(topic)
    try:
        docs = c.execute("SELECT id, title FROM documents WHERE title IS NOT NULL").fetchall()
        inv["documents"] = [
            {"id": r["id"], "title": r["title"]} for r in docs if _overlaps(r["title"], toks)
        ][:30]
    except sqlite3.Error as e:
        inv["notes"].append(f"documents table unavailable: {e}")
    inv["counts"] = {
        "knowledge_hits": len(inv["knowledge_hits"]),
        "chunk_hits": len(inv["chunk_hits"]),
        "matching_documents": len(inv["documents"]),
    }
    inv["notes"].append(
        "Vector retrieval not attempted from the runner (no embeddings endpoint "
        "here); inventory is FTS-only and says so."
    )
    c.close()
    return inv


def _gaps_from_gap_table(c, topic, toks, gaps, notes):
    try:
        for r in c.execute(
            "SELECT id, unit, scope, evidence_absent, gap_class FROM gap "
            "WHERE status IN ('proposed','ratified','assigned')"
        ):
            blob = " ".join(filter(None, (r["unit"], r["scope"], r["evidence_absent"])))
            if _overlaps(blob, toks):
                q = (r["unit"] or r["scope"] or "").strip() or r["evidence_absent"][:120]
                gaps.append({"query": f"{topic}: {q}", "origin": "gap_table", "gap_id": r["id"]})
    except sqlite3.Error as e:
        notes.append(f"gap table unavailable: {e}")


def _gaps_from_json_column(c, sql, column, origin, toks, gaps, notes, label):
    try:
        for r in c.execute(sql):
            for q in json.loads(r[column] or "[]"):
                if _overlaps(q, toks):
                    gaps.append({"query": q, "origin": origin})
    except (sqlite3.Error, ValueError) as e:
        notes.append(f"{label} unavailable: {e}")


def intake_gaps(topic):
    """Deterministic phase 2: gaps become queue units.

    Sources, in order of authority: the gap table (Gap Engine), cached
    suggested_queries (corpus hygiene), topic-profile gap proposals.  When all
    three offer nothing on this topic, deterministic seed facets keep the run
    useful — labeled as such, never disguised as detected gaps.
    """
    toks = _tokens(topic)
    gaps, notes = [], []
    c = _corpus_conn()
    if c is not None:
        _gaps_from_gap_table(c, topic, toks, gaps, notes)
        _gaps_from_json_column(
            c,
            "SELECT suggested_queries_json FROM work_gap_cache",
            "suggested_queries_json",
            "suggested_queries",
            toks,
            gaps,
            notes,
            "gap cache",
        )
        _gaps_from_json_column(
            c,
            "SELECT gaps FROM topic_profiles",
            "gaps",
            "topic_profile",
            toks,
            gaps,
            notes,
            "topic profiles",
        )
        c.close()
    else:
        notes.append("Orivellum DB absent — no detected gaps available.")

    seen, unique = set(), []
    for g in gaps:
        key = " ".join(g["query"].lower().split())
        if key not in seen:
            seen.add(key)
            unique.append(g)
    if len(unique) < 3:
        for facet in (
            f"{topic} — overview and key concepts",
            f"{topic} — foundational principles and definitions",
            f"{topic} — common misconceptions and criticisms",
            f"{topic} — current state and recent developments",
        ):
            key = " ".join(facet.lower().split())
            if key not in seen:
                seen.add(key)
                unique.append({"query": facet, "origin": "seed_facet"})
        notes.append(
            "Fewer than 3 detected gaps matched this topic — deterministic seed "
            "facets added (labeled seed_facet, not detected gaps)."
        )
    return unique[:MAX_GAPS], notes


# ── plan ─────────────────────────────────────────────────────────────────────


def plan(target, run_dir):  # noqa: ARG001 (run_dir: harness contract)
    topic = str(target).strip()
    if not topic:
        raise ValueError('research needs a topic: --target "your topic"')
    inv = inventory(topic)
    gaps, gap_notes = intake_gaps(topic)
    units = [
        {
            "kind": "inventory",
            "ref": f"inventory::{topic[:60]}",
            "payload": {"topic": topic, "inventory": inv},
        }
    ]
    for g in gaps:
        units.append(
            {"kind": "gap", "ref": f"gap::{g['query'][:80]}", "payload": {"topic": topic, **g}}
        )
    unavailable = []
    if not inv["available"]:
        unavailable.append("corpus inventory (Orivellum DB not found)")
    if not os.environ.get("TAVILY_API_KEY", "").strip():
        unavailable.append("web search (TAVILY_API_KEY not set — gap units will fail, not fake)")
    return {
        "topic": topic,
        "inventory_counts": inv["counts"],
        "inventory_notes": inv["notes"] + gap_notes,
        "gap_origins": sorted({g["origin"] for g in gaps}),
        "units": units,
        "unavailable": unavailable,
        "meta": {"gaps": len(gaps)},
    }


# ── phase 3: per-gap research ────────────────────────────────────────────────


class _LLMResult:
    def __init__(self, text):
        self.text = text


def _planner_llm(messages, *, max_tokens=250, temperature=0.3, **_kw):
    """Adapter so websearch's query planner can use the runner's model client."""
    user = messages[-1]["content"] if messages else ""
    return _LLMResult(llm.chat("", user, max_tokens=max_tokens, temperature=temperature) or "")


def _run_search(query):
    """Call the existing Orivellum websearch pipeline (RRF fusion + BM25
    passage ranking + source-quality scoring). Returns (context, citations,
    provider_errors). Isolated here so tests can stub the network."""
    src = _orivellum_src()
    if src not in sys.path:
        sys.path.insert(0, src)
    from orivellum.capabilities import websearch

    context, citations, diag = websearch.research_web(
        query, profile=RESEARCH_PROFILE, llm_call_fn=_planner_llm
    )
    return context, citations, list(diag.provider_errors)


DIGEST_SYS = (
    "You are a research analyst producing a structured digest from web "
    "evidence for a knowledge base. Reply ONLY as JSON with keys: "
    "summary (one short paragraph), "
    "claims (list, max 10, each {claim, sources: ['S1', ...], quote, "
    "confidence: 'low'|'medium'|'high'} — quote is a verbatim excerpt copied "
    "EXACTLY from the cited source's passage, max 300 characters), "
    "not_found (list of things the question asked that the sources do not "
    "answer). Every claim MUST cite at least one [S#] source that supports "
    "it. If the sources do not answer the question, say so in not_found "
    "rather than guessing."
)


def _norm(s):
    return " ".join((s or "").split()).lower()


def verify_claims(raw_claims, context, citations):
    """Deterministic gate: a claim survives only if every cited source id is
    real and its quote appears verbatim in the fetched evidence. The model
    proposes; code disposes."""
    valid_ids = {c["id"] for c in citations}
    ctx = _norm(context)
    kept, dropped = [], 0
    for cl in (raw_claims or [])[:12]:
        if not isinstance(cl, dict):
            dropped += 1
            continue
        text = (cl.get("claim") or "").strip()
        sids = [s for s in (cl.get("sources") or []) if s in valid_ids]
        quote = (cl.get("quote") or "").strip()
        conf = cl.get("confidence")
        if conf not in ("low", "medium", "high"):
            conf = "low"
        if not text or not sids or not quote or _norm(quote) not in ctx:
            dropped += 1
            continue
        kept.append({"claim": text, "sources": sids, "quote": quote[:300], "confidence": conf})
    return kept, dropped


def _digest_dir(run_id):
    d = Path(CFG.runs_dir) / str(run_id) / "digests"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _worker_inventory(run_id, unit):
    p = unit["payload"]
    inv = p["inventory"]
    digest = {"kind": "inventory", "counts": inv["counts"], "notes": inv["notes"], "by": None}
    samples = "\n".join(k["text"] for k in inv["knowledge_hits"][:15] + inv["chunk_hits"][:5])
    if samples:
        out = llm.as_json(
            llm.chat(
                "Summarise what this corpus ALREADY holds on the topic "
                f"'{p['topic']}'. Reply as JSON: {{summary: 2-3 sentences, "
                "held: [subtopics clearly covered, max 8]}}. Describe only "
                "what is in the excerpts; never invent coverage.",
                shield.wrap(samples, "corpus excerpts"),
                max_tokens=300,
            )
        )
        if out:
            digest["summary"] = out.get("summary")
            digest["held"] = out.get("held")
            digest["by"] = "model"
    if digest["by"] is None:
        digest["by"] = "structure-only"
    store.note(run_id, f"inventory: {inv['counts']}")
    return digest


def _worker_gap(run_id, unit):
    p = unit["payload"]
    query = p["query"]
    if not os.environ.get("TAVILY_API_KEY", "").strip():
        raise RuntimeError("TAVILY_API_KEY not set — gap cannot be researched")
    context, citations, provider_errors = _run_search(query)

    retrieved = store.now()[:10]
    sources = [
        {
            "id": c["id"],
            "title": c.get("title") or "",
            "url": c["url"],
            "kind": c.get("kind", "web"),
            "retrieved": retrieved,
        }
        for c in citations
    ]

    # Injection screening on everything fetched, before any model call.
    for h in shield.screen(context, where=unit["ref"]):
        store.add_finding(
            run_id,
            "HIGH",
            "INJECT-WEB",
            unit["ref"],
            f"Injection-shaped text in fetched web content: {h['kind']}",
            detail=h["match"],
            source="shield",
            fix="Treat this source as hostile; do not let its claims through review.",
        )

    digest = {
        "kind": "gap",
        "query": query,
        "origin": p.get("origin"),
        "gap_id": p.get("gap_id"),
        "sources": sources,
        "claims": [],
        "dropped_claims": 0,
        "not_found": [],
        "provider_errors": provider_errors[:5],
        "by": "retrieval-only",
    }
    if not sources:
        digest["not_found"] = [f"No usable web sources retrieved for: {query}"]
    else:
        out = llm.as_json(
            llm.chat(
                DIGEST_SYS,
                f"Research question: {query}\n\n" + shield.wrap(context, "web evidence"),
                max_tokens=900,
            )
        )
        if out:
            kept, dropped = verify_claims(out.get("claims"), context, citations)
            digest["claims"] = kept
            digest["dropped_claims"] = dropped
            digest["summary"] = out.get("summary")
            nf = out.get("not_found")
            digest["not_found"] = [str(x)[:200] for x in nf][:6] if isinstance(nf, list) else []
            digest["by"] = "model"
            if dropped:
                store.add_finding(
                    run_id,
                    "MEDIUM",
                    "CLAIM-UNSOURCED",
                    unit["ref"],
                    f"{dropped} model claim(s) dropped: unverifiable source or quote",
                    source="verifier",
                    fix="Dropped, not kept. Nothing unsourced enters the digest.",
                )

    (_digest_dir(run_id) / f"gap-{unit['ord']:03d}.json").write_text(
        json.dumps(digest, indent=1), encoding="utf-8"
    )
    return digest


def unit_worker(run_id, unit):
    if unit["kind"] == "inventory":
        return _worker_inventory(run_id, unit)
    return _worker_gap(run_id, unit)


# ── phase 5: completeness accounting for the report ─────────────────────────


def _gap_digests(run_id):
    return [d for d in store.digests(run_id, kind="gap")]


def final_pass(run_id):
    ds = _gap_digests(run_id)
    run = store.get_run(run_id)
    planned = (run["plan"].get("meta") or {}).get("gaps", len(ds))
    covered = [d for d in ds if d["digest"].get("claims")]
    uncovered = [d for d in ds if not d["digest"].get("claims")]
    total_claims = sum(len(d["digest"]["claims"]) for d in covered)

    for d in uncovered:
        why = "; ".join(d["digest"].get("not_found") or []) or (
            "retrieval-only run (no model synthesis)"
            if d["digest"].get("by") == "retrieval-only"
            else "no claim survived source verification"
        )
        store.add_finding(
            run_id,
            "MEDIUM",
            "GAP-UNCOVERED",
            d["ref"],
            "Gap researched but NOT covered by a verified, sourced claim",
            detail=why[:300],
            source="metric",
            fix="Re-run with a sharper query, or research it by hand.",
            unique=True,
        )

    # Consolidated machine-readable artifact — the input to the writeback
    # milestone (T-M2). Claims here are proposals, never authority.
    out = {
        "topic": run["plan"].get("topic"),
        "gaps_planned": planned,
        "gaps_researched": len(ds),
        "gaps_covered": len(covered),
        "claims_total": total_claims,
        "digests": [d["digest"] for d in ds],
    }
    d = Path(CFG.runs_dir) / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "research_digests.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    lines = [
        f"- Gaps planned: **{planned}** · researched: **{len(ds)}** · "
        f"covered by at least one verified claim: **{len(covered)}**",
        f"- Verified claims: **{total_claims}** (every one carries a source URL, "
        "a retrieval date, and a quote that appears verbatim in the evidence)",
    ]
    if uncovered:
        lines.append("\n**Not found / not covered:**")
        for u in uncovered[:20]:
            nf = "; ".join(u["digest"].get("not_found") or []) or "no verified claim"
            lines.append(f"- `{u['digest']['query'][:80]}` — {nf[:160]}")
    inv = run["plan"].get("inventory_counts") or {}
    inv_line = (
        f"knowledge hits {inv.get('knowledge_hits', 0)}, chunk hits "
        f"{inv.get('chunk_hits', 0)}, matching documents {inv.get('matching_documents', 0)}"
    )
    return {
        "sections": [
            ("Research coverage — what was found and what was not", "\n".join(lines)),
            ("Corpus inventory (phase 1, deterministic)", inv_line),
        ]
    }


# ── phase 4: curriculum ──────────────────────────────────────────────────────

_REVIEW_OFFSETS = [1, 3, 7, 14]


def plan_items(run_id):
    """Curriculum nodes in the existing training_plan six-field item shape,
    with prerequisites and a spaced review schedule. Derived from verified
    digests by code — the model contributed only the (verified) claims."""
    run = store.get_run(run_id)
    topic = run["plan"].get("topic") or "topic"
    ds = _gap_digests(run_id)
    covered = [d for d in ds if d["digest"].get("claims")]
    if not ds:
        return []

    overview_topic = f"{topic} — what the corpus already holds"
    items = [
        dict(
            topic=overview_topic,
            why="Research starts from the existing corpus, never from zero.",
            evidence=[f"run {run_id} inventory"],
            read="The inventory section of this run's report.",
            check="Open the report and confirm the inventory counts match your corpus.",
            question=f"What does the corpus already cover on {topic}, and what was missing?",
            prereq=[],
            schedule={"start_day": 0, "review_after_days": _REVIEW_OFFSETS},
        )
    ]
    for i, d in enumerate(covered, 1):
        dg = d["digest"]
        first = dg["claims"][0]
        items.append(
            dict(
                topic=dg["query"][:120],
                why=(dg.get("summary") or "A named gap in the corpus, now researched.")[:300],
                evidence=[s["url"] for s in dg["sources"][:6]],
                read="; ".join(f"{s['title'] or s['url']} ({s['url']})" for s in dg["sources"][:2]),
                check="Open the cited source and confirm the quote supporting the first claim.",
                question=f"What evidence supports this, and what would falsify it: "
                f"{first['claim'][:180]}",
                prereq=[overview_topic],
                schedule={"start_day": i, "review_after_days": _REVIEW_OFFSETS},
            )
        )

    d = Path(CFG.runs_dir) / str(run_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "curriculum.json").write_text(
        json.dumps({"topic": topic, "items": items}, indent=1), encoding="utf-8"
    )
    return items
