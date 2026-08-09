/**
 * A-01 Mail Steward — /mail
 *
 * Three-pane desktop layout:
 *  Left   — Attention queue (high → medium → low)
 *  Centre — Message reader (subject, sender, assessment rationale)
 *  Right  — Assessment panel + action buttons
 */
import { useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { useLocation } from "wouter";
import {
  Mail, MailOpen, RefreshCw, Settings, Plug, Loader2,
  Shield, ShieldAlert, ShieldCheck, AlertTriangle,
  MoveRight, Reply, MoveLeft, Clock, CheckCircle2,
  Inbox, ArrowRightCircle, RotateCcw, BookOpen, Globe,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ─────────────────────────────────────────────────────────────────────

interface MailSummary {
  connected: boolean;
  send_enabled: boolean;
  total_synced: number;
  high_attention: number;
  pending_actions: number;
  unread: number;
}

interface MailRecord {
  id: string;
  subject: string | null;
  sender_name: string | null;
  sender_domain: string | null;
  received_at: string | null;
  has_attachments: boolean;
  importance: string;
  is_read: boolean;
  lifecycle_state: string;
  attention_level: string | null;
  needs_reply: boolean | null;
  recommended_action: string | null;
  confidence: number | null;
  is_high_risk: boolean | null;
  assessment_id: string | null;
  action_request_id: string | null;
}

interface MailAssessment {
  attention_level: string;
  rationale: string;
  suggested_reply: string | null;
  needs_reply: boolean;
  recommended_action: string;
  confidence: number;
  is_high_risk: boolean;
  injection_flagged: boolean;
  model_id: string;
  signals_json: string;
}

interface ActionOption {
  type: string;
  nonce: string;
  label: string;
  action_request_id?: string;   // present on UNDO_MOVE actions
}

interface DecisionDetail {
  record: MailRecord;
  assessment: MailAssessment | null;
  available_actions: ActionOption[];
  audit_trail: any[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtDate(iso: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  if (d.toDateString() === now.toDateString()) {
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleDateString([], { month: "short", day: "numeric" });
}

function attentionStyle(level: string | null) {
  if (level === "high")   return { color: "var(--rust)", background: "var(--rust-soft)", borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)" };
  if (level === "medium") return { color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" };
  return { color: "var(--ink-soft)", background: "transparent", borderColor: "var(--line)" };
}

function AttentionBadge({ level }: { level: string | null }) {
  const label = level === "high" ? "High" : level === "medium" ? "Med" : "Low";
  return (
    <span
      className="text-[10px] font-semibold px-1.5 py-0.5 rounded border uppercase tracking-wide"
      style={attentionStyle(level)}
    >
      {label}
    </span>
  );
}

function ConfidenceBar({ value }: { value: number | null }) {
  const pct = Math.round((value ?? 0) * 100);
  const color = pct >= 80 ? "var(--green-2)" : pct >= 50 ? "var(--gilt)" : "var(--rust)";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
        <div className="h-full rounded-full transition-all" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="text-xs tabular-nums" style={{ color }}>{pct}%</span>
    </div>
  );
}

// ── Queue pane ─────────────────────────────────────────────────────────────────

function QueueItem({ record, selected, onClick }: { record: MailRecord; selected: boolean; onClick: () => void }) {
  return (
    <button
      className={`w-full text-left px-3 py-2.5 border-b border-border/30 hover:bg-accent/30 transition-colors focus:outline-none ${selected ? "bg-accent/50" : ""}`}
      onClick={onClick}
    >
      <div className="flex items-start justify-between gap-2 mb-0.5">
        <span className="text-sm font-medium truncate flex-1 leading-snug">
          {record.is_read ? null : <span className="inline-block w-1.5 h-1.5 rounded-full bg-primary mr-1.5 mb-0.5 align-middle" />}
          {record.subject || "(no subject)"}
        </span>
        <span className="text-[11px] text-muted-foreground whitespace-nowrap shrink-0">{fmtDate(record.received_at)}</span>
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        <span className="text-xs text-muted-foreground truncate">@{record.sender_domain || "unknown"}</span>
        {record.attention_level && record.attention_level !== "low" && (
          <AttentionBadge level={record.attention_level} />
        )}
        {record.needs_reply && (
          <span className="text-[10px] border rounded px-1 py-0.5 uppercase tracking-wide"
            style={{ color: "var(--gilt)", borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" }}>
            Reply
          </span>
        )}
        {record.is_high_risk && (
          <span className="text-[10px] border rounded px-1 py-0.5 uppercase tracking-wide"
            style={{ color: "var(--rust)", borderColor: "color-mix(in srgb,var(--rust) 28%,transparent)", background: "var(--rust-soft)" }}>
            Risk
          </span>
        )}
      </div>
    </button>
  );
}

// ── Message reader ─────────────────────────────────────────────────────────────

function MessageReader({ detail }: { detail: DecisionDetail | null; loading: boolean }) {
  if (!detail) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-muted-foreground gap-3 p-8">
        <MailOpen size={40} className="opacity-30" />
        <p className="text-sm">Select a message to read</p>
      </div>
    );
  }

  const { record, assessment } = detail;
  const signals: string[] = (() => {
    try { return assessment ? JSON.parse((assessment as any).signals_json || "[]") : []; } catch { return []; }
  })();

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      <div className="px-5 py-4 border-b border-border/40">
        <h2 className="text-base font-semibold leading-snug mb-1">{record.subject || "(no subject)"}</h2>
        <div className="flex items-center gap-3 text-sm text-muted-foreground flex-wrap">
          <span className="font-medium text-foreground">{record.sender_name || "@" + (record.sender_domain || "unknown")}</span>
          <span>@{record.sender_domain}</span>
          <span>{record.received_at ? new Date(record.received_at).toLocaleString() : ""}</span>
          {record.has_attachments && <Badge variant="outline" className="text-[10px] px-1.5 py-0">📎 Attachment</Badge>}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        {/* Assessment rationale */}
        {assessment && (
          <div className="glass-card rounded-lg p-4 space-y-3">
            <div className="flex items-center gap-2">
              <Shield size={14} className="shrink-0" style={{ color: "var(--gilt)" }} />
              <span className="text-xs font-semibold uppercase tracking-wide" style={{ color: "var(--gilt)" }}>
                AI Assessment
              </span>
              <span className="ml-auto text-xs text-muted-foreground">{assessment.model_id}</span>
            </div>
            <p className="text-sm leading-relaxed">{assessment.rationale}</p>
            <ConfidenceBar value={assessment.confidence} />
          </div>
        )}

        {/* Threat evidence */}
        {signals.length > 0 && (
          <div className="rounded-lg p-3 border space-y-1"
            style={{ borderColor: "color-mix(in srgb,var(--rust) 28%,transparent)", background: "var(--rust-soft)" }}>
            <div className="flex items-center gap-1.5 mb-2">
              <ShieldAlert size={13} style={{ color: "var(--rust)" }} />
              <span className="text-xs font-semibold" style={{ color: "var(--rust)" }}>Threat signals</span>
            </div>
            {signals.map((s, i) => (
              <p key={i} className="text-xs" style={{ color: "var(--rust)" }}>• {s}</p>
            ))}
          </div>
        )}

        {/* Suggested reply preview */}
        {assessment?.suggested_reply && (
          <div className="glass-card rounded-lg p-4">
            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">Suggested reply</p>
            <p className="text-sm leading-relaxed whitespace-pre-wrap text-muted-foreground">{assessment.suggested_reply}</p>
          </div>
        )}

        {/* State chip */}
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Clock size={12} />
          <span>State: {record.lifecycle_state}</span>
        </div>
      </div>
    </div>
  );
}

// ── Assessment panel ──────────────────────────────────────────────────────────

interface WorkOption { id: string; title: string | null; }

function AssessmentPanel({
  detail, sendEnabled, works, onCompose, onMove, onUndo, onDefer, onSync,
  onAddToKnowledge, loading,
}: {
  detail: DecisionDetail | null;
  sendEnabled: boolean;
  works: WorkOption[];
  onCompose: (action: ActionOption) => void;
  onMove: (action: ActionOption) => void;
  onUndo: (action: ActionOption) => void;
  onDefer: () => void;
  onSync: () => void;
  onAddToKnowledge: (workId: string | null, research: boolean) => Promise<void>;
  loading: boolean;
}) {
  const [knowledgeOpen,    setKnowledgeOpen]    = useState(false);
  const [knowledgeWorkId,  setKnowledgeWorkId]  = useState("");
  const [knowledgeResearch, setKnowledgeResearch] = useState(false);
  const [knowledgeSaving,  setKnowledgeSaving]  = useState(false);
  if (!detail) {
    return (
      <div className="flex flex-col gap-3 p-4">
        <Button variant="outline" size="sm" className="w-full gap-2" onClick={onSync}>
          <RefreshCw size={13} />
          Sync now
        </Button>
      </div>
    );
  }

  const { record, assessment, available_actions } = detail;
  const draftAction = available_actions.find(a => a.type === "CREATE_DRAFT");
  const moveAction  = available_actions.find(a => a.type === "MOVE");
  const undoAction  = available_actions.find(a => a.type === "UNDO_MOVE");

  return (
    <div className="flex flex-col gap-3 p-4 overflow-y-auto">
      {/* Attention level */}
      <div className="glass-card rounded-lg p-3 space-y-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Attention</p>
        <div className="flex items-center gap-2">
          <AttentionBadge level={record.attention_level} />
          {record.needs_reply && <span className="text-xs text-muted-foreground">• needs reply</span>}
        </div>
        {assessment && (
          <div className="pt-1">
            <p className="text-xs text-muted-foreground mb-1">Confidence</p>
            <ConfidenceBar value={assessment.confidence} />
          </div>
        )}
        {assessment?.recommended_action && assessment.recommended_action !== "NONE" && (
          <p className="text-xs text-muted-foreground">
            Recommended: <span className="font-medium text-foreground">{assessment.recommended_action.replace(/_/g, " ").toLowerCase()}</span>
          </p>
        )}
      </div>

      {/* Actions */}
      <div className="space-y-2">
        {draftAction && (
          <Button
            className="w-full gap-2 justify-start"
            size="sm"
            onClick={() => onCompose(draftAction)}
            disabled={loading}
          >
            <Reply size={13} />
            {sendEnabled ? "Compose & send reply" : "Compose reply"}
          </Button>
        )}
        {moveAction && (
          <Button
            variant="outline"
            className="w-full gap-2 justify-start"
            size="sm"
            onClick={() => onMove(moveAction)}
            disabled={loading}
          >
            <MoveRight size={13} />
            Move to Review
          </Button>
        )}
        {undoAction && (
          <Button
            variant="outline"
            className="w-full gap-2 justify-start"
            size="sm"
            onClick={() => onUndo(undoAction)}
            disabled={loading}
            title="Move this message back to its original folder"
          >
            <RotateCcw size={13} />
            Undo move
          </Button>
        )}
        <Button
          variant="ghost"
          className="w-full gap-2 justify-start text-muted-foreground"
          size="sm"
          onClick={onDefer}
          disabled={loading}
        >
          <Clock size={13} />
          Defer
        </Button>
      </div>

      {/* Risk warning */}
      {record.is_high_risk && (
        <div className="rounded p-2 text-xs border"
          style={{ color: "var(--rust)", borderColor: "color-mix(in srgb,var(--rust) 28%,transparent)", background: "var(--rust-soft)" }}>
          <ShieldAlert size={11} className="inline mr-1" />
          High-risk message — compose action unavailable until risk is reviewed.
        </div>
      )}

      {/* Add to Knowledge */}
      <div className="border-t border-border/30 pt-2 mt-1 space-y-2">
        {!knowledgeOpen ? (
          <Button
            variant="outline"
            size="sm"
            className="w-full gap-2 justify-start"
            onClick={() => setKnowledgeOpen(true)}
            disabled={loading}
          >
            <BookOpen size={13} />
            Save to Knowledge
          </Button>
        ) : (
          <div className="space-y-2 rounded-lg border border-border/50 p-2.5 bg-muted/20">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">Save to Knowledge</p>
            <select
              value={knowledgeWorkId}
              onChange={e => setKnowledgeWorkId(e.target.value)}
              className="w-full text-xs rounded border border-border bg-background px-2 py-1 outline-none focus:ring-1 focus:ring-primary/40"
            >
              <option value="">Global (no Work)</option>
              {works.map(w => (
                <option key={w.id} value={w.id}>{w.title ?? "Untitled"}</option>
              ))}
            </select>
            <label className="flex items-center gap-2 cursor-pointer text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={knowledgeResearch}
                onChange={e => setKnowledgeResearch(e.target.checked)}
                className="rounded"
              />
              <Globe size={11} />
              Research sender online first
            </label>
            <div className="flex gap-1.5">
              <Button
                size="sm"
                className="flex-1 text-xs h-7"
                disabled={knowledgeSaving}
                onClick={async () => {
                  setKnowledgeSaving(true);
                  try {
                    await onAddToKnowledge(knowledgeWorkId || null, knowledgeResearch);
                    setKnowledgeOpen(false);
                    setKnowledgeWorkId("");
                    setKnowledgeResearch(false);
                  } finally {
                    setKnowledgeSaving(false);
                  }
                }}
              >
                {knowledgeSaving ? <Loader2 size={11} className="animate-spin" /> : "Save"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="text-xs h-7 px-2"
                onClick={() => setKnowledgeOpen(false)}
                disabled={knowledgeSaving}
              >
                Cancel
              </Button>
            </div>
          </div>
        )}
      </div>

      <Button variant="outline" size="sm" className="w-full gap-2 mt-2" onClick={onSync}>
        <RefreshCw size={13} />
        Sync inbox
      </Button>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function MailPage() {
  const [, navigate] = useLocation();
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [acting, setActing] = useState(false);

  // Works list — for the "Save to Knowledge" work selector
  const { data: worksResp } = useQuery<{ works: WorkOption[] }>({
    queryKey: ["works-list"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works`);
      return r.ok ? r.json() : { works: [] };
    },
    staleTime: 60_000,
  });
  const works = worksResp?.works ?? [];

  // Summary (connected state, counts)
  const { data: summary, isLoading: sumLoading } = useQuery<MailSummary>({
    queryKey: ["mail-summary"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/mail/summary`);
      if (!r.ok) throw new Error("mail summary failed");
      return r.json();
    },
    refetchInterval: 30_000,
  });

  // Decision queue
  const { data: queue, isLoading: queueLoading } = useQuery<{ decisions: MailRecord[]; total: number }>({
    queryKey: ["mail-attention"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/mail/attention?limit=100`);
      if (!r.ok) throw new Error("mail attention failed");
      return r.json();
    },
    enabled: !!summary?.connected,
    refetchInterval: 30_000,
  });

  // Selected decision detail
  const { data: detail, isLoading: detailLoading } = useQuery<DecisionDetail>({
    queryKey: ["mail-decision", selectedId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/mail/decisions/${selectedId}`);
      if (!r.ok) throw new Error("decision not found");
      return r.json();
    },
    enabled: !!selectedId,
  });

  const invalidate = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["mail-summary"] });
    qc.invalidateQueries({ queryKey: ["mail-attention"] });
    if (selectedId) qc.invalidateQueries({ queryKey: ["mail-decision", selectedId] });
  }, [qc, selectedId]);

  const handleSync = useCallback(async () => {
    try {
      await apiFetch(`${BASE}/mail/sync`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      toast.success("Sync started");
      setTimeout(invalidate, 3000);
    } catch { toast.error("Sync failed"); }
  }, [invalidate]);

  const handleCompose = useCallback(async (action: ActionOption) => {
    if (!selectedId) return;
    setActing(true);
    try {
      const r = await apiFetch(`${BASE}/mail/decisions/${selectedId}/draft`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nonce: action.nonce }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error((err as any).detail || "Draft creation failed");
      }
      const data = await r.json();
      toast.success("Draft created in Outlook");
      // sendNonce is NOT passed in the URL — it is fetched fresh at send time
      // to avoid exposing single-use authorization tokens in browser history.
      navigate(`/mail/compose/${data.action_request_id}?recordId=${selectedId}`);
    } catch (e: any) {
      toast.error(e.message || "Failed to create draft");
    } finally {
      setActing(false);
    }
  }, [selectedId, navigate]);

  const handleMove = useCallback(async (action: ActionOption) => {
    if (!selectedId) return;
    setActing(true);
    try {
      const r = await apiFetch(`${BASE}/mail/decisions/${selectedId}/move`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ destination: "review", nonce: action.nonce }),
      });
      if (!r.ok) throw new Error("Move failed");
      toast.success("Moved to Review folder");
      invalidate();
      setSelectedId(null);
    } catch (e: any) {
      toast.error(e.message || "Move failed");
    } finally {
      setActing(false);
    }
  }, [selectedId, invalidate]);

  const handleUndo = useCallback(async (action: ActionOption) => {
    const arId = action.action_request_id;
    if (!arId) return;
    setActing(true);
    try {
      const r = await apiFetch(`${BASE}/mail/actions/${arId}/undo`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nonce: action.nonce }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error((err as any).detail || "Undo failed");
      }
      toast.success("Move reversed — message returned to its original folder");
      invalidate();
      setSelectedId(null);
    } catch (e: any) {
      toast.error(e.message || "Could not undo move");
    } finally {
      setActing(false);
    }
  }, [invalidate]);

  const handleAddToKnowledge = useCallback(async (workId: string | null, research: boolean) => {
    if (!selectedId) return;
    const r = await apiFetch(`${BASE}/mail/decisions/${selectedId}/add-to-knowledge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ work_id: workId, research }),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error((err as any).detail ?? "Could not save to knowledge");
    }
    const data = await r.json();
    toast.success(data.researched
      ? "Saved to knowledge with online research"
      : "Saved to knowledge base");
  }, [selectedId]);

  const handleDefer = useCallback(() => {
    setSelectedId(null);
    toast("Deferred — message stays in queue");
  }, []);

  // Redirect to connect page if not connected
  if (!sumLoading && summary && !summary.connected) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-6 p-8">
        <div className="glass-card rounded-xl p-8 max-w-sm text-center space-y-4">
          <div className="w-12 h-12 rounded-full bg-accent/40 flex items-center justify-center mx-auto">
            <Mail size={24} style={{ color: "var(--gilt)" }} />
          </div>
          <h2 className="text-lg font-semibold">Connect your Outlook</h2>
          <p className="text-sm text-muted-foreground leading-relaxed">
            Link your Microsoft account to start reviewing AI-assessed email.
            Your message body is never stored — only metadata and AI analysis.
          </p>
          <Button className="w-full gap-2" onClick={() => navigate("/mail/connect")}>
            <Plug size={14} />
            Connect Outlook
          </Button>
          <Button variant="ghost" size="sm" className="w-full gap-1" onClick={() => navigate("/mail/settings")}>
            <Settings size={12} />
            Settings
          </Button>
        </div>
      </div>
    );
  }

  const decisions = queue?.decisions ?? [];
  const sorted = [...decisions].sort((a, b) => {
    const levelOrder = { high: 0, medium: 1, low: 2 };
    const la = levelOrder[a.attention_level as keyof typeof levelOrder] ?? 2;
    const lb = levelOrder[b.attention_level as keyof typeof levelOrder] ?? 2;
    if (la !== lb) return la - lb;
    return (b.received_at ?? "").localeCompare(a.received_at ?? "");
  });

  return (
    <div className="flex-1 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border/40">
        <div className="flex items-center gap-3">
          <Mail size={16} style={{ color: "var(--gilt)" }} />
          <span className="font-semibold text-sm">Correspondence</span>
          {summary?.high_attention ? (
            <Badge variant="destructive" className="text-[10px] px-1.5 py-0">
              {summary.high_attention} high
            </Badge>
          ) : null}
          {summary?.unread ? (
            <span className="text-xs text-muted-foreground">{summary.unread} unread</span>
          ) : null}
          {sumLoading && <Loader2 size={12} className="animate-spin text-muted-foreground" />}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={handleSync} title="Sync now">
            <RefreshCw size={13} />
          </Button>
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => navigate("/mail/settings")} title="Settings">
            <Settings size={13} />
          </Button>
        </div>
      </div>

      {/* Three-pane layout */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: queue */}
        <div className="w-72 shrink-0 border-r border-border/40 flex flex-col overflow-hidden">
          <div className="px-3 py-2 border-b border-border/20 flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Queue ({sorted.length})
            </span>
          </div>
          <div className="flex-1 overflow-y-auto">
            {queueLoading ? (
              <div className="space-y-px p-1">
                {[...Array(5)].map((_, i) => (
                  <div key={i} className="px-3 py-3 space-y-1">
                    <Skeleton className="h-3.5 w-full" />
                    <Skeleton className="h-3 w-2/3" />
                  </div>
                ))}
              </div>
            ) : sorted.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-2 p-6 text-muted-foreground">
                <Inbox size={28} className="opacity-30" />
                <p className="text-xs text-center">No messages need attention</p>
              </div>
            ) : sorted.map(r => (
              <QueueItem
                key={r.id}
                record={r}
                selected={r.id === selectedId}
                onClick={() => setSelectedId(r.id)}
              />
            ))}
          </div>
        </div>

        {/* Centre: reader */}
        <div className="flex-1 flex flex-col overflow-hidden border-r border-border/40">
          {detailLoading ? (
            <div className="flex-1 p-5 space-y-4">
              <Skeleton className="h-5 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : (
            <MessageReader detail={detail ?? null} loading={detailLoading} />
          )}
        </div>

        {/* Right: assessment panel */}
        <div className="w-56 shrink-0 overflow-y-auto">
          <AssessmentPanel
            detail={detail ?? null}
            sendEnabled={summary?.send_enabled ?? false}
            works={works}
            onCompose={handleCompose}
            onMove={handleMove}
            onUndo={handleUndo}
            onDefer={handleDefer}
            onSync={handleSync}
            onAddToKnowledge={handleAddToKnowledge}
            loading={acting}
          />
        </div>
      </div>
    </div>
  );
}
