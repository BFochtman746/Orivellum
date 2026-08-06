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


# ── Dimensionality mismatch detection ─────────────────────────────────────────

def get_stored_vector_dim(db: "OrivellumDB") -> int | None:
    """Return the dimensionality of currently stored vectors, or None if empty.

    Samples the most common ``dim`` value across all rows in the ``vectors``
    table.  Using MODE rather than a single row avoids being misled by a stray
    legacy entry with a different size.
    """
    try:
        with db._lock:
            row = db._conn.execute(
                "SELECT dim, COUNT(*) AS cnt FROM vectors"
                " WHERE dim IS NOT NULL GROUP BY dim ORDER BY cnt DESC LIMIT 1"
            ).fetchone()
        return int(row["dim"]) if row else None
    except Exception:
        return None


def get_live_embedder_dim(timeout: float = 8.0) -> int | None:
    """Return the output dimensionality of the configured embedder, or None.

    Sends a minimal probe embedding and counts the returned vector length.
    Does NOT touch the circuit breaker — this is a deliberate health check.
    """
    try:
        base_url, model = _serving()
        payload = json.dumps({
            "model": model,
            "input": ["dim probe"],
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/embeddings", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        items = data.get("data", [])
        if items and items[0].get("embedding"):
            return len(items[0]["embedding"])
    except Exception:
        pass
    return None


def count_embeddable_items(db: "OrivellumDB") -> dict[str, int]:
    """Return the total and already-vectorized counts per object type.

    Used by the reindex status endpoint so the UI can show progress.
    """
    try:
        with db._lock:
            c = db._conn
            chunk_total = c.execute(
                "SELECT COUNT(*) FROM chunks WHERE length(text) > 40"
            ).fetchone()[0]
            chunk_done = c.execute(
                "SELECT COUNT(*) FROM vectors WHERE object_type='chunk'"
            ).fetchone()[0]
            know_total = c.execute(
                "SELECT COUNT(*) FROM knowledge WHERE review_status != 'rejected'"
                "  AND length(text) > 20"
            ).fetchone()[0]
            know_done = c.execute(
                "SELECT COUNT(*) FROM vectors WHERE object_type='knowledge'"
            ).fetchone()[0]
            cc_total = c.execute(
                "SELECT COUNT(*) FROM conversation_chunks WHERE length(text) > 30"
            ).fetchone()[0]
            cc_done = c.execute(
                "SELECT COUNT(*) FROM vectors WHERE object_type='conv_chunk'"
            ).fetchone()[0]
        return {
            "chunk_total": chunk_total, "chunk_done": chunk_done,
            "knowledge_total": know_total, "knowledge_done": know_done,
            "conv_chunk_total": cc_total, "conv_chunk_done": cc_done,
            "total": chunk_total + know_total + cc_total,
            "done":  chunk_done + know_done + cc_done,
        }
    except Exception:
        return {"total": 0, "done": 0}


def run_full_reindex(db: "OrivellumDB", *, batch_size: int = 64) -> int:
    """Delete all vectors then re-embed everything in batches.

    Designed to run in a background daemon thread.  Progress is written to the
    ``reindex_done`` setting after each batch so the status endpoint can report
    live progress.  Hybrid FTS5 search continues to serve queries during the
    reindex — the vector cache simply returns empty until rows reappear.

    Returns the total number of new vectors stored.
    """
    try:
        # 1. Clear all existing vectors (avoids dim-space mixing)
        with db._lock:
            db._conn.execute("DELETE FROM vectors")
            db._conn.commit()
        invalidate_vector_cache()
        logger.info("Reindex: cleared all vectors — starting re-embedding")

        # 2. Count embeddable items and write initial progress
        counts = count_embeddable_items(db)
        total = counts["total"]
        db.set_setting("reindex_total", str(total))
        db.set_setting("reindex_done", "0")

        # 3. Re-embed in batches — call backfill_embeddings repeatedly until done
        embedded_total = 0
        while True:
            n = backfill_embeddings(db, max_items=batch_size)
            if n == 0:
                break   # endpoint down or nothing left
            embedded_total += n
            db.set_setting("reindex_done", str(embedded_total))
            logger.debug("Reindex progress: %d / %d", embedded_total, total)

        logger.info("Reindex complete: %d vectors written", embedded_total)
        return embedded_total
    finally:
        db.set_setting("reindex_running", "false")


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
    """Embed chunks, knowledge items, and conversation chunks that lack vectors.

    Extends to cover ``conversation_chunks`` so exchanges stored during an
    embedding-endpoint outage are automatically back-filled once the endpoint
    recovers — ensuring semantic recall works even for older conversations.

    Returns the number of new vectors stored (0 when endpoint unavailable).
    """
    embedded = 0
    for object_type, sql, _use_prefix in (
        # Chunks: fetch context_prefix so we embed prefix+text when available.
        ("chunk",
         """SELECT c.id, c.text, c.context_prefix FROM chunks c
            LEFT JOIN vectors v ON v.object_id = c.id AND v.object_type='chunk'
            WHERE v.id IS NULL AND length(c.text) > 40 LIMIT ?""",
         True),
        ("knowledge",
         """SELECT k.id, k.text, NULL as context_prefix FROM knowledge k
            LEFT JOIN vectors v ON v.object_id = k.id AND v.object_type='knowledge'
            WHERE v.id IS NULL AND k.review_status != 'rejected'
              AND length(k.text) > 20 LIMIT ?""",
         False),
        ("conv_chunk",
         """SELECT cc.id, cc.text, NULL as context_prefix FROM conversation_chunks cc
            LEFT JOIN vectors v ON v.object_id = cc.id AND v.object_type='conv_chunk'
            WHERE v.id IS NULL AND length(cc.text) > 30 LIMIT ?""",
         False),
    ):
        with db._lock:
            rows = db._conn.execute(sql, (max_items,)).fetchall()
        for i in range(0, len(rows), _BACKFILL_BATCH):
            batch = rows[i:i + _BACKFILL_BATCH]
            # For chunks: prepend any stored context prefix to the embedded text
            # so the vector reflects the enriched representation used at query time.
            if _use_prefix:
                texts = [
                    ((r["context_prefix"] + "\n\n" + r["text"])
                     if r["context_prefix"] else r["text"])
                    for r in batch
                ]
            else:
                texts = [r["text"] for r in batch]
            vecs = embed_texts(texts)
            if vecs is None:
                return embedded  # endpoint down — stop quietly
            for r, v in zip(batch, vecs):
                db.store_vector(r["id"], object_type, pack_vector(v), len(v))
                embedded += 1
    if embedded:
        logger.info("Embedded %d new object(s)", embedded)
    return embedded


def _mark_chunk_embedding_method(db: "OrivellumDB", chunk_id: str, method: str) -> None:
    """Mark a chunk's embedding_method column.  Non-fatal — never raises."""
    try:
        with db._lock:
            db._conn.execute(
                "UPDATE chunks SET embedding_method=? WHERE id=?", (method, chunk_id)
            )
            db._conn.commit()
    except Exception:
        pass


def embed_chunks_for_doc(doc_id: str, db: "OrivellumDB") -> int:
    """Embed all chunks of one document that don't have vectors yet.

    When ``use_late_chunking`` is enabled in DB settings **and** the embeddings
    endpoint confirms per-token output support (probe), the late-chunking path
    runs first — encoding the full document once and mean-pooling token vectors
    within each chunk's character span.  Chunks outside the embedding window
    (``char_start >= _MAX_TEXT_LEN``) or chunks without stored offsets are
    **always** caught by the standard per-chunk fallback that follows.

    The standard path marks each stored vector with
    ``embedding_method='standard'`` so that late-chunked and standard-chunked
    rows can be distinguished in the schema.  Pre-migration rows (NULL method)
    represent chunks that existed before v82.

    Safe to run in a daemon thread.  Shutdown-safe: catches
    ``sqlite3.ProgrammingError`` (closed DB) so daemon threads never emit an
    unhandled exception during app shutdown or test teardown.
    """
    import sqlite3 as _sqlite3
    try:
        # ── Late-chunking path (gated) ─────────────────────────────────────
        # Runs first when the setting is on and the endpoint is probed as
        # capable.  Does NOT return early — a second standard-path pass
        # immediately after ensures any chunks outside the encoding window
        # (char_start ≥ _MAX_TEXT_LEN) always receive a vector.
        use_late = db.get_setting("use_late_chunking", "false").lower() == "true"
        late_stored = 0
        if use_late and probe_late_chunking_support():
            late_stored = _embed_chunks_late(doc_id, db)
            if late_stored:
                logger.info("Late-chunked %d chunk(s) for doc %s", late_stored, doc_id[:8])

        # ── Standard per-chunk embedding ───────────────────────────────────
        # Picks up every chunk that still has no vector: those with
        # char_start outside the late-chunking window, those without stored
        # offsets, and all chunks when late chunking is disabled or unsupported.
        with db._lock:
            rows = db._conn.execute(
                """SELECT c.id, c.text, c.context_prefix FROM chunks c
                   LEFT JOIN vectors v ON v.object_id = c.id AND v.object_type='chunk'
                   WHERE c.doc_id=? AND v.id IS NULL AND length(c.text) > 40""",
                (doc_id,),
            ).fetchall()
        embedded = 0
        for i in range(0, len(rows), _BACKFILL_BATCH):
            batch = rows[i:i + _BACKFILL_BATCH]
            # Prepend context_prefix when available so the vector reflects the
            # enriched representation used at retrieval time.
            texts = [
                ((r["context_prefix"] + "\n\n" + r["text"])
                 if r["context_prefix"] else r["text"])
                for r in batch
            ]
            vecs = embed_texts(texts)
            if vecs is None:
                # Endpoint down — stop here; nightly backfill will catch up.
                break
            for r, v in zip(batch, vecs):
                db.store_vector(r["id"], "chunk", pack_vector(v), len(v))
                _mark_chunk_embedding_method(db, r["id"], "standard")
                embedded += 1

        total = late_stored + embedded
        if total:
            logger.info(
                "Embedded doc %s — %d late + %d standard chunk(s)",
                doc_id[:8], late_stored, embedded,
            )
        return total
    except _sqlite3.ProgrammingError:
        # DB was closed (e.g. during test teardown or app shutdown) before the
        # daemon thread reached the DB call.  Non-fatal: nightly backfill handles
        # any missing vectors.
        logger.debug("embed_chunks_for_doc: DB closed before embed for %s (non-fatal)", doc_id[:8])
        return 0
    except Exception as exc:
        logger.debug("embed_chunks_for_doc: non-fatal error for %s: %s", doc_id[:8], exc)
        return 0


def _embed_chunks_late(doc_id: str, db: "OrivellumDB") -> int:
    """Internal: attempt the late-chunking path for one document.

    Loads the document's extracted text and all unembedded chunk spans, then
    calls :func:`embed_with_late_chunking`.  Returns 0 when the document has
    no extractedtext yet (pipeline stores it in step 4, after chunking in
    step 2 — the embedding step runs asynchronously so extracted_text will
    have been committed by the time the daemon thread starts).
    """
    # Load the document's full extracted text.
    with db._lock:
        doc_row = db._conn.execute(
            "SELECT extracted_text FROM documents WHERE id=?", (doc_id,)
        ).fetchone()
    if not doc_row or not doc_row["extracted_text"]:
        return 0

    full_text = doc_row["extracted_text"]

    # Load unembedded chunks with valid non-NULL character spans only.
    # Chunks with NULL offsets (beyond the 100k cap, or from pages-only docs)
    # must be handled by the standard per-chunk fallback in embed_chunks_for_doc;
    # including them here would produce fabricated positions and mark them 'late'.
    with db._lock:
        rows = db._conn.execute(
            """SELECT c.id, c.char_start, c.char_end FROM chunks c
               LEFT JOIN vectors v ON v.object_id = c.id AND v.object_type='chunk'
               WHERE c.doc_id=? AND v.id IS NULL AND length(c.text) > 40
                 AND c.char_start IS NOT NULL AND c.char_end IS NOT NULL
               ORDER BY c.char_start, c.rowid""",
            (doc_id,),
        ).fetchall()

    if not rows:
        return 0

    chunk_infos = [(r["id"], r["char_start"], r["char_end"]) for r in rows]
    return embed_with_late_chunking(full_text, chunk_infos, db)


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
    elif object_type == "conv_chunk":
        # Conversation exchange chunks — each row is one user+assistant turn.
        # Use LEFT JOIN for conversations so chunks from deleted or test conversations
        # remain searchable (conv_title will be NULL in that case).
        all_sql = """SELECT v.object_id, v.embedding, v.dim,
                            cc.text, cc.conv_id, c.title AS conv_title,
                            cc.created_at
                     FROM vectors v
                     JOIN conversation_chunks cc ON cc.id = v.object_id
                     LEFT JOIN conversations c ON c.id = cc.conv_id
                     WHERE v.object_type='conv_chunk'"""
    else:
        all_sql = """SELECT v.object_id, v.embedding, v.dim,
                            c.text, c.context_prefix, c.doc_id,
                            d.title AS doc_title, d.work_id
                     FROM vectors v
                     JOIN chunks c ON c.id = v.object_id
                     JOIN documents d ON d.id = c.doc_id
                     WHERE v.object_type='chunk'"""

    entries = _load_vecs(db, object_type, all_sql, ())

    query_dim = len(qvec)
    _NOISE_FLOOR = 0.25
    skipped_dim = 0
    scored = []
    for obj_id, fields, nvec in entries:
        # ── Dimension guard ───────────────────────────────────────────────────
        # When the embedder model has been changed, stored vectors may live in a
        # different dimension space than the query embedding.  _dot() uses zip()
        # which silently truncates the longer vector, producing invalid scores.
        # We discard mismatched vectors entirely so BM25/FTS remains the sole
        # source of results until a full re-index is completed.
        if len(nvec) != query_dim:
            skipped_dim += 1
            continue
        # Skip entries that don't belong to the requested work scope
        if work_id and fields.get("work_id") != work_id:
            continue
        s = _dot(qvec, nvec)
        if s > _NOISE_FLOOR:
            d = dict(fields)
            d["id"] = obj_id
            d["score"] = round(s, 4)
            scored.append(d)
    if skipped_dim:
        logger.debug(
            "semantic_search: skipped %d vector(s) with mismatched dim "
            "(stored ≠ %d) — re-index required",
            skipped_dim, query_dim,
        )
    scored.sort(key=lambda d: d["score"], reverse=True)
    return scored[:limit]


_RRF_K = 60  # standard reciprocal-rank-fusion constant


def hybrid_search_chunks(query: str, db: "OrivellumDB", limit: int = 10,
                         work_id: str | None = None,
                         fts_weight: float = 0.5,
                         semantic_weight: float = 0.5) -> list[dict]:
    """Hybrid chunk retrieval: FTS5 BM25 + semantic cosine, fused with weighted RRF.

    Each result carries ``rrf_score`` and ``match_type`` ("keyword",
    "semantic", or "both").  Degrades gracefully:
    - embeddings unavailable → pure BM25 results
    - FTS finds nothing (short/conceptual query) → pure semantic results

    Args:
        fts_weight:      Relative weight for BM25/FTS hits in RRF fusion.
                         Higher → exact keyword matches rank more strongly.
        semantic_weight: Relative weight for semantic cosine hits in RRF
                         fusion.  Higher → broad conceptual matches rank
                         more strongly.  Weights need not sum to 1.
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

    # Weighted reciprocal rank fusion:
    #   score = fts_weight/(k+rank_fts) + semantic_weight/(k+rank_sem)
    fused: dict[str, dict] = {}
    for rank, hit in enumerate(fts):
        cid = hit.get("id")
        if not cid:
            continue
        entry = fused.setdefault(cid, {"hit": hit, "score": 0.0, "sources": set()})
        entry["score"] += fts_weight / (_RRF_K + rank + 1)
        entry["sources"].add("keyword")
    for rank, hit in enumerate(sem):
        cid = hit.get("id")
        if not cid:
            continue
        entry = fused.setdefault(cid, {"hit": hit, "score": 0.0, "sources": set()})
        entry["score"] += semantic_weight / (_RRF_K + rank + 1)
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


def embed_conversation_exchange(
    conv_id: str,
    user_text: str,
    assistant_text: str,
    db: "OrivellumDB",
) -> str | None:
    """Embed one user+assistant exchange and store it as a conversation chunk.

    Combines both sides of the exchange into a single text unit, embeds it,
    stores the chunk in ``conversation_chunks``, and writes the vector to
    ``vectors`` with ``object_type='conv_chunk'``.

    Returns the new chunk_id on success, or None when the embeddings endpoint
    is unavailable (the chunk is still stored in conversation_chunks for future
    backfill via the nightly nightshift pass).
    """
    combined = f"User: {user_text[:600].strip()}\n\nAssistant: {assistant_text[:600].strip()}"
    # Always persist the chunk so it is available for keyword recall and future
    # embedding backfill — even for very short exchanges.
    chunk_id = db.add_conversation_chunk(conv_id, combined)
    # Attempt to embed; skip only when the text is too short to produce a
    # meaningful vector (embedding is best-effort, persistence is always done).
    if len(combined) >= 10:
        vec = embed_text(combined)
        if vec is not None:
            db.store_vector(chunk_id, "conv_chunk", pack_vector(vec), len(vec))
            logger.debug("Embedded conv_chunk %s for conv %s", chunk_id[:8], conv_id[:8])
    return chunk_id


# ── Late chunking ─────────────────────────────────────────────────────────────
#
# Late chunking (Jina AI, 2024): encode the full document once, then
# mean-pool token-level embeddings within each chunk's character span.
# Each chunk vector inherits its surrounding context, improving similarity
# search for short or ambiguous passages.
#
# Requires an embeddings endpoint that supports per-token output.  The probe
# sends a small request with ``return_token_embeddings: true`` and checks
# whether the response contains a 2-D ``embedding`` array (list of lists).
# If the endpoint returns a flat 1-D array the feature is silently disabled
# and standard per-chunk embedding is used instead.
#
# Probe results are cached in-process so the probe round-trip is paid only
# once per server start (or after an explicit re-probe via the settings API).

_late_chunking_probe_cache: bool | None = None
_late_chunking_probe_lock = threading.Lock()


def probe_late_chunking_support(*, force: bool = False) -> bool:
    """Return True when the configured embeddings endpoint supports per-token output.

    The result is cached in-process after the first successful probe.  Pass
    ``force=True`` to invalidate the cache and re-probe (used by the settings
    API so the user can refresh after switching models).

    Never raises — returns False on any network or parse error.
    """
    global _late_chunking_probe_cache
    with _late_chunking_probe_lock:
        if not force and _late_chunking_probe_cache is not None:
            return _late_chunking_probe_cache
        result = _run_late_chunking_probe()
        _late_chunking_probe_cache = result
        logger.info(
            "Late-chunking probe: endpoint %s per-token embeddings",
            "supports" if result else "does NOT support",
        )
        return result


def _run_late_chunking_probe() -> bool:
    """Execute the actual probe request.  Returns True iff token-level output detected."""
    if time.monotonic() < _unavailable_until:
        return False
    try:
        base_url, model = _serving()
        payload = json.dumps({
            "model": model,
            "input": "Late chunking probe.",
            "return_token_embeddings": True,
        }).encode()
        req = urllib.request.Request(
            f"{base_url}/embeddings", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        items = data.get("data", [])
        if not items:
            return False
        emb = items[0].get("embedding")
        # Per-token: embedding is a list of lists — shape (seq_len, dim).
        # Standard: embedding is a flat list — shape (dim,).
        return (
            isinstance(emb, list)
            and len(emb) > 0
            and isinstance(emb[0], list)
        )
    except Exception as exc:
        logger.debug("Late-chunking probe failed: %s", exc)
        return False


def embed_with_late_chunking(
    full_text: str,
    chunk_infos: list[tuple[str, int | None, int | None]],
    db: "OrivellumDB",
) -> int:
    """Embed document chunks using the late-chunking technique.

    Submits the full document text to the embeddings endpoint with
    ``return_token_embeddings=True``, receives per-token vectors
    ``(seq_len × dim)``, then mean-pools within each chunk's character span.
    Each resulting vector reflects its surrounding document context rather
    than the chunk text in isolation.

    Args:
        full_text: The document's full extracted text (truncated to
            ``_MAX_TEXT_LEN`` before submission).
        chunk_infos: List of ``(chunk_id, char_start, char_end)`` tuples.
            ``char_start`` / ``char_end`` are byte offsets within *full_text*;
            pass ``None`` for chunks without stored offsets — the encoder
            estimates the span using proportional interpolation.
        db: Database handle (used to write vectors and mark embedding_method).

    Returns:
        Number of new vectors stored.  Returns 0 if the endpoint does not
        support per-token output (caller should then fall back to standard
        per-chunk embedding).
    """
    global _late_chunking_probe_cache  # updated on flat-vector detection below

    if not chunk_infos or not full_text:
        return 0

    # Truncate to what the model actually receives.
    text = full_text[:_MAX_TEXT_LEN]
    text_len = len(text)
    if text_len == 0:
        return 0

    # Request per-token embeddings from the endpoint.
    global _unavailable_until
    if time.monotonic() < _unavailable_until:
        return 0

    base_url, model = _serving()
    payload = json.dumps({
        "model": model,
        "input": text,
        "return_token_embeddings": True,
    }).encode()
    req = urllib.request.Request(
        f"{base_url}/embeddings", data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_EMBED_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        _unavailable_until = time.monotonic() + _FAIL_COOLDOWN
        logger.debug("Late-chunking embed request failed: %s", exc)
        return 0

    items = data.get("data", [])
    if not items:
        return 0

    token_embs = items[0].get("embedding")
    if not isinstance(token_embs, list) or not token_embs or not isinstance(token_embs[0], list):
        # Endpoint returned a flat vector — not token-level; invalidate probe
        # cache so the next call re-evaluates (e.g. after a model switch).
        with _late_chunking_probe_lock:
            _late_chunking_probe_cache = False  # noqa: F841 (module-level via closure)
        logger.debug("Late-chunking: endpoint returned flat embedding, marking unsupported")
        return 0

    seq_len = len(token_embs)
    dim = len(token_embs[0])
    if dim == 0:
        return 0

    stored = 0
    for chunk_id, char_start, char_end in chunk_infos:
        # ── Derive token span ──────────────────────────────────────────────
        # Only process chunks with valid non-NULL spans.  NULL-span chunks
        # (beyond the 100k extracted-text cap, or from pages-only docs) must
        # not receive fabricated positions — they are left unembedded here
        # so the standard per-chunk path handles them.
        if char_start is None or char_end is None:
            continue
        # Skip chunks not WHOLLY within the submitted text window.
        # A chunk straddling the 6k boundary would pool only its prefix,
        # producing a misleading vector and blocking the standard fallback.
        # Leaving it unembedded here lets embed_chunks_for_doc pick it up.
        if char_start >= text_len or char_end > text_len or char_start >= char_end:
            continue
        cs, ce = char_start, char_end

        # Linear interpolation: char position → approximate token index.
        t_start = int(cs / text_len * seq_len)
        t_end = int(ce / text_len * seq_len)
        t_end = max(t_start + 1, min(t_end, seq_len))  # at least 1 token

        # Mean-pool the token embeddings within the span.
        span = token_embs[t_start:t_end]
        n_tokens = len(span)
        pooled = [sum(span[i][j] for i in range(n_tokens)) / n_tokens
                  for j in range(dim)]

        # Store vector and mark the chunk's embedding method.
        db.store_vector(chunk_id, "chunk", pack_vector(pooled), dim)
        try:
            with db._lock:
                db._conn.execute(
                    "UPDATE chunks SET embedding_method='late' WHERE id=?",
                    (chunk_id,),
                )
                db._conn.commit()
        except Exception:
            pass  # non-fatal — vector is already stored
        stored += 1

    return stored


def semantic_search_conversations(
    query: str,
    db: "OrivellumDB",
    limit: int = 5,
) -> list[dict]:
    """Semantic search over conversation chunks.

    Delegates to :func:`semantic_search` with ``object_type='conv_chunk'``.
    Falls back to [] when embeddings are unavailable (caller should then call
    ``db.search_conversation_chunks`` for keyword-based degraded search).
    """
    return semantic_search(query, db, object_type="conv_chunk", limit=limit)


def hybrid_search_knowledge(query: str, db: "OrivellumDB", limit: int = 10,
                            work_id: str | None = None,
                            fts_weight: float = 0.5,
                            semantic_weight: float = 0.5) -> list[dict]:
    """Merge FTS (keyword) and semantic hits via weighted RRF, deduplicated.

    Falls back to pure FTS when embeddings are unavailable, and to pure
    semantic when FTS returns nothing (e.g. very short or conceptual queries).

    Args:
        fts_weight:      Relative weight for BM25/FTS hits in RRF fusion.
                         Higher → exact keyword / proper-noun matches rank first.
        semantic_weight: Relative weight for semantic cosine hits in RRF
                         fusion.  Higher → thematically related items surface
                         even when exact wording differs.
    """
    fetch = min(limit * 2, 50)
    fts = db.search_knowledge(query, work_id=work_id, limit=fetch)
    sem = semantic_search(query, db, "knowledge", limit=fetch, work_id=work_id)
    if not sem:
        return fts[:limit]
    if not fts:
        return sem[:limit]

    # Weighted RRF fusion — same formula as hybrid_search_chunks.
    fused: dict[str, dict] = {}
    for rank, hit in enumerate(fts):
        kid = hit.get("id")
        if not kid:
            continue
        entry = fused.setdefault(kid, {"hit": hit, "score": 0.0})
        entry["score"] += fts_weight / (_RRF_K + rank + 1)
    for rank, hit in enumerate(sem):
        kid = hit.get("id")
        if not kid:
            continue
        entry = fused.setdefault(kid, {"hit": hit, "score": 0.0})
        entry["score"] += semantic_weight / (_RRF_K + rank + 1)
        entry["hit"].setdefault("score", hit.get("score"))

    ranked = sorted(fused.values(), key=lambda e: e["score"], reverse=True)
    return [e["hit"] for e in ranked[:limit]]
