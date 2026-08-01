---
name: Duplicate send guard
description: Server-side guard preventing duplicate user messages when a request fires twice.
---

# Duplicate send guard

## Location
`src/orivellum/api/routes/conversations.py` — near the top of `send_message()`, before `db.add_message()`.

## How it works
1. Acquires `db._lock` and queries for a matching user message with the same `conv_id` + `text` stored within the last 5 seconds
2. If a recent duplicate exists, skips the `db.add_message()` call but still proceeds with the AI response
3. Protects against React StrictMode double-calls, browser retries, and accidental double-taps

## Tech debt
- Directly accesses `db._lock` and `db._conn` (private attributes); task #139 tracks moving this to a public `db.is_duplicate_user_message(conv_id, text, within_seconds)` method
- No idempotency key — if the same message text is legitimately sent twice within 5s, only the first will be stored

**Why:** Without this, React StrictMode in development can fire the POST twice, storing two identical user messages and triggering two assistant replies.
