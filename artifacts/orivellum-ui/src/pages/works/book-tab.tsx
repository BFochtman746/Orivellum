import { useState } from "react";
import { Link } from "wouter";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  BookOpen,
  Crown,
  FileText,
  Loader2,
  AlertTriangle,
  Compass,
  CheckCircle2,
  CircleDashed,
  CircleAlert,
} from "lucide-react";
import { toast } from "sonner";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ─── Types (endpoint is not in the generated client) ─────────────────────────

interface BookVersion {
  id: string;
  title: string | null;
  kind: string | null;
  readiness: string;
  created_at: string;
  lifecycle: string;
  word_count: number;
  is_canonical: boolean;
}

interface OutlineChapter {
  id: string;
  seq: number;
  level: number;
  title: string | null;
  word_count: number;
  knowledge_count: number;
  chapter_status: "present" | "incomplete" | "missing";
}

interface BookGap {
  kind: string;
  severity: "high" | "medium" | "low";
  title: string;
  description: string;
}

interface BookIntelligence {
  canonical: (BookVersion & { canonical_source: "declared" | "auto" }) | null;
  versions: BookVersion[];
  outline: OutlineChapter[];
  expected_chapters: number;
  completeness: {
    structural_pct: number;
    content_pct: number;
    research_pct: number;
    editorial_pct: number;
  };
  knowledge_total: number;
  knowledge_reviewed: number;
  gaps: BookGap[];
  next_action: string;
}

// ─── Small pieces ─────────────────────────────────────────────────────────────

const GAUGES: { key: keyof BookIntelligence["completeness"]; label: string; hint: string }[] = [
  { key: "structural_pct", label: "Structure", hint: "chapters present vs expected" },
  { key: "content_pct", label: "Content", hint: "words vs full-length draft" },
  { key: "research_pct", label: "Research", hint: "chapters with ≥3 knowledge items" },
  { key: "editorial_pct", label: "Editorial", hint: "knowledge items reviewed" },
];

function gaugeColor(pct: number) {
  if (pct >= 75) return "bg-emerald-500/70";
  if (pct >= 40) return "bg-amber-500/70";
  return "bg-red-500/60";
}

const STATUS_CHIP: Record<OutlineChapter["chapter_status"], { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  present: { label: "Present", cls: "bg-emerald-50 text-emerald-700 border-emerald-200", Icon: CheckCircle2 },
  incomplete: { label: "Incomplete", cls: "bg-amber-50 text-amber-700 border-amber-200", Icon: CircleDashed },
  missing: { label: "Missing", cls: "bg-red-50 text-red-700 border-red-200", Icon: CircleAlert },
};

const SEV_CLS: Record<BookGap["severity"], string> = {
  high: "border-red-200 bg-red-50/60 text-red-800",
  medium: "border-amber-200 bg-amber-50/60 text-amber-800",
  low: "border-border/60 bg-muted/30 text-muted-foreground",
};

// ─── Book tab ─────────────────────────────────────────────────────────────────

export function BookTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [settingCanonical, setSettingCanonical] = useState<string | null>(null);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["book-intelligence", workId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/book-intelligence`);
      if (!r.ok) throw new Error("Failed to load book intelligence");
      return r.json() as Promise<BookIntelligence>;
    },
    enabled: !!workId,
    staleTime: 30_000,
  });

  const handleSetCanonical = async (docId: string) => {
    setSettingCanonical(docId);
    try {
      const r = await apiFetch(`${BASE}/library/${docId}/lifecycle`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lifecycle: "canonical" }),
      });
      if (!r.ok) throw new Error("Failed");
      await queryClient.invalidateQueries({ queryKey: ["book-intelligence", workId] });
      toast.success("Canonical manuscript set");
    } catch {
      toast.error("Could not set canonical manuscript");
    } finally {
      setSettingCanonical(null);
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="text-center py-16 text-muted-foreground font-mono text-sm">
        Could not load the book intelligence view. Is the server running?
      </div>
    );
  }

  const { canonical, versions, outline, completeness, gaps, next_action } = data;

  return (
    <div className="space-y-8">
      {/* Next action */}
      <Card className="border-primary/30 bg-primary/[0.03]">
        <CardContent className="p-4 flex items-start gap-3">
          <Compass className="w-5 h-5 text-primary mt-0.5 shrink-0" />
          <div>
            <div className="text-[10px] font-mono uppercase tracking-widest text-primary/70 mb-1">
              Next recommended action
            </div>
            <p className="font-serif text-base leading-snug">{next_action}</p>
          </div>
        </CardContent>
      </Card>

      {/* Completeness gauges */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {GAUGES.map(({ key, label, hint }) => {
          const pct = completeness[key] ?? 0;
          return (
            <Card key={key}>
              <CardContent className="p-4">
                <div className="flex items-baseline justify-between">
                  <span className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">{label}</span>
                  <span className="text-lg font-semibold font-mono">{pct}%</span>
                </div>
                <div className="mt-2 h-1.5 bg-muted rounded-full overflow-hidden">
                  <div className={`h-full rounded-full transition-all duration-700 ${gaugeColor(pct)}`} style={{ width: `${pct}%` }} />
                </div>
                <div className="mt-1.5 text-[10px] text-muted-foreground/70 leading-tight">{hint}</div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <div className="grid md:grid-cols-5 gap-6">
        {/* Outline */}
        <div className="md:col-span-3 space-y-3">
          <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground flex items-center gap-2">
            <BookOpen className="w-3.5 h-3.5" /> Outline
            <span className="text-muted-foreground/50 normal-case tracking-normal">
              {outline.length} chapter{outline.length !== 1 ? "s" : ""} · {data.expected_chapters} expected
            </span>
          </h3>
          {outline.length === 0 ? (
            <div className="text-sm text-muted-foreground italic font-serif py-6 text-center border border-dashed border-border/60 rounded-lg">
              No chapter structure detected yet — link a manuscript with headings, or reprocess an existing one.
            </div>
          ) : (
            <div className="space-y-1">
              {outline.map((c) => {
                const chip = STATUS_CHIP[c.chapter_status];
                return (
                  <div
                    key={c.id}
                    className="flex items-center gap-3 py-2 px-3 rounded-lg border border-border/40 bg-card/50"
                    style={{ marginLeft: `${Math.min(c.level - 1, 2) * 16}px` }}
                  >
                    <chip.Icon className={`w-4 h-4 shrink-0 ${c.chapter_status === "present" ? "text-emerald-500" : c.chapter_status === "incomplete" ? "text-amber-500" : "text-red-400"}`} />
                    <span className="font-serif text-sm truncate flex-1" title={c.title ?? undefined}>
                      {c.title || "Untitled section"}
                    </span>
                    <span className="text-[10px] font-mono text-muted-foreground shrink-0" title="Word count">
                      {c.word_count.toLocaleString()} w
                    </span>
                    <span
                      className={`text-[10px] font-mono shrink-0 px-1.5 py-0.5 rounded border ${c.knowledge_count === 0 ? "bg-red-50 text-red-600 border-red-200" : c.knowledge_count < 3 ? "bg-amber-50 text-amber-700 border-amber-200" : "bg-muted/40 text-muted-foreground border-border/50"}`}
                      title="Knowledge items supporting this chapter"
                    >
                      {c.knowledge_count} research
                    </span>
                    <span className={`text-[10px] font-mono shrink-0 px-1.5 py-0.5 rounded border ${chip.cls}`}>{chip.label}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right column: versions + gaps */}
        <div className="md:col-span-2 space-y-6">
          {/* Versions */}
          <div className="space-y-3">
            <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground flex items-center gap-2">
              <FileText className="w-3.5 h-3.5" /> Manuscript versions
            </h3>
            {versions.length === 0 ? (
              <div className="text-sm text-muted-foreground italic font-serif py-4 text-center border border-dashed border-border/60 rounded-lg">
                No documents linked to this Work yet.
              </div>
            ) : (
              <div className="space-y-1.5">
                {versions.map((v) => (
                  <div
                    key={v.id}
                    className={`group flex items-center gap-2 py-2 px-3 rounded-lg border ${v.is_canonical ? "border-primary/40 bg-primary/[0.04]" : "border-border/40 bg-card/50"}`}
                  >
                    {v.is_canonical && <Crown className="w-3.5 h-3.5 text-primary shrink-0" />}
                    <Link href={`/library/${v.id}`} className="font-serif text-sm truncate flex-1 hover:underline" title={v.title ?? undefined}>
                      {v.title || "Untitled"}
                    </Link>
                    <Badge variant="secondary" className="font-mono text-[9px] uppercase shrink-0">{v.kind ?? "?"}</Badge>
                    <span className="text-[10px] font-mono text-muted-foreground shrink-0">{v.word_count.toLocaleString()} w</span>
                    {v.is_canonical ? (
                      <span className="text-[9px] font-mono uppercase text-primary shrink-0">
                        Canonical{canonical?.canonical_source === "auto" ? " (auto)" : ""}
                      </span>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 px-2 text-[10px] font-mono opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
                        disabled={settingCanonical === v.id}
                        onClick={() => handleSetCanonical(v.id)}
                      >
                        {settingCanonical === v.id ? <Loader2 className="w-3 h-3 animate-spin" /> : "Make canonical"}
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Gaps */}
          <div className="space-y-3">
            <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground flex items-center gap-2">
              <AlertTriangle className="w-3.5 h-3.5" /> Gaps
              {gaps.length > 0 && (
                <span className="px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-amber-100 text-amber-700 leading-none">{gaps.length}</span>
              )}
            </h3>
            {gaps.length === 0 ? (
              <div className="text-sm text-emerald-700 font-serif py-4 text-center border border-emerald-200 bg-emerald-50/50 rounded-lg">
                No gaps detected — this book looks well covered.
              </div>
            ) : (
              <div className="space-y-1.5">
                {gaps.map((g, i) => (
                  <div key={i} className={`py-2 px-3 rounded-lg border text-sm ${SEV_CLS[g.severity]}`}>
                    <div className="font-medium font-serif leading-snug">{g.title}</div>
                    <div className="text-xs opacity-80 mt-0.5 leading-snug">{g.description}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
