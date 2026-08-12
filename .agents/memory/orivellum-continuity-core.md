---
name: iPhone continuity core (outbox + journal replay + push)
description: Durable offline outbox, generation-event journal replay, and web push — design constraints and hard-won lessons.
---

# Continuity core

## Outbox (client, IndexedDB)
- Op ID is generated BEFORE any network attempt and doubles as the server's
  `client_msg_id` — that is the exactly-once mechanism; never invent a second id.
- **Orphaned "sending" ops**: a page death (iOS kill, reload) mid-flush strands
  ops in `sending`, which `listPendingOps` skips — they would never send again.
  `flushOutbox` requeues every `sending` op at flush start (single-flight per
  session makes this safe; cross-tab double-send is covered by idempotency).
- **Queued-bubble drop race**: any UI check for "is something still queued"
  must use `listOps()` (all undelivered ops), NOT `listPendingOps()` — mid-flush
  an op is briefly `sending` and the bubble vanishes before delivery otherwise.
- Chat page has a handoff effect that clears local optimistic bubbles when
  server rows refetch: it must PRESERVE queued/failed/incomplete bubbles —
  those have no server row, so clearing them makes the message disappear.
- IDB `getAll()` returns key order; same-millisecond `createdAt` ties let later
  ops overtake. Ordering needs a monotonic timestamp (`max(now, last+1)`).

## Generation journal (server)
- Pump task consumes the LLM generator independently of the HTTP tail, so a
  dropped connection never kills generation. The job row is created on the
  FIRST async iteration — sync routes have no running loop at call time.
- `ON CONFLICT(dedupe_key)` against a PARTIAL unique index must repeat the
  index predicate: `ON CONFLICT(dedupe_key) WHERE dedupe_key IS NOT NULL` —
  bare form is an OperationalError.
- Client replay: after the job finishes, the recovered bubble hands back to
  refetched server rows (~800 ms) — the recovered badge persists via a
  recovered-ids localStorage set keyed by server message id.

## Web push
- pywebpush imported lazily inside `send_to_all` so tests can
  `patch("pywebpush.webpush")`. 404/410 subscriptions are pruned on send.
- Payload carries only kind + deep link (no content) by design.

## Testing lessons
- TestClient fixtures must re-init `_deps` AFTER entering the context —
  lifespan overwrites it (pattern in tests/test_gen_journal.py).
- Push endpoint tests must hit `/api/system/push/config` first to provision
  VAPID keys or `send_to_all` silently no-ops.
- vitest outbox tests: fresh `new IDBFactory()` (fake-indexeddb) +
  `vi.resetModules()` per test.
