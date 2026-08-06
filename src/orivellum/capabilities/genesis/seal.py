"""
GENESIS seal + ledger verification.
"""
from __future__ import annotations
from .gates import STAGE_CODES, sha256_text, canonical, now_iso, GENESIS_HASH


def compute_seal(conn, book_id: str, title: str, length: int, acts: int,
                 author: str) -> dict:
    """
    Build the manifest dict, verify all G0-G8 are PASSED,
    record the seal entry in the ledger, flip book state.
    Returns the manifest dict.
    Raises ValueError on pre-condition failures.
    """
    # Check G0-G8 passed
    stage_rows = {
        r[0]: r[1]
        for r in conn.execute(
            "SELECT stage_code, status FROM genesis_stages WHERE book_id=?", (book_id,)
        ).fetchall()
    }
    for code in STAGE_CODES[:-1]:  # G0..G8
        status = stage_rows.get(code, "PENDING")
        if status != "PASSED":
            raise ValueError(f"Cannot seal: {code} is {status} (must be PASSED)")

    # Check G9 artifact has no <<FILL>> placeholders
    g9_row = conn.execute(
        "SELECT content FROM genesis_artifacts WHERE book_id=? AND stage_code='G9'",
        (book_id,),
    ).fetchone()
    if not g9_row or not g9_row[0] or "<<FILL>>" in g9_row[0]:
        raise ValueError(
            "Cannot seal: G9 artifact still contains <<FILL>> placeholders or is empty"
        )

    # Build manifest over all artifact SHA-256s
    art_rows = conn.execute(
        "SELECT stage_code, sha256 FROM genesis_artifacts WHERE book_id=? ORDER BY stage_code",
        (book_id,),
    ).fetchall()
    entries = [{"code": r[0], "sha256": r[1] or ""} for r in art_rows]
    package_hash = sha256_text(canonical({"book_id": book_id, "artifacts": entries}))

    at = now_iso()
    manifest = {
        "book_id": book_id,
        "title": title,
        "length": length,
        "acts": acts,
        "sealed_at": at,
        "author_signoff": author,
        "artifacts": entries,
        "package_sha256": package_hash,
        "handoff_target": "BPOS:B0",
    }

    # Append seal + handoff to ledger
    from .gates import ledger_append
    seal_hash = ledger_append(conn, book_id, "seal", {
        "author": author,
        "package_sha256": package_hash,
    })
    ledger_append(conn, book_id, "handoff.b0", {
        "target": "BPOS:B0",
        "package_sha256": package_hash,
    })

    # Mark G9 passed
    conn.execute(
        "INSERT INTO genesis_stages (book_id, stage_code, status) VALUES (?,?,?) "
        "ON CONFLICT(book_id, stage_code) DO UPDATE SET status=excluded.status",
        (book_id, "G9", "PASSED"),
    )

    return manifest


def verify_ledger(conn, book_id: str) -> tuple[bool, str]:
    """
    Walk the ledger chain and verify each hash.
    Returns (ok, message).
    """
    rows = conn.execute(
        "SELECT seq, kind, payload, prev_hash, hash FROM genesis_ledger "
        "WHERE book_id=? ORDER BY seq",
        (book_id,),
    ).fetchall()

    if not rows:
        return True, "Ledger is empty (no entries yet)"

    prev = GENESIS_HASH
    for seq, kind, payload_str, stored_prev, stored_hash in rows:
        if stored_prev != prev:
            return False, (
                f"Chain break at seq={seq}: prev_hash mismatch "
                f"(expected {prev[:12]}… got {stored_prev[:12]}…)"
            )
        body = canonical({"seq": seq, "kind": kind, "payload": payload_str})
        # Note: payload in DB is already canonical JSON string (not re-encoded)
        expected = sha256_text(prev + body)
        if expected != stored_hash:
            return False, (
                f"Hash mismatch at seq={seq} (kind={kind}): "
                f"expected {expected[:12]}… got {stored_hash[:12]}…"
            )
        prev = stored_hash

    return True, f"Ledger intact — {len(rows)} entries verified"
