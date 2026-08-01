---
name: Chat UX improvements
description: Key chat page UX decisions — sidebar, streaming resilience, model picker, work badges.
---

## Rules

- **Tab-visibility resilience**: `visibilitychange` listener calls `flushAccumulator()` when tab becomes visible while sending; already in place at top of component.
- **Sidebar work badge**: conversations with `work_id` show a `BookOpen` icon (w-2.5 h-2.5 text-primary/50) beside the last-message preview in the sidebar.
- **Sidebar timestamps**: relative times computed inline using `updated_at` (now / Xm / Xh / MMM d).
- **Conversation rename**: pencil icon on hover (`startRename` → `commitRename` via `useUpdateConversation`).
- **Work badge in header**: `convWorkId` from `activeConv?.conversation?.work_id`; clickable link → `/works/:id` via `setLocation`.
- **Model picker**: `ModelPicker` component in header uses `useUpdateConversation` with `{ model: value }` — `model` field IS in the `ConversationUpdate` schema; tsc may warn but it's valid.
- **Copy button**: assistant messages show a copy button (uses `navigator.clipboard`); `Check` icon replaces `Copy` after copy.
- **Dark code blocks**: `bg-zinc-900 text-zinc-100` for block code; `bg-zinc-800 text-zinc-200` for inline (task #27 implemented in main agent).
- **MarkdownContent**: uses `ReactMarkdown` + `remarkGfm` with custom renderers for `code`, `pre`, `blockquote`, `a`.

**Why:** Streaming in iframes can drop events when the tab isn't focused; the visibility flush ensures accumulated tokens aren't lost.

**How to apply:** Any change to sidebar items should check `(c as any).work_id` since work_id isn't in the generated Conversation type. Import `BookOpen` from lucide-react.
