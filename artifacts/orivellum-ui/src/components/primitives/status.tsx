import { CheckCircle2, Loader2, AlertTriangle, XCircle, CircleDashed } from "lucide-react";

export type StatusKind = "ok" | "busy" | "warn" | "danger" | "idle";

const STYLE: Record<StatusKind, { icon: typeof CheckCircle2; color: string; spin?: boolean }> = {
  ok: { icon: CheckCircle2, color: "var(--gd-success)" },
  busy: { icon: Loader2, color: "var(--gd-info)", spin: true },
  warn: { icon: AlertTriangle, color: "var(--gd-caution)" },
  danger: { icon: XCircle, color: "var(--gd-danger)" },
  idle: { icon: CircleDashed, color: "var(--gd-dim)" },
};

/**
 * Status — dual-coded status chip: icon shape + text label + color, so state
 * never reads through color alone (deuteranopia pass).
 */
export function Status({ kind, label }: { kind: StatusKind; label: string }) {
  const s = STYLE[kind];
  const Icon = s.icon;
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs font-medium"
      style={{ color: s.color }}
    >
      <Icon className={`w-3.5 h-3.5 ${s.spin ? "animate-spin motion-reduce:animate-none" : ""}`} aria-hidden />
      {label}
    </span>
  );
}
