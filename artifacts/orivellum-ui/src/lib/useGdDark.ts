/**
 * useGdDark — flips the document root to the dark shadcn/VELLUM token set
 * while a GD-app page is mounted, so portal-rendered content (Select
 * dropdowns, Sheets/drawers, toasts) inherits the dark tokens too.
 * Cleaned up on unmount; a no-op in the legacy console, which keeps the
 * light parchment look.
 *
 * Returns whether the dark treatment is active so callers can also add the
 * `dark` class to their root element to cover the first paint before the
 * effect runs.
 */
import { useEffect } from "react";
import { isLegacyShell } from "@/lib/apps";

export function useGdDark(): boolean {
  const gdDark = !isLegacyShell();
  useEffect(() => {
    if (!gdDark) return;
    document.documentElement.classList.add("dark");
    return () => document.documentElement.classList.remove("dark");
  }, [gdDark]);
  return gdDark;
}
