import { useState } from "react";
import { useParams, Link } from "wouter";
import { 
  useGetWork, 
  useGetWorkDocuments, 
  useGetWorkKnowledge, 
  useGetWorkTasks,
  useGetWorkConversations,
  useCreateWorkTask,
  getGetWorkQueryKey,
  getGetWorkTasksQueryKey,
  getGetWorkDocumentsQueryKey,
  getGetWorkKnowledgeQueryKey,
  getGetWorkConversationsQueryKey
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { 
  ArrowLeft, 
  FileText, 
  Network, 
  CheckSquare, 
  MessageSquare,
  Plus,
  Clock,
  MoreVertical
} from "lucide-react";

export default function WorkDetail() {
  const { workId } = useParams();
  const queryClient = useQueryClient();
  
  const { data: workResp, isLoading: loadingWork } = useGetWork(workId!, { query: { enabled: !!workId, queryKey: getGetWorkQueryKey(workId!) }});
  
  const work = workResp?.work;

  return (
    <div className="space-y-6 animate-in fade-in duration-500 pb-20">
      <div className="flex items-center gap-4 text-sm font-mono uppercase tracking-widest text-muted-foreground mb-8">
        <Link href="/works" className="hover:text-foreground transition-colors flex items-center gap-1">
          <ArrowLeft className="w-3 h-3" /> Works
        </Link>
        <span>/</span>
        <span className="text-foreground">{loadingWork ? <Skeleton className="w-20 h-4 inline-block align-middle" /> : work?.title}</span>
      </div>

      {loadingWork ? (
        <div className="space-y-4">
          <Skeleton className="h-12 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      ) : work ? (
        <div className="space-y-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h1 className="text-4xl font-serif font-semibold tracking-tight">{work.title}</h1>
              {work.description && (
                <p className="text-lg text-muted-foreground font-serif italic mt-2 max-w-3xl leading-relaxed">
                  {work.description}
                </p>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button variant="outline" size="icon"><MoreVertical className="w-4 h-4" /></Button>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <Badge variant="outline" className="font-mono text-xs uppercase bg-primary/5 text-primary border-primary/20">{work.status}</Badge>
            <Badge variant="secondary" className="font-mono text-xs uppercase">{work.work_type}</Badge>
            <span className="text-sm font-mono text-muted-foreground flex items-center gap-1">
              <Clock className="w-3 h-3" />
              Created {work.created_at ? format(new Date(work.created_at), 'MMM d, yyyy') : 'Unknown'}
            </span>
          </div>
        </div>
      ) : null}

      <div className="pt-8">
        <Tabs defaultValue="documents" className="w-full">
          <TabsList className="w-full justify-start border-b border-border/50 rounded-none bg-transparent h-auto p-0 space-x-6">
            <TabsTrigger value="documents" className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent py-3 px-1 font-mono text-xs uppercase tracking-wider">
              <FileText className="w-4 h-4 mr-2" /> Documents
            </TabsTrigger>
            <TabsTrigger value="knowledge" className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent py-3 px-1 font-mono text-xs uppercase tracking-wider">
              <Network className="w-4 h-4 mr-2" /> Knowledge
            </TabsTrigger>
            <TabsTrigger value="tasks" className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent py-3 px-1 font-mono text-xs uppercase tracking-wider">
              <CheckSquare className="w-4 h-4 mr-2" /> Tasks
            </TabsTrigger>
            <TabsTrigger value="conversations" className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent py-3 px-1 font-mono text-xs uppercase tracking-wider">
              <MessageSquare className="w-4 h-4 mr-2" /> Conversations
            </TabsTrigger>
          </TabsList>

          <div className="mt-8">
            <TabsContent value="documents">
              <DocumentsTab workId={workId!} />
            </TabsContent>
            <TabsContent value="knowledge">
              <KnowledgeTab workId={workId!} />
            </TabsContent>
            <TabsContent value="tasks">
              <TasksTab workId={workId!} />
            </TabsContent>
            <TabsContent value="conversations">
              <ConversationsTab workId={workId!} />
            </TabsContent>
          </div>
        </Tabs>
      </div>
    </div>
  );
}

// Separate components for tabs to keep file clean and lazy load data ideally

function DocumentsTab({ workId }: { workId: string }) {
  const { data: docsResp, isLoading } = useGetWorkDocuments(workId, { query: { enabled: !!workId, queryKey: getGetWorkDocumentsQueryKey(workId) } });
  
  if (isLoading) return <Skeleton className="h-64 w-full" />;
  
  const docs = docsResp?.documents || [];

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-xl font-serif font-medium">Source Material</h3>
        <Button size="sm" variant="outline" className="gap-2">
          <Plus className="w-4 h-4" /> Add Document
        </Button>
      </div>
      
      {docs.length > 0 ? (
        <div className="grid gap-3">
          {docs.map(doc => (
            <Card key={doc.id} className="hover-elevate cursor-pointer">
              <CardContent className="p-4 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <FileText className="w-5 h-5 text-muted-foreground" />
                  <div>
                    <h4 className="font-medium">{doc.title || doc.source || 'Untitled'}</h4>
                    <div className="flex gap-2 mt-1">
                      <Badge variant="secondary" className="text-[10px] uppercase font-mono">{doc.kind}</Badge>
                      <Badge variant="outline" className="text-[10px] uppercase font-mono">{doc.readiness}</Badge>
                    </div>
                  </div>
                </div>
                <div className="text-xs font-mono text-muted-foreground">
                  {doc.created_at ? format(new Date(doc.created_at), 'MMM d, yyyy') : ''}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <p className="text-muted-foreground">No documents added to this work yet.</p>
        </div>
      )}
    </div>
  );
}

function KnowledgeTab({ workId }: { workId: string }) {
  const { data: knowResp, isLoading } = useGetWorkKnowledge(workId, {}, { query: { enabled: !!workId, queryKey: getGetWorkKnowledgeQueryKey(workId, {}) } });
  
  if (isLoading) return <Skeleton className="h-64 w-full" />;
  
  const knowledge = knowResp?.knowledge || [];

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-xl font-serif font-medium">Structured Knowledge</h3>
      </div>
      
      {knowledge.length > 0 ? (
        <div className="grid gap-3">
          {knowledge.map(item => (
            <Card key={item.id}>
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="flex items-center gap-2 mb-2">
                      <Badge variant="outline" className="text-[10px] uppercase font-mono border-primary/30 text-primary">{item.kind}</Badge>
                      <Badge variant="secondary" className="text-[10px] uppercase font-mono">{item.review_status}</Badge>
                    </div>
                    {item.subject && item.predicate && item.object ? (
                      <div className="font-mono text-sm bg-muted/30 p-2 rounded border border-border/50">
                        <span className="font-semibold text-primary">{item.subject}</span>{" "}
                        <span className="text-muted-foreground">{item.predicate}</span>{" "}
                        <span className="font-semibold">{item.object}</span>
                      </div>
                    ) : (
                      <p className="text-sm font-serif leading-relaxed">{item.text}</p>
                    )}
                  </div>
                  {item.confidence && (
                    <div className="text-xs font-mono px-2 py-1 bg-muted rounded">
                      {(item.confidence * 100).toFixed(0)}%
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <p className="text-muted-foreground">No knowledge extracted yet.</p>
        </div>
      )}
    </div>
  );
}

function TasksTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const { data: tasksResp, isLoading } = useGetWorkTasks(workId, {}, { query: { enabled: !!workId, queryKey: getGetWorkTasksQueryKey(workId) } });
  const createTask = useCreateWorkTask();
  const [newTaskText, setNewTaskText] = useState("");

  const handleAddTask = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTaskText.trim()) return;
    createTask.mutate({ workId, data: { text: newTaskText } }, {
      onSuccess: () => {
        setNewTaskText("");
        queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) });
      }
    });
  };
  
  if (isLoading) return <Skeleton className="h-64 w-full" />;
  
  const tasks = tasksResp?.tasks || [];

  return (
    <div className="space-y-6 max-w-3xl">
      <form onSubmit={handleAddTask} className="flex gap-2">
        <Input 
          placeholder="Add a new task..." 
          value={newTaskText}
          onChange={(e) => setNewTaskText(e.target.value)}
          className="bg-background/50"
        />
        <Button type="submit" disabled={!newTaskText.trim() || createTask.isPending}>Add</Button>
      </form>

      <div className="space-y-2">
        {tasks.length > 0 ? (
          tasks.map(task => (
            <div key={task.id} className="flex items-start gap-3 p-3 rounded-lg hover:bg-muted/30 transition-colors group border border-transparent hover:border-border/50">
              <Checkbox id={task.id} className="mt-1" checked={task.status === 'completed'} />
              <div className="flex-1 space-y-1">
                <label 
                  htmlFor={task.id}
                  className={`text-sm font-medium leading-none cursor-pointer ${task.status === 'completed' ? 'line-through text-muted-foreground' : ''}`}
                >
                  {task.text}
                </label>
              </div>
              <Badge variant="outline" className="text-[9px] uppercase font-mono opacity-0 group-hover:opacity-100 transition-opacity">
                Priority {task.priority || 0}
              </Badge>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground italic">No tasks pending for this work.</p>
        )}
      </div>
    </div>
  );
}

function ConversationsTab({ workId }: { workId: string }) {
  const { data: convResp, isLoading } = useGetWorkConversations(workId, { query: { enabled: !!workId, queryKey: getGetWorkConversationsQueryKey(workId) } });
  
  if (isLoading) return <Skeleton className="h-64 w-full" />;
  
  const conversations = convResp?.conversations || [];

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-xl font-serif font-medium">Conversations</h3>
        <Button size="sm" variant="outline" className="gap-2">
          <Plus className="w-4 h-4" /> New Discussion
        </Button>
      </div>
      
      {conversations.length > 0 ? (
        <div className="grid gap-3">
          {conversations.map(conv => (
            <Link key={conv.id} href={`/chat?id=${conv.id}`}>
              <Card className="hover-elevate cursor-pointer">
                <CardContent className="p-4 flex items-center justify-between">
                  <div className="space-y-1">
                    <h4 className="font-medium text-lg">{conv.title || 'Untitled Conversation'}</h4>
                    <p className="text-sm text-muted-foreground truncate max-w-xl">
                      {conv.last_message || 'No messages yet.'}
                    </p>
                  </div>
                  <div className="text-right text-xs font-mono text-muted-foreground space-y-1 shrink-0">
                    <div>{conv.message_count || 0} msgs</div>
                    <div>{conv.updated_at ? format(new Date(conv.updated_at), 'MMM d') : ''}</div>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <p className="text-muted-foreground">No conversations linked to this work.</p>
        </div>
      )}
    </div>
  );
}
