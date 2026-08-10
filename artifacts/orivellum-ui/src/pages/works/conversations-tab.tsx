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


export function ConversationsTab({ workId }: { workId: string }) {
  const [, setLocation] = useLocation();
  const queryClient = useQueryClient();
  const { data: convResp, isLoading } = useGetWorkConversations(workId, {
    query: { enabled: !!workId, queryKey: getGetWorkConversationsQueryKey(workId) },
  });
  const createConv = useCreateConversation();

  const handleNewDiscussion = () => {
    createConv.mutate(
      { data: { title: "New Discussion", work_id: workId } },
      {
        onSuccess: (res) => {
          queryClient.invalidateQueries({ queryKey: getGetWorkConversationsQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
          if (res?.conversation?.id) setLocation(`/chat?id=${res.conversation.id}`);
        },
        onError: () => toast.error("Could not start conversation"),
      }
    );
  };

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  const conversations = convResp?.conversations ?? [];

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-xl font-serif font-medium">Conversations</h3>
        <Button
          size="sm"
          variant="outline"
          className="gap-2"
          onClick={handleNewDiscussion}
          disabled={createConv.isPending}
        >
          {createConv.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Plus className="w-4 h-4" />
          )}
          New Discussion
        </Button>
      </div>

      {conversations.length > 0 ? (
        <div className="grid gap-3">
          {conversations.map((conv) => (
            <Link key={conv.id} href={`/chat?id=${conv.id}`}>
              <Card className="hover-elevate cursor-pointer">
                <CardContent className="p-4 flex items-center justify-between">
                  <div className="space-y-1">
                    <h4 className="font-medium text-lg">{conv.title || "Untitled Conversation"}</h4>
                    <p className="text-sm text-muted-foreground truncate max-w-xl">
                      {conv.last_message || "No messages yet."}
                    </p>
                  </div>
                  <div className="text-right text-xs font-mono text-muted-foreground space-y-1 shrink-0">
                    <div>{conv.message_count || 0} msgs</div>
                    <div>{conv.updated_at ? format(new Date(conv.updated_at), "MMM d") : ""}</div>
                    {(conv as any).model && (
                      <div className="text-[10px] opacity-60 font-mono">
                        {String((conv as any).model).split("/").pop()?.split("-").slice(0, 3).join("-")}
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <MessageSquare className="w-8 h-8 mx-auto mb-3 opacity-20" />
          <p className="text-muted-foreground">No conversations linked to this work.</p>
          <Button size="sm" variant="outline" className="gap-2 mt-4" onClick={handleNewDiscussion} disabled={createConv.isPending}>
            <Plus className="w-4 h-4" /> Start a Discussion
          </Button>
        </div>
      )}
    </div>
  );
}

// ─── Search tab ───────────────────────────────────────────────────────────────

