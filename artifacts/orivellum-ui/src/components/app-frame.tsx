/**
 * AppFrame — the GD-industrial full-screen app shell.
 *
 * Wraps a single app's pages with an immovable header: Home control (48px),
 * stencil app name, per-app nav strip, and the ambient connectivity ribbon.
 * The shell never moves (Layout Law 6); route changes swap content only.
 *
 * Legacy pages manage their own scrolling (as they did under AppLayout's
 * <main overflow-hidden>), so the content host is .gd-content, not .gd-scroll.
 */
import React, { useEffect } from "react";
import { useLocation, Link } from "wouter";
import { LayoutGrid } from "lucide-react";
import type { AppDef } from "@/lib/apps";
import { useConnectivity } from "@/lib/useConnectivity";

export function connState(apiReachable: boolean, aiReachable: boolean): {
  state: "up" | "degraded" | "down";
  label: string;
} {
  if (!apiReachable) return { state: "down", label: "OFFLINE" };
  if (!aiReachable) return { state: "degraded", label: "AI OFFLINE" };
  return { state: "up", label: "ONLINE" };
}

export function AppFrame({ app, children }: { app: AppDef; children: React.ReactNode }) {
  const [location, setLocation] = useLocation();
  const path = location.split("?")[0];
  const { apiReachable, aiReachable } = useConnectivity();
  const conn = connState(apiReachable, aiReachable);

  // Per-app accent tint on <html data-app> — steel never changes, only accent
  useEffect(() => {
    document.documentElement.dataset.app = app.id;
    return () => {
      delete document.documentElement.dataset.app;
    };
  }, [app.id]);

  const goHome = () => {
    setLocation("/");
  };

  // A chip is active when its href matches the path — but if several route
  // hrefs prefix-match (e.g. "/learning" and "/learning/review"), only the
  // longest (most specific) match lights up.
  const matches = (href: string) => path === href || path.startsWith(href + "/");
  const bestMatch = app.routes
    .filter((r) => matches(r.href))
    .reduce<string | null>((best, r) => (best && best.length >= r.href.length ? best : r.href), null);
  const isActive = (href: string) => href === bestMatch;

  return (
    <div className="gd-shell @container" data-conn={conn.state}>
      <header className="gd-head">
        <div className="gd-head-row">
          <button
            className="gd-iconbtn"
            onClick={goHome}
            aria-label="Home Screen"
            data-testid="button-home"
          >
            <LayoutGrid className="w-5 h-5" aria-hidden />
          </button>
          <h1 className="gd-mark">
            <em>{app.name}</em>
          </h1>
          {/* Per-app nav strip — only this app's pages (Law: full-screen focus) */}
          {app.routes.length > 1 && (
            <nav
              className="ml-auto flex gap-2 overflow-x-auto"
              style={{ scrollbarWidth: "none", WebkitOverflowScrolling: "touch" }}
              aria-label={`${app.name} navigation`}
            >
              {app.routes.map((r) => (
                <Link
                  key={r.href}
                  href={r.href}
                  className="gd-chip"
                  data-active={isActive(r.href)}
                  data-testid={`nav-${r.href.slice(1)}`}
                >
                  {r.name}
                </Link>
              ))}
            </nav>
          )}
        </div>
        {/* Ambient state ribbon — color + width + text (deuteranopia dual-coding) */}
        <div className="gd-ribbon" aria-hidden>
          <i />
        </div>
        <div className="gd-ribbon-label">
          <b data-testid="text-conn-state">{conn.label}</b>
        </div>
      </header>
      {/* Content host — mirrors the legacy AppLayout content surface exactly
          (overflow-auto + padding + max-width), so unmigrated pages keep the
          same scrolling contract they had under the old sidebar shell. */}
      <main className="gd-content">
        <div className="flex-1 min-h-0 overflow-auto w-full max-w-[1400px] mx-auto px-4 @[560px]:px-6 @[1024px]:px-8 py-4 @[560px]:py-6 @[1024px]:py-8 flex flex-col">
          {children}
        </div>
      </main>
    </div>
  );
}
