/**
 * Writing hub — entry screen of the Writing app (GD-industrial primitives).
 *
 * Lists the user's Works with book-health at a glance: readiness (dual-coded
 * icon + text, never color alone), document/knowledge/task counts, and a
 * direct path into each Work's detail workspace. Reorganization + reskin
 * only — all data comes from the existing works list endpoint.
 */
import { useState } from "react";
import { Link, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { useListWorks } from "@workspace/api-client-react";
import { apiFetch } from "@/lib/auth";
import { format } from "date-fns";
import {
  BookOpen,
  Plus,
  Library,
  CheckCircle2,
  AlertTriangle,
  Loader2,
  ChevronRight,
  CircleDashed,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface BookEntry {
  id: string; // work id
  pipeline_status: string; // B0…B17
  stage_label: string;
  word_count: number;
  chapter_count: number;
}

function stagePct(status: string): number {
  const n = parseInt(status?.replace("B", "") ?? "0", 10);
  return isNaN(n) ? 0 : Math.round((n / 17) * 100);
}

interface WorkRow {
  id?: string;
  title?: string;
  description?: string | null;
  status?: string;
  work_type?: string;
  doc_count?: number;
  knowledge_count?: number;
  pending_tasks?: number;
  ready_doc_count?: number;
  error_doc_count?: number;
  processing_doc_count?: number;
  obj_created?: string;
}

/** Book-health chip — icon + text + width-of-meaning, never color alone. */
function HealthChip({ w }: { w: WorkRow }) {
  const docs = w.doc_count ?? 0;
  const errors = w.error_doc_count ?? 0;
  const processing = w.processing_doc_count ?? 0;
  const ready = w.ready_doc_count ?? 0;

  if (!docs) {
    return (
      <span
        className="inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded"
        style={{ color: "var(--gd-dim)", border: "1px solid var(--gd-line)" }}
      >
        <CircleDashed className="w-3 h-3" aria-hidden /> No documents
      </span>
    );
  }
  if (errors > 0) {
    return (
      <span
        className="inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded"
        style={{ color: "var(--gd-danger)", border: "1px solid var(--gd-danger)" }}
      >
        <AlertTriangle className="w-3 h-3" aria-hidden />
        {errors} error{errors !== 1 ? "s" : ""}
      </span>
    );
  }
  if (processing > 0) {
    return (
      <span
        className="inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded"
        style={{ color: "var(--gd-caution)", border: "1px solid var(--gd-caution)" }}
      >
        <Loader2 className="w-3 h-3 animate-spin" aria-hidden />
        {processing} processing
      </span>
    );
  }
  if (ready === docs) {
    return (
      <span
        className="inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded"
        style={{ color: "var(--gd-success)", border: "1px solid var(--gd-success)" }}
      >
        <CheckCircle2 className="w-3 h-3" aria-hidden /> Ready
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded"
      style={{ color: "var(--gd-muted)", border: "1px solid var(--gd-line)" }}
    >
      <CircleDashed className="w-3 h-3" aria-hidden />
      {ready}/{docs} ready
    </span>
  );
}

export default function WritingHub() {
  const [, setLocation] = useLocation();
  const [showArchived, setShowArchived] = useState(false);
  const { data: worksResp, isLoading } = useListWorks(
    { query: { refetchInterval: 30_000, staleTime: 20_000 } } as any,
  );
  // Batch pipeline stages — same endpoint the Books page uses (no N+1)
  const { data: booksResp } = useQuery<{ books: BookEntry[] }>({
    queryKey: ["books"],
    queryFn: () => apiFetch(`${BASE}/books`).then((r) => r.json()),
    staleTime: 30_000,
  });
  const bookByWork = new Map((booksResp?.books ?? []).map((b) => [b.id, b]));

  const all = (worksResp?.works ?? []) as WorkRow[];
  const works = all
    .filter((w) => (showArchived ? true : w.status !== "archived"))
    .sort((a, b) => {
      // Active first, then most recently created
      const s = (x: WorkRow) => (x.status === "active" ? 0 : x.status === "complete" ? 1 : 2);
      if (s(a) !== s(b)) return s(a) - s(b);
      return (b.obj_created ?? "").localeCompare(a.obj_created ?? "");
    });
  const archivedCount = all.filter((w) => w.status === "archived").length;

  return (
    <div className="pb-10">
      {/* Section header */}
      <div className="flex items-end justify-between gap-3 pt-2 pb-4">
        <div>
          <p className="gd-eyebrow">Works in progress</p>
          <h2
            className="mt-1"
            style={{
              fontFamily: "var(--gd-display)",
              fontSize: 24,
              fontWeight: 600,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: "var(--gd-text)",
            }}
          >
            Your books
          </h2>
        </div>
        <button
          className="gd-chip"
          onClick={() => setLocation("/works?create=1")}
          data-testid="button-new-work"
        >
          <Plus className="w-3.5 h-3.5" aria-hidden /> New Work
        </button>
      </div>

      {/* Work tiles */}
      {isLoading ? (
        <div className="grid gap-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-28 w-full rounded-[10px]" />
          ))}
        </div>
      ) : works.length > 0 ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {works.map((w) => {
            const book = w.id ? bookByWork.get(w.id) : undefined;
            return (
            <Link
              key={w.id}
              href={`/works/${w.id}`}
              className="gd-tile"
              data-testid={`tile-work-${w.id}`}
            >
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div
                    className="truncate"
                    style={{ fontFamily: "var(--gd-display)", fontSize: 17, fontWeight: 600, letterSpacing: "0.02em" }}
                  >
                    {w.title || "Untitled"}
                  </div>
                  <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                    <span className="gd-eyebrow">{w.work_type ?? "research"}</span>
                    {w.status && w.status !== "active" && (
                      <span className="gd-eyebrow" style={{ color: "var(--gd-muted)" }}>
                        · {w.status}
                      </span>
                    )}
                    <HealthChip w={w} />
                  </div>
                </div>
                <ChevronRight className="w-4 h-4 shrink-0 mt-1" style={{ color: "var(--gd-dim)" }} aria-hidden />
              </div>
              {/* Pipeline stage — dual-coded: stage code + label + progress bar */}
              {book && (
                <div className="mt-2" data-testid={`stage-${w.id}`}>
                  <div className="flex items-center justify-between gap-2">
                    <span
                      className="text-[11px]"
                      style={{ fontFamily: "var(--gd-data)", color: "var(--gd-bronze)" }}
                    >
                      {book.pipeline_status} · {book.stage_label}
                    </span>
                    <span className="text-[11px]" style={{ color: "var(--gd-dim)" }}>
                      {stagePct(book.pipeline_status)}%
                    </span>
                  </div>
                  <div
                    className="h-1 rounded-full mt-1 overflow-hidden"
                    style={{ background: "var(--gd-line)" }}
                    role="progressbar"
                    aria-valuenow={stagePct(book.pipeline_status)}
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-label={`Pipeline stage ${book.pipeline_status} — ${book.stage_label}`}
                  >
                    <div
                      className="h-full rounded-full"
                      style={{ width: `${stagePct(book.pipeline_status)}%`, background: "var(--gd-bronze)" }}
                    />
                  </div>
                </div>
              )}
              <div
                className="flex items-center gap-4 pt-2 mt-auto"
                style={{ borderTop: "1px solid var(--gd-line)" }}
              >
                {[
                  { label: "Docs", value: w.doc_count ?? 0 },
                  { label: "Knowledge", value: w.knowledge_count ?? 0 },
                  { label: "Tasks", value: w.pending_tasks ?? 0 },
                ].map(({ label, value }) => (
                  <span key={label} className="flex items-baseline gap-1.5">
                    <span style={{ fontFamily: "var(--gd-data)", fontSize: 14, fontWeight: 600 }}>{value}</span>
                    <span className="gd-eyebrow">{label}</span>
                  </span>
                ))}
                {w.obj_created && (
                  <span className="ml-auto text-[11px]" style={{ color: "var(--gd-dim)" }}>
                    {format(new Date(w.obj_created), "MMM d")}
                  </span>
                )}
              </div>
            </Link>
            );
          })}
        </div>
      ) : (
        <div
          className="gd-panel text-center py-12"
          style={{ borderStyle: "dashed" }}
        >
          <BookOpen className="w-10 h-10 mx-auto mb-3" style={{ color: "var(--gd-dim)" }} aria-hidden />
          <p className="text-[15px] font-medium">No works yet</p>
          <p className="text-[13px] mt-1 max-w-sm mx-auto" style={{ color: "var(--gd-muted)" }}>
            Create a Work for each book, or turn documents you've already uploaded into Works.
          </p>
          <div className="flex items-center justify-center gap-2 mt-4">
            <button className="gd-chip" onClick={() => setLocation("/works?create=1")}>
              <Plus className="w-3.5 h-3.5" aria-hidden /> New Work
            </button>
            <button className="gd-chip" onClick={() => setLocation("/works")}>
              <Library className="w-3.5 h-3.5" aria-hidden /> Import from Library
            </button>
          </div>
        </div>
      )}

      {/* Secondary actions */}
      <div className="mt-6 grid gap-2">
        <button
          className="gd-row w-full"
          onClick={() => setLocation("/works")}
          data-testid="row-all-works"
        >
          <Library className="w-4 h-4" style={{ color: "var(--gd-dim)" }} aria-hidden />
          <span className="flex-1 text-left text-[14px]">
            All works &amp; import tools
          </span>
          <ChevronRight className="w-4 h-4" style={{ color: "var(--gd-dim)" }} aria-hidden />
        </button>
        {archivedCount > 0 && (
          <button
            className="gd-row w-full"
            onClick={() => setShowArchived((v) => !v)}
            data-testid="row-toggle-archived"
          >
            <BookOpen className="w-4 h-4" style={{ color: "var(--gd-dim)" }} aria-hidden />
            <span className="flex-1 text-left text-[14px]">
              {showArchived ? "Hide" : "Show"} archived ({archivedCount})
            </span>
          </button>
        )}
      </div>
    </div>
  );
}
