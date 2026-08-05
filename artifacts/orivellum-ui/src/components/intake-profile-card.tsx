/**
 * IntakeProfileCard — renders the result of the Universal Intake pipeline.
 *
 * Shows:
 *   • What the document is (tier badge, kind)
 *   • Short summary
 *   • Confidence bar
 *   • Filed-to work link
 *   • Suggested action buttons (type-aware)
 *   • Optional research summary
 */
import { useState } from "react";
import {
  Star, Library, FileText, MessageSquare, Archive, Sparkles,
  BookMarked, Target, Globe, ExternalLink, CheckCircle2,
  AlertTriangle, Loader2, ChevronDown, ChevronUp,
  Receipt, Lightbulb, Zap, Check,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Link, useLocation } from "wouter";
import { apiFetch } from "@/lib/auth";
import { toast } from "sonner";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ─────────────────────────────────────────────────────────────────────

export interface SuggestedAction {
  id: string;
  label: string;
  description: string;
  kind: string;
}

export interface IntakeProfile {
  doc_id: string;
  what_it_is: string;
  kind: string;
  tier: string;
  filed_to: string | null;
  filed_to_id: string | null;
  confidence: number;
  summary: string;
  word_count: number;
  headings: string[];
  text_snippet: string | null;   // first ~500 chars of extracted text for chat grounding
  suggested_actions: SuggestedAction[];
  research_summary: string | null;
  research_sources: Array<{ title?: string; url?: string }>;
  error: string | null;
}

// ── Tier visual config ────────────────────────────────────────────────────────

const TIER_CONFIG: Record<string, { label: string; icon: typeof Star; cls: string }> = {
  canon:        { label: "CANON",        icon: Star,          cls: "bg-violet-100 text-violet-700 border-violet-200" },
  source:       { label: "SOURCE",       icon: Library,       cls: "bg-blue-100 text-blue-700 border-blue-200" },
  artifact:     { label: "ARTIFACT",     icon: FileText,      cls: "bg-amber-100 text-amber-700 border-amber-200" },
  system:       { label: "SYSTEM",       icon: FileText,      cls: "bg-slate-100 text-slate-600 border-slate-200" },
  conversation: { label: "CONVERSATION", icon: MessageSquare, cls: "bg-emerald-100 text-emerald-700 border-emerald-200" },
};

// ── Action icons ──────────────────────────────────────────────────────────────

const ACTION_ICONS: Record<string, typeof Star> = {
  slot_book:       BookMarked,
  file_taxes:      Receipt,
  find_gaps:       Target,
  research:        Globe,
  extract_actions: Lightbulb,
  link_work:       ExternalLink,
  archive:         Archive,
  chat:            MessageSquare,
};

// ── Sub-components ────────────────────────────────────────────────────────────

function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const color = pct >= 80 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden">
        <div className={`h-full rounded-full ${color} transition-all`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-muted-foreground">{pct}%</span>
    </div>
  );
}

function ActionButton({
  action,
  docId,
  workId,
  textSnippet,
  onResearch,
  onLinkWork,
  onFindGaps,
  onArchived,
  onRetry,
}: {
  action: SuggestedAction;
  docId: string;
  workId?: string | null;
  textSnippet?: string | null;
  onResearch: () => void;
  onLinkWork: () => void;
  onFindGaps: () => void;
  onArchived?: () => void;
  onRetry?: () => void;
}) {
  const [, navigate] = useLocation();
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const Icon = done ? Check : (ACTION_ICONS[action.kind] ?? Zap);

  /**
   * Create a conversation then immediately POST a first message so the chat
   * has document context. When the doc is not linked to a Work, the text_snippet
   * is included directly in the first message for grounding.
   */
  async function openChat(title: string, promptTemplate: string): Promise<void> {
    const convResp = await apiFetch(`${BASE}/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, work_id: workId ?? undefined }),
    });
    if (!convResp.ok) throw new Error("Could not create conversation");
    const convData = await convResp.json();
    const id: string | undefined = convData.conversation?.id;
    if (!id) throw new Error("Server did not return a conversation ID");

    // When not linked to a Work, inject extracted text for document grounding
    let message = promptTemplate;
    if (!workId && textSnippet) {
      message = `Document excerpt:\n\n${textSnippet}\n\n---\n\n${promptTemplate}`;
    }
    await apiFetch(`${BASE}/conversations/${id}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: message, stream: false }),
    });
    navigate(`/chat/${id}`);
  }

  const handleClick = async () => {
    switch (action.kind) {
      case "research":
        return onResearch();

      case "chat": {
        setBusy(true);
        try {
          await openChat(
            "Document discussion",
            "Give me an overview of this document's key points and how it might be useful."
          );
        } catch (e: any) {
          toast.error(e.message ?? "Could not open chat");
        } finally {
          setBusy(false);
        }
        return;
      }

      case "link_work":
        return onLinkWork();

      case "find_gaps":
        return onFindGaps();

      case "slot_book":
        navigate(workId ? `/works/${workId}?tab=book` : "/books");
        return;

      case "file_taxes":
        navigate(`/library/${docId}`);
        return;

      case "retry":
        onRetry?.();
        return;

      case "extract_actions": {
        // Create a work-linked conversation then send the action-item extraction prompt
        setBusy(true);
        try {
          await openChat(
            "Action items",
            "List all action items, tasks, to-dos, and deadlines from this document. " +
            "Group them by owner or deadline if possible."
          );
        } catch (e: any) {
          toast.error(e.message ?? "Could not open chat");
        } finally {
          setBusy(false);
        }
        return;
      }

      case "archive": {
        setBusy(true);
        try {
          const resp = await apiFetch(`${BASE}/library/${docId}/lifecycle`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ lifecycle: "archived" }),
          });
          if (!resp.ok) throw new Error("Archive request failed");
          setDone(true);
          toast.success("Document archived");
          onArchived?.();
        } catch (e: any) {
          toast.error(e.message ?? "Archive failed");
        } finally {
          setBusy(false);
        }
        return;
      }

      default:
        toast.info(action.description);
    }
  };

  return (
    <Button
      size="sm"
      variant="outline"
      className="gap-2 text-xs font-mono h-8"
      title={action.description}
      onClick={handleClick}
      disabled={busy || done}
    >
      {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Icon className="w-3.5 h-3.5" />}
      {action.label}
    </Button>
  );
}

// ── Main card ─────────────────────────────────────────────────────────────────

interface IntakeProfileCardProps {
  profile: IntakeProfile;
  /** Called when user clicks "Link to a Work" */
  onLinkWork?: (docId: string) => void;
  /** Called when user clicks "Find Related Gaps / Find Research Gaps" */
  onFindGaps?: (docId: string) => void;
  /** Called when user clicks "Check Again" on a still-processing document */
  onRetry?: (docId: string) => void;
  /** Compact mode — no headings, no research section */
  compact?: boolean;
}

export function IntakeProfileCard({
  profile,
  onLinkWork,
  onFindGaps,
  onRetry,
  compact = false,
}: IntakeProfileCardProps) {
  const [showResearchConfirm, setShowResearchConfirm] = useState(false);
  const [researching, setResearching] = useState(false);
  const [researchProfile, setResearchProfile] = useState<IntakeProfile | null>(null);
  const [showHeadings, setShowHeadings] = useState(false);
  const [archived, setArchived] = useState(false);

  const active = researchProfile ?? profile;
  const tierCfg = TIER_CONFIG[active.tier] ?? TIER_CONFIG.source;
  const TierIcon = tierCfg.icon;

  const handleResearch = async () => {
    setResearching(true);
    setShowResearchConfirm(false);
    try {
      const resp = await apiFetch(`${BASE}/intake/research`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ doc_id: profile.doc_id, confirmed: true }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? "Research failed");
      }
      const data: IntakeProfile = await resp.json();
      setResearchProfile(data);
      toast.success("Research complete — results saved as a knowledge note");
    } catch (err: any) {
      toast.error(err.message ?? "Research failed");
    } finally {
      setResearching(false);
    }
  };

  if (active.error) {
    return (
      <Card className="border-destructive/30 bg-destructive/5">
        <CardContent className="p-4 flex items-start gap-3">
          <AlertTriangle className="w-4 h-4 text-destructive mt-0.5 flex-shrink-0" />
          <div>
            <p className="text-sm font-medium text-destructive">Intake failed</p>
            <p className="text-xs text-muted-foreground mt-1">{active.error}</p>
          </div>
        </CardContent>
      </Card>
    );
  }

  return (
    <>
      <Card className="border-border/50 animate-in fade-in slide-in-from-bottom-2 duration-300">
        <CardContent className="p-5 space-y-4">
          {/* ── Header row ── */}
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1 min-w-0">
              <div className="flex items-center gap-2 flex-wrap">
                <Badge
                  variant="outline"
                  className={`text-[9px] font-mono tracking-widest px-2 py-0.5 gap-1 ${tierCfg.cls}`}
                >
                  <TierIcon className="w-2.5 h-2.5" />
                  {tierCfg.label}
                </Badge>
                <span className="text-[10px] font-mono text-muted-foreground uppercase">
                  {active.kind}
                </span>
                {active.word_count > 0 && (
                  <span className="text-[10px] font-mono text-muted-foreground">
                    {active.word_count.toLocaleString()}w
                  </span>
                )}
              </div>
              <h3 className="text-sm font-semibold leading-snug text-foreground">
                {active.what_it_is}
              </h3>
              {active.filed_to && active.filed_to_id && (
                <Link href={`/works/${active.filed_to_id}`}>
                  <span className="text-xs text-primary hover:underline flex items-center gap-1 cursor-pointer">
                    <BookMarked className="w-3 h-3" />
                    Filed under: {active.filed_to}
                  </span>
                </Link>
              )}
            </div>
            <div className="flex items-center gap-1.5 flex-shrink-0">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
              <span className="text-[10px] font-mono text-muted-foreground">Intake complete</span>
            </div>
          </div>

          {/* ── Confidence ── */}
          <div className="space-y-1">
            <span className="text-[9px] font-mono uppercase tracking-wider text-muted-foreground">
              Classification confidence
            </span>
            <ConfidenceBar value={active.confidence} />
          </div>

          {/* ── Summary ── */}
          {active.summary && (
            <div className="space-y-1">
              <span className="text-[9px] font-mono uppercase tracking-wider text-muted-foreground">
                Summary
              </span>
              <p className="text-xs text-muted-foreground leading-relaxed line-clamp-4">
                {active.summary}
              </p>
            </div>
          )}

          {/* ── Headings (collapsible, non-compact) ── */}
          {!compact && active.headings.length > 0 && (
            <div>
              <button
                className="flex items-center gap-1 text-[9px] font-mono uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors"
                onClick={() => setShowHeadings(h => !h)}
              >
                {showHeadings ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                {active.headings.length} section{active.headings.length !== 1 ? "s" : ""}
              </button>
              {showHeadings && (
                <ul className="mt-2 space-y-0.5 pl-3 border-l border-border/50">
                  {active.headings.map((h, i) => (
                    <li key={i} className="text-xs text-muted-foreground truncate">{h}</li>
                  ))}
                </ul>
              )}
            </div>
          )}

          {/* ── Research summary ── */}
          {active.research_summary && (
            <div className="rounded-lg border border-blue-200/60 bg-blue-50/50 p-3 space-y-2">
              <div className="flex items-center gap-1.5">
                <Globe className="w-3.5 h-3.5 text-blue-500" />
                <span className="text-[9px] font-mono uppercase tracking-wider text-blue-600">
                  Web Research
                </span>
              </div>
              <p className="text-xs text-blue-800 leading-relaxed">{active.research_summary}</p>
              {active.research_sources.length > 0 && (
                <div className="space-y-0.5 pt-1">
                  {active.research_sources.map((s, i) => (
                    s.url && (
                      <a
                        key={i}
                        href={s.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="flex items-center gap-1 text-[10px] text-blue-600 hover:underline"
                      >
                        <ExternalLink className="w-2.5 h-2.5 flex-shrink-0" />
                        <span className="truncate">{s.title || s.url}</span>
                      </a>
                    )
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ── Suggested actions ── */}
          {active.suggested_actions.length > 0 && (
            <div className="space-y-2">
              <span className="text-[9px] font-mono uppercase tracking-wider text-muted-foreground">
                Suggested actions
              </span>
              <div className="flex flex-wrap gap-2">
                {!archived && active.suggested_actions.map((action) => (
                  researching && action.kind === "research" ? (
                    <Button key={action.id} size="sm" variant="outline" disabled className="gap-2 text-xs font-mono h-8">
                      <Loader2 className="w-3.5 h-3.5 animate-spin" /> Researching…
                    </Button>
                  ) : (
                    <ActionButton
                      key={action.id}
                      action={action}
                      docId={profile.doc_id}
                      workId={active.filed_to_id}
                      textSnippet={active.text_snippet}
                      onResearch={() => setShowResearchConfirm(true)}
                      onLinkWork={() => onLinkWork?.(profile.doc_id)}
                      onFindGaps={() => onFindGaps?.(profile.doc_id)}
                      onArchived={() => setArchived(true)}
                      onRetry={() => onRetry?.(profile.doc_id)}
                    />
                  )
                ))}
                {archived && (
                  <span className="text-xs text-muted-foreground flex items-center gap-1">
                    <Check className="w-3.5 h-3.5 text-emerald-500" /> Archived
                  </span>
                )}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Research confirmation dialog */}
      <AlertDialog open={showResearchConfirm} onOpenChange={setShowResearchConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle className="flex items-center gap-2">
              <Globe className="w-4 h-4 text-blue-500" />
              Confirm web research
            </AlertDialogTitle>
            <AlertDialogDescription>
              This will send the document title to Tavily (external search API) and retrieve
              live web results. No document content is sent externally — only the title.
              Results are saved as a knowledge note linked to this document.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleResearch}>
              Confirm — Research It
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

// ── Loading skeleton ──────────────────────────────────────────────────────────

export function IntakeProfileSkeleton() {
  return (
    <Card className="border-border/50 animate-pulse">
      <CardContent className="p-5 space-y-4">
        <div className="space-y-2">
          <div className="h-4 bg-muted rounded w-24" />
          <div className="h-4 bg-muted rounded w-48" />
        </div>
        <div className="h-2 bg-muted rounded w-full" />
        <div className="space-y-1.5">
          <div className="h-3 bg-muted rounded w-full" />
          <div className="h-3 bg-muted rounded w-4/5" />
        </div>
        <div className="flex gap-2">
          <div className="h-8 bg-muted rounded w-28" />
          <div className="h-8 bg-muted rounded w-20" />
        </div>
      </CardContent>
    </Card>
  );
}
