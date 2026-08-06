/**
 * Topics graph page — /topics
 *
 * Browsable topic clusters produced by the nightshift clustering pass.
 * Shows topic names, document counts, and lets the user drill into any
 * topic to see which documents it contains.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocation } from "wouter";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Input } from "@/components/ui/input";
import {
  Network, FileText, ChevronRight, RefreshCw, Loader2,
  Layers, Search, BookOpen, X,
} from "lucide-react";
import { toast } from "sonner";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface Topic {
  id: string;
  name: string;
  kind: string;
  doc_count: number;
  what_it_is: string | null;
  purpose: string | null;
  created_at: string;
}

interface TopicDocument {
  id: string;
  title: string;
  kind: string | null;
  readiness: string | null;
  work_id: string | null;
  word_count: number;
}

interface TopicDetail {
  topic: { id: string; name: string; kind: string; created_at: string };
  profile: {
    what_it_is: string; purpose: string;
    connected: string[]; gaps: string[];
  } | null;
  documents: TopicDocument[];
  doc_count: number;
}

export default function TopicsPage() {
  const [, navigate] = useLocation();
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [rebuilding, setRebuilding] = useState(false);

  const { data, isLoading, refetch } = useQuery<{ topics: Topic[]; total: number }>({
    queryKey: ["topics"],
    queryFn: () => apiFetch(`${BASE}/topics`).then((r) => r.json()),
    staleTime: 60_000,
  });

  const { data: detail, isLoading: detailLoading } = useQuery<TopicDetail>({
    queryKey: ["topic", selectedId],
    queryFn: () => apiFetch(`${BASE}/topics/${selectedId}`).then((r) => r.json()),
    enabled: !!selectedId,
    staleTime: 60_000,
  });

  const topics = (data?.topics ?? []).filter((t) =>
    !search || t.name.toLowerCase().includes(search.toLowerCase())
  );

  const handleRebuild = async () => {
    setRebuilding(true);
    try {
      const resp = await apiFetch(`${BASE}/topics/rebuild`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_profiles: true }),
      });
      if (!resp.ok) throw new Error("Rebuild failed");
      toast.success("Clustering rebuild started", {
        description: "New topics will appear once the background job finishes.",
      });
      setTimeout(() => refetch(), 5_000);
    } catch (e: any) {
      toast.error(e.message ?? "Could not start rebuild");
    } finally {
      setRebuilding(false);
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-300 pb-20">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Network className="w-6 h-6 text-primary" />
          <div>
            <span className="eyebrow mb-1">Everything, linked</span>
            <h1 className="vellum-h1">The Web</h1>
            <div className="gilt-rule w-24" />
            <p className="text-[13px] mt-1.5" style={{ color: 'var(--ink-soft)' }}>
              Concepts and sources — and the edges between them.
            </p>
          </div>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={handleRebuild}
          disabled={rebuilding}
          className="gap-1.5"
        >
          {rebuilding
            ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
            : <RefreshCw className="w-3.5 h-3.5" />}
          Rebuild clusters
        </Button>
      </div>

      {/* Stats bar */}
      {data && (
        <div className="flex items-center gap-6 text-sm font-mono text-muted-foreground border-b border-border/40 pb-4">
          <span>{data.total} topic{data.total !== 1 ? "s" : ""}</span>
          <span>{data.topics.reduce((a, t) => a + t.doc_count, 0)} documents clustered</span>
          {search && (
            <span className="text-primary">{topics.length} matching</span>
          )}
        </div>
      )}

      <div className={`grid gap-6 ${selectedId ? "grid-cols-1 md:grid-cols-2" : "grid-cols-1"}`}>
        {/* Topic list */}
        <div className="space-y-4">
          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter topics…"
              className="pl-9"
            />
          </div>

          {isLoading ? (
            <div className="space-y-3">
              {[1, 2, 3, 4, 5].map((i) => <Skeleton key={i} className="h-20 w-full" />)}
            </div>
          ) : topics.length === 0 ? (
            <div className="text-center py-20 bg-muted/10 border border-dashed rounded-lg space-y-3">
              <Layers className="w-10 h-10 text-muted-foreground mx-auto opacity-40" />
              <p className="text-muted-foreground font-serif">
                {search ? "No topics match your search." : "No topic clusters yet."}
              </p>
              {!search && (
                <p className="text-sm text-muted-foreground/60 max-w-xs mx-auto">
                  Topics are built automatically during the nightly maintenance pass.
                  Click "Rebuild clusters" to run clustering now.
                </p>
              )}
            </div>
          ) : (
            <div className="space-y-2">
              {topics.map((topic) => (
                <Card
                  key={topic.id}
                  onClick={() => setSelectedId(selectedId === topic.id ? null : topic.id)}
                  className={`cursor-pointer transition-colors ${
                    selectedId === topic.id
                      ? "border-primary/50 bg-primary/5"
                      : "hover:border-primary/20 hover:bg-muted/30"
                  }`}
                >
                  <CardContent className="p-4 flex items-center justify-between gap-4">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                        <Network className="w-3.5 h-3.5 text-primary" />
                      </div>
                      <div className="min-w-0">
                        <p className="font-medium text-sm truncate">{topic.name}</p>
                        {topic.what_it_is && (
                          <p className="text-[11px] text-muted-foreground truncate mt-0.5">
                            {topic.what_it_is}
                          </p>
                        )}
                        <div className="flex items-center gap-2 mt-1">
                          <Badge variant="secondary" className="font-mono text-[10px]">
                            {topic.doc_count} doc{topic.doc_count !== 1 ? "s" : ""}
                          </Badge>
                          <span className="text-[10px] font-mono text-muted-foreground/50 uppercase">
                            {topic.kind === "semantic_cluster" ? "semantic" : topic.kind}
                          </span>
                        </div>
                      </div>
                    </div>
                    <ChevronRight className={`w-4 h-4 text-muted-foreground shrink-0 transition-transform ${selectedId === topic.id ? "rotate-90" : ""}`} />
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </div>

        {/* Topic detail panel */}
        {selectedId && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-semibold font-serif">
                {detail?.topic.name ?? "Loading…"}
              </h2>
              <button
                onClick={() => setSelectedId(null)}
                className="text-muted-foreground/50 hover:text-muted-foreground transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Profile */}
            {detail?.profile && (
              <div className="p-3 rounded-lg bg-muted/30 border border-border/50 space-y-2">
                <p className="text-sm font-medium">{detail.profile.what_it_is}</p>
                {detail.profile.purpose && (
                  <p className="text-[11px] text-muted-foreground">{detail.profile.purpose}</p>
                )}
                {detail.profile.gaps.length > 0 && (
                  <div className="space-y-1">
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Gaps</p>
                    {detail.profile.gaps.slice(0, 3).map((g, i) => (
                      <p key={i} className="text-[11px] text-amber-700">• {g}</p>
                    ))}
                  </div>
                )}
              </div>
            )}

            {/* Documents */}
            {detailLoading ? (
              <div className="space-y-2">
                {[1, 2, 3].map((i) => <Skeleton key={i} className="h-14 w-full" />)}
              </div>
            ) : detail?.documents.length === 0 ? (
              <p className="text-sm text-muted-foreground italic">No documents in this topic.</p>
            ) : (
              <div className="space-y-2">
                <p className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">
                  {detail?.doc_count ?? 0} document{(detail?.doc_count ?? 0) !== 1 ? "s" : ""}
                </p>
                {detail?.documents.map((doc) => (
                  <div
                    key={doc.id}
                    onClick={() => navigate(`/library/${doc.id}`)}
                    className="flex items-center gap-3 p-3 rounded-lg border border-border/50 hover:border-primary/20 hover:bg-muted/20 cursor-pointer transition-colors"
                  >
                    <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium truncate">{doc.title || "(untitled)"}</p>
                      <div className="flex items-center gap-2 mt-0.5">
                        {doc.kind && (
                          <Badge variant="outline" className="font-mono text-[10px] uppercase py-0">
                            {doc.kind}
                          </Badge>
                        )}
                        {doc.word_count > 0 && (
                          <span className="text-[10px] font-mono text-muted-foreground">
                            {doc.word_count.toLocaleString()} words
                          </span>
                        )}
                      </div>
                    </div>
                    <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
