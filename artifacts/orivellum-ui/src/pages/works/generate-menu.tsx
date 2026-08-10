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


const GEN_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

type GenFormat = "excel" | "report-pdf" | "report-docx" | "slides" | "bundle";

interface GenerationResult {
  ok: boolean;
  doc_id: string;
  filename: string;
  path: string;
  download_url: string;
}

export function GenerateMenu({ workId }: { workId: string }) {
  const [busy, setBusy] = useState<GenFormat | null>(null);
  const [result, setResult] = useState<GenerationResult | null>(null);
  // Accumulate every generated path so Bundle can zip them all
  const [generatedPaths, setGeneratedPaths] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const generate = async (format: GenFormat) => {
    // Snapshot accumulated paths BEFORE clearing state — Bundle needs them
    const pathsSnapshot = generatedPaths.slice();
    setBusy(format);
    setError(null);
    setResult(null);
    try {
      let resp: Response;
      if (format === "excel") {
        resp = await apiFetch(`${GEN_BASE}/generate/excel`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ work_id: workId }),
        });
      } else if (format === "slides") {
        resp = await apiFetch(`${GEN_BASE}/generate/slides`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ work_id: workId }),
        });
      } else if (format === "bundle") {
        if (pathsSnapshot.length === 0) {
          throw new Error("Generate at least one format first, then Bundle will zip them all.");
        }
        resp = await apiFetch(`${GEN_BASE}/generate/bundle`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            work_id: workId,
            paths: pathsSnapshot,
            name: "bundle",
          }),
        });
      } else {
        // PDF or DOCX report
        const fmt = format === "report-pdf" ? "pdf" : "docx";
        resp = await apiFetch(`${GEN_BASE}/generate/report`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ work_id: workId, format: fmt }),
        });
      }
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error((body as any).detail ?? `Generation failed (${resp.status})`);
      }
      const data: GenerationResult = await resp.json();
      setResult(data);
      // Accumulate path (deduplicated) so Bundle can zip all outputs for this session
      if (format !== "bundle") {
        setGeneratedPaths((prev) => prev.includes(data.path) ? prev : [...prev, data.path]);
      }
      toast.success(`${data.filename} ready`, {
        description: "Click Download to save the file.",
        duration: 8000,
        action: {
          label: "Download",
          onClick: () => {
            const a = document.createElement("a");
            a.href = `${GEN_BASE}/generate/download?path=${encodeURIComponent(data.path)}`;
            a.download = data.filename;
            a.click();
          },
        },
      });
    } catch (e: any) {
      setError(e.message ?? "Generation failed");
      toast.error(e.message ?? "Generation failed");
    } finally {
      setBusy(null);
    }
  };

  const downloadResult = () => {
    if (!result) return;
    const a = document.createElement("a");
    a.href = `${GEN_BASE}/generate/download?path=${encodeURIComponent(result.path)}`;
    a.download = result.filename;
    a.click();
  };

  const formats: { id: GenFormat; label: string; icon: React.ElementType; desc: string }[] = [
    { id: "excel",      label: "Excel Workbook",  icon: FileSpreadsheet, desc: "Knowledge, docs & tasks as .xlsx" },
    { id: "report-pdf", label: "Report (PDF)",    icon: FileType,        desc: "Research report as .pdf" },
    { id: "report-docx",label: "Report (Word)",   icon: FileText,        desc: "Research report as .docx" },
    { id: "slides",     label: "Slide Deck",      icon: Presentation,    desc: "Knowledge slides as .pptx" },
    { id: "bundle",     label: "Bundle (ZIP)",    icon: Package,         desc: "Zip all generated outputs" },
  ];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          size="sm"
          variant="outline"
          className="gap-1.5 text-xs transition-opacity hover:opacity-80"
          style={{ color: "var(--green-2)", borderColor: "color-mix(in srgb, var(--green-2) 30%, transparent)" }}
          disabled={!!busy}
        >
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
          Generate
          {!busy && <ChevronDown className="w-3 h-3 opacity-60" />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="text-xs font-mono text-muted-foreground uppercase tracking-wider">
          Export formats
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {formats.map(({ id, label, icon: Icon, desc }) => (
          <DropdownMenuItem
            key={id}
            onClick={() => generate(id)}
            disabled={!!busy}
            className="gap-2 cursor-pointer"
          >
            {busy === id
              ? <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
              : <Icon className="w-4 h-4 text-muted-foreground" />}
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium">{label}</div>
              <div className="text-[11px] text-muted-foreground truncate">{desc}</div>
            </div>
          </DropdownMenuItem>
        ))}
        {result && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={downloadResult} className="gap-2 cursor-pointer" style={{ color: "var(--green-2)" }}>
              <Download className="w-4 h-4" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">Download last output</div>
                <div className="text-[11px] truncate text-muted-foreground">{result.filename}</div>
              </div>
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ─── Quick chat button (header shortcut) ──────────────────────────────────────

export function QuickChatButton({ workId }: { workId: string }) {
  const [, setLocation] = useLocation();
  const queryClient = useQueryClient();
  const createConv = useCreateConversation();

  const handleClick = () => {
    createConv.mutate(
      { data: { title: "New Discussion", work_id: workId } },
      {
        onSuccess: (res) => {
          queryClient.invalidateQueries({ queryKey: getGetWorkConversationsQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
          if (res?.conversation?.id) setLocation(`/chat?id=${res.conversation.id}`);
        },
        onError: () => toast.error("Could not start conversation"),
      }
    );
  };

  return (
    <button
      onClick={handleClick}
      disabled={createConv.isPending}
      title="Start a new discussion about this work"
      className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground/50 hover:text-primary transition-colors px-2 py-1 rounded hover:bg-primary/5"
    >
      {createConv.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <MessageSquarePlus className="w-3.5 h-3.5" />}
      Chat
    </button>
  );
}

// ─── Conversations tab ────────────────────────────────────────────────────────

