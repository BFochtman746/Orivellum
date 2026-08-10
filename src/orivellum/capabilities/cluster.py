"""Topic clustering and cross-document graph construction.

Pipeline
--------
1. Load chunk vectors from the `vectors` table (object_type='chunk').
2. Average chunk vectors per document → one document-level embedding.
3. Run numpy k-means (cosine space) to assign each document to a cluster.
4. Extract TF-IDF top terms per cluster → cluster label.
5. Write clusters to `topics` and `topic_members`.
6. Compute pairwise cosine similarity between document embeddings and write
   the top-K neighbours for each document to `doc_links` so the Related
   panel can be served without recomputing at query time.

No ML framework beyond numpy is required.  The vectors are already stored
as normalised float32 blobs; dot-product == cosine similarity.
"""

from __future__ import annotations

import logging
import math
import re
import struct
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.cluster")

_LINK_NEIGHBOURS = 10  # store top-N similar docs per document
_MIN_SIM_THRESHOLD = 0.35  # minimum cosine similarity to store a link
_MAX_K = 20  # cap number of topics
_MIN_DOCS_FOR_CLUSTER = 2  # a cluster must have at least this many docs
_KMEANS_ITERS = 30
_LABEL_TERMS = 4  # top TF-IDF terms to use in topic label

# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _uid() -> str:
    return str(uuid.uuid4())


def _unpack(blob: bytes, dim: int) -> np.ndarray:
    arr = np.array(struct.unpack(f"<{dim}f", blob), dtype=np.float32)
    return arr


def _norm(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v)
    return v / n if n > 1e-9 else v


# --------------------------------------------------------------------------- #
# Load vectors                                                                 #
# --------------------------------------------------------------------------- #


def _load_doc_vectors(db: OrivellumDB) -> dict[str, np.ndarray]:
    """Return {doc_id: normalised document embedding} by averaging chunk vecs.

    Documents without any chunk vectors are excluded.
    """
    with db._lock:
        rows = db._conn.execute(
            """SELECT c.doc_id, v.embedding, v.dim
               FROM vectors v
               JOIN chunks c ON c.id = v.object_id
               WHERE v.object_type = 'chunk'
                 AND v.embedding IS NOT NULL
                 AND v.dim > 0""",
        ).fetchall()

    # Accumulate chunk vectors per doc
    doc_vecs: dict[str, list[np.ndarray]] = defaultdict(list)
    for row in rows:
        try:
            blob, dim = row["embedding"], row["dim"]
            doc_vecs[row["doc_id"]].append(_unpack(blob, dim))
        except Exception:
            continue

    # Average chunk vectors → doc embedding
    doc_embs: dict[str, np.ndarray] = {}
    for doc_id, vecs in doc_vecs.items():
        avg = np.mean(vecs, axis=0)
        doc_embs[doc_id] = _norm(avg)
    return doc_embs


# --------------------------------------------------------------------------- #
# K-means (cosine space)                                                       #
# --------------------------------------------------------------------------- #


def _kmeans_cosine(X: np.ndarray, k: int, n_iter: int = _KMEANS_ITERS) -> np.ndarray:
    """K-means on normalised vectors using dot-product as similarity.

    Args:
        X: (n, d) float32 array of unit-normalised embeddings.
        k: number of clusters.
        n_iter: maximum iterations.

    Returns:
        Integer label array of shape (n,).
    """
    n = X.shape[0]
    if n <= k:
        return np.arange(n, dtype=np.int32)

    # K-means++ initialisation
    rng = np.random.default_rng(42)
    chosen = [int(rng.integers(n))]
    for _ in range(k - 1):
        sims = X @ X[chosen].T  # (n, len(chosen))
        max_sim = sims.max(axis=1)  # closest centroid similarity
        probs = 1.0 - max_sim  # prefer points far from existing centroids
        probs = np.clip(probs, 0, None)
        s = probs.sum()
        if s < 1e-12:
            # All remaining points are equidistant; pick randomly
            remaining = [i for i in range(n) if i not in chosen]
            chosen.append(int(rng.choice(remaining)))
        else:
            probs /= s
            chosen.append(int(rng.choice(n, p=probs)))

    centroids = _norm_rows(X[chosen].copy())  # (k, d)
    labels = np.zeros(n, dtype=np.int32)

    for _ in range(n_iter):
        # Assignment step: nearest centroid by cosine (highest dot product)
        sims = X @ centroids.T  # (n, k)
        new_labels = np.argmax(sims, axis=1).astype(np.int32)
        if np.all(new_labels == labels):
            break
        labels = new_labels
        # Update step: recompute centroids as normalised means
        new_centroids = np.zeros_like(centroids)
        for j in range(k):
            mask = labels == j
            if mask.any():
                avg = X[mask].mean(axis=0)
                new_centroids[j] = _norm(avg)
            else:
                # Empty cluster: reinitialise to a random data point
                new_centroids[j] = X[int(rng.integers(n))]
        centroids = new_centroids

    return labels


def _norm_rows(X: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(X, axis=1, keepdims=True)
    norms = np.where(norms < 1e-9, 1.0, norms)
    return X / norms


# --------------------------------------------------------------------------- #
# TF-IDF topic labelling                                                       #
# --------------------------------------------------------------------------- #

_STOP = frozenset(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "of",
        "for",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "have",
        "has",
        "had",
        "this",
        "that",
        "with",
        "as",
        "from",
        "by",
        "it",
        "its",
        "he",
        "she",
        "they",
        "we",
        "you",
        "i",
        "do",
        "did",
        "will",
        "would",
        "which",
        "who",
        "what",
        "how",
        "when",
        "where",
        "there",
        "here",
        "than",
        "then",
        "also",
        "just",
        "can",
        "may",
        "might",
        "not",
        "no",
        "more",
        "all",
        "one",
        "two",
        "three",
        "four",
        "five",
        "document",
        "research",
        "study",
        "analysis",
        "include",
        "includes",
        "including",
        "use",
        "uses",
        "using",
        "provide",
        "provides",
        "providing",
        "data",
        "based",
        "between",
        "across",
        "different",
        "used",
        "other",
        "than",
        "only",
        "both",
        "some",
        "each",
        "many",
        "most",
    ]
)

_WORD_RE = re.compile(r"\b[a-z][a-z\-]{2,}\b")


def _tokenize(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOP]


def _tfidf_labels(
    cluster_texts: dict[int, list[str]], n_terms: int = _LABEL_TERMS
) -> dict[int, str]:
    """Compute TF-IDF top terms per cluster and return a label string."""
    # Compute DF across clusters (each cluster is a document)
    all_clusters = list(cluster_texts.keys())
    n_docs = len(all_clusters)
    df: Counter = Counter()
    cluster_tf: dict[int, Counter] = {}
    for cid, texts in cluster_texts.items():
        all_words = []
        for t in texts:
            all_words.extend(_tokenize(t))
        tf = Counter(all_words)
        cluster_tf[cid] = tf
        df.update(set(tf.keys()))

    labels: dict[int, str] = {}
    for cid in all_clusters:
        tf = cluster_tf[cid]
        total = sum(tf.values()) or 1
        scores: dict[str, float] = {}
        for term, count in tf.items():
            idf = math.log((n_docs + 1) / (df[term] + 1)) + 1
            scores[term] = (count / total) * idf
        top = sorted(scores, key=lambda x: -scores[x])[:n_terms]
        labels[cid] = " · ".join(top) if top else f"cluster-{cid}"
    return labels


# --------------------------------------------------------------------------- #
# DB helpers (topics / topic_members / doc_links)                              #
# --------------------------------------------------------------------------- #


def _clear_topics(db: OrivellumDB) -> None:
    """Remove all clustering output rows (idempotent rebuild)."""
    with db._lock:
        db._conn.execute("DELETE FROM topic_members")
        db._conn.execute("DELETE FROM topics")
        db._conn.execute("DELETE FROM doc_links WHERE link_type='semantic'")
        db._conn.commit()


def _write_topics(
    db: OrivellumDB,
    clusters: dict[int, list[str]],  # cluster_id → [doc_id, …]
    labels: dict[int, str],  # cluster_id → label
) -> dict[int, str]:
    """Insert topics + topic_members; return {cluster_id → topic_id}."""
    now = _now()
    cluster_to_topic: dict[int, str] = {}
    for cid, doc_ids in clusters.items():
        if len(doc_ids) < _MIN_DOCS_FOR_CLUSTER:
            continue
        label = labels.get(cid, f"Topic {cid}")
        # Deduplicate label if it already exists (add count suffix)
        unique_label = label
        suffix = 1
        while True:
            with db._lock:
                exists = db._conn.execute(
                    "SELECT id FROM topics WHERE name=?", (unique_label,)
                ).fetchone()
            if not exists:
                break
            suffix += 1
            unique_label = f"{label} ({suffix})"
        label = unique_label

        tid = _uid()
        with db._lock:
            db._conn.execute(
                "INSERT INTO topics(id, name, kind, meta, created_at) VALUES(?,?,?,?,?)",
                (tid, label, "semantic_cluster", '{"source":"cluster","algorithm":"kmeans"}', now),
            )
            for doc_id in doc_ids:
                db._conn.execute(
                    "INSERT OR IGNORE INTO topic_members(topic_id, object_id, object_type) VALUES(?,?,?)",
                    (tid, doc_id, "document"),
                )
            db._conn.commit()
        cluster_to_topic[cid] = tid
    return cluster_to_topic


def _write_doc_links(db: OrivellumDB, doc_ids: list[str], X: np.ndarray) -> int:
    """Compute pairwise cosine similarity and store top-K links per doc."""
    n = len(doc_ids)
    if n < 2:
        return 0
    now = _now()
    # Full pairwise similarity matrix — feasible for n ≤ ~5000
    sim_matrix = X @ X.T  # (n, n)
    written = 0
    with db._lock:
        db._conn.execute("DELETE FROM doc_links WHERE link_type='semantic'")
        for i in range(n):
            # Find top-K neighbours (excluding self)
            row = sim_matrix[i].copy()
            row[i] = -1.0  # exclude self
            top_j = np.argsort(-row)[:_LINK_NEIGHBOURS]
            for j in top_j:
                sim = float(row[j])
                if sim < _MIN_SIM_THRESHOLD:
                    break
                a, b = doc_ids[i], doc_ids[j]
                # Store canonical order (a < b) to avoid double-entries
                if a > b:
                    a, b = b, a
                try:
                    db._conn.execute(
                        """INSERT OR REPLACE INTO doc_links
                           (id, doc_a_id, doc_b_id, similarity, link_type, created_at)
                           VALUES(?,?,?,?,?,?)""",
                        (_uid(), a, b, sim, "semantic", now),
                    )
                    written += 1
                except Exception:
                    pass
        db._conn.commit()
    return written


# --------------------------------------------------------------------------- #
# Chunk text loading                                                            #
# --------------------------------------------------------------------------- #


def _load_chunk_texts(db: OrivellumDB, doc_ids: list[str]) -> dict[str, list[str]]:
    """Return {doc_id: [chunk_text, …]} for the given documents."""
    if not doc_ids:
        return {}
    placeholders = ",".join(["?"] * len(doc_ids))
    with db._lock:
        rows = db._conn.execute(
            f"SELECT doc_id, text FROM chunks WHERE doc_id IN ({placeholders}) AND text IS NOT NULL",
            doc_ids,
        ).fetchall()
    result: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        result[r["doc_id"]].append(r["text"])
    return dict(result)


# --------------------------------------------------------------------------- #
# Main entry point                                                              #
# --------------------------------------------------------------------------- #


def run_clustering(db: OrivellumDB) -> dict:
    """Cluster all vectorised documents and rebuild topic/link tables.

    Returns a summary dict with clustering statistics.
    """
    logger.info("Clustering: loading document vectors…")
    doc_embs = _load_doc_vectors(db)

    n_docs = len(doc_embs)
    if n_docs < _MIN_DOCS_FOR_CLUSTER:
        logger.info("Clustering: only %d documents with vectors — skipping", n_docs)
        return {"status": "skipped", "reason": f"only {n_docs} vectorised documents", "topics": 0}

    doc_ids = list(doc_embs.keys())
    X = np.stack([doc_embs[d] for d in doc_ids], axis=0).astype(np.float32)  # (n, d)

    # Choose k: sqrt heuristic, capped
    k = min(_MAX_K, max(2, int(math.sqrt(n_docs))))
    logger.info("Clustering: k-means k=%d over %d docs (dim=%d)", k, n_docs, X.shape[1])

    labels_arr = _kmeans_cosine(X, k)

    # Group doc_ids by cluster label
    clusters: dict[int, list[str]] = defaultdict(list)
    for doc_id, lbl in zip(doc_ids, labels_arr.tolist()):
        clusters[int(lbl)].append(doc_id)

    # Load chunk texts for TF-IDF labelling
    logger.info("Clustering: loading chunk texts for label generation…")
    chunk_texts_map = _load_chunk_texts(db, doc_ids)
    cluster_texts: dict[int, list[str]] = {}
    for cid, cdoc_ids in clusters.items():
        texts = []
        for did in cdoc_ids:
            texts.extend(chunk_texts_map.get(did, []))
        cluster_texts[cid] = texts

    labels = _tfidf_labels(cluster_texts)
    logger.info("Clustering: generated labels: %s", {c: l for c, l in list(labels.items())[:5]})

    # Write to DB (idempotent — clears previous results first)
    logger.info("Clustering: writing topics to DB…")
    _clear_topics(db)
    cluster_to_topic = _write_topics(db, clusters, labels)

    # Write cross-document similarity links
    logger.info("Clustering: writing doc_links…")
    links_written = _write_doc_links(db, doc_ids, X)

    n_topics = len(cluster_to_topic)
    logger.info(
        "Clustering complete: %d topics from %d docs, %d links",
        n_topics,
        n_docs,
        links_written,
    )
    return {
        "status": "ok",
        "docs_clustered": n_docs,
        "topics": n_topics,
        "doc_links": links_written,
        "k": k,
    }
