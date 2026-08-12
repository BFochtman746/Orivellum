---
name: iPhone continuity core (outbox + journal replay + push)
description: Durable offline outbox, generation-event journal replay, and web push — design constraints and hard-won lessons.
---

# Continuity core

## Outbox (client, IndexedDB)
- The op ID is generated BEFORE any network attempt and doubles as the
  server's `client_msg_id` — that is the exactly-once mechanism; never invent
  a second id.
- A page death mid-flush strands ops in a "sending" state that pending-only
  listings skip; the flusher must requeue in-flight ops at start (safe:
  single-flight per session, cross-tab covered by idempotency).
- Any UI check for "is something still queued" must consider ALL undelivered
  ops, not just pending ones — mid-flight ops briefly leave pending and the
  optimistic bubble vanishes otherwise. Same lesson for handoff-to-server-rows
  effects: they must preserve queued/failed/incomplete bubbles (no server row
  exists for them yet).
- IDB `getAll()` returns key order; same-millisecond timestamps let later ops
  overtake. Ordering needs a monotonic timestamp (`max(now, last+1)`).

## Generation journal (server)
- The pump task consumes the LLM generator independently of the HTTP tail —
  a dropped connection never kills generation. The job row must be created on
  the FIRST async iteration (sync routes have no running loop at call time).
- The live relay to the tail must be bounded and detach when the tail closes
  or stalls; the journal is the durable recovery path, never the relay.
- **Idempotency settlement**: when generation is journalled, the route's
  'processing' claim can only be settled where the pump ends — complete on a
  persisted terminal assistant message, RELEASE on failure. Leaving the claim
  open makes retries 409 until stale-timeout and then duplicate the reply.
- `ON CONFLICT` against a PARTIAL unique index must repeat the index
  predicate (`... WHERE col IS NOT NULL`) or SQLite raises OperationalError.
- Client replay: the recovered bubble hands back to refetched server rows
  shortly after the job finishes — recovered-state badges must be keyed by
  server message id, not by the transient bubble.

## Web push
- SSRF rule: subscription endpoints are accepted ONLY from an allowlist of
  real push provider hosts (Apple/Google/Mozilla/Microsoft). Never use
  resolve-and-check DNS validation — it is always a TOCTOU race (rebinding)
  against the HTTP client's own resolution. Re-check the allowlist at
  delivery time too (string check, prunes legacy rows).
- Payload carries only kind + deep link (no content) by design; 404/410
  subscriptions are pruned on send.

## Testing lessons
- TestClient fixtures must re-init app deps AFTER entering the context —
  lifespan overwrites them.
- Push endpoint tests must provision VAPID keys first or fan-out silently
  no-ops. Import pywebpush lazily at send time so tests can patch it.
- vitest outbox tests: fresh `new IDBFactory()` (fake-indexeddb) +
  `vi.resetModules()` per test.
