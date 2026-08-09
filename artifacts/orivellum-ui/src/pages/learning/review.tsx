/**
 * Knowledge review — tap-triage queue for AI-suggested knowledge items.
 *
 * Learning-app home for approving/rejecting what the AI harvested from
 * documents. Uses the existing unified review inbox, filtered to knowledge
 * items; approve/reject/defer resolve through the same claim-first endpoint
 * the Command review page uses, so both stay consistent.
 */
import { useState } from "react";
import { useLocation } from "wouter";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { toast } from "sonner";
import {
  Inbox,
  Check,
  X,
  Clock,
  ChevronRight,
  Loader2,
} from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface ReviewItem {
  id: string; // namespaced "knowledge:<uuid>"
  item_type: string;
  title: string;
  description?: string;
  confidence?: number | null;
  work_id?: string | null;
  work_title?: string | null;
  evidence?: {
    source_doc?: string | null;
    source_doc_id?: string | null;
    subject?: string | null;
    predicate?: string | null;
    object?: string | null;
  };
  created_at?: string;
}

export default function KnowledgeReview() {
  const [, setLocation] = useLocation();
  const queryClient = useQueryClient();
  // Items resolved this visit — kept out of the list immediately (optimistic)
  const [resolved, setResolved] = useState<Record<string, "approve" | "reject" | "defer">>({});
  const [pendingId, setPendingId] = useState<string | null>(null);

  const { data, isLoading } = useQuery<{ items: ReviewItem[]; counts_by_type?: Record<string, number> }>({
    queryKey: ["review-queue-knowledge"],
    queryFn: () => apiFetch(`${BASE}/review/queue?limit=200`).then((r) => r.json()),
    staleTime: 15_000,
  });

  const resolve = useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" | "defer" }) =>
      apiFetch(`${BASE}/review/${id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ decision, reason: "" }),
      }).then(async (r) => {
        if (r.status === 409) throw new Error("already");
        if (!r.ok) throw new Error("failed");
        return r.json();
      }),
    onSuccess: (_d, { id, decision }) => {
      setResolved((m) => ({ ...m, [id]: decision }));
      queryClient.invalidateQueries({ queryKey: ["review-queue"] });
      queryClient.invalidateQueries({ queryKey: ["review-queue-knowledge"] });
    },
    onError: (e: Error, { id }) => {
      if (e.message === "already") {
        // Someone (or another tab) already handled it — just drop it from view
        setResolved((m) => ({ ...m, [id]: "defer" }));
        queryClient.invalidateQueries({ queryKey: ["review-queue-knowledge"] });
        toast.info("That item was already reviewed");
      } else {
        toast.error("Could not save your decision — try again");
      }
    },
    onSettled: () => setPendingId(null),
  });

  const act = (id: string, decision: "approve" | "reject" | "defer") => {
    if (pendingId) return; // one at a time
    setPendingId(id);
    resolve.mutate({ id, decision });
  };

  const items = (data?.items ?? []).filter(
    (i) => i.item_type === "knowledge" && !resolved[i.id],
  );
  const doneCount = Object.keys(resolved).length;

  return (
    <div className="pb-10 max-w-2xl">
      <div className="flex items-end justify-between gap-3 pt-2 pb-4">
        <div>
          <p className="gd-eyebrow">AI-suggested knowledge</p>
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
            Review queue
          </h2>
        </div>
        <div className="text-right">
          <div style={{ fontFamily: "var(--gd-data)", fontSize: 18, fontWeight: 600 }}>
            {items.length}
          </div>
          <div className="gd-eyebrow">Waiting</div>
        </div>
      </div>

      {isLoading ? (
        <div className="grid gap-3">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-32 w-full rounded-[10px]" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <div className="gd-panel text-center py-12" style={{ borderStyle: "dashed" }}>
          <Inbox className="w-10 h-10 mx-auto mb-3" style={{ color: "var(--gd-dim)" }} aria-hidden />
          <p className="text-[15px] font-medium">
            {doneCount > 0 ? `All done — ${doneCount} reviewed` : "Nothing waiting for review"}
          </p>
          <p className="text-[13px] mt-1 max-w-sm mx-auto" style={{ color: "var(--gd-muted)" }}>
            When the AI extracts knowledge from your documents, it lands here for a quick
            approve-or-reject pass.
          </p>
          <button className="gd-chip mt-4" onClick={() => setLocation("/learning")}>
            Back to Learning
          </button>
        </div>
      ) : (
        <div className="grid gap-3">
          {items.map((item) => {
            const busy = pendingId === item.id;
            return (
              <div key={item.id} className="gd-panel" data-testid={`review-${item.id}`}>
                {/* Context line */}
                <div className="flex items-center gap-2 flex-wrap mb-2">
                  {item.work_title && item.work_id && (
                    <button
                      className="gd-eyebrow inline-flex items-center gap-1"
                      style={{ color: "var(--gd-bronze)" }}
                      onClick={() => setLocation(`/works/${item.work_id}?tab=knowledge`)}
                    >
                      {item.work_title} <ChevronRight className="w-3 h-3" aria-hidden />
                    </button>
                  )}
                  {typeof item.confidence === "number" && (
                    <span className="gd-eyebrow" style={{ color: "var(--gd-dim)" }}>
                      {Math.round(item.confidence * 100)}% confident
                    </span>
                  )}
                </div>

                <p className="text-[15px] leading-snug">{item.title}</p>
                {item.description && (
                  <p className="text-[13px] mt-1" style={{ color: "var(--gd-muted)" }}>
                    {item.description}
                  </p>
                )}
                {item.evidence?.source_doc && (
                  <p className="text-[12px] mt-1.5" style={{ color: "var(--gd-dim)" }}>
                    From: {item.evidence.source_doc}
                  </p>
                )}

                {/* Triage actions — 48px targets */}
                <div
                  className="grid grid-cols-3 gap-2 mt-3 pt-3"
                  style={{ borderTop: "1px solid var(--gd-line)" }}
                >
                  <button
                    className="flex items-center justify-center gap-1.5 rounded-[8px] text-[13px] font-medium min-h-12"
                    style={{ color: "var(--gd-success)", border: "1px solid var(--gd-success)" }}
                    disabled={busy}
                    onClick={() => act(item.id, "approve")}
                    data-testid={`approve-${item.id}`}
                  >
                    {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden /> : <Check className="w-4 h-4" aria-hidden />}
                    Keep
                  </button>
                  <button
                    className="flex items-center justify-center gap-1.5 rounded-[8px] text-[13px] font-medium min-h-12"
                    style={{ color: "var(--gd-danger)", border: "1px solid var(--gd-danger)" }}
                    disabled={busy}
                    onClick={() => act(item.id, "reject")}
                    data-testid={`reject-${item.id}`}
                  >
                    <X className="w-4 h-4" aria-hidden /> Reject
                  </button>
                  <button
                    className="flex items-center justify-center gap-1.5 rounded-[8px] text-[13px] font-medium min-h-12"
                    style={{ color: "var(--gd-muted)", border: "1px solid var(--gd-line)" }}
                    disabled={busy}
                    onClick={() => act(item.id, "defer")}
                    data-testid={`defer-${item.id}`}
                  >
                    <Clock className="w-4 h-4" aria-hidden /> Later
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
