"""Near-duplicate document detection using MinHash sketches.

Detects documents that share substantial text overlap without being
exact SHA-256 duplicates.  Uses a pure-Python MinHash implementation
(no external dependencies) so it runs on any system.

Jaccard similarity threshold defaults:
  ≥ 0.85 → near_duplicate  (almost identical, likely same content)
  ≥ 0.60 → likely_revision (significant overlap, probably a draft)
  < 0.60 → ignored
"""
from __future__ import annotations

import hashlib
import logging
import re
import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────

_NUM_PERM = 128        # number of hash functions in the sketch
_SHINGLE_SIZE = 5      # word n-gram size
_NEAR_DUP_THRESH = 0.85
_REVISION_THRESH = 0.60
_MIN_WORDS = 100       # skip very short documents


# ── MinHash sketch ────────────────────────────────────────────────────────────

def _shingles(text: str, k: int = _SHINGLE_SIZE) -> set[str]:
    """Return the set of word k-grams from normalised text."""
    words = re.sub(r"\s+", " ", text.lower()).split()
    if len(words) < k:
        return {" ".join(words)}
    return {" ".join(words[i: i + k]) for i in range(len(words) - k + 1)}


def _minhash(shingles: set[str], num_perm: int = _NUM_PERM) -> bytes:
    """Compute a MinHash sketch and return it packed as `num_perm` uint32 values."""
    if not shingles:
        return b"\xff" * (num_perm * 4)

    mins: list[int] = [0xFFFF_FFFF] * num_perm

    for shingle in shingles:
        # Use SHA-256 of (seed_byte + shingle) as the hash oracle.
        # This gives num_perm effectively independent hash functions cheaply.
        digest = hashlib.sha256(shingle.encode("utf-8", errors="replace")).digest()
        # Extract num_perm/8 groups of 4 bytes and XOR with per-perm seeds
        for i in range(num_perm):
            seed = i.to_bytes(4, "big")
            h = hashlib.sha256(seed + shingle.encode("utf-8", errors="replace")).digest()
            val = struct.unpack_from(">I", h, 0)[0]
            if val < mins[i]:
                mins[i] = val

    return struct.pack(f">{num_perm}I", *mins)


def _jaccard(sketch_a: bytes, sketch_b: bytes, num_perm: int = _NUM_PERM) -> float:
    """Estimate Jaccard similarity from two MinHash sketches."""
    if len(sketch_a) != len(sketch_b) or len(sketch_a) != num_perm * 4:
        return 0.0
    a = struct.unpack(f">{num_perm}I", sketch_a)
    b = struct.unpack(f">{num_perm}I", sketch_b)
    matches = sum(1 for x, y in zip(a, b) if x == y)
    return matches / num_perm


# ── Public API ────────────────────────────────────────────────────────────────

def compute_and_store(doc_id: str, text: str, db: "OrivellumDB") -> bytes | None:
    """Compute MinHash for `text` and store it in `minhash_sig`.

    Returns the sketch bytes, or None if the text is too short.
    """
    words = text.split()
    if len(words) < _MIN_WORDS:
        return None

    sig = _minhash(_shingles(text))
    try:
        with db._lock:
            db._conn.execute(
                "INSERT OR REPLACE INTO minhash_sig(doc_id, sig, created_at) VALUES(?,?,datetime('now'))",
                (doc_id, sig),
            )
            db._conn.commit()
    except Exception as exc:
        logger.debug("minhash store failed for %s: %s", doc_id, exc)
        return None
    return sig


def find_and_record_near_duplicates(
    doc_id: str, sig: bytes, db: "OrivellumDB"
) -> list[tuple[str, float, str]]:
    """Compare `sig` against all stored sketches; write hits to `doc_dupes`.

    Returns list of (other_doc_id, similarity, kind) for detected pairs.
    """
    results: list[tuple[str, float, str]] = []
    try:
        with db._lock:
            rows = db._conn.execute(
                "SELECT doc_id, sig FROM minhash_sig WHERE doc_id != ?", (doc_id,)
            ).fetchall()
    except Exception as exc:
        logger.debug("minhash query failed: %s", exc)
        return results

    for row in rows:
        other_id = row["doc_id"]
        other_sig = bytes(row["sig"])
        sim = _jaccard(sig, other_sig)

        if sim >= _NEAR_DUP_THRESH:
            kind = "near_duplicate"
        elif sim >= _REVISION_THRESH:
            kind = "likely_revision"
        else:
            continue

        # Avoid duplicate pair entries (a,b) and (b,a)
        try:
            with db._lock:
                existing = db._conn.execute(
                    """SELECT id FROM doc_dupes
                       WHERE (doc_a_id=? AND doc_b_id=?) OR (doc_a_id=? AND doc_b_id=?)""",
                    (doc_id, other_id, other_id, doc_id),
                ).fetchone()
                if not existing:
                    import uuid as _uuid_mod
                    dupe_id = str(_uuid_mod.uuid4())
                    db._conn.execute(
                        """INSERT INTO doc_dupes(id, doc_a_id, doc_b_id, similarity, kind, created_at)
                           VALUES(?,?,?,?,?,datetime('now'))""",
                        (dupe_id, doc_id, other_id, round(sim, 4), kind),
                    )
                    db._conn.commit()
                    try:
                        db.audit("document.near_duplicate_found", object_id=doc_id,
                                 object_type="document", actor="system",
                                 detail=f"dup={other_id[:8]} sim={round(sim, 4)}")
                    except Exception:
                        pass
                    logger.info(
                        "Near-dup detected: %s ↔ %s  similarity=%.2f  kind=%s",
                        doc_id[:8], other_id[:8], sim, kind,
                    )
        except Exception as exc:
            logger.debug("doc_dupes insert failed: %s", exc)

        results.append((other_id, sim, kind))

    return results
