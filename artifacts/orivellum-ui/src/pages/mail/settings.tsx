/**
 * A-01 Mail Steward — /mail/settings
 */
import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { useLocation } from "wouter";
import {
  ArrowLeft, Save, Trash2, Loader2, AlertTriangle,
} from "lucide-react";
import {
  Page, Panel, Status, ErrorState, LoadingState, ConfirmAction,
} from "@/components/primitives";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface MailSettings {
  send_enabled: boolean;
  lemonade_url: string;
  lemonade_model: string;
  sync_folders: string[];
  account_display: string;
  threat_feeds_enabled: boolean;
  context_days: number;
}

export default function MailSettingsPage() {
  const [, navigate] = useLocation();
  const qc = useQueryClient();
  const [saving, setSaving] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [disconnectOpen, setDisconnectOpen] = useState(false);

  const [lemonadeUrl,   setLemonadeUrl]   = useState("");
  const [lemonadeModel, setLemonadeModel] = useState("");
  const [syncFolders,   setSyncFolders]   = useState("inbox");
  const [sendEnabled,   setSendEnabled]   = useState(false);
  const [feedsEnabled,  setFeedsEnabled]  = useState(true);
  const [contextDays,   setContextDays]   = useState("30");

  const { data: settings, isLoading, isError, refetch } = useQuery<MailSettings>({
    queryKey: ["mail-settings"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/mail/settings`);
      if (!r.ok) throw new Error("failed");
      return r.json();
    },
  });

  const { data: summary } = useQuery<{ connected: boolean }>({
    queryKey: ["mail-summary"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/mail/summary`);
      return r.json();
    },
  });

  useEffect(() => {
    if (!settings) return;
    setLemonadeUrl(settings.lemonade_url);
    setLemonadeModel(settings.lemonade_model);
    setSyncFolders((settings.sync_folders || ["inbox"]).join(", "));
    setSendEnabled(settings.send_enabled);
    setFeedsEnabled(settings.threat_feeds_enabled);
    setContextDays(String(settings.context_days ?? 30));
  }, [settings]);

  const handleSave = async () => {
    setSaving(true);
    try {
      // Always send all fields so users can clear previously-set values.
      // Empty lemonade_url resets to the server default; empty lemonade_model
      // clears the override. sync_folders falls back to ["inbox"] when blank.
      const folders = syncFolders.split(",").map(s => s.trim()).filter(Boolean);
      const r = await apiFetch(`${BASE}/mail/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lemonade_url:         lemonadeUrl,          // "" = reset to default
          lemonade_model:       lemonadeModel,        // "" = clear override
          sync_folders:         folders.length ? folders : ["inbox"],
          send_enabled:         sendEnabled,
          threat_feeds_enabled: feedsEnabled,
          context_days:         Math.max(0, parseInt(contextDays, 10) || 0),
        }),
      });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        throw new Error((e as any).detail || "Save failed");
      }
      toast.success("Settings saved");
      qc.invalidateQueries({ queryKey: ["mail-settings"] });
      qc.invalidateQueries({ queryKey: ["mail-summary"] });
    } catch (e: any) {
      toast.error(e.message || "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const handleDisconnect = async () => {
    setDisconnecting(true);
    try {
      const r = await apiFetch(`${BASE}/mail/disconnect`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: "disconnect" }),
      });
      if (!r.ok) throw new Error("Disconnect failed");
      toast.success("Disconnected from Outlook");
      qc.invalidateQueries({ queryKey: ["mail-summary"] });
      navigate("/mail");
    } catch (e: any) {
      toast.error(e.message || "Disconnect failed");
    } finally {
      setDisconnecting(false);
    }
  };

  return (
    <div className="flex-1 flex flex-col overflow-y-auto">
      <Page
        eyebrow="Mail Steward"
        title="Mail settings"
        actions={
          <Button variant="ghost" size="icon" className="min-h-11 min-w-11" onClick={() => navigate("/mail")} title="Back to Mail">
            <ArrowLeft size={14} />
          </Button>
        }
      >
        {/* Account */}
        <Panel className="space-y-3">
          <h2 className="text-sm font-semibold">Account</h2>
          {isLoading ? (
            <LoadingState rows={1} label="Loading account" />
          ) : isError ? (
            <ErrorState
              title="Couldn't load mail settings"
              detail="The settings could not be fetched."
              onRetry={() => refetch()}
            />
          ) : (
            <>
              {settings?.account_display ? (
                <div className="flex items-center gap-2 flex-wrap">
                  <Status kind="ok" label={settings.account_display} />
                  <Badge variant="outline" className="text-[10px]">Connected</Badge>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Not connected</p>
              )}
              {summary?.connected && (
                <ConfirmAction
                  open={disconnectOpen}
                  onOpenChange={setDisconnectOpen}
                  title="Disconnect Outlook?"
                  consequence="All synced mail records will remain but no new mail will be fetched."
                  confirmLabel="Disconnect"
                  destructive
                  onConfirm={handleDisconnect}
                  trigger={
                    <Button
                      variant="destructive"
                      size="sm"
                      className="gap-2 min-h-11"
                      disabled={disconnecting}
                      data-testid="button-disconnect-outlook"
                    >
                      {disconnecting ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                      Disconnect Outlook
                    </Button>
                  }
                />
              )}
              {!summary?.connected && (
                <Button size="sm" className="gap-2 min-h-11" onClick={() => navigate("/mail/connect")}>
                  Connect Outlook
                </Button>
              )}
            </>
          )}
        </Panel>

        {/* Send gate */}
        <Panel className="space-y-3">
          <h2 className="text-sm font-semibold">Send gate</h2>
          <div className="flex items-start gap-3">
            <button
              className={`mt-0.5 w-9 h-5 rounded-full border-2 transition-colors relative ${sendEnabled ? "bg-primary border-primary" : "bg-muted border-border"}`}
              onClick={() => setSendEnabled(v => !v)}
              role="switch"
              aria-checked={sendEnabled}
            >
              <span className={`absolute top-0.5 w-3.5 h-3.5 rounded-full bg-primary-foreground shadow transition-transform ${sendEnabled ? "translate-x-4" : "translate-x-0.5"}`} />
            </button>
            <div>
              <p className="text-sm font-medium">Enable send</p>
              <p className="text-xs text-muted-foreground leading-relaxed">
                Requires <code className="text-[11px] bg-muted px-1 rounded">Mail.Send</code> scope in your Entra app registration.
                Without this scope enabled in Azure Portal the send call will return 403.
              </p>
            </div>
          </div>
          {sendEnabled && (
            <div className="flex items-start gap-2 rounded p-2 text-xs border"
              style={{ borderColor: "var(--gd-line-control)", background: "var(--gd-bronze-soft)", color: "var(--gd-bronze)" }}>
              <AlertTriangle size={11} className="shrink-0 mt-0.5" />
              Make sure <strong>Mail.Send</strong> is added as a delegated permission in your Entra app before enabling this.
            </div>
          )}
        </Panel>

        {/* AI model */}
        <Panel className="space-y-3">
          <h2 className="text-sm font-semibold">Local AI model (Lemonade)</h2>
          <div className="space-y-2">
            <label className="text-xs text-muted-foreground">URL</label>
            <Input
              value={lemonadeUrl}
              onChange={e => setLemonadeUrl(e.target.value)}
              placeholder="http://127.0.0.1:13305/api/v1"
              className="font-mono text-sm"
            />
          </div>
          <div className="space-y-2">
            <label className="text-xs text-muted-foreground">Model ID (optional)</label>
            <Input
              value={lemonadeModel}
              onChange={e => setLemonadeModel(e.target.value)}
              placeholder="Leave blank to use server default"
              className="font-mono text-sm"
            />
          </div>
        </Panel>

        {/* Sync */}
        <Panel className="space-y-3">
          <h2 className="text-sm font-semibold">Sync folders</h2>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Comma-separated folder names</label>
            <Input
              value={syncFolders}
              onChange={e => setSyncFolders(e.target.value)}
              placeholder="inbox"
              className="font-mono text-sm"
            />
            <p className="text-xs text-muted-foreground">e.g. inbox, sentitems, junkemail</p>
          </div>
        </Panel>

        {/* Chat context window */}
        <Panel className="space-y-3">
          <h2 className="text-sm font-semibold">Chat context window</h2>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Days</label>
            <Input
              type="number"
              min={0}
              value={contextDays}
              onChange={e => setContextDays(e.target.value)}
              placeholder="30"
              className="font-mono text-sm w-28"
            />
            <p className="text-xs text-muted-foreground leading-relaxed">
              Only emails received within this many days are injected into chat.
              Set to <code className="text-[11px] bg-muted px-1 rounded">0</code> to include all time.
            </p>
          </div>
        </Panel>

        {/* Threat feeds */}
        <Panel className="space-y-3">
          <h2 className="text-sm font-semibold">Threat feeds</h2>
          <div className="flex items-center gap-3">
            <button
              className={`w-9 h-5 rounded-full border-2 transition-colors relative ${feedsEnabled ? "bg-primary border-primary" : "bg-muted border-border"}`}
              onClick={() => setFeedsEnabled(v => !v)}
              role="switch"
              aria-checked={feedsEnabled}
            >
              <span className={`absolute top-0.5 w-3.5 h-3.5 rounded-full bg-primary-foreground shadow transition-transform ${feedsEnabled ? "translate-x-4" : "translate-x-0.5"}`} />
            </button>
            <p className="text-sm">Enable OpenPhish + URLhaus checks</p>
          </div>
        </Panel>

        <Button className="w-full gap-2 min-h-11" onClick={handleSave} disabled={saving}>
          {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />}
          Save settings
        </Button>
      </Page>
    </div>
  );
}
