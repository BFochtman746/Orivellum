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
  doc_count: number;
  updated_at?: string;
}

const STAGE_COLOR: Record<string, string> = {
  B0: "bg-zinc-100 text-zinc-700 border-zinc-200",
  B1: "bg-blue-50 text-blue-700 border-blue-200",
  B2: "bg-blue-50 text-blue-700 border-blue-200",
  B3: "bg-violet-50 text-violet-700 border-violet-200",
  B4: "bg-violet-50 text-violet-700 border-violet-200",
  B5: "bg-violet-50 text-violet-700 border-violet-200",
  B6: "bg-amber-50 text-amber-700 border-amber-200",
  B7: "bg-amber-50 text-amber-700 border-amber-200",
  B8: "bg-amber-50 text-amber-700 border-amber-200",
  B9: "bg-orange-50 text-orange-700 border-orange-200",
  B10: "bg-orange-50 text-orange-700 border-orange-200",
  B11: "bg-orange-50 text-orange-700 border-orange-200",
  B12: "bg-emerald-50 text-emerald-700 border-emerald-200",
  B13: "bg-emerald-50 text-emerald-700 border-emerald-200",
  B14: "bg-emerald-50 text-emerald-700 border-emerald-200",
  B15: "bg-teal-50 text-teal-700 border-teal-200",
  B16: "bg-teal-50 text-teal-700 border-teal-200",
  B17: "bg-green-50 text-green-700 border-green-200",
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

function PromoteButton({ workId, workTitle }: { workId: string; workTitle: string }) {
  const queryClient = useQueryClient();
  const { mutate, isPending } = useMutation({
    mutationFn: () =>
      apiFetch(`${BASE}/works/${workId}/pipeline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: workTitle }),
      }).then(r => { if (!r.ok) throw new Error("promote failed"); return r.json(); }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["books"] });
      queryClient.invalidateQueries({ queryKey: getListWorksQueryKey({}) });
      toast.success(`"${workTitle}" promoted to book pipeline`);
    },
    onError: () => toast.error("Could not start book pipeline"),
  });

  return (
    <Button
      size="sm"
      variant="outline"
      className="gap-1.5 text-xs font-mono"
      disabled={isPending}
      onClick={e => { e.preventDefault(); mutate(); }}
    >
      <Plus className="w-3 h-3" />
      {isPending ? "Starting…" : "Promote to Book"}
    </Button>
  );
}

// ─── Book card ────────────────────────────────────────────────────────────────

function BookCard({ book }: { book: BookEntry }) {
  const progress = stageProgress(book.pipeline_status);
  const stageClass = STAGE_COLOR[book.pipeline_status] ?? "bg-muted text-muted-foreground border-border";
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
                  <Sparkles className="w-4 h-4 text-green-500 shrink-0" />
                )}
              </div>
              {book.description && (
                <p className="text-sm text-muted-foreground line-clamp-2 mt-0.5">{book.description}</p>
              )}
            </div>
            <Badge
              variant="outline"
              className={`text-[10px] font-mono uppercase shrink-0 ${stageClass}`}
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
                className={`h-full rounded-full transition-all duration-500 ${isPublished ? "bg-green-500" : "bg-primary/70"}`}
                style={{ width: `${progress}%` }}
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
                <span>{book.chapter_count} ch</span>
              )}
              {book.doc_count > 0 && (
                <span>{book.doc_count} doc{book.doc_count !== 1 ? "s" : ""}</span>
              )}
            </div>
            <Button
              size="sm"
              variant="ghost"
              className="gap-1 text-xs h-7 opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity"
              onClick={e => e.stopPropagation()}
              asChild
            >
              <Link href={`/works/${book.id}`}>
                Open <ChevronRight className="w-3 h-3" />
              </Link>
            </Button>
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
                <Sparkles className="w-4 h-4 text-green-500" /> Published
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
