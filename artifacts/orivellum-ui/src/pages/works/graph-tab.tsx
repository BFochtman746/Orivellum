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


const API_BASE_GRAPH = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// Entity kind filter config for the Works graph tab
const GRAPH_ENTITY_KINDS = [
  { value: "concept",   label: "Concepts",   color: "#8b5cf6" },
  { value: "person",    label: "People",     color: "#6366f1" },
  { value: "place",     label: "Places",     color: "#10b981" },
  { value: "theme",     label: "Themes",     color: "#f59e0b" },
  { value: "scripture", label: "Scripture",  color: "#ef4444" },
] as const;

export function GraphTab({ workId }: { workId: string }) {
  const [, navigate]      = useLocation();
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(new Set());

  const { data: graphData, isLoading, error } = useQuery({
    queryKey: ["workGraph", workId],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE_GRAPH}/works/${workId}/graph?limit=150`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json() as Promise<{ nodes: any[]; edges: any[]; node_count: number; edge_count: number }>;
    },
    staleTime: 30_000,
  });

  const toggleKind = (kind: string) => {
    setHiddenKinds(prev => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind); else next.add(kind);
      return next;
    });
  };

  const handleNavigate = (node: GNode) => {
    if (node.type === "document") {
      navigate(`/library/${node.id}`);
    }
  };

  return (
    <div className="space-y-4">
      {/* Entity kind filter chips */}
      {!!graphData?.nodes?.length && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-muted-foreground font-medium">Show:</span>
          {GRAPH_ENTITY_KINDS.map(({ value, label, color }) => {
            const on = !hiddenKinds.has(value);
            return (
              <button
                key={value}
                onClick={() => toggleKind(value)}
                className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium border transition-all
                  ${on
                    ? "bg-background border-border text-foreground"
                    : "bg-transparent border-border/30 text-muted-foreground/50"
                  }`}
              >
                <span className="w-2 h-2 rounded-full shrink-0"
                  style={{ background: on ? color : "#94a3b8" }} />
                {label}
              </button>
            );
          })}
        </div>
      )}

      <KnowledgeGraph
        nodes={graphData?.nodes ?? []}
        edges={graphData?.edges ?? []}
        hiddenKinds={hiddenKinds}
        onNavigate={handleNavigate}
        height={480}
        loading={isLoading}
        error={error ? String(error) : undefined}
        nodeCount={graphData?.node_count}
        edgeCount={graphData?.edge_count}
      />
    </div>
  );
}


// ─── Quiz tab ─────────────────────────────────────────────────────────────────

