"""Semantic embeddings & hybrid search.

Uses the configured OpenAI-compatible serving endpoint's /embeddings route
(Lemonade and Ollama both expose one) with the configured embedder model.
Degrades gracefully: when the endpoint is unreachable or returns an error,
callers fall back to pure BM25/FTS search.

Vectors are stored in the existing `vectors` table (BLOB float32) and
compared with pure-Python cosine similarity — no numpy dependency. At
Orivellum's scale (thousands of chunks, not millions) a linear scan is
milliseconds.
"""
from __future__ import annotations

import json
import logging
import struct
import time
import urllib.request
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.embeddings")

_EMBED_TIMEOUT = 30         # batch/backfill calls (background, latency-tolerant)
_QUERY_TIMEOUT = 4          # interactive query-time embedding (search, chat)
_MAX_TEXT_LEN = 6000        # chars per embedded text
_BACKFILL_BATCH = 16        # texts per API call

# Circuit breaker: after a failed embeddings call, skip further attempts for a
# short window so a down/unconfigured endpoint never adds per-request latency
# to search or chat (they fall back to BM25 instantly during the cooldown).
_FAIL_COOLDOWN = 60.0       # seconds
_unavailable_until = 0.0


def _reset_circuit_breaker() -> None:
    """Test hook: clear the failure cooldown."""
    global _unavailable_until
    _unavailable_until = 0.0


def _serving():
    from orivellum.api._deps import get_config
    cfg = get_config()
    return cfg.serving.base_url.rstrip("/"), getattr(cfg.serving, "embedder_model",
                                                     "Qwen3-Embedding-0.6B")


def embed_texts(texts: list[str],
                timeout: float = _EMBED_TIMEOUT) -> list[list[float]] | None:
    """Embed a batch of texts. Returns None when the endpoint is unavailable.

    A failed call opens a short cooldown during which subsequent calls return
    None immediately (no network attempt), so interactive paths keep BM25-level
    latency while the endpoint is down. Any success closes the cooldown.
    """
    global _unavailable_until
    if not texts:
        return []
    if time.monotonic() < _unavailable_until:
        return None
    base_url, model = _serving()
    payload = json.dumps({
        "model": model,
        "input": [t[:_MAX_TEXT_LEN] for t in texts],
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/embeddings", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        vecs = [d.get("embedding") for d in items]
        if len(vecs) != len(texts) or any(not v for v in vecs):
            logger.warning("Embeddings response malformed (%d/%d vectors)",
                           len(vecs), len(texts))
            return None
        _unavailable_until = 0.0
        return vecs
    except Exception as exc:
        _unavailable_until = time.monotonic() + _FAIL_COOLDOWN
        logger.debug("Embeddings unavailable (cooldown %.0fs): %s",
                     _FAIL_COOLDOWN, exc)
        return None


def embed_text(text: str) -> list[float] | None:
    """Embed a single text. Returns None when the endpoint is unavailable."""
    vecs = embed_texts([text])
    return vecs[0] if vecs else None


def pack_vector(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vector(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def backfill_embeddings(db: "OrivellumDB", max_items: int = 200) -> int:
    """Embed chunks and knowledge items that don't have vectors yet.

    Returns the number of new vectors stored (0 when endpoint unavailable).
    """
    embedded = 0
    for object_type, sql in (
        ("chunk",
         """SELECT c.id, c.text FROM chunks c
            LEFT JOIN vectors v ON v.object_id = c.id AND v.object_type='chunk'
            WHERE v.id IS NULL AND length(c.text) > 40 LIMIT ?"""),
        ("knowledge",
         """SELECT k.id, k.text FROM knowledge k
            LEFT JOIN vectors v ON v.object_id = k.id AND v.object_type='knowledge'
            WHERE v.id IS NULL AND k.review_status != 'rejected'
              AND length(k.text) > 20 LIMIT ?"""),
    ):
        with db._lock:
            rows = db._conn.execute(sql, (max_items,)).fetchall()
        for i in range(0, len(rows), _BACKFILL_BATCH):
            batch = rows[i:i + _BACKFILL_BATCH]
            vecs = embed_texts([r["text"] for r in batch])
            if vecs is None:
                return embedded  # endpoint down — stop quietly
            for r, v in zip(batch, vecs):
                db.store_vector(r["id"], object_type, pack_vector(v), len(v))
                embedded += 1
    if embedded:
        logger.info("Embedded %d new object(s)", embedded)
    return embedded


def embed_chunks_for_doc(doc_id: str, db: "OrivellumDB") -> int:
    """Embed all chunks of one document that don't have vectors yet.

    Called from the pipeline right after chunking so fresh documents become
    semantically searchable without waiting for the nightly backfill.  Safe to
    run in a daemon thread; returns the number of vectors stored (0 when the
    embeddings endpoint is unavailable).
    """
    with db._lock:
        rows = db._conn.execute(
            """SELECT c.id, c.text FROM chunks c
               LEFT JOIN vectors v ON v.object_id = c.id AND v.object_type='chunk'
               WHERE c.doc_id=? AND v.id IS NULL AND length(c.text) > 40""",
            (doc_id,),
        ).fetchall()
    embedded = 0
    for i in range(0, len(rows), _BACKFILL_BATCH):
        batch = rows[i:i + _BACKFILL_BATCH]
        vecs = embed_texts([r["text"] for r in batch])
        if vecs is None:
            return embedded  # endpoint down — nightly backfill will catch up
        for r, v in zip(batch, vecs):
            db.store_vector(r["id"], "chunk", pack_vector(v), len(v))
            embedded += 1
    if embedded:
        logger.info("Embedded %d chunk(s) for doc %s", embedded, doc_id[:8])
    return embedded


def semantic_search(query: str, db: "OrivellumDB", object_type: str = "knowledge",
                    limit: int = 10, work_id: str | None = None) -> list[dict]:
    """Cosine-rank stored vectors against the query embedding.

    Returns [] when embeddings are unavailable so callers can fall back to FTS.
    Each hit: {"id", "score", plus the joined text/subject columns}.
    """
    # Short timeout: this sits on interactive search/chat paths, so a slow or
    # down endpoint must not stall the request (the breaker then skips retries).
    qvecs = embed_texts([query], timeout=_QUERY_TIMEOUT)
    if not qvecs:
        return []
    qvec = qvecs[0]

    if object_type == "knowledge":
        # Select the full canonical knowledge shape so semantic hits are
        # interchangeable with FTS hits downstream (provenance, meta, etc.).
        join_sql = """SELECT v.object_id, v.embedding, v.dim,
                             k.text, k.subject, k.predicate, k.object, k.kind,
                             k.work_id, k.confidence, k.review_status,
                             k.source_doc_id, k.source_chunk_id, k.source_offset,
                             k.meta, k.created_at
                      FROM vectors v JOIN knowledge k ON k.id = v.object_id
                      WHERE v.object_type='knowledge'
                        AND k.review_status IN ('auto','approved')"""
        params: tuple = ()
        if work_id:
            join_sql += " AND k.work_id = ?"
            params = (work_id,)
    else:
        join_sql = """SELECT v.object_id, v.embedding, v.dim,
                             c.text, c.doc_id, d.title AS doc_title, d.work_id
                      FROM vectors v
                      JOIN chunks c ON c.id = v.object_id
                      JOIN documents d ON d.id = c.doc_id
                      WHERE v.object_type='chunk'"""
        params = ()
        if work_id:
            join_sql += " AND d.work_id = ?"
            params = (work_id,)

    with db._lock:
        rows = db._conn.execute(join_sql, params).fetchall()

    scored = []
    for r in rows:
        try:
            vec = unpack_vector(r["embedding"], r["dim"])
        except Exception:
            continue
        s = cosine(qvec, vec)
        if s > 0.25:  # noise floor
            d = {k: r[k] for k in r.keys() if k not in ("embedding", "dim")}
            d["id"] = d.pop("object_id")
            d["score"] = round(s, 4)
            scored.append(d)
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:limit]


_RRF_K = 60  # standard reciprocal-rank-fusion constant


def hybrid_search_chunks(query: str, db: "OrivellumDB", limit: int = 10,
                         work_id: str | None = None) -> list[dict]:
    """Hybrid chunk retrieval: FTS5 BM25 + semantic cosine, fused with RRF.

    Each result carries ``rrf_score`` and ``match_type`` ("keyword",
    "semantic", or "both").  Degrades gracefully:
    - embeddings unavailable → pure BM25 results
    - FTS finds nothing (short/conceptual query) → pure semantic results
    """
    fetch = min(max(limit * 2, 20), 50)
    try:
        fts = db.search_chunks(query, work_id=work_id, limit=fetch)
    except Exception:
        fts = []
    sem = semantic_search(query, db, "chunk", limit=fetch, work_id=work_id)

    if not sem:
        for r in fts:
            r.setdefault("match_type", "keyword")
        return fts[:limit]
    if not fts:
        for r in sem:
            r.setdefault("match_type", "semantic")
        return sem[:limit]

    # Reciprocal rank fusion: score = Σ 1/(k + rank) over both ranked lists.
    fused: dict[str, dict] = {}
    for rank, hit in enumerate(fts):
        cid = hit.get("id")
        if not cid:
            continue
        entry = fused.setdefault(cid, {"hit": hit, "score": 0.0, "sources": set()})
        entry["score"] += 1.0 / (_RRF_K + rank + 1)
        entry["sources"].add("keyword")
    for rank, hit in enumerate(sem):
        cid = hit.get("id")
        if not cid:
            continue
        entry = fused.setdefault(cid, {"hit": hit, "score": 0.0, "sources": set()})
        entry["score"] += 1.0 / (_RRF_K + rank + 1)
        entry["sources"].add("semantic")
        # Prefer the FTS hit dict (has snippet/bm25) but carry the cosine score
        entry["hit"].setdefault("score", hit.get("score"))

    ranked = sorted(fused.values(), key=lambda e: e["score"], reverse=True)
    results: list[dict] = []
    for e in ranked[:limit]:
        hit = e["hit"]
        hit["rrf_score"] = round(e["score"], 6)
        hit["match_type"] = "both" if len(e["sources"]) == 2 else next(iter(e["sources"]))
        results.append(hit)
    return results


def hybrid_search_knowledge(query: str, db: "OrivellumDB", limit: int = 10,
                            work_id: str | None = None) -> list[dict]:
    """Merge FTS (keyword) and semantic hits, deduplicated, semantic-first order
    interleaved by normalized rank. Falls back to pure FTS when embeddings are off."""
    # Over-fetch both sources so overlap between them can't leave the merged
    # result short of `limit`.
    fetch = min(limit * 2, 50)
    fts = db.search_knowledge(query, work_id=work_id, limit=fetch)
    sem = semantic_search(query, db, "knowledge", limit=fetch, work_id=work_id)
    if not sem:
        return fts[:limit]
    seen: set = set()
    merged: list[dict] = []
    # Interleave: semantic result, then FTS result, alternating
    for pair in zip(sem + [None] * len(fts), fts + [None] * len(sem)):
        for hit in pair:
            if hit and hit.get("id") not in seen:
                seen.add(hit["id"])
                merged.append(hit)
    return merged[:limit]
