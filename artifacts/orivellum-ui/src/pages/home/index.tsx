/**
 * Home Screen — the GD-industrial launcher.
 *
 * Wordmark, ambient status ribbon, and large launch tiles (one per app).
 * Tapping a tile enters that app full-screen (AppFrame); everything else is
 * eliminated to stay focused. The old sidebar console remains reachable
 * behind the "Legacy console" row during the transition.
 *
 * Layout laws honored here: one scroll container (.gd-scroll), tile/row
 * primitives only, 4 tiles above the fold on a phone (2×2 grid), 48px
 * thumb targets, ambient state ribbon instead of toasts.
 */
import { useEffect } from "react";
import { useLocation } from "wouter";
import { PanelLeft, ChevronRight } from "lucide-react";
import { APPS, setLegacyShell } from "@/lib/apps";
import { connState } from "@/components/app-frame";
import { useConnectivity } from "@/lib/useConnectivity";

export default function HomeScreen() {
  const [, setLocation] = useLocation();
  const { apiReachable, aiReachable } = useConnectivity();
  const conn = connState(apiReachable, aiReachable);

  // The Home Screen carries no app tint — clear any leftover data-app
  useEffect(() => {
    delete document.documentElement.dataset.app;
  }, []);

  const launch = (entry: string) => {
    setLegacyShell(false);
    setLocation(entry);
  };

  const openLegacy = () => {
    setLegacyShell(true);
    setLocation("/dashboard");
  };

  return (
    <div className="gd-shell" data-conn={conn.state}>
      <header className="gd-head">
        <div className="gd-head-row">
          <h1 className="gd-mark">
            ORI<em>VELLUM</em>
          </h1>
        </div>
        <div className="gd-ribbon" aria-hidden>
          <i />
        </div>
        <div className="gd-ribbon-label">
          <b data-testid="text-conn-state">{conn.label}</b>
          <span>Sovereign knowledge</span>
        </div>
      </header>

      <main className="gd-scroll">
        <p className="gd-eyebrow" style={{ padding: "20px 4px 10px" }}>
          Workspaces
        </p>

        {/* Launch tiles — 2 columns on phones (4 above the fold), 3 wider */}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
          {APPS.map((app) => {
            const Icon = app.icon;
            return (
              <button
                key={app.id}
                className="gd-tile"
                onClick={() => launch(app.entry)}
                data-testid={`tile-${app.id}`}
                style={{ minHeight: 132 }}
              >
                <span
                  className="inline-flex items-center justify-center rounded-[8px]"
                  style={{
                    width: 44,
                    height: 44,
                    background: `var(--gd-${accentVar(app.id)}-soft, var(--gd-accent-soft))`,
                    color: `var(--gd-${accentVar(app.id)}, var(--gd-accent))`,
                    border: "1px solid var(--gd-line)",
                  }}
                  aria-hidden
                >
                  <Icon className="w-6 h-6" strokeWidth={1.75} />
                </span>
                <span className="mt-auto">
                  <span
                    className="block"
                    style={{
                      fontFamily: "var(--gd-stencil)",
                      fontSize: 15,
                      letterSpacing: "0.12em",
                      textTransform: "uppercase",
                    }}
                  >
                    {app.name}
                  </span>
                  <span className="block text-[12px] mt-1" style={{ color: "var(--gd-muted)" }}>
                    {app.tagline}
                  </span>
                </span>
              </button>
            );
          })}
        </div>

        {/* Transition escape hatch — the old sidebar console */}
        <p className="gd-eyebrow" style={{ padding: "24px 4px 10px" }}>
          Transition
        </p>
        <button
          className="gd-row w-full"
          onClick={openLegacy}
          data-testid="row-legacy"
        >
          <PanelLeft className="w-5 h-5" style={{ color: "var(--gd-dim)" }} aria-hidden />
          <span className="flex-1 text-left">
            <span className="block text-[14px] font-medium">Legacy console</span>
            <span className="block text-[12px]" style={{ color: "var(--gd-muted)" }}>
              The old sidebar with every page, while apps are being moved in
            </span>
          </span>
          <ChevronRight className="w-4 h-4" style={{ color: "var(--gd-dim)" }} aria-hidden />
        </button>
      </main>
    </div>
  );
}

/**
 * Map each app to its fixed accent token so tiles keep their identity color
 * on the Home Screen (where no data-app tint is active).
 * writing→bronze · learning/library→olive · chat→sonar · studio/mail→slate ·
 * command→caution.
 */
function accentVar(appId: string): string {
  switch (appId) {
    case "writing":
      return "bronze";
    case "learning":
    case "library":
      return "olive";
    case "chat":
      return "sonar";
    case "studio":
    case "mail":
      return "slate";
    case "command":
      return "caution";
    default:
      return "accent";
  }
}
