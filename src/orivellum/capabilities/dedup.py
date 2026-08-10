"""Near-duplicate document detection using MinHash sketches + banded LSH.

Detects documents that share substantial text overlap without being
exact SHA-256 duplicates.  Uses a pure-Python MinHash implementation
(no external dependencies) so it runs on any system.

Jaccard similarity thresholds:
  ≥ 0.85 → near_duplicate  (almost identical, likely same content)
  ≥ 0.60 → likely_revision (significant overlap, probably a draft)
  < 0.60 → ignored

Scalability
-----------
The naive approach loads every stored signature and computes Jaccard
one by one — O(n) per import.  At 5,000+ documents this becomes
noticeable.

This module maintains a **banded LSH index** in memory:
- 128 MinHash values are split into 32 bands of 4 values each.
- For a pair with Jaccard similarity s the probability of sharing at
  least one band is  1 − (1 − s⁴)³² ≈ 99 % at s = 0.60 and ≈ 100 %
  at s = 0.85 — so recall is excellent at both thresholds.
- Candidate generation is O(bands) = O(32) regardless of library size.
- Jaccard is only computed for the tiny candidate set, not all n docs.

Consistency guarantees
-----------------------
- The index is scoped to a single DB connection object.  When the app is
  reinitialized with a different DB (e.g. in tests), the index is torn
  down and rebuilt automatically.
- When a document is deleted, ``evict_from_lsh_index`` removes it from
  the in-memory index so it can never appear as a stale candidate.
- Before recording a hit, ``_maybe_record`` verifies that the candidate
  document still exists in the DB.  If not, it evicts the stale entry
  and skips the hit — nothing is appended to results.
- Results are only appended when the pair is either newly persisted or
  already exists in doc_dupes — never on INSERT failure.
"""
from __future__ import annotations

import hashlib
import logging
import re
import struct
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orivellum.database.db import OrivellumDB

logger = logging.getLogger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────

_NUM_PERM        = 128   # number of hash functions in the sketch
_SHINGLE_SIZE    = 5     # word n-gram size
_NEAR_DUP_THRESH = 0.85
_REVISION_THRESH = 0.60
_MIN_WORDS       = 100   # skip very short documents

# LSH band parameters — 32 bands × 4 rows = 128 = _NUM_PERM
# P(candidate | s=0.60) ≈ 99 %   P(candidate | s=0.85) ≈ 100 %
_BANDS = 32
_ROWS  = 4

_SAME_WORK_CAP = 200   # max candidates when scoped to a single Work


# ── In-memory LSH index ───────────────────────────────────────────────────────

_lsh_lock:  threading.Lock                   = threading.Lock()
_lsh_index: dict[tuple[int, int], list[str]] = {}   # (band, bucket) → [doc_id, ...]
_lsh_sigs:  dict[str, tuple[int, ...]]       = {}   # doc_id → unpacked sig ints
_lsh_built: bool     = False
_lsh_db_id: int | None = None               # id(db._conn) when index was built


def _sig_to_ints(sig: bytes) -> tuple[int, ...]:
    """Unpack a MinHash sig blob into a tuple of _NUM_PERM uint32 values."""
    return struct.unpack(f">{_NUM_PERM}I", sig)


def _add_to_lsh(doc_id: str, sig_ints: tuple[int, ...]) -> None:
    """Insert one document into the index.  **Caller must hold _lsh_lock.**"""
    for b in range(_BANDS):
        band = sig_ints[b * _ROWS: (b + 1) * _ROWS]
        key = (b, hash(band))
        bucket = _lsh_index.get(key)
        if bucket is None:
            _lsh_index[key] = [doc_id]
        else:
            bucket.append(doc_id)
    _lsh_sigs[doc_id] = sig_ints


def evict_from_lsh_index(doc_id: str) -> None:
    """Remove a document from the in-memory LSH index.

    Call this whenever a document or its ``minhash_sig`` row is deleted so
    the index can never return stale candidates for that document.

    Thread-safe; no-op if the document was never indexed.
    """
    with _lsh_lock:
        sig_ints = _lsh_sigs.pop(doc_id, None)
        if sig_ints is None:
            return          # not in index — nothing to do
        for b in range(_BANDS):
            band = sig_ints[b * _ROWS: (b + 1) * _ROWS]
            key = (b, hash(band))
            bucket = _lsh_index.get(key)
            if bucket:
                try:
                    bucket.remove(doc_id)
                except ValueError:
                    pass
                if not bucket:
                    del _lsh_index[key]


def _ensure_index_built(db: OrivellumDB) -> None:
    """Lazily load all stored signatures into the in-memory LSH index.

    The index is scoped to a single DB connection.  If called with a
    different DB than the one used to build the index (e.g. after app
    reinitialization in tests), the index is torn down and rebuilt from
    the new DB.  Thread-safe via double-checked locking.
    """
    global _lsh_built, _lsh_db_id
    conn_id = id(db._conn)
    if _lsh_built and _lsh_db_id == conn_id:
        return          # fast path — index is current

    with _lsh_lock:
        if _lsh_built and _lsh_db_id == conn_id:
            return      # re-check under lock

        if _lsh_db_id != conn_id:
            # Different DB connection — tear down the old index first.
            _lsh_index.clear()
            _lsh_sigs.clear()
            _lsh_built = False
            _lsh_db_id = None

        try:
            with db._lock:
                rows = db._conn.execute(
                    "SELECT doc_id, sig FROM minhash_sig"
                ).fetchall()
            for row in rows:
                try:
                    sig_ints = _sig_to_ints(bytes(row["sig"]))
                    _add_to_lsh(row["doc_id"], sig_ints)
                except Exception:
                    pass        # malformed row — skip
            _lsh_built = True
            _lsh_db_id = conn_id
            logger.info("LSH index built: %d documents indexed", len(_lsh_sigs))
        except Exception as exc:
            logger.warning(
                "LSH index build failed (dedup will fall back gracefully): %s", exc
            )


def rebuild_lsh_index(db: OrivellumDB) -> int:
    """Tear down and rebuild the LSH index from the current minhash_sig table.

    Safe to call at server startup after the DB is ready.  Returns the number
    of documents indexed.
    """
    global _lsh_built, _lsh_db_id
    with _lsh_lock:
        _lsh_index.clear()
        _lsh_sigs.clear()
        _lsh_built = False
        _lsh_db_id = None
    _ensure_index_built(db)
    return len(_lsh_sigs)


def _reset_lsh_index() -> None:
    """Clear the in-memory LSH index.  **For testing only.**"""
    global _lsh_built, _lsh_db_id
    with _lsh_lock:
        _lsh_index.clear()
        _lsh_sigs.clear()
        _lsh_built = False
        _lsh_db_id = None


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
        encoded = shingle.encode("utf-8", errors="replace")
        for i in range(num_perm):
            seed = i.to_bytes(4, "big")
            h = hashlib.sha256(seed + encoded).digest()
            val = struct.unpack_from(">I", h, 0)[0]
            if val < mins[i]:
                mins[i] = val

    return struct.pack(f">{num_perm}I", *mins)


def _jaccard(sketch_a: bytes, sketch_b: bytes, num_perm: int = _NUM_PERM) -> float:
    """Estimate Jaccard similarity from two MinHash sketches (bytes form)."""
    if len(sketch_a) != len(sketch_b) or len(sketch_a) != num_perm * 4:
        return 0.0
    a = struct.unpack(f">{num_perm}I", sketch_a)
    b = struct.unpack(f">{num_perm}I", sketch_b)
    matches = sum(1 for x, y in zip(a, b) if x == y)
    return matches / num_perm


def _jaccard_ints(a: tuple[int, ...], b: tuple[int, ...]) -> float:
    """Estimate Jaccard similarity from two pre-unpacked sig tuples.

    Avoids ``struct.unpack`` on the hot comparison path.
    """
    matches = sum(1 for x, y in zip(a, b) if x == y)
    return matches / _NUM_PERM


# ── Shared write helper ────────────────────────────────────────────────────────

def _maybe_record(
    doc_id: str,
    other_id: str,
    sim: float,
    db: OrivellumDB,
    results: list[tuple[str, float, str]],
) -> None:
    """If *sim* crosses a threshold, persist the pair and append to *results*.

    Consistency guarantees:
    - Verifies *other_id* still exists in the DB before inserting.  If it
      has been deleted (stale index entry), evicts it from the LSH index
      and returns without appending.
    - Only appends to *results* when the pair is either already in
      doc_dupes or was successfully inserted — never on failure.
    """
    if sim >= _NEAR_DUP_THRESH:
        kind = "near_duplicate"
    elif sim >= _REVISION_THRESH:
        kind = "likely_revision"
    else:
        return

    recorded_ok = False
    try:
        import uuid as _uuid_mod
        with db._lock:
            existing = db._conn.execute(
                """SELECT id FROM doc_dupes
                   WHERE (doc_a_id=? AND doc_b_id=?) OR (doc_a_id=? AND doc_b_id=?)""",
                (doc_id, other_id, other_id, doc_id),
            ).fetchone()
            if existing:
                recorded_ok = True  # pair already known — still surface it
            else:
                # Verify other_id still exists before inserting (guards against
                # stale LSH index entries left by a document deletion).
                other_exists = db._conn.execute(
                    "SELECT 1 FROM documents WHERE id=?", (other_id,)
                ).fetchone()
                if not other_exists:
                    # Stale entry — evict from index so it can't appear again.
                    evict_from_lsh_index(other_id)
                    return
                dupe_id = str(_uuid_mod.uuid4())
                db._conn.execute(
                    """INSERT INTO doc_dupes(id, doc_a_id, doc_b_id, similarity, kind, created_at)
                       VALUES(?,?,?,?,?,datetime('now'))""",
                    (dupe_id, doc_id, other_id, round(sim, 4), kind),
                )
                db._conn.commit()
                recorded_ok = True
                try:
                    db.audit(
                        "document.near_duplicate_found",
                        object_id=doc_id,
                        object_type="document",
                        actor="system",
                        detail=f"dup={other_id[:8]} sim={round(sim, 4)}",
                    )
                except Exception:
                    pass
                logger.info(
                    "Near-dup detected: %s ↔ %s  similarity=%.2f  kind=%s",
                    doc_id[:8], other_id[:8], sim, kind,
                )
    except Exception as exc:
        logger.debug("doc_dupes insert failed: %s", exc)

    if recorded_ok:
        results.append((other_id, sim, kind))


# ── Public API ────────────────────────────────────────────────────────────────

def compute_and_store(doc_id: str, text: str, db: OrivellumDB) -> bytes | None:
    """Compute MinHash for *text*, persist it, and update the LSH index.

    Returns the sketch bytes, or None if the text is too short.
    """
    words = text.split()
    if len(words) < _MIN_WORDS:
        return None

    sig = _minhash(_shingles(text))
    try:
        with db._lock:
            db._conn.execute(
                "INSERT OR REPLACE INTO minhash_sig(doc_id, sig, created_at)"
                " VALUES(?,?,datetime('now'))",
                (doc_id, sig),
            )
            db._conn.commit()
    except Exception as exc:
        logger.debug("minhash store failed for %s: %s", doc_id, exc)
        return None

    # Incrementally update the in-memory index only when it is already
    # scoped to this DB — avoids triggering a full rebuild on every import
    # before the first comparison call.
    sig_ints = _sig_to_ints(sig)
    conn_id = id(db._conn)
    with _lsh_lock:
        if _lsh_built and _lsh_db_id == conn_id:
            _add_to_lsh(doc_id, sig_ints)

    return sig


def find_and_record_near_duplicates(
    doc_id: str,
    sig: bytes,
    db: OrivellumDB,
    work_id: str | None = None,
) -> list[tuple[str, float, str]]:
    """Compare *sig* against stored sketches; write hits to ``doc_dupes``.

    **Work-scoped path** (``work_id`` provided)
      Queries the DB for same-work signatures only.  The result set is bounded
      by ``_SAME_WORK_CAP`` (200), so this path is already O(W) where W is the
      number of documents in the Work.

    **Global path** (``work_id`` is None)
      Uses the banded LSH index for O(32) candidate generation regardless of
      library size.  Only documents that share at least one hash band are
      fully compared.  Candidates are validated against the documents table
      before any pair is recorded — stale index entries (from deleted docs)
      are evicted and silently skipped.

    Returns list of (other_doc_id, similarity, kind) for detected pairs.
    Results are only included when the pair was successfully persisted or
    already existed in doc_dupes — never on failure or for deleted documents.
    """
    results: list[tuple[str, float, str]] = []
    sig_ints = _sig_to_ints(sig)

    if work_id:
        # ── Work-scoped: bounded DB scan, no index needed ─────────────────────
        try:
            with db._lock:
                rows = db._conn.execute(
                    """SELECT ms.doc_id, ms.sig
                       FROM minhash_sig ms
                       JOIN documents d ON d.id = ms.doc_id
                       WHERE ms.doc_id != ?
                         AND d.work_id = ?
                       LIMIT ?""",
                    (doc_id, work_id, _SAME_WORK_CAP),
                ).fetchall()
        except Exception as exc:
            logger.debug("minhash work-scoped query failed: %s", exc)
            return results

        for row in rows:
            other_ints = _sig_to_ints(bytes(row["sig"]))
            sim = _jaccard_ints(sig_ints, other_ints)
            _maybe_record(doc_id, row["doc_id"], sim, db, results)

        return results

    # ── Global: LSH candidate generation → Jaccard only on candidates ─────────
    _ensure_index_built(db)

    # Gather candidate doc_ids that share ≥ 1 band bucket with the query.
    candidates: set[str] = set()
    with _lsh_lock:
        for b in range(_BANDS):
            band = sig_ints[b * _ROWS: (b + 1) * _ROWS]
            for cid in _lsh_index.get((b, hash(band)), []):
                if cid != doc_id:
                    candidates.add(cid)
        # Pull sig ints from memory — zero DB I/O for the comparison itself.
        candidate_ints = {
            cid: _lsh_sigs[cid] for cid in candidates if cid in _lsh_sigs
        }

    # Validate candidates against the documents table in one round-trip.
    # This catches stale index entries from deleted documents without
    # making the hot path any slower for the common (no-deletion) case.
    if candidate_ints:
        placeholders = ",".join("?" * len(candidate_ints))
        try:
            with db._lock:
                live_rows = db._conn.execute(
                    f"SELECT id FROM documents WHERE id IN ({placeholders})",
                    list(candidate_ints),
                ).fetchall()
            live_ids = {r["id"] for r in live_rows}
            # Evict any stale entries found during this check.
            stale = set(candidate_ints) - live_ids
            for stale_id in stale:
                evict_from_lsh_index(stale_id)
            candidate_ints = {
                cid: ints for cid, ints in candidate_ints.items() if cid in live_ids
            }
        except Exception as exc:
            logger.debug("Candidate existence check failed: %s", exc)
            # Proceed with unfiltered candidates — _maybe_record will catch
            # individual stale entries at write time.

    # Full Jaccard comparison — only against the (typically tiny) candidate set.
    for other_id, other_ints in candidate_ints.items():
        sim = _jaccard_ints(sig_ints, other_ints)
        _maybe_record(doc_id, other_id, sim, db, results)

    return results
