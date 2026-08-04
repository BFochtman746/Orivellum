"""Semantic embeddings & hybrid search.

Uses the configured OpenAI-compatible serving endpoint's /embeddings route
(Lemonade and Ollama both expose one) with the configured embedder model.
Degrades gracefully: when the endpoint is unreachable or returns an error,
callers fall back to pure BM25/FTS search.

Vectors are stored in the existing `vectors` table (BLOB float32) and
compared in memory using dot-product on pre-normalized vectors (equivalent
to cosine similarity with no sqrt overhead) — no numpy dependency.

Performance strategy
--------------------
1. **Pre-normalization at cache load**: every vector is normalized to unit
   length once on first load. Subsequent cosine comparisons reduce to a
   single dot-product loop (no magnitude computation on each query).
2. **Process-level vector cache** keyed by ``(db_path, object_type)``:
   unpacked + normalized vectors are kept in a module-level dict so that
   each ``OrivellumDB`` instance has its own isolated cache and multiple
   instances in the same process never cross-contaminate each other.
3. **Version-based invalidation** (not count-based):
   ``bump_vector_cache_version(db_path, object_type)`` increments a
   monotonic integer counter for that key.  ``_load_vecs`` compares the
   cached version against the current counter; any mismatch triggers a
   full reload from SQLite.  The bump is called from:
   - ``db.store_vector`` — after every vector write, *including*
     DELETE+INSERT replacements that leave the row count unchanged
   - ``db.update_knowledge_review_status`` — so approved↔rejected
     eligibility changes are reflected immediately
   - the governance batch-review endpoint — because it writes
     ``review_status`` via raw SQL, bypassing the helper above
   Warm-path cost: two integer comparisons under a threading.Lock —
   zero SQL round-trips.
4. **Work-id filtering in Python**: the cache stores all vectors for a
   type; work_id filtering happens in the scoring loop so a single cache
   entry serves every query scope for that DB+type pair.
"""
from __future__ import annotations

import json
import logging
import struct
import threading
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

# ── Vector cache ──────────────────────────────────────────────────────────────
#
# Design decisions:
#
# 1. Cache key is (db_path, object_type) — not just object_type — so multiple
#    OrivellumDB instances in the same process (tests, multi-tenant) never
#    share or cross-contaminate each other's vectors.
#
# 2. Invalidation is version-based, not count-based.  db.store_vector calls
#    bump_vector_cache_version() after *every* write, including DELETE+INSERT
#    replacements of an existing vector (which leave the row count unchanged
#    and therefore would not be caught by count comparison).
#    db.update_knowledge_review_status also bumps "knowledge" so eligibility
#    changes (approved↔rejected) are reflected promptly.
#
# 3. The warm path compares two integers under a lock — zero SQL round-trips.
#
# 4. Vectors are pre-normalized at cache-load time so cosine similarity
#    reduces to a plain dot product during scoring.
#
# Thread safety: _cache_lock guards both dicts.  Cached entry lists are never
# mutated after they are stored — a fresh list is always built and then
# assigned.

_cache_lock = threading.Lock()
# (db_path, object_type) → (entries, version_at_load)
_vec_cache: dict[tuple[str, str], tuple[list, int]] = {}
# (db_path, object_type) → monotonically increasing write version
_version_counters: dict[tuple[str, str], int] = {}


def _norm_vec(vec: list[float]) -> list[float]:
    """Return a unit-length copy of *vec*; unchanged if the vector is zero."""
    mag = sum(x * x for x in vec) ** 0.5
    if mag == 0.0:
        return vec
    inv = 1.0 / mag
    return [x * inv for x in vec]


def _dot(a: list[float], b: list[float]) -> float:
    """Dot product of two vectors.  Equals cosine similarity when both are unit-length."""
    return sum(x * y for x, y in zip(a, b))


def bump_vector_cache_version(db_path: str, object_type: str) -> None:
    """Increment the write version for (db_path, object_type).

    Called from ``db.store_vector`` after every vector write (additions *and*
    replacements) and from ``db.update_knowledge_review_status`` so that
    eligibility changes invalidate the knowledge cache.

    The next ``_load_vecs`` call for this key will see a stale version and
    rebuild from SQLite.  The bump is O(1) under a lock — no DB access.
    """
    key = (db_path, object_type)
    with _cache_lock:
        _version_counters[key] = _version_counters.get(key, 0) + 1


def invalidate_vector_cache() -> None:
    """Test hook: wipe all in-memory vector caches and version counters."""
    with _cache_lock:
        _vec_cache.clear()
        _version_counters.clear()


def _load_vecs(db: "OrivellumDB", object_type: str,
               all_sql: str, all_params: tuple) -> list:
    """Return cached vector entries, rebuilding when the write version changed.

    *all_sql* must fetch ALL rows for the object_type (no work_id filter) so
    the single cache entry serves every query scope for that DB+type pair.

    Returns list of (object_id, field_dict, normalized_vec).

    Warm path: two integer comparisons under a lock, no SQL.
    Cold / stale path: one DB read, unpack, normalize, store.
    """
    key = (db._path, object_type)

    # Snapshot the version before we release the lock to start loading.
    # If a bump happens while we load, ver_before < current version after load,
    # so we store (entries, ver_before) which is LESS than the bumped version;
    # the next caller will see the mismatch and rebuild with the fresh data.
    with _cache_lock:
        ver_before = _version_counters.get(key, 0)
        cached = _vec_cache.get(key)
        if cached is not None and cached[1] == ver_before:
            return cached[0]  # warm hit — zero SQL

    # Cache miss or stale: load from DB outside the lock.
    with db._lock:
        rows = db._conn.execute(all_sql, all_params).fetchall()

    entries: list = []
    for r in rows:
        try:
            raw = unpack_vector(r["embedding"], r["dim"])
            nv  = _norm_vec(raw)
        except Exception:
            continue
        field = {k: r[k] for k in r.keys() if k not in ("embedding", "dim")}
        entries.append((field["object_id"], field, nv))

    with _cache_lock:
        _vec_cache[key] = (entries, ver_before)

    logger.debug("Vector cache rebuilt: db=%s type=%s n=%d",
                 db._path, object_type, len(entries))
    return entries


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

    Performance: vectors are loaded once from SQLite, pre-normalized, and kept
    in ``_vec_cache``.  The cache is invalidated by count-comparison whenever
    ``count_vectors`` changes (i.e. after any ``store_vector`` call).
    Work-id filtering is applied in-process so a single cache entry serves all
    query scopes for the same object_type.
    """
    # Short timeout: this sits on interactive search/chat paths, so a slow or
    # down endpoint must not stall the request (the breaker then skips retries).
    qvecs = embed_texts([query], timeout=_QUERY_TIMEOUT)
    if not qvecs:
        return []
    # Normalize the query vector so cosine similarity = dot product
    qvec = _norm_vec(qvecs[0])

    if object_type == "knowledge":
        # Full canonical knowledge shape so semantic hits are interchangeable
        # with FTS hits downstream (provenance, meta, etc.).
        # Load ALL knowledge vectors (no work_id filter) — filtered in Python.
        all_sql = """SELECT v.object_id, v.embedding, v.dim,
                            k.text, k.subject, k.predicate, k.object, k.kind,
                            k.work_id, k.confidence, k.review_status,
                            k.source_doc_id, k.source_chunk_id, k.source_offset,
                            k.meta, k.created_at
                     FROM vectors v JOIN knowledge k ON k.id = v.object_id
                     WHERE v.object_type='knowledge'
                       AND k.review_status != 'rejected'"""
    else:
        all_sql = """SELECT v.object_id, v.embedding, v.dim,
                            c.text, c.doc_id, d.title AS doc_title, d.work_id
                     FROM vectors v
                     JOIN chunks c ON c.id = v.object_id
                     JOIN documents d ON d.id = c.doc_id
                     WHERE v.object_type='chunk'"""

    entries = _load_vecs(db, object_type, all_sql, ())

    _NOISE_FLOOR = 0.25
    scored = []
    for obj_id, fields, nvec in entries:
        # Skip entries that don't belong to the requested work scope
        if work_id and fields.get("work_id") != work_id:
            continue
        s = _dot(qvec, nvec)
        if s > _NOISE_FLOOR:
            d = dict(fields)
            d["id"] = obj_id
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
