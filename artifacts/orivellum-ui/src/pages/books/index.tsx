import { useState } from "react";
import { Link, useLocation } from "wouter";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  BookOpen, ArrowRight, Plus, BookMarked, FileText,
  ChevronRight, Sparkles,
} from "lucide-react";
import { useListWorks, getListWorksQueryKey } from "@workspace/api-client-react";
import { toast } from "sonner";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface BookEntry {
  id: string;
  title: string;
  description?: string;
  pipeline_id: string;
  pipeline_status: string;
  stage_label: string;
  word_count: number;
  chapter_count: number;
  chapters_extracted: number;
  chapters_drafted: number;
  chapters_approved: number;
  doc_count: number;
  updated_at?: string;
}

/** Compact "how far along" label: approved chapters have necessarily been
 *  drafted, so the drafted figure counts both. Returns null when there are
 *  no chapters or nothing beyond extraction has happened yet. */
export function chapterProgressLabel(b: {
  chapter_count: number;
  chapters_drafted?: number;
  chapters_approved?: number;
}): string | null {
  const total = b.chapter_count ?? 0;
  if (!total) return null;
  const approved = b.chapters_approved ?? 0;
  const drafted = (b.chapters_drafted ?? 0) + approved;
  if (approved >= total) return `All ${total} chapters approved`;
  if (drafted === 0) return null;
  const base = `${drafted} of ${total} drafted`;
  return approved > 0 ? `${base} · ${approved} approved` : base;
}

// Stage ladder (B0→B17) — kept visually distinct across the run using VELLUM
// tokens and color-mix blends so each phase group reads as a step forward.
const STAGE_NEUTRAL: React.CSSProperties = {};
const STAGE_STYLE: Record<string, React.CSSProperties> = {
  // B0 — neutral (uses text-muted-foreground via STAGE_CLS)
  B0: {},
  // B1–B2 — early / info: soft gilt at low strength
  B1: { color: "var(--gilt)", background: "color-mix(in srgb, var(--gilt) 8%, transparent)", borderColor: "color-mix(in srgb, var(--gilt) 18%, transparent)" },
  B2: { color: "var(--gilt)", background: "color-mix(in srgb, var(--gilt) 8%, transparent)", borderColor: "color-mix(in srgb, var(--gilt) 18%, transparent)" },
  // B3–B5 — AI/design (was violet): gilt, nearest VELLUM token
  B3: { color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" },
  B4: { color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" },
  B5: { color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" },
  // B6–B8 — building (amber): gilt strong
  B6: { color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "color-mix(in srgb, var(--gilt) 40%, transparent)" },
  B7: { color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "color-mix(in srgb, var(--gilt) 40%, transparent)" },
  B8: { color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "color-mix(in srgb, var(--gilt) 40%, transparent)" },
  // B9–B11 — later build (orange): gilt/rust blend, warmer than gilt
  B9: { color: "color-mix(in srgb, var(--gilt) 55%, var(--rust))", background: "color-mix(in srgb, var(--rust) 8%, transparent)", borderColor: "color-mix(in srgb, var(--rust) 22%, transparent)" },
  B10: { color: "color-mix(in srgb, var(--gilt) 55%, var(--rust))", background: "color-mix(in srgb, var(--rust) 8%, transparent)", borderColor: "color-mix(in srgb, var(--rust) 22%, transparent)" },
  B11: { color: "color-mix(in srgb, var(--gilt) 55%, var(--rust))", background: "color-mix(in srgb, var(--rust) 8%, transparent)", borderColor: "color-mix(in srgb, var(--rust) 22%, transparent)" },
  // B12–B14 — success (emerald): green-2
  B12: { color: "var(--green-2)", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)" },
  B13: { color: "var(--green-2)", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)" },
  B14: { color: "var(--green-2)", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)" },
  // B15–B16 — near-done (teal): green blend, distinct from B12–B14 and B17
  B15: { color: "color-mix(in srgb, var(--green-2) 70%, var(--green-raw))", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-raw) 28%, transparent)" },
  B16: { color: "color-mix(in srgb, var(--green-2) 70%, var(--green-raw))", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-raw) 28%, transparent)" },
  // B17 — published (deep forest): green-raw
  B17: { color: "var(--green-raw)", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-raw) 40%, transparent)" },
};

function stageProgress(status: string): number {
  const n = parseInt(status?.replace("B", "") ?? "0", 10);
  return isNaN(n) ? 0 : Math.round((n / 17) * 100);
}

function formatWords(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000)     return `${Math.round(n / 1_000)}k`;
  return String(n);
}

// ─── Promote button (Works without a pipeline) ────────────────────────────────

interface PromotionEligibility {
  eligible: boolean;
  reasons: string[];
  checks: Array<{ rule: string; label: string; ok: boolean; reason: string | null }>;
}

function PromoteButton({ workId, workTitle }: { workId: string; workTitle: string }) {
  const queryClient = useQueryClient();

  // Refusals must state the specific unmet reason — never a bare disabled button.
  const { data: elig, isLoading: eligLoading } = useQuery<PromotionEligibility>({
    queryKey: ["promotion-eligibility", workId],
    queryFn: () =>
      apiFetch(`${BASE}/works/${workId}/promotion-eligibility`).then(r => {
        if (!r.ok) throw new Error("eligibility check failed");
        return r.json();
      }),
    staleTime: 30_000,
  });

  const { mutate, isPending } = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/pipeline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: workTitle }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        const reasons: string[] | undefined = body?.detail?.reasons;
        throw new Error(
          Array.isArray(reasons) && reasons.length > 0
            ? reasons.join(" ")
            : "Could not start book pipeline",
        );
      }
      return r.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["books"] });
      queryClient.invalidateQueries({ queryKey: getListWorksQueryKey({}) });
      queryClient.invalidateQueries({ queryKey: ["promotion-eligibility", workId] });
      toast.success(`"${workTitle}" promoted to book pipeline`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  if (elig && !elig.eligible) {
    return (
      <div className="flex flex-col items-end gap-1 max-w-[240px] shrink-0">
        <span
          className="inline-flex items-center gap-1.5 text-[10px] font-mono uppercase tracking-wider px-2 py-1 rounded-md border"
          style={{ color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" }}
        >
          <BookOpen className="w-3 h-3" /> Not ready for promotion
        </span>
        <ul className="space-y-0.5 text-right">
          {elig.reasons.map((reason, i) => (
            <li key={i} className="text-[10px] text-muted-foreground leading-snug">
              {reason}
            </li>
          ))}
        </ul>
      </div>
    );
  }

  // While eligibility is loading show a labelled pending state; if the check
  // itself failed, keep the button usable — the POST refuses with specific
  // reasons anyway, so there is never a bare disabled button.
  return (
    <Button
      size="sm"
      variant="outline"
      className="gap-1.5 text-xs font-mono"
      disabled={isPending || eligLoading}
      onClick={e => { e.preventDefault(); mutate(); }}
    >
      <Plus className="w-3 h-3" />
      {isPending ? "Starting…" : eligLoading ? "Checking eligibility…" : "Promote to Book"}
    </Button>
  );
}

// ─── Book card ────────────────────────────────────────────────────────────────

function BookCard({ book }: { book: BookEntry }) {
  const progress = stageProgress(book.pipeline_status);
  const stageStyle = STAGE_STYLE[book.pipeline_status] ?? STAGE_NEUTRAL;
  const stageClass = STAGE_STYLE[book.pipeline_status] ? "" : "text-muted-foreground";
  const isPublished = book.pipeline_status === "B17";

  return (
    <Link href={`/works/${book.id}`}>
      <Card className="hover-elevate cursor-pointer transition-all hover:border-primary/50 group">
        <CardContent className="p-5 space-y-4">
          {/* Header */}
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="font-serif font-semibold text-lg leading-snug group-hover:text-primary transition-colors line-clamp-1">
                  {book.title}
                </h3>
                {isPublished && (
                  <Sparkles className="w-4 h-4 shrink-0" style={{ color: "var(--green-2)" }} />
                )}
              </div>
              {book.description && (
                <p className="text-sm text-muted-foreground line-clamp-2 mt-0.5">{book.description}</p>
              )}
            </div>
            <Badge
              variant="outline"
              className={`text-[10px] font-mono uppercase shrink-0 ${stageClass}`}
              style={stageStyle}
            >
              {book.pipeline_status} · {book.stage_label}
            </Badge>
          </div>

          {/* Progress bar */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-[10px] font-mono text-muted-foreground">
              <span>{book.stage_label}</span>
              <span>{progress}%</span>
            </div>
            <div className="h-1.5 bg-muted rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-500 ${isPublished ? "" : "bg-primary/70"}`}
                style={{ width: `${progress}%`, ...(isPublished ? { background: "var(--green-2)" } : {}) }}
              />
            </div>
          </div>

          {/* Stats + CTA */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4 text-xs text-muted-foreground font-mono">
              {book.word_count > 0 && (
                <span className="flex items-center gap-1">
                  <FileText className="w-3 h-3" />
                  {formatWords(book.word_count)}w
                </span>
              )}
              {book.chapter_count > 0 && (
                <span data-testid={`chapter-progress-${book.id}`}>
                  {chapterProgressLabel(book) ?? `${book.chapter_count} ch`}
                </span>
              )}
              {book.doc_count > 0 && (
                <span>{book.doc_count} doc{book.doc_count !== 1 ? "s" : ""}</span>
              )}
            </div>
            {/* Decorative CTA — the whole card is already a link; a nested
                <Link> here renders an <a> inside an <a> (hydration error). */}
            <span className="inline-flex items-center gap-1 text-xs h-7 px-2 rounded-md opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity text-muted-foreground">
              Open <ChevronRight className="w-3 h-3" />
            </span>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

// ─── Works-without-pipeline section ─────────────────────────────────────────

function NonBookWorks({ bookWorkIds }: { bookWorkIds: Set<string> }) {
  const { data, isLoading } = useListWorks(
    {},
    { query: { queryKey: getListWorksQueryKey({}), staleTime: 30_000 } },
  );
  const eligible = (data?.works ?? []).filter(w => w.id && !bookWorkIds.has(w.id));
  if (isLoading || eligible.length === 0) return null;

  return (
    <div className="space-y-3 pt-4 border-t border-border/40">
      <h2 className="text-base font-serif font-medium text-muted-foreground">
        Other Works — not yet promoted
      </h2>
      <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
        {eligible.map(w => (
          <Link key={w.id} href={`/works/${w.id}`}>
            <div className="group flex items-start justify-between gap-3 p-4 rounded-xl border border-border/50 hover:border-primary/30 bg-card hover:bg-muted/10 transition-all cursor-pointer">
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium line-clamp-1 group-hover:text-primary transition-colors">{w.title}</p>
                <p className="text-[10px] font-mono text-muted-foreground mt-0.5 uppercase">{w.work_type}</p>
              </div>
              <PromoteButton workId={w.id!} workTitle={w.title ?? ""} />
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function BooksPage() {
  const { data, isLoading } = useQuery<{ books: BookEntry[] }>({
    queryKey: ["books"],
    queryFn: () => apiFetch(`${BASE}/books`).then(r => r.json()),
    staleTime: 20_000,
    refetchInterval: 30_000,
  });

  const books = data?.books ?? [];
  const bookWorkIds = new Set(books.map(b => b.id));
  const activeBooks = books.filter(b => b.pipeline_status !== "B17");
  const publishedBooks = books.filter(b => b.pipeline_status === "B17");

  return (
    <div className="space-y-8 max-w-5xl animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <BookMarked className="w-6 h-6 text-primary" />
            <h1 className="text-3xl font-serif font-semibold tracking-tight">Books</h1>
          </div>
          <p className="text-muted-foreground text-sm">
            Long-form writing projects moving through the 17-stage pipeline.
          </p>
        </div>
        <Button asChild size="sm" variant="outline" className="gap-2 font-mono text-xs uppercase tracking-wider">
          <Link href="/works?create=1">
            <Plus className="w-3.5 h-3.5" /> New Work
          </Link>
        </Button>
      </div>

      {/* In-progress books */}
      {isLoading ? (
        <div className="grid sm:grid-cols-2 gap-4">
          {[1, 2, 3].map(i => <Skeleton key={i} className="h-44 w-full rounded-xl" />)}
        </div>
      ) : books.length === 0 ? (
        <Card className="border-dashed bg-muted/20">
          <CardContent className="p-12 text-center space-y-4">
            <BookOpen className="w-10 h-10 text-muted-foreground mx-auto opacity-40" />
            <div className="space-y-1">
              <p className="font-medium text-foreground">No books yet</p>
              <p className="text-sm text-muted-foreground">
                Promote an existing Work to start the 17-stage book pipeline,
                or create a new Work and promote it here.
              </p>
            </div>
            <Button asChild size="sm">
              <Link href="/works">Go to Works</Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <>
          {activeBooks.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-lg font-serif font-semibold">In Progress</h2>
              <div className="grid sm:grid-cols-2 gap-4">
                {activeBooks.map(b => <BookCard key={b.id} book={b} />)}
              </div>
            </div>
          )}
          {publishedBooks.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-lg font-serif font-semibold flex items-center gap-2">
                <Sparkles className="w-4 h-4" style={{ color: "var(--green-2)" }} /> Published
              </h2>
              <div className="grid sm:grid-cols-2 gap-4">
                {publishedBooks.map(b => <BookCard key={b.id} book={b} />)}
              </div>
            </div>
          )}
        </>
      )}

      {/* Other works eligible for promotion */}
      {!isLoading && <NonBookWorks bookWorkIds={bookWorkIds} />}
    </div>
  );
}
