/**
 * ResponsiveShell — the single shell every route renders inside (WP1).
 *
 * Five persistent destinations (Home, Chat, Works, Library, More):
 *   320–767px  — fixed safe-area-aware bottom tab bar (MobileTabBar)
 *   768–1199px — compact icon rail on the left (DesktopRail)
 *   1200px+    — labeled rail
 *
 * The header keeps the ambient connectivity ribbon (state is ambient, never
 * a toast) and shows the destination name; contextual sibling pages render
 * as ≥44px ContextBar tabs — the old 36px .gd-chip route strips are gone.
 *
 * The content host mirrors the legacy AppLayout/AppFrame content surface
 * exactly (overflow-auto + padding + max-width), so unmigrated pages keep
 * the same scrolling contract. Both breakpoint variants render and are
 * toggled by CSS so there is no resize-listener flicker.
 */
import React, { useEffect, useState } from "react";
import { Link, useLocation } from "wouter";
import { Settings } from "lucide-react";
import { useConnectivity } from "@/lib/useConnectivity";
import {
  accentAppForPath,
  activeDestId,
  activeTabHref,
  contextTabsForPath,
  shellTitleForPath,
} from "@/lib/destinations";
import { MobileTabBar } from "./mobile-tab-bar";
import { DesktopRail } from "./desktop-rail";
import { ContextBar } from "./context-bar";
import { GlobalActionSheet } from "./global-action-sheet";

export function connState(apiReachable: boolean, aiReachable: boolean): {
  state: "up" | "degraded" | "down";
  label: string;
} {
  if (!apiReachable) return { state: "down", label: "OFFLINE" };
  if (!aiReachable) return { state: "degraded", label: "AI OFFLINE" };
  return { state: "up", label: "ONLINE" };
}

export function ResponsiveShell({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();
  const path = location.split("?")[0];
  const { apiReachable, aiReachable } = useConnectivity();
  const conn = connState(apiReachable, aiReachable);
  const [moreOpen, setMoreOpen] = useState(false);

  const active = activeDestId(path);
  const title = shellTitleForPath(path);
  const tabs = contextTabsForPath(path);
  const activeTab = tabs ? activeTabHref(path, tabs) : null;
  const isHome = path === "/";

  // Per-destination accent tint on <html data-app> — the GD token layer is
  // untouched in WP1; only the id source changed (destinations, not apps).
  const accent = accentAppForPath(path);
  useEffect(() => {
    if (accent) document.documentElement.dataset.app = accent;
    else delete document.documentElement.dataset.app;
  }, [accent]);

  // Close the More sheet whenever navigation happens underneath it.
  useEffect(() => {
    setMoreOpen(false);
  }, [path]);

  return (
    <div className="gd-shell shell-root @container" data-conn={conn.state}>
      <DesktopRail active={active} onMore={() => setMoreOpen(true)} />

      <div className="shell-main">
        <header className="gd-head">
          <div className="gd-head-row">
            {isHome || !title ? (
              <h1 className="gd-mark">
                ORI<em>VELLUM</em>
              </h1>
            ) : (
              <h1 className="gd-mark">
                <em>{title}</em>
              </h1>
            )}
            {isHome && (
              <Link
                href="/system"
                className="gd-iconbtn ml-auto"
                aria-label="Settings"
                data-testid="button-settings"
              >
                <Settings className="w-5 h-5" aria-hidden />
              </Link>
            )}
          </div>

          {tabs && <ContextBar tabs={tabs} activeHref={activeTab} label={title ?? "Section"} />}

          {/* Ambient state ribbon — color + width + text (deuteranopia dual-coding) */}
          <div className="gd-ribbon" aria-hidden>
            <i />
          </div>
          <div className="gd-ribbon-label">
            <b data-testid="text-conn-state">{conn.label}</b>
            {isHome && <span>Sovereign knowledge</span>}
          </div>
        </header>

        {/* Content host — mirrors the legacy AppLayout content surface exactly
            (overflow-auto + padding + max-width), so unmigrated pages keep the
            same scrolling contract they had under the old shells. */}
        <main className="gd-content">
          <div className="flex-1 min-h-0 overflow-auto w-full max-w-[1400px] mx-auto px-4 @[560px]:px-6 @[1024px]:px-8 py-4 @[560px]:py-6 @[1024px]:py-8 flex flex-col">
            {children}
          </div>
        </main>

        <MobileTabBar active={active} onMore={() => setMoreOpen(true)} />
      </div>

      <GlobalActionSheet open={moreOpen} onOpenChange={setMoreOpen} />
    </div>
  );
}
