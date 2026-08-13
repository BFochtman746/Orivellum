import { useState } from "react";
import { useLocation } from "wouter";
import {
  useCreateConversation,
  getGetWorkStatsQueryKey,
  getGetWorkConversationsQueryKey,
  getListConversationsQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import {
  Loader2,
  MessageSquarePlus,
  ChevronDown,
  FileText,
  FileSpreadsheet,
  FileType,
  Presentation,
  Package,
  Download,
  Zap,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toast } from "sonner";

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
          className="gap-1.5 text-xs transition-opacity hover:opacity-80 min-h-11"
          style={{ color: "var(--gd-primary)", borderColor: "var(--gd-primary-soft)" }}
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
            <DropdownMenuItem onClick={downloadResult} className="gap-2 cursor-pointer" style={{ color: "var(--gd-primary)" }}>
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

