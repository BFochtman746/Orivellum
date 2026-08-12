"""Derive real subjects from content clusters — RE-PROJECTION Phase 4.

Pipeline
--------
1. Select eligible documents: unassigned (work_id IS NULL), tier eligible for
   Works (never ARTIFACT/SYSTEM), doc_type never 'generated'.
2. Load document-level embeddings (chunk vectors averaged — same substrate as
   topic clustering; documents without vectors are excluded from clustering
   and simply remain collection-only residue).
3. Cluster WITHIN each collection first (cosine k-means), then MERGE clusters
   across collections whose centroids are close — cross-collection spread is
   the genuineness signal for a real subject.
4. Each surviving cluster becomes a proposed Work: deterministic fingerprint
   over the sorted member doc ids, content-derived name (model best-effort
   via the llm gateway, deterministic TF-IDF fallback — filenames are NEVER
   naming inputs), exemplars, dominant doc_type, collection spread, quality
   stats.
5. Proposals are UPSERTed by fingerprint: re-runs refresh rows still in
   status='proposed' and never touch ratified/rejected rows.

This module only *proposes*.  Works are created exclusively through signed
ratification in the review queue (see api/routes/review.py).  The substrate
invariant holds: nothing here mutates documents, chunks, or vectors.
"""

from __future__ import annotations

import hashlib
import logging
import math
from collections import Counter, defaultdict
from typing import TYPE_CHECKING, Any

import numpy as np

from orivellum.capabilities.classify import EXCLUDED_FROM_WORKS
from orivellum.capabilities.cluster import (
    _kmeans_cosine,
    _load_chunk_texts,
    _load_doc_vectors,
    _tfidf_labels,
)

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger("orivellum.work_proposals")

MIN_CLUSTER_SIZE = 3  # a subject needs at least this many documents
_MAX_K_PER_COLLECTION = 8
_MERGE_THRESHOLD = 0.80  # centroid cosine similarity to merge across collections
_EXEMPLARS = 3
_NAME_TEXT_CHARS = 3000  # content sample handed to the model for naming

# Author-selectable ontology domains (drives downstream ontology work).
VALID_DOMAINS = frozenset({"narrative", "technical", "governance", "reference"})


# --------------------------------------------------------------------------- #
# Eligibility                                                                  #
# --------------------------------------------------------------------------- #


def _eligible_docs(db: OrivellumDB) -> dict[str, dict]:
    """Unassigned documents that may legally become Work members.

    Tier exclusions (ARTIFACT/SYSTEM) and generated outputs never enter a
    subject cluster.  Docs already on a Work are never re-clustered — a
    ratified assignment is not re-litigated by a machine pass.
    """
    excluded_tiers = tuple(t.value for t in EXCLUDED_FROM_WORKS)
    placeholders = ",".join(["?"] * len(excluded_tiers))
    with db._lock:
        rows = db._conn.execute(
            f"""SELECT d.id, d.title, d.kind, d.tier, d.doc_type, d.collection_id
                FROM documents d
                JOIN objects o ON o.id = d.id AND o.lifecycle != 'deleted'
                WHERE d.work_id IS NULL
                  AND COALESCE(d.quarantined, 0) = 0
                  AND (d.tier IS NULL OR d.tier NOT IN ({placeholders}))
                  AND COALESCE(d.doc_type, '') != 'generated'""",
            excluded_tiers,
        ).fetchall()
    return {r["id"]: dict(r) for r in rows}


# --------------------------------------------------------------------------- #
# Clustering                                                                   #
# --------------------------------------------------------------------------- #


def _cluster_within_collections(
    doc_embs: dict[str, np.ndarray], docs: dict[str, dict]
) -> list[list[str]]:
    """Cosine k-means per collection; returns clusters of >= MIN_CLUSTER_SIZE."""
    by_collection: dict[str, list[str]] = defaultdict(list)
    for did in doc_embs:
        by_collection[docs[did].get("collection_id") or "__none__"].append(did)

    clusters: list[list[str]] = []
    for _cid, dids in by_collection.items():
        if len(dids) < 2:
            # A single document can still contribute to a cross-collection
            # subject via the merge step — carry it through as a singleton.
            clusters.append(list(dids))
            continue
        X = np.stack([doc_embs[d] for d in dids], axis=0).astype(np.float32)
        k = min(_MAX_K_PER_COLLECTION, max(1, round(math.sqrt(len(dids)))))
        labels = _kmeans_cosine(X, k)
        groups: dict[int, list[str]] = defaultdict(list)
        for did, lbl in zip(dids, labels.tolist(), strict=True):
            groups[int(lbl)].append(did)
        # Keep every non-empty group — the MIN_CLUSTER_SIZE gate is applied
        # AFTER the cross-collection merge so small per-collection fragments of
        # a genuine subject can still combine into a proposal.
        clusters.extend(g for g in groups.values() if g)
    return clusters


def _centroid(doc_embs: dict[str, np.ndarray], members: list[str]) -> np.ndarray:
    c = np.mean([doc_embs[d] for d in members], axis=0)
    n = np.linalg.norm(c)
    return c / n if n > 1e-9 else c


def _merge_across_collections(
    clusters: list[list[str]], doc_embs: dict[str, np.ndarray], docs: dict[str, dict]
) -> list[list[str]]:
    """Greedy union of clusters whose centroids are cosine-close AND that come
    from DIFFERENT collections.

    Cross-collection agreement is the genuineness signal: two collections
    independently producing the same subject cluster is strong evidence the
    subject is real, not an import accident.  Two clusters from the SAME
    collection are never merged directly — the within-collection k-means just
    split them apart, and re-fusing them here would undo that separation.
    (Single-collection clusters are still valid proposals on their own; they
    simply carry a spread of one collection as weaker evidence.)
    """
    if len(clusters) <= 1:
        return clusters
    centroids = [_centroid(doc_embs, c) for c in clusters]
    # Within-collection clusters are pure — one origin collection each.
    origins = [docs[c[0]].get("collection_id") or "__none__" for c in clusters]
    parent = list(range(len(clusters)))
    # Invariant: a merged component holds AT MOST ONE cluster per source
    # collection.  A pairwise same-collection check is not enough — union-find
    # would still let two same-collection clusters meet transitively through a
    # bridge cluster from a third collection, re-fusing a within-collection
    # split into one overbroad subject.
    comp_origins: dict[int, set[str]] = {i: {origins[i]} for i in range(len(clusters))}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(clusters)):
        for j in range(i + 1, len(clusters)):
            if float(np.dot(centroids[i], centroids[j])) >= _MERGE_THRESHOLD:
                ri, rj = find(i), find(j)
                if ri == rj:
                    continue
                if comp_origins[ri] & comp_origins[rj]:
                    continue  # would place two same-collection clusters together
                parent[rj] = ri
                comp_origins[ri] |= comp_origins.pop(rj)

    merged: dict[int, list[str]] = defaultdict(list)
    for i, members in enumerate(clusters):
        merged[find(i)].extend(members)
    return [sorted(set(m)) for m in merged.values()]


# --------------------------------------------------------------------------- #
# Naming — content only, never filenames                                       #
# --------------------------------------------------------------------------- #


def _tfidf_name(member_texts: list[str], residue_texts: list[str]) -> str:
    """Deterministic content-derived name from TF-IDF top terms."""
    corpus = {0: member_texts}
    if residue_texts:
        corpus[1] = residue_texts
    label = _tfidf_labels(corpus).get(0, "")
    terms = [t.strip() for t in label.split("·") if t.strip()]
    if not terms:
        return "Untitled subject"
    return " ".join(w.capitalize() for w in terms[:3])


def _model_name(db: Any, cfg: Any, sample: str) -> str | None:
    """Best-effort model naming via the llm gateway. Never raises."""
    if cfg is None or not sample.strip():
        return None
    from orivellum.capabilities.llm import llm_call

    result = llm_call(
        [
            {
                "role": "system",
                "content": (
                    "You name subjects. Given content excerpts from a cluster of "
                    "documents, reply with ONLY a short descriptive subject name "
                    "(2-6 words). No quotes, no punctuation, no explanations."
                ),
            },
            {"role": "user", "content": sample[:_NAME_TEXT_CHARS]},
        ],
        cfg=cfg,
        db=db,
        purpose="work_proposal_naming",
        timeout=20,
        temperature=0.2,
        max_tokens=32,
    )
    if not result.ok or not result.text:
        return None
    name = result.text.strip().strip('"').strip()
    if not name or len(name) > 120 or "\n" in name:
        return None
    return name


# --------------------------------------------------------------------------- #
# Main entry point                                                             #
# --------------------------------------------------------------------------- #


def generate_work_proposals(db: OrivellumDB, cfg: Any = None) -> dict:
    """Run the clustering pass and upsert Work proposals.

    Deterministic and idempotent: fingerprints derive from sorted member doc
    ids, so re-runs refresh still-proposed rows and never clobber
    ratifications or rejections.  Read-only on the substrate.
    """
    docs = _eligible_docs(db)
    all_embs = _load_doc_vectors(db)
    doc_embs = {d: v for d, v in all_embs.items() if d in docs}

    stats: dict[str, Any] = {
        "eligible_docs": len(docs),
        "vectorised_docs": len(doc_embs),
        "clusters_within": 0,
        "clusters_merged": 0,
        "proposals_upserted": 0,
        "proposals_skipped_resolved": 0,
    }
    if len(doc_embs) < MIN_CLUSTER_SIZE:
        stats["status"] = "skipped"
        stats["reason"] = f"only {len(doc_embs)} eligible vectorised documents"
        return stats

    within = _cluster_within_collections(doc_embs, docs)
    stats["clusters_within"] = len(within)
    merged = _merge_across_collections(within, doc_embs, docs)
    merged = [m for m in merged if len(m) >= MIN_CLUSTER_SIZE]
    stats["clusters_merged"] = len(merged)

    member_ids_all = sorted({d for m in merged for d in m})
    chunk_texts = _load_chunk_texts(db, member_ids_all)

    for members in merged:
        members = sorted(members)
        fingerprint = hashlib.sha256("\n".join(members).encode("utf-8")).hexdigest()
        centroid = _centroid(doc_embs, members)

        # Exemplars: docs closest to the cluster centroid.
        sims = {d: float(np.dot(doc_embs[d], centroid)) for d in members}
        exemplars = sorted(members, key=lambda d: -sims[d])[:_EXEMPLARS]

        # Dominant doc_type + collection spread.
        type_counts = Counter((docs[d].get("doc_type") or "unknown") for d in members)
        dominant_doc_type = type_counts.most_common(1)[0][0]
        spread = Counter((docs[d].get("collection_id") or "") for d in members)
        collection_spread = {k or "unassigned": v for k, v in spread.items()}

        cohesion = round(sum(sims.values()) / len(sims), 4)
        cluster_stats = {
            "cohesion": cohesion,
            "collections": len(collection_spread),
            "doc_type_counts": dict(type_counts),
        }

        # Content-derived name — chunk text only, filenames excluded by design.
        member_texts = [t for d in members for t in chunk_texts.get(d, [])]
        residue_texts = [
            t for d in member_ids_all if d not in set(members) for t in chunk_texts.get(d, [])
        ]
        sample = "\n".join(member_texts)[:_NAME_TEXT_CHARS]
        name = _model_name(db, cfg, sample)
        name_source = "model"
        if not name:
            name = _tfidf_name(member_texts, residue_texts)
            name_source = "tfidf"

        row = db.upsert_work_proposal(
            fingerprint=fingerprint,
            suggested_name=name,
            name_source=name_source,
            member_doc_ids=members,
            exemplar_doc_ids=exemplars,
            dominant_doc_type=dominant_doc_type,
            collection_spread=collection_spread,
            cluster_stats=cluster_stats,
        )
        if row is None:
            stats["proposals_skipped_resolved"] += 1
        else:
            stats["proposals_upserted"] += 1

    stats["status"] = "ok"
    logger.info("Work proposals: %s", stats)
    return stats
