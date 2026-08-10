import { useState, useEffect, useRef, useMemo } from "react";
import { useParams, Link, useLocation, useSearch } from "wouter";
import { ErrorBoundary } from "@/components/error-boundary";
import {
  useGetWork,
  useGetWorkStats,
  useUpdateWork,
  useDeleteWork,
  useDeleteKnowledgeItem,
  useGetWorkDocuments,
  useGetWorkKnowledge,
  useGetWorkTasks,
  useGetWorkConversations,
  useCreateWorkTask,
  useUpdateWorkTask,
  useCreateConversation,
  useListLibrary,
  getGetWorkQueryKey,
  getGetWorkStatsQueryKey,
  getListWorksQueryKey,
  getGetWorkTasksQueryKey,
  getGetWorkDocumentsQueryKey,
  getGetWorkKnowledgeQueryKey,
  getGetWorkConversationsQueryKey,
  getListConversationsQueryKey,
  useGetEmbeddingsStatus,
  getGetEmbeddingsStatusQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient, useQuery, useMutation } from "@tanstack/react-query";
import { format } from "date-fns";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  ArrowLeft,
  FileText,
  Network,
  CheckSquare,
  MessageSquare,
  Plus,
  Clock,
  Loader2,
  Sparkles,
  ThumbsUp,
  ThumbsDown,
  Pencil,
  Check,
  X,
  Trash2,
  GraduationCap,
  RefreshCw,
  ChevronRight,
  MessageSquarePlus,
  Unlink,
  Search,
  BookOpen,
  ChevronDown,
  Trophy,
  BarChart2,
  AlertTriangle,
  TrendingUp,
  Lightbulb,
  Brain,
  Star,
  GitBranch,
  Share2,
  FileSpreadsheet,
  FileType,
  Presentation,
  Package,
  Download,
  Zap,
  Film,
  Scroll,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Textarea } from "@/components/ui/textarea";
import { BookTab }       from "./book-tab";
import { BrainstormTab } from "./brainstorm-tab";
import { TrailerTab }    from "./trailer-tab";
import { GenesisTab }    from "./genesis-tab";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { KnowledgeGraph, GNode } from "@/components/knowledge-graph";
import { LearnTab } from "@/pages/learning/learn-tab";


export function SearchTab({ workId, initialQuery = "" }: { workId: string; initialQuery?: string }) {
  const [, navigate] = useLocation();
  const [query, setQuery] = useState(initialQuery);
  const [submitted, setSubmitted] = useState("");
  const [results, setResults] = useState<{ knowledge: any[]; chunks: any[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Monotonic counter so a slow earlier response can't clobber a newer one.
  const searchSeq = useRef(0);

  // Embeddings circuit-breaker status (#203) — no network call, just reads in-process state
  const { data: embedStatus } = useGetEmbeddingsStatus({
    query: { queryKey: getGetEmbeddingsStatusQueryKey(), staleTime: 30_000, refetchInterval: 30_000 },
  });

  // Focus the input when the tab is first activated (mount). Deferred so it
  // runs after Radix Tabs' own focus management (which focuses the trigger).
  useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(t);
  }, []);

  const runSearch = async (q: string) => {
    const seq = ++searchSeq.current;
    setSubmitted(q);
    setLoading(true);
    setError(null);
    try {
      const base = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
      const res = await apiFetch(`${base}/works/${workId}/search?q=${encodeURIComponent(q)}&limit=20`);
      if (!res.ok) throw new Error(`Search failed: ${res.status}`);
      const data = await res.json();
      if (seq === searchSeq.current) setResults(data);
    } catch (err: any) {
      if (seq === searchSeq.current) setError(err.message ?? "Search failed");
    } finally {
      if (seq === searchSeq.current) setLoading(false);
    }
  };

  // Search-as-you-type with a 350 ms debounce.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      // Cleared input → drop results and invalidate any in-flight request.
      searchSeq.current++;
      setResults(null);
      setSubmitted("");
      setError(null);
      setLoading(false);
      return;
    }
    if (q === submitted) return; // already showing results for this query
    const t = setTimeout(() => runSearch(q), 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const handleSearch = (e?: React.FormEvent) => {
    e?.preventDefault();
    const q = query.trim();
    if (!q) return;
    runSearch(q); // explicit re-submission bypasses the debounce
  };

  const total = (results?.knowledge.length ?? 0) + (results?.chunks.length ?? 0);

  return (
    <div className="space-y-6">
      <form onSubmit={handleSearch} className="flex gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            ref={inputRef}
            className="pl-9 font-mono text-sm"
            placeholder="Search knowledge and documents…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
        </div>
        <Button type="submit" disabled={!query.trim() || loading}>
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Search"}
        </Button>
      </form>

      {/* Semantic search readiness banner (#203) */}
      {embedStatus?.circuit_open && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-md border text-xs font-mono" style={{ color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" }}>
          <Search className="w-3.5 h-3.5 shrink-0" />
          <span>Semantic search is temporarily unavailable — showing keyword results only. The embeddings service will retry automatically.</span>
        </div>
      )}

      {error && (
        <p className="text-sm text-destructive">{error}</p>
      )}

      {results && (
        <div className="space-y-8">
          <p className="text-xs font-mono text-muted-foreground">
            {total} result{total !== 1 ? "s" : ""} for <span className="text-foreground">"{submitted}"</span>
          </p>

          {/* Knowledge hits */}
          {results.knowledge.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-xs font-mono uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                <Network className="w-3.5 h-3.5" /> Knowledge ({results.knowledge.length})
              </h3>
              <div className="space-y-2">
                {results.knowledge.map((item: any) => (
                  <Card key={item.id} className="p-3">
                    <div className="flex items-start gap-3">
                      <Badge variant="secondary" className="text-[10px] shrink-0 mt-0.5">
                        {item.kind ?? "fact"}
                      </Badge>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm">{item.text}</p>
                        {item.subject && (
                          <p className="text-xs font-mono text-muted-foreground mt-1">
                            {item.subject}
                            {item.relation && <> · {item.relation}</>}
                            {item.object && <> · {item.object}</>}
                          </p>
                        )}
                      </div>
                      {item.confidence != null && (() => {
                        const pct = item.confidence * 100;
                        const tier: { label: string; style: React.CSSProperties } =
                          pct >= 80 ? { label: "High", style: { color: "var(--green-2)", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)" } }
                          : pct >= 50 ? { label: "Med", style: { color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" } }
                          : { label: "Low", style: { color: "var(--rust)", background: "var(--rust-soft)", borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)" } };
                        return (
                          <span className="text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded border shrink-0" style={tier.style}>
                            {pct.toFixed(0)}% {tier.label}
                          </span>
                        );
                      })()}
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {/* Document chunk hits */}
          {results.chunks.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-xs font-mono uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                <FileText className="w-3.5 h-3.5" /> Documents ({results.chunks.length})
              </h3>
              <div className="space-y-2">
                {results.chunks.map((chunk: any) => (
                  <Card
                    key={chunk.id}
                    className="p-3 cursor-pointer hover:bg-muted/30 transition-colors"
                    onClick={() => navigate(`/library/${chunk.doc_id}`)}
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-medium truncate">{chunk.doc_title ?? chunk.doc_id}</span>
                        {chunk.doc_kind && (
                          <Badge variant="outline" className="text-[10px]">{chunk.doc_kind}</Badge>
                        )}
                      </div>
                      <p className="text-sm text-muted-foreground line-clamp-3">{chunk.text}</p>
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {total === 0 && (
            <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
              <Search className="w-8 h-8 mx-auto mb-3 opacity-20" />
              <p className="text-muted-foreground text-sm">No results found for "{submitted}"</p>
              <p className="text-xs text-muted-foreground mt-1">Try different keywords or check that documents have been fully extracted.</p>
            </div>
          )}
        </div>
      )}

      {!results && !loading && (
        <div className="text-center py-16 text-muted-foreground">
          <Search className="w-10 h-10 mx-auto mb-4 opacity-15" />
          <p className="text-sm">Search across all knowledge items and document text in this Work.</p>
        </div>
      )}
    </div>
  );
}

// ─── Gaps tab ────────────────────────────────────────────────────────────────

