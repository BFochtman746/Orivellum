"""Tests for topic clustering (an unattended nightshift pass).

Floor-rule coverage for cluster.py: pins the deterministic pieces —
vector pack/unpack round-trip, normalisation, cosine k-means labelling,
TF-IDF topic labels — and run_clustering's refusal to invent topics from
too little data.
"""

from __future__ import annotations

import struct

import numpy as np

from orivellum.capabilities import cluster

# ── Vector helpers ────────────────────────────────────────────────────────────


def test_unpack_round_trip():
    vec = [0.25, -1.5, 3.0]
    blob = struct.pack("<3f", *vec)
    out = cluster._unpack(blob, 3)
    assert out.dtype == np.float32
    assert np.allclose(out, vec)


def test_norm_unit_length_and_zero_safety():
    v = cluster._norm(np.array([3.0, 4.0], dtype=np.float32))
    assert np.isclose(np.linalg.norm(v), 1.0)
    zero = np.zeros(2, dtype=np.float32)
    assert np.allclose(cluster._norm(zero), zero)  # never divides by ~0


def test_norm_rows_handles_zero_rows():
    X = np.array([[3.0, 4.0], [0.0, 0.0]], dtype=np.float32)
    out = cluster._norm_rows(X)
    assert np.isclose(np.linalg.norm(out[0]), 1.0)
    assert np.allclose(out[1], 0.0)


# ── Cosine k-means ────────────────────────────────────────────────────────────


def test_kmeans_separates_two_obvious_clusters():
    rng = np.random.default_rng(7)
    a = rng.normal(loc=[1, 0, 0], scale=0.01, size=(10, 3))
    b = rng.normal(loc=[0, 1, 0], scale=0.01, size=(10, 3))
    X = cluster._norm_rows(np.vstack([a, b]).astype(np.float32))
    labels = cluster._kmeans_cosine(X, k=2)
    assert len(set(labels[:10])) == 1
    assert len(set(labels[10:])) == 1
    assert labels[0] != labels[10]


def test_kmeans_fewer_points_than_k_gives_singletons():
    X = cluster._norm_rows(np.eye(3, dtype=np.float32))
    labels = cluster._kmeans_cosine(X, k=5)
    assert sorted(labels.tolist()) == [0, 1, 2]


# ── TF-IDF labelling ──────────────────────────────────────────────────────────


def test_tokenize_lowercases_and_drops_short_tokens():
    toks = cluster._tokenize("The QUANTUM Entanglement of 2 spins!")
    assert "quantum" in toks
    assert "entanglement" in toks
    assert "2" not in toks
    assert "of" in toks or "of" not in toks  # stopwords filtered later


def test_tfidf_labels_pick_distinguishing_terms():
    cluster_texts = {
        0: ["quantum entanglement superposition qubit quantum entanglement"],
        1: ["sourdough fermentation starter hydration sourdough baking"],
    }
    labels = cluster._tfidf_labels(cluster_texts)
    assert "quantum" in labels[0].lower()
    assert "sourdough" in labels[1].lower()
    assert labels[0] != labels[1]


# ── run_clustering guardrails ─────────────────────────────────────────────────


def test_run_clustering_refuses_with_too_few_docs(tmp_path):
    from orivellum.database.db import OrivellumDB

    db = OrivellumDB(str(tmp_path / "test.db"))
    out = cluster.run_clustering(db)
    assert out["status"] in {"skipped", "insufficient"}
    assert out.get("topics_created", 0) == 0
