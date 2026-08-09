---
name: Toast systems
description: Two toast systems coexist in the web UI — which Toaster renders which calls.
---
# Toast systems — durable lesson

The UI has TWO toast systems: the shadcn `useToast` store (rendered by `@/components/ui/toaster`) and sonner's `toast()` (rendered only by sonner's own `<Toaster />`). They do not cross-render — for months, dozens of pages called sonner's `toast()` while only the shadcn Toaster was mounted, so all those success/error notifications silently never appeared. Both Toasters are now mounted in the app shell.

**How to apply:** when adding a toast, either system works (both render now), but if a toast "doesn't show", check which system the call belongs to before debugging timing. Long-term the two should be consolidated (follow-up task exists).
