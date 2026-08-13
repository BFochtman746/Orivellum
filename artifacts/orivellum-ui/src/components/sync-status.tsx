/**
 * SyncStatusChip — honest, single-glance sync state for the chat surface.
 *
 * Distinguishes the states that matter on a phone:
 *   offline      — the device itself has no network
 *   unreachable  — device online, but the Orivellum server can't be reached
 *   ai-offline   — server fine, AI engine down (messages still deliver)
 *   syncing      — queued operations are flushing right now
 *   queued       — N operations saved on this device awaiting delivery
 *   synced       — nothing pending, everything reachable
 */

import { CloudOff, Loader2, RefreshCw, Wifi, WifiOff } from "lucide-react";

import { useOutboxState } from "@/hooks/use-outbox";
import { useConnectivity } from "@/lib/useConnectivity";

export type SyncState =
  | "offline"
  | "unreachable"
  | "ai-offline"
  | "syncing"
  | "queued"
  | "synced";

export function computeSyncState(args: {
  online: boolean;
  apiReachable: boolean;
  aiReachable: boolean;
  flushing: boolean;
  queued: number;
}): SyncState {
  if (!args.online) return "offline";
  if (!args.apiReachable) return "unreachable";
  if (args.flushing) return "syncing";
  if (args.queued > 0) return "queued";
  if (!args.aiReachable) return "ai-offline";
  return "synced";
}

const LABELS: Record<SyncState, (n: number) => string> = {
  offline: (n) => (n > 0 ? `Offline — queued (${n})` : "Offline"),
  unreachable: (n) => (n > 0 ? `Server unreachable — queued (${n})` : "Server unreachable"),
  "ai-offline": () => "AI offline",
  syncing: () => "Syncing…",
  queued: (n) => `Queued (${n})`,
  synced: () => "Synced",
};

export function SyncStatusChip({ compact = false }: { compact?: boolean }) {
  const { apiReachable, aiReachable } = useConnectivity();
  const { queued, flushing, online } = useOutboxState();
  const state = computeSyncState({ online, apiReachable, aiReachable, flushing, queued });
  const label = LABELS[state](queued);

  const icon =
    state === "syncing" ? (
      <Loader2 className="w-3 h-3 animate-spin" />
    ) : state === "offline" ? (
      <WifiOff className="w-3 h-3" />
    ) : state === "unreachable" ? (
      <CloudOff className="w-3 h-3" />
    ) : state === "queued" ? (
      <RefreshCw className="w-3 h-3" />
    ) : state === "ai-offline" ? (
      <WifiOff className="w-3 h-3" />
    ) : (
      <Wifi className="w-3 h-3" />
    );

  const tone =
    state === "synced"
      ? { color: "hsl(var(--primary))" }
      : state === "offline" || state === "unreachable"
        ? { color: "var(--gd-danger)" }
        : state === "ai-offline"
          ? undefined
          : { color: "var(--gd-bronze)" };

  return (
    <span
      data-testid="sync-status-chip"
      data-sync-state={state}
      className={`flex items-center gap-1.5 font-mono ${compact ? "text-[10px]" : "text-xs"} ${
        tone ? "" : "text-muted-foreground"
      }`}
      style={tone}
      title={
        state === "queued" || state === "offline" || state === "unreachable"
          ? "Saved on this device — will deliver when the connection returns"
          : undefined
      }
    >
      {icon}
      <span>{label}</span>
    </span>
  );
}
