---
name: Chat send failure UX
description: How failed AI messages are rendered on web vs mobile.
---

# Chat send failure UX

## Web (`artifacts/orivellum-ui/src/pages/chat/index.tsx`)
- When a stream throws a non-AbortError, the assistant bubble is **kept** with `failed: true` and the error label as its text
- Bubble gets `bg-destructive/5 border border-destructive/30 text-destructive` styling
- `finally` block filters local messages to `m.incomplete || m.failed` so both types survive the cleanup
- The "AI service currently unavailable" inline text from non-OK HTTP responses shows as normal streamed content (not marked failed)

## Mobile (`artifacts/mobile/app/chat/[id].tsx`)
- Still removes the bubble on error and shows only a toast — **not yet updated**
- Task #140 tracks adding the same failed-bubble pattern to mobile

**Why:** Users need to know their message was not delivered; silently removing the bubble is confusing.
