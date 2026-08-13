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
import { LoadingState, EmptyState, ErrorState, ConfirmAction } from "@/components/primitives";


const WORK_API_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

export function TasksTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const { data: tasksResp, isLoading, isError, refetch } = useGetWorkTasks(workId, {}, {
    query: { enabled: !!workId, queryKey: getGetWorkTasksQueryKey(workId) },
  });
  const createTask = useCreateWorkTask();
  const updateTask = useUpdateWorkTask();
  const [newTaskText, setNewTaskText] = useState("");
  const [newTaskPriority, setNewTaskPriority] = useState<number>(0);
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [editTaskText, setEditTaskText] = useState("");

  const handleAdd = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTaskText.trim()) return;
    createTask.mutate(
      { workId, data: { text: newTaskText, priority: newTaskPriority || undefined } },
      {
        onSuccess: () => {
          setNewTaskText("");
          setNewTaskPriority(0);
          queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
          toast.success("Task added");
        },
        onError: () => toast.error("Could not add task"),
      }
    );
  };

  const handleStartEdit = (task: { id?: string; text?: string }) => {
    setEditingTaskId(task.id ?? null);
    setEditTaskText(task.text ?? "");
  };

  const handleSaveEdit = (taskId: string) => {
    const trimmed = editTaskText.trim();
    setEditingTaskId(null);
    if (!trimmed) return;
    updateTask.mutate(
      { workId, taskId, data: { text: trimmed } },
      {
        onSuccess: () => queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) }),
        onError: () => toast.error("Could not update task"),
      }
    );
  };

  const handleChangePriority = (taskId: string, current: number) => {
    const next = ((current ?? 0) + 1) % 4;
    updateTask.mutate(
      { workId, taskId, data: { priority: next } },
      {
        onSuccess: () => queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) }),
        onError: () => toast.error("Could not update priority"),
      }
    );
  };

  const handleToggle = (taskId: string, current: string) => {
    const next = current === "completed" ? "pending" : "completed";
    updateTask.mutate(
      { workId, taskId, data: { status: next } },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
        },
        onError: () => toast.error("Could not update task"),
      }
    );
  };

  const [taskFilter, setTaskFilter] = useState<"all" | "pending" | "completed">("all");

  const allTasks = tasksResp?.tasks ?? [];
  const filteredTasks = taskFilter === "all" ? allTasks : allTasks.filter((t) => t.status === taskFilter);
  // WP5: window the task list so huge backlogs don't mount thousands of rows.
  const [taskWindow, setTaskWindow] = useState(60);
  const tasks = filteredTasks.slice(0, taskWindow);
  const hiddenTaskCount = filteredTasks.length - tasks.length;

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Filter chips */}
      <div className="flex items-center gap-2">
        {(["all", "pending", "completed"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setTaskFilter(f)}
            className={`px-3 min-h-11 rounded-full text-xs font-mono uppercase tracking-wider border transition-colors ${
              taskFilter === f
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-transparent text-muted-foreground border-border hover:border-primary/50"
            }`}
          >
            {f === "all" ? `All (${allTasks.length})` : f === "pending" ? `Pending (${allTasks.filter((t) => t.status !== "completed").length})` : `Done (${allTasks.filter((t) => t.status === "completed").length})`}
          </button>
        ))}
      </div>

      <form onSubmit={handleAdd} className="flex gap-2">
        <Input
          placeholder="Add a new task…"
          value={newTaskText}
          onChange={(e) => setNewTaskText(e.target.value)}
          className="bg-background/50 flex-1"
        />
        <select
          value={newTaskPriority}
          onChange={(e) => setNewTaskPriority(Number(e.target.value))}
          className="h-9 rounded-md border border-input bg-background px-2 text-xs font-mono text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          title="Priority (0 = none, higher = more urgent)"
        >
          <option value={0}>P0</option>
          <option value={1}>P1</option>
          <option value={2}>P2</option>
          <option value={3}>P3</option>
        </select>
        <Button type="submit" className="min-h-11" disabled={!newTaskText.trim() || createTask.isPending}>
          {createTask.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : "Add"}
        </Button>
      </form>

      <div className="space-y-2">
        {isLoading ? (
          <LoadingState rows={4} label="Loading tasks" />
        ) : isError ? (
          <ErrorState
            title="Couldn't load tasks"
            detail="The tasks for this work failed to load."
            onRetry={() => refetch()}
          />
        ) : tasks.length > 0 ? (
          tasks.map((task) => (
            <div
              key={task.id}
              className="flex items-start gap-3 p-3 rounded-lg hover:bg-muted/30 transition-colors group border border-transparent hover:border-border/50 min-h-11"
            >
              <Checkbox
                id={task.id}
                className="mt-1"
                checked={task.status === "completed"}
                onCheckedChange={() => handleToggle(task.id!, task.status ?? "pending")}
                disabled={updateTask.isPending}
              />
              <div className="flex-1 space-y-1">
                {editingTaskId === task.id ? (
                  <input
                    autoFocus
                    className="w-full text-sm bg-transparent border-b border-primary outline-none pb-0.5"
                    value={editTaskText}
                    onChange={(e) => setEditTaskText(e.target.value)}
                    onBlur={() => handleSaveEdit(task.id!)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSaveEdit(task.id!);
                      if (e.key === "Escape") setEditingTaskId(null);
                    }}
                  />
                ) : (
                  <label
                    htmlFor={task.id}
                    className={`block text-sm font-medium leading-snug cursor-pointer line-clamp-3 ${
                      task.status === "completed" ? "line-through text-muted-foreground" : ""
                    }`}
                    onDoubleClick={() => handleStartEdit(task)}
                    title="Double-click to edit"
                  >
                    {task.text}
                  </label>
                )}
              </div>
              <button
                onClick={() => handleChangePriority(task.id!, task.priority ?? 0)}
                className="opacity-0 group-hover:opacity-100 transition-opacity min-h-11 flex items-center shrink-0"
                title="Click to cycle priority"
              >
                <Badge
                  variant="outline"
                  className="text-[9px] uppercase font-mono cursor-pointer hover:bg-primary/10"
                  style={
                    task.priority === 1 ? { borderColor: "color-mix(in srgb, var(--gd-danger) 55%, transparent)", color: "var(--gd-danger)" } :
                    task.priority === 2 ? { borderColor: "color-mix(in srgb, var(--gd-bronze) 45%, transparent)", color: "var(--gd-bronze)" } :
                    task.priority === 3 ? { borderColor: "var(--gd-line-control)", color: "var(--gd-dim)" } :
                    undefined
                  }
                >
                  P{task.priority || 0}
                </Badge>
              </button>
              <ConfirmAction
                title="Delete task?"
                consequence="This task will be permanently removed from this work. This cannot be undone."
                confirmLabel="Delete"
                destructive
                onConfirm={() => {
                  if (!task.id) return;
                  apiFetch(`${WORK_API_BASE}/works/${workId}/tasks/${task.id}`, { method: "DELETE" })
                    .then(() => {
                      queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) });
                      queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
                    })
                    .catch(() => toast.error("Could not delete task"));
                }}
                trigger={
                  <button
                    className="opacity-0 group-hover:opacity-100 transition-opacity min-h-11 px-1 rounded hover:bg-destructive/10 hover:text-destructive text-muted-foreground shrink-0"
                    title="Delete task"
                    data-testid={`delete-task-${task.id}`}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                }
              />
            </div>
          ))
        ) : (
          <EmptyState
            icon={<CheckSquare />}
            title="No tasks yet for this work"
            description="Add a task above to start tracking work items."
          />
        )}
        {hiddenTaskCount > 0 && (
          <div className="flex justify-center pt-1">
            <Button
              variant="outline"
              className="min-h-11 font-mono text-xs"
              onClick={() => setTaskWindow((n) => n + 60)}
            >
              Show more tasks ({hiddenTaskCount} hidden)
            </Button>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Generate menu ────────────────────────────────────────────────────────────

