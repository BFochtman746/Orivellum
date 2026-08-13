/**
 * Commonplace — /notes
 *
 * Frictionless daily note capture. Blocks land in today's inbox; processing
 * (nightly, or the button here) asks the AI to propose a filing category.
 * Proposals are approved in the review inbox (Chancery); approved notes are
 * filed into the append-only markdown vault and the daily report below.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "wouter";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import { Page, EmptyState, ErrorState, LoadingState, ConfirmAction } from "@/components/primitives";
import {
  NotebookPen, Send, Sparkles, Loader2, Inbox, Clock, CheckCircle2,
  XCircle, Trash2, ChevronLeft, ChevronRight, FileText, Scale,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface NoteBlock {
  id: string;
  day: string;
  text: string;
  source: string;
  status: "inbox" | "proposed" | "approved" | "rejected" | "filed";
  proposal: string | null;
  error: string | null;
  created_at: string;
}

interface NotesResponse {
  day: string;
  blocks: NoteBlock[];
  counts: Record<string, number>;
}

const STATUS_META: Record<NoteBlock["status"], { label: string; icon: typeof Inbox; cls: string; style?: React.CSSProperties }> = {
  inbox:    { label: "Inbox",        icon: Inbox,        cls: "text-muted-foreground" },
  proposed: { label: "Awaiting you", icon: Scale,        cls: "", style: { color: "var(--gd-bronze)" } },
  approved: { label: "Approved",     icon: CheckCircle2, cls: "", style: { color: "var(--gd-success)" } },
  filed:    { label: "Filed",        icon: CheckCircle2, cls: "", style: { color: "var(--gd-success)" } },
  rejected: { label: "Dismissed",    icon: XCircle,      cls: "text-muted-foreground/60" },
};

function shiftDay(day: string, delta: number): string {
  const d = new Date(`${day}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + delta);
  return d.toISOString().slice(0, 10);
}

function todayStr(): string {
  return new Date().toISOString().slice(0, 10);
}

function proposalOf(b: NoteBlock): { title?: string; categories?: string[]; kind?: string } {
  try {
    return b.proposal ? JSON.parse(b.proposal) : {};
  } catch {
    return {};
  }
}

export default function NotesPage() {
  const qc = useQueryClient();
  const [day, setDay] = useState(todayStr());
  const [draft, setDraft] = useState("");
  const isToday = day === todayStr();

  const { data, isLoading, isError, refetch } = useQuery<NotesResponse>({
    queryKey: ["notes", day],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/notes?day=${day}`);
      if (!r.ok) throw new Error("Failed to load notes");
      return r.json();
    },
    refetchInterval: 15_000,
  });

  const { data: report } = useQuery<{ day: string; report: string }>({
    queryKey: ["notes-report", day],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/notes/report?day=${day}`);
      if (!r.ok) throw new Error("Failed to load report");
      return r.json();
    },
    staleTime: 60_000,
  });

  // Filing category policy — server-owned; shown as a legend so users know
  // where processed notes can land in the vault.
  const { data: policy } = useQuery({
    queryKey: ["notes", "policy"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/notes/policy`);
      if (!r.ok) throw new Error("Failed to load note policy");
      return r.json() as Promise<{ categories: { id: string; label: string }[] }>;
    },
    staleTime: 5 * 60_000,
  });
  const categories = policy?.categories ?? [];

  const capture = useMutation({
    mutationFn: async (text: string) => {
      const r = await apiFetch(`${BASE}/notes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, day: isToday ? undefined : day }),
      });
      if (!r.ok) throw new Error((await r.json()).detail ?? "Capture failed");
      return r.json();
    },
    onSuccess: () => {
      setDraft("");
      qc.invalidateQueries({ queryKey: ["notes", day] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const processNow = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/notes/process`, { method: "POST" });
      if (!r.ok) throw new Error("Processing failed to start");
      return r.json() as Promise<{ started: boolean; detail?: string }>;
    },
    onSuccess: (res) => {
      if (res.started) {
        toast.success("Processing your notes — proposals will appear in the review inbox shortly.");
        setTimeout(() => qc.invalidateQueries({ queryKey: ["notes", day] }), 5000);
      } else {
        toast.info(res.detail ?? "Nothing to process.");
      }
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const r = await apiFetch(`${BASE}/notes/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error((await r.json()).detail ?? "Delete failed");
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notes", day] }),
    onError: (e: Error) => toast.error(e.message),
  });

  const blocks = data?.blocks ?? [];
  const counts = data?.counts ?? {};
  const inboxCount = counts.inbox ?? 0;
  const proposedCount = counts.proposed ?? 0;

  const submit = () => {
    const text = draft.trim();
    if (text) capture.mutate(text);
  };

  return (
    <Page
      eyebrow="Capture through the day. Processed nightly — or now, if you like."
      title="Commonplace"
      actions={
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5 min-h-11"
          onClick={() => processNow.mutate()}
          disabled={processNow.isPending || inboxCount === 0}
        >
          {processNow.isPending
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : <Sparkles className="w-3.5 h-3.5" />}
          Process now{inboxCount > 0 ? ` (${inboxCount})` : ""}
        </Button>
      }
    >
      {/* Capture box */}
      <div className="rounded-xl border border-border bg-card p-3 space-y-2">
        <Textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={isToday
            ? "What's on your mind? Ideas, reminders, decisions — one thought per note."
            : `Add a note to ${day}…`}
          className="min-h-20 resize-y border-0 focus-visible:ring-0 p-1 text-sm"
          aria-label="New note"
        />
        <div className="flex items-center justify-between">
          <span className="text-[10px] font-mono text-muted-foreground">
            Ctrl/Cmd + Enter to capture
          </span>
          <Button size="sm" className="gap-1.5" onClick={submit}
                  disabled={capture.isPending || !draft.trim()}>
            {capture.isPending
              ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
              : <Send className="w-3.5 h-3.5" />}
            Capture
          </Button>
        </div>
      </div>

      {/* Filing categories — where processed notes end up in the vault */}
      {categories.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" data-testid="legend-note-categories">
          <span className="text-[10px] font-mono text-muted-foreground mr-0.5">
            Notes are filed under:
          </span>
          {categories.map((c) => (
            <span key={c.id}
                  className="text-[10px] px-1.5 py-0.5 rounded-full border border-border text-muted-foreground">
              {c.label}
            </span>
          ))}
        </div>
      )}

      {/* Day switcher */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1">
          <Button variant="ghost" size="sm" className="h-7 w-7 p-0"
                  onClick={() => setDay(shiftDay(day, -1))} aria-label="Previous day">
            <ChevronLeft className="w-4 h-4" />
          </Button>
          <span className="font-mono text-sm">{isToday ? `Today · ${day}` : day}</span>
          <Button variant="ghost" size="sm" className="h-7 w-7 p-0"
                  onClick={() => setDay(shiftDay(day, 1))}
                  disabled={isToday} aria-label="Next day">
            <ChevronRight className="w-4 h-4" />
          </Button>
          {!isToday && (
            <Button variant="ghost" size="sm" className="h-7 text-xs"
                    onClick={() => setDay(todayStr())}>
              Today
            </Button>
          )}
        </div>
        {proposedCount > 0 && (
          <Link href="/review"
                className="text-xs text-primary hover:underline inline-flex items-center gap-1">
            <Scale className="w-3.5 h-3.5" />
            {proposedCount} awaiting your approval →
          </Link>
        )}
      </div>

      {/* Blocks */}
      {isLoading ? (
        <LoadingState rows={2} label="Loading notes" />
      ) : isError ? (
        <ErrorState
          title="Couldn't load notes"
          detail="Your captured notes failed to load. Check your connection and try again."
          onRetry={() => refetch()}
        />
      ) : blocks.length === 0 ? (
        <EmptyState
          icon={<Inbox />}
          title={`Nothing captured ${isToday ? "yet today" : `on ${day}`}`}
          description="Jot a thought in the box above — one idea per note."
        />
      ) : (
        <div className="space-y-2">
          {blocks.map((b) => {
            const meta = STATUS_META[b.status];
            const StatusIcon = meta.icon;
            const p = proposalOf(b);
            return (
              <div key={b.id}
                   className={`group rounded-lg border border-border bg-card px-3 py-2.5 ${b.status === "rejected" ? "opacity-50" : ""}`}>
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm whitespace-pre-wrap flex-1 min-w-0">{b.text}</p>
                  {b.status === "inbox" && (
                    <ConfirmAction
                      title="Delete this note?"
                      consequence="This removes the captured note permanently. This can't be undone."
                      confirmLabel="Delete"
                      destructive
                      onConfirm={() => remove.mutate(b.id)}
                      trigger={
                        <Button variant="ghost" size="sm"
                                className="min-h-11 min-w-11 p-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100 shrink-0"
                                aria-label="Delete note"
                                data-testid={`button-delete-note-${b.id}`}>
                          <Trash2 className="w-3.5 h-3.5" />
                        </Button>
                      }
                    />
                  )}
                </div>
                <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                  <span className={`inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider ${meta.cls}`} style={meta.style}>
                    <StatusIcon className="w-3 h-3" />
                    {meta.label}
                  </span>
                  {p.categories && p.categories.length > 0 && (
                    <span className="text-[10px] font-mono text-muted-foreground">
                      → {p.categories.join(", ")}
                    </span>
                  )}
                  {b.error && b.status === "inbox" && (
                    <span className="text-[10px] text-destructive">{b.error}</span>
                  )}
                  <span className="text-[10px] font-mono text-muted-foreground/60 ml-auto">
                    <Clock className="w-3 h-3 inline mr-0.5 -mt-px" />
                    {new Date(b.created_at + (b.created_at.endsWith("Z") ? "" : "Z"))
                      .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Daily report */}
      {report && report.report && (counts.approved ?? 0) + (counts.filed ?? 0) > 0 && (
        <div className="rounded-xl border border-border bg-card p-4">
          <div className="flex items-center gap-2 mb-2">
            <FileText className="w-4 h-4" />
            <h2 className="font-display text-base">Daily report</h2>
            <Badge variant="outline" className="text-[10px] font-mono ml-auto">
              from approved notes only
            </Badge>
          </div>
          <pre className="text-xs whitespace-pre-wrap font-sans text-muted-foreground leading-relaxed">
            {report.report}
          </pre>
        </div>
      )}
    </Page>
  );
}
