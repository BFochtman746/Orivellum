---
name: Library upload path (streaming)
description: How file imports flow into the library — multipart streaming endpoint, dedup race handling, body-limit exemption.
---

# Library upload path

- Web import dialog posts `multipart/form-data` to **POST /api/library/upload** via raw `XMLHttpRequest` (`xhr.upload.onprogress` gives real upload progress; `withCredentials` for session-cookie auth). The old base64 JSON path **POST /api/library/import** is kept for backward compat (mobile still uses it).
- Backend streams `UploadFile` in 1 MB chunks to a `NamedTemporaryFile(suffix=".part")` in the library root while computing SHA-256 incrementally; shared `_ingest_file()` then dedups/creates and `shutil.move`s into the sha-sharded tree.
- **Why streaming:** base64 JSON needed 2×+ file size in RAM and hit the 50 MB body cap.
- **Body limit:** `limit_body_size` middleware in app.py has `_BODY_LIMIT_EXEMPT` containing `/api/library/upload` — any new streaming route must be added there or uploads 413 at `max_body_bytes` (default 50 MB).
- **Dedup race:** two concurrent identical uploads both pass the sha lookup; the loser hits the `sha256` UNIQUE constraint in `create_document` — caught (`sqlite3.IntegrityError`), our file removed unless it IS the winner's path, responds `duplicate: true`.
- **Temp lifecycle:** tmp closed in `finally` (Windows can't unlink open files); unlinked on any exception or duplicate; `_cleanup_stale_parts()` removes `*.part` older than 1 h on each import call.
- **Filename collision guard:** different-content files sharing shard dir + name get a `sha[:12]_` prefix instead of overwriting.
