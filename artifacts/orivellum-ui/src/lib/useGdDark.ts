/**
 * useGdDark — LEGACY BRIDGE (delete in WP3).
 *
 * Historical behavior: mounting a GD page force-flipped the document root to
 * the dark shadcn/VELLUM token set. Since WP2 the saved appearance preference
 * is the single source of truth (src/lib/theme.ts owns the `.dark` class), so
 * a page is never allowed to force appearance on its own.
 *
 * This hook now just reports whether the resolved theme is Hull so existing
 * callers can keep conditionally styling their roots until WP3 migrates them
 * onto semantic tokens and deletes this file.
 */
import { useThemePreference } from "@/lib/theme";

export function useGdDark(): boolean {
  return useThemePreference().resolved === "hull";
}
