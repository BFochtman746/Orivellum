---
name: Toast system
description: Sonner is the single toast system in the web UI — never reintroduce shadcn useToast.
---
# Toast system — durable lesson

Sonner is the ONLY toast system (consolidated Aug 2026). The shadcn `useToast` store, `@/components/ui/toaster`, and `@/components/ui/toast` were deleted from the Orivellum UI, and `@radix-ui/react-toast` removed from its dependencies. The single `<SonnerToaster position="top-center" richColors closeButton />` is mounted in App.tsx.

**Why:** two systems coexisted for months and only one Toaster was mounted, so dozens of pages' notifications silently never rendered. One system prevents this failure class.

**How to apply:** new notifications must use `import { toast } from "sonner"` (`toast.success/error(title, { description })`). Do not re-scaffold the shadcn toast component in orivellum-ui (the mockup-sandbox package keeps its own shadcn toast — that's fine, separate package). Keep the SonnerToaster mounted or all notifications vanish.
