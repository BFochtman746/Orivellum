"""Golden-set retrieval evaluation — nDCG@k / Recall@k per retrieval channel.

The golden set is a curated list of query → relevant-id judgments stored in
``golden_queries`` (schema v109).  Each golden has a ``kind``:

  chunk      relevance is judged at the DOCUMENT level — ``relevant_ids``
             holds doc ids; ranked ids come from chunk hits' ``doc_id``
             (deduplicated, first occurrence keeps the rank).
  knowledge  relevance is judged at the knowledge-item level —
             ``relevant_ids`` holds knowledge item ids.

``evaluate_retrieval`` scores every enabled channel per golden:

  fts        SQLite FTS5 (db.search_chunks / db.search_knowledge)
  semantic   vector search (capabilities.embeddings.semantic_search)
  hybrid     weighted-RRF fusion (hybrid_search_chunks / hybrid_search_knowledge)

A channel that is unavailable (e.g. embeddings endpoint down) is reported as
``null`` — "not measured", never zero.  Summaries are persisted via
``bench.save_bench_run(kind="retrieval_eval")`` so improvements are provable
run over run.
"""

from __future__ import annotations

import json
import logging
import math
import uuid
from typing import Any

logger = logging.getLogger("orivellum.evalset")

_KINDS = ("chunk", "knowledge")


# ──────────────────────────────────────────────────────────────────────────────
# Metrics (pure)
# ──────────────────────────────────────────────────────────────────────────────


def _dcg(gains: list[float]) -> float:
    return sum(g / math.log2(i + 2) for i, g in enumerate(gains))


def ndcg_at_k(ranked_ids: list, relevant_ids: list, k: int = 5) -> float:
    """Binary-relevance nDCG@k."""
    rel = set(relevant_ids)
    if not rel:
        return 0.0
    gains = [1.0 if r in rel else 0.0 for r in ranked_ids[:k]]
    ideal = [1.0] * min(len(rel), k)
    idcg = _dcg(ideal)
    return (_dcg(gains) / idcg) if idcg else 0.0


def recall_at_k(ranked_ids: list, relevant_ids: list, k: int = 5) -> float:
    rel = set(relevant_ids)
    if not rel:
        return 0.0
    return len(rel & set(ranked_ids[:k])) / len(rel)


# ──────────────────────────────────────────────────────────────────────────────
# Golden CRUD
# ──────────────────────────────────────────────────────────────────────────────


def list_goldens(db: Any, kind: str | None = None) -> list[dict]:
    q = (
        "SELECT id, query, kind, relevant_ids, work_id, notes, source, "
        "created_at FROM golden_queries"
    )
    args: list = []
    if kind:
        q += " WHERE kind=?"
        args.append(kind)
    q += " ORDER BY created_at DESC"
    with db._lock:
        rows = db._conn.execute(q, args).fetchall()
    return [_golden_dict(r) for r in rows]


def add_golden(
    db: Any,
    *,
    query: str,
    kind: str,
    relevant_ids: list,
    work_id: str | None = None,
    notes: str = "",
    source: str = "manual",
) -> dict:
    if kind not in _KINDS:
        raise ValueError(f"kind must be one of {_KINDS}")
    query = (query or "").strip()
    if not query:
        raise ValueError("query must be non-empty")
    if not relevant_ids:
        raise ValueError("relevant_ids must be non-empty")
    gid = str(uuid.uuid4())
    with db._lock:
        db._conn.execute(
            "INSERT INTO golden_queries (id, query, kind, relevant_ids,"
            " work_id, notes, source) VALUES (?,?,?,?,?,?,?)",
            (
                gid,
                query,
                kind,
                json.dumps([str(r) for r in relevant_ids]),
                work_id,
                notes or "",
                source,
            ),
        )
        db._conn.commit()
        row = db._conn.execute(
            "SELECT id, query, kind, relevant_ids, work_id, notes, source,"
            " created_at FROM golden_queries WHERE id=?",
            (gid,),
        ).fetchone()
    return _golden_dict(row)


def delete_golden(db: Any, golden_id: str) -> bool:
    with db._lock:
        cur = db._conn.execute("DELETE FROM golden_queries WHERE id=?", (golden_id,))
        db._conn.commit()
    return cur.rowcount > 0


def _golden_dict(row) -> dict:
    try:
        rel = json.loads(row[3] or "[]")
    except json.JSONDecodeError:
        rel = []
    return {
        "id": row[0],
        "query": row[1],
        "kind": row[2],
        "relevant_ids": rel,
        "work_id": row[4],
        "notes": row[5],
        "source": row[6],
        "created_at": row[7],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Auto-seeding — bootstrap goldens from distinctive stored content
# ──────────────────────────────────────────────────────────────────────────────


def auto_seed_goldens(db: Any, *, n: int = 20) -> dict:
    """Propose goldens by sampling distinctive phrases from stored chunks.

    Each sampled chunk yields one golden of kind ``chunk`` whose relevant id
    is the chunk's source document.  This bootstraps measurement on day one;
    user-curated goldens (real questions) should replace these over time.
    Idempotent-ish: skips queries that already exist verbatim.
    """
    n = max(1, min(int(n), 50))
    with db._lock:
        rows = db._conn.execute(
            "SELECT c.doc_id, c.text FROM chunks c "
            "WHERE length(c.text) > 300 ORDER BY RANDOM() LIMIT ?",
            (n * 2,),
        ).fetchall()
        existing = {r[0] for r in db._conn.execute("SELECT query FROM golden_queries").fetchall()}
    created: list[dict] = []
    for doc_id, text in rows:
        if len(created) >= n:
            break
        phrase = _distinctive_phrase(text)
        if not phrase or phrase in existing:
            continue
        try:
            created.append(
                add_golden(
                    db,
                    query=phrase,
                    kind="chunk",
                    relevant_ids=[doc_id],
                    notes="auto-seeded from chunk sample",
                    source="auto",
                )
            )
            existing.add(phrase)
        except ValueError:
            continue
    return {"created": len(created), "goldens": created}


def _distinctive_phrase(text: str, words: int = 8) -> str:
    """Pick a mid-text phrase — starts of chunks are often boilerplate."""
    toks = text.split()
    if len(toks) < words + 4:
        return ""
    start = len(toks) // 3
    return " ".join(toks[start : start + words])


# ──────────────────────────────────────────────────────────────────────────────
# Evaluation
# ──────────────────────────────────────────────────────────────────────────────


def evaluate_retrieval(db: Any, *, k: int = 5, label: str = "") -> dict:
    """Score every golden across fts / semantic / hybrid channels.

    Returns and persists a summary with per-channel average nDCG@k and
    Recall@k.  Channels that raise (embeddings down, etc.) are recorded as
    unavailable and excluded from averages rather than scored 0.
    """
    from orivellum.capabilities import embeddings as _emb
    from orivellum.capabilities.bench import save_bench_run

    k = max(1, min(int(k), 20))
    goldens = list_goldens(db)
    rows: list[dict] = []
    channel_errors: dict[str, str] = {}

    for g in goldens:
        row: dict = {"golden_id": g["id"], "query": g["query"], "kind": g["kind"], "channels": {}}
        for channel in ("fts", "semantic", "hybrid"):
            if channel in channel_errors:
                row["channels"][channel] = None
                continue
            try:
                ranked = _ranked_ids(db, _emb, channel, g, k)
            except Exception as exc:
                channel_errors[channel] = f"{type(exc).__name__}: {exc}"[:200]
                logger.warning("eval channel %s failed: %s", channel, channel_errors[channel])
                row["channels"][channel] = None
                continue
            if ranked is None:
                row["channels"][channel] = None
                continue
            row["channels"][channel] = {
                "ids": ranked[:k],
                "ndcg": round(ndcg_at_k(ranked, g["relevant_ids"], k), 3),
                "recall": round(recall_at_k(ranked, g["relevant_ids"], k), 3),
            }
        rows.append(row)

    def _avg(channel: str, metric: str):
        vals = [r["channels"][channel][metric] for r in rows if r["channels"].get(channel)]
        return round(sum(vals) / len(vals), 3) if vals else None

    summary = {
        "k": k,
        "n_goldens": len(goldens),
        "channels": {
            ch: {
                "ndcg": _avg(ch, "ndcg"),
                "recall": _avg(ch, "recall"),
                "scored": sum(1 for r in rows if r["channels"].get(ch)),
                "error": channel_errors.get(ch),
            }
            for ch in ("fts", "semantic", "hybrid")
        },
        "rows": rows,
    }
    if goldens:
        stored = save_bench_run(db, "retrieval_eval", label, summary)
        summary["run_id"] = stored["id"]
        summary["ts"] = stored["ts"]
    return summary


def _ranked_ids(db: Any, emb_mod: Any, channel: str, golden: dict, k: int) -> list | None:
    """Ranked candidate ids for one golden on one channel.

    chunk goldens are judged at doc level (ids are doc_ids, deduplicated in
    rank order); knowledge goldens use knowledge item ids.  Returns ``None``
    when the channel yields nothing scoreable (treated as unavailable only if
    it raised — an empty result list is a legitimate score of 0).
    """
    fetch = max(k * 3, 10)  # over-fetch: doc-level dedup shrinks the list
    query = golden["query"]
    if golden["kind"] == "chunk":
        if channel == "fts":
            hits = db.search_chunks(query, limit=fetch)
        elif channel == "semantic":
            hits = emb_mod.semantic_search(query, db, object_type="chunk", limit=fetch)
        else:
            hits = emb_mod.hybrid_search_chunks(query, db, limit=fetch)
        return _dedup([h.get("doc_id") for h in hits if h.get("doc_id")])
    # knowledge
    if channel == "fts":
        hits = db.search_knowledge(query, limit=fetch)
    elif channel == "semantic":
        hits = emb_mod.semantic_search(query, db, object_type="knowledge", limit=fetch)
    else:
        hits = emb_mod.hybrid_search_knowledge(query, db, limit=fetch)
    return _dedup([h.get("id") for h in hits if h.get("id")])


def _dedup(ids: list) -> list:
    seen: set = set()
    out: list = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out
